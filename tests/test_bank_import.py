import base64
import importlib
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'AI_accounting_agent/backend'))
from bank_import import parse_csv


def encoded(text, encoding='utf-8-sig'):
    return base64.b64encode(text.encode(encoding)).decode()


def test_csv_keeps_repeated_purchases_and_does_not_guess_direction():
    rows = parse_csv(encoded('日期,金额,摘要\n2026-09-01,35.50,午餐\n2026-09-01,35.50,午餐\n'), '账户甲')
    assert len(rows) == 2 and rows[0]['import_key'] != rows[1]['import_key']
    assert all(r['type'] == '' and r['errors'] for r in rows)
    rows = parse_csv(encoded('交易时间,交易金额,币种,备注,账号\n2026-09-01 12:30:00,-35.50,CNY,午餐,private-12345\n', 'gb18030'), '甲')
    assert rows[0]['type'] == 'expense' and rows[0]['amount'] == '35.50'
    assert 'private-12345' not in json.dumps(rows)
    rows = parse_csv(encoded('日期,收入金额,支出金额,摘要\n2026-09-01,6800,0,工资\n2026-09-02,0,35.50,午餐\n'), '甲')
    assert [(r['type'], r['category']) for r in rows] == [('income','工资'),('expense','餐饮')]


@pytest.mark.parametrize('amount', ['0', '0.001', 'nan', '1e3', '1,00.00'])
def test_csv_invalid_amount_requires_correction(amount):
    rows = parse_csv(encoded(f'日期,金额,收支\n2026-09-01,"{amount}",支出\n'), '甲')
    assert rows[0]['errors'] and rows[0]['amount'] == ''


def test_csv_rejects_currency_and_duplicate_transaction_ids():
    with pytest.raises(ValueError, match='不是人民币'):
        parse_csv(encoded('日期,金额,币种\n2026-09-01,1,USD\n'), '甲')
    with pytest.raises(ValueError, match='重复流水号'):
        parse_csv(encoded('日期,金额,流水号\n2026-09-01,1,A\n2026-09-02,2,A\n'), '甲')


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_BOOKKEEPER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AI_BOOKKEEPER_IMPORT_JSONL', 'false')
    monkeypatch.setenv('LLM_PROVIDER', 'disabled')
    monkeypatch.setenv('FINANCE_PUBLIC_ORIGINS', 'http://testserver')
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    with TestClient(module.app) as client:
        session = client.get('/finance/session').json()
        client.headers.update({'Origin':'http://testserver','X-Qingcai-CSRF':session['csrf']})
        yield client, module
    module._db_conn.close()


def preview(client, text):
    response = client.post('/finance/imports/preview', json={'content':encoded(text),'account_label':'工资卡'})
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, p, **kwargs):
    rows = [{key:r[key] for key in ('row','event_date','type','amount','category','description','movement')} for r in p['rows']]
    return client.post(f"/finance/imports/{p['id']}/commit", json={'challenge':p['challenge'],'rows':rows,**kwargs})


STATEMENT = '日期,收支,金额,摘要,分类,流水号,资金性质\n2026-09-01,收入,6800.00,工资,工资,A,收支\n2026-09-02,支出,35.50,午餐,餐饮,B,收支\n2026-09-03,支出,500.00,自己的另一账户,其他,C,内部划转\n'


def test_preview_confirmation_scope_dedup_and_internal_principal(workspace):
    client, module = workspace
    client.post('/bills', json={'event_date':'2026-09-01','category':'工资','type':'income','amount':99999,'description':'示例工资','source':'synthetic_v2'})
    p = preview(client, STATEMENT)
    assert client.get('/health').json()['bill_count'] == 1  # preview never writes
    assert commit(client, p, challenge='wrong-challenge-0000000').status_code == 409
    result = commit(client,p)
    assert result.status_code == 200 and result.json()['created'] == 3
    assert commit(client,p).json() == result.json()
    assert client.get('/health').json()['bill_count'] == 4
    assert client.get('/health').json()['visible_bill_count'] == 3
    assert client.get('/health').json()['dataset']['synthetic'] is False
    assert client.get('/finance/overview').json()['profile']['ledger_scope'] == 'personal'
    report = client.get('/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30').json()['custom']
    assert report['summary'] == {'income_total':6800.0,'expense_total':35.5}
    assert client.get('/bills/summary').json()['total_expense'] == 35.5
    flow = client.get('/finance/overview').json()['cashflow']
    assert flow['observed_months'] == 1 and flow['monthly_income_minor'] == 680000
    assert flow['monthly_expense_minor'] == 3550
    assert '99999' not in module.build_retrieval_context()
    again = commit(client, preview(client,STATEMENT))
    assert again.json()['created'] == 0 and again.json()['duplicates'] == 3
    assert client.get('/health').json()['bill_count'] == 4


def test_import_conflict_rolls_back_batch_and_cannot_cross_session(workspace):
    client, module = workspace
    p = preview(client, STATEMENT)
    second = TestClient(module.app)
    try:
        s=second.get('/finance/session').json()
        second.headers.update({'Origin':'http://testserver','X-Qingcai-CSRF':s['csrf']})
        assert commit(second,p).status_code == 404
    finally:
        second.close()
    assert commit(client,p).status_code == 200
    conflict = '日期,收支,金额,摘要,分类,流水号\n2026-09-04,支出,10,新交易,其他,NEW\n2026-09-01,收入,8800,工资,工资,A\n'
    assert commit(client,preview(client,conflict)).status_code == 409
    assert client.get('/health').json()['bill_count'] == 3


def test_import_recovers_after_ledger_commit_and_lost_result(workspace, monkeypatch):
    client,module=workspace
    service=module.finance_router.workspace()
    original=service.import_bills
    def lost(records):
        original(records)
        raise RuntimeError('lost result')
    p=preview(client,STATEMENT)
    monkeypatch.setattr(service,'import_bills',lost)
    with pytest.raises(RuntimeError):
        commit(client,p)
    assert client.get('/health').json()['bill_count'] == 3
    monkeypatch.setattr(service,'import_bills',original)
    result=commit(client,p)
    assert result.status_code == 200 and result.json()['created'] == 3
    assert client.get('/health').json()['bill_count'] == 3


def test_goal_progress_is_persistent_idempotent_and_never_moves_money(workspace):
    client,module=workspace
    profile={'goal_action':'carry','goal_name':'旅行','goal_amount':'5000.00','goal_recorded_amount':'100.00','goal_percent':30,'goal_cycle':'week'}
    client.post('/finance/profile',json=profile)
    before=client.get('/finance/overview').json()['assets_minor']
    data={'amount':'50.25','request_id':'goal-progress-one'}
    result=client.post('/finance/goal/progress',json=data)
    assert result.json() == {'goal_recorded_amount':'150.25','bank_effect':False}
    assert client.post('/finance/goal/progress',json=data).json() == result.json()
    assert client.post('/finance/goal/progress',json={**data,'amount':'51'}).status_code == 409
    module.finance_router.reset()
    after=client.get('/finance/overview').json()
    assert after['profile']['goal_recorded_amount']=='150.25' and after['assets_minor']==before
