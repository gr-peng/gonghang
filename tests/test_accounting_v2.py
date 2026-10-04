import importlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'AI_accounting_agent/backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
from accounting_schema import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from rebuild_accounting_data import corpus, make_ledger
from datetime import date


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_BOOKKEEPER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AI_BOOKKEEPER_IMPORT_JSONL', 'false')
    monkeypatch.setenv('LLM_PROVIDER', 'api')
    monkeypatch.delenv('LLM_HEALTH_URL', raising=False)
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    yield module
    module._db_conn.close()


def test_all_synthetic_input_amounts_match_labels_and_categories():
    for split in ['train', 'validation', 'test']:
        rows = corpus(split, 8)
        seen = set()
        for row in rows:
            if row['task'] != 'extract': continue
            payload = json.loads(row['messages'][-1]['content'])
            amounts = re.findall(r'(\d+(?:\.\d+)?)元', row['messages'][-2]['content'])
            assert amounts and Decimal(amounts[0]) == Decimal(str(payload['amount']))
            assert payload['category'] in (INCOME_CATEGORIES if payload['type']=='income' else EXPENSE_CATEGORIES)
            seen.add(payload['category'])
        assert seen == set(EXPENSE_CATEGORIES + INCOME_CATEGORIES)


def test_months_refunds_and_salary_are_coherent():
    rows = make_ledger(date(2026,10,3))
    assert rows == make_ledger(date(2026,10,3))
    assert all(r['event_date'] <= '2026-10-03' and r['amount'] > 0 for r in rows)
    salary = [r for r in rows if r['category']=='工资']
    assert len(salary) == 12
    assert len({r['event_date'][:7] for r in salary}) == 12
    for refund in [r for r in rows if r['category']=='退款']:
        assert any(r['category']=='购物' and r['amount']==refund['amount'] and r['event_date'][:7]==refund['event_date'][:7] and r['event_date']<refund['event_date'] for r in rows)


def test_reports_preserve_new_categories_and_income(app):
    client = TestClient(app.app)
    for category, kind, amount in [('住房','expense',5800),('医疗健康','expense',38.5),('工资','income',22500)]:
        response=client.post('/bills',json=dict(event_date='2026-09-01',category=category,type=kind,amount=amount,currency='CNY',description=category))
        assert response.status_code == 201
    report=client.get('/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30').json()['custom']
    assert report['summary']['expense_total']==5838.5
    assert report['summary']['income_total']==22500
    assert {r['name']:r['value'] for r in report['pie']} == {'住房':5800,'医疗健康':38.5}
    assert app._normalize_category('工资')=='工资'
    assert app._normalize_category('医疗健康')=='医疗健康'


def test_parse_validates_model_output_and_never_writes(app, monkeypatch):
    client=TestClient(app.app)
    payload=dict(event_date='2026-10-03',category='工资',type='income',amount=22500,currency='CNY',description='工资',payment_method='银行卡')
    monkeypatch.setattr(app, 'api_chat', lambda *a,**k: json.dumps(payload,ensure_ascii=False))
    response=client.post('/bills/parse',json={'text':'今天银行卡收到工资22500元','reference_date':'2026-10-03'})
    assert response.status_code==200 and response.json()['category']=='工资'
    assert response.json()['payment_method']=='银行卡'
    assert client.get('/bills').json()==[]
    payload['category']='餐饮'
    assert client.post('/bills/parse',json={'text':'工资22500元','reference_date':'2026-10-03'}).status_code==502
    monkeypatch.setattr(app,'api_chat',lambda *a,**k:'{"needs_clarification":true,"reason":"请提供金额"}')
    assert client.post('/bills/parse',json={'text':'午餐','reference_date':'2026-10-03'}).json()['needs_clarification'] is True


def test_no_mixed_currency_aggregation(app):
    client=TestClient(app.app)
    payload=dict(event_date='2026-10-03',category='其他',type='expense',amount=50,currency='USD',description='测试')
    assert client.post('/bills',json=payload).status_code==422
    payload['currency']='元'
    assert client.post('/bills',json=payload).json()['currency']=='CNY'
    payload.update(category='工资',currency='CNY',type='expense')
    assert client.post('/bills',json=payload).status_code==422
