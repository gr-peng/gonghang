"""Regressions for failures reproduced in the October judge walkthrough."""
import json
from pathlib import Path
import pytest
from test_finance_planning import planning_workspace, bill, profile, seed_months
from test_bank_import import workspace, preview, commit, STATEMENT
from research_quality import valid_quote, quote_quality, grounded_report, stock_facts


@pytest.mark.parametrize('text', ['今天午饭35元，打车18元，帮我记两笔。','午饭花35元，另外打车花18元','午饭35，打车18','午饭35元打车18元','记三笔：工资6800元，吃饭35元，交通18元'])
def test_multi_bill_never_saves_partial_draft(workspace, monkeypatch, text):
    client, app = workspace
    monkeypatch.setattr(app, 'api_chat', lambda *a, **k: pytest.fail('Ambiguous multi-entry must not call model'))
    result = client.post('/bills/parse', json={'text':text, 'reference_date':'2026-10-04'})
    assert result.status_code == 200, result.text
    assert result.json()['needs_clarification'] and result.json()['code']=='multiple_transactions'
    assert 'amount' not in result.json() and client.get('/health').json()['bill_count']==0


@pytest.mark.parametrize('text', ['买两杯奶茶共35元','原价35元优惠5元，实付30元','衣服退回80元','午饭35元'])
def test_single_bill_is_not_mistaken_for_batch(text):
    from accounting_schema import multiple_bill_reason
    assert not multiple_bill_reason(text)


def test_salary_only_and_unconfirmed_expenses_cannot_unlock_longterm(planning_workspace):
    c, m = planning_workspace
    for month in (7,8,9): bill(c,6800,'income','工资',day=f'2026-{month:02d}-05')
    s=profile(c,risk_answers=[2]*4,cashflow_confirmed=True)
    assert not s['planning']['ready'] and s['investable_minor']==0
    assert s['planning']['emergency']['status']=='needs_expenses'
    assert not s['products'][1]['eligible']
    assert c.post('/finance/operations/review',json={'kind':'subscribe','product_id':'growth','amount':'1','request_id':'salary-only-test'}).status_code==409
    for month in (7,8,9): bill(c,2000,'expense','住房',day=f'2026-{month:02d}-06')
    s=profile(c,cashflow_confirmed=False)
    assert not s['planning']['ready'] and not s['products'][1]['eligible']
    s=profile(c,cashflow_confirmed=True,debt_monthly_amount='800')
    assert s['planning']['ready'] and s['products'][1]['eligible']
    assert s['planning']['monthly']['available_minor']==400000
    assert s['planning']['emergency']['target_minor']==840000
    assert m.finance_router.workspace().bank.snapshot()['execution_count']==0


def test_goal_change_is_explicit_and_progress_undo_is_idempotent(workspace):
    c,m=workspace
    old=profile(c,goal_name='旅行',goal_amount='12000',goal_months=12)['profile']
    def progress(rid,amount,goal=old['goal_id']):
        return c.post('/finance/goal/progress',json={'amount':amount,'request_id':rid,'goal_id':goal})
    assert progress('travel-3000','3000').status_code==200
    stale=profile(c,**old)['profile']
    assert stale['goal_recorded_amount']=='3000.00'  # Saving stale fields cannot erase progress.
    changed={**stale,'goal_name':'电脑','goal_amount':'3000'}
    assert c.post('/finance/profile',json=changed).status_code==409
    new=profile(c,**changed,goal_action='replace')['profile']
    assert new['goal_recorded_amount']=='0' and new['goal_id']!=old['goal_id']
    assert progress('travel-late','50').status_code==409
    assert c.post('/finance/goal/progress/travel-3000/undo',json={}).status_code==404
    assert progress('computer-200','200',new['goal_id']).status_code==200
    records=c.get('/finance/goal/progress').json()['records'];assert len(records)==1
    for _ in range(2): assert c.post('/finance/goal/progress/computer-200/undo',json={}).json()['goal_recorded_amount']=='0.00'
    assert progress('computer-200','200',new['goal_id']).status_code==409
    assert c.get('/finance/overview').json()['cash_minor']==5000000


def test_goal_scenario_is_read_only_and_does_not_require_bank_parameters(workspace,monkeypatch):
    c,m=workspace
    old=profile(c,goal_name='旅行',goal_amount='12000',goal_months=12)['profile']
    c.post('/finance/goal/progress',json={'amount':'3000','request_id':'scenario-progress','goal_id':old['goal_id']})
    before=c.get('/finance/overview').json()
    import finance_workspace
    monkeypatch.setattr(finance_workspace,'api_chat',lambda *a,**k:pytest.fail('Simple scenario uses verified goal arithmetic'))
    result=c.post('/finance/assistant',json={'messages':[{'role':'user','content':'从12个月缩短为6个月，每月留多少？只测算'}]}).json()
    assert result['scenario']=={'months':6,'monthly_required_minor':150000,'remaining_minor':900000}
    assert result['draft'] is None and '1,500.00' in result['reply']
    after=c.get('/finance/overview').json()
    assert before['profile']==after['profile'] and before['cash_minor']==after['cash_minor']


def test_bill_edit_retains_import_identity_and_detects_stale_writes(workspace):
    c,m=workspace
    assert commit(c,preview(c,STATEMENT)).status_code==200
    row=next(x for x in c.get('/bills').json() if x['type']=='expense' and x['category']=='餐饮')
    data={k:row[k] for k in ['event_date','amount','type','category','description','currency']}
    data.update(amount=38.60,description='校正午餐',payment_method='微信支付',expected_version=row['version'])
    result=c.put('/bills/'+str(row['id']),json=data)
    assert result.status_code==200,result.text
    updated=result.json();assert updated['id']==row['id'] and updated['amount']==38.6
    assert updated['source']==row['source']
    for k,v in row['metadata'].items():
        if k!='payment_method': assert updated['metadata'][k]==v
    assert updated['metadata']['payment_method']=='微信支付'
    assert c.put('/bills/'+str(row['id']),json={**data,'amount':77}).status_code==409
    assert m._db_conn.execute('SELECT count(*) FROM bill_revisions').fetchone()[0]==1
    assert c.get('/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30').json()['custom']['summary']['expense_total']==38.6
    assert c.get('/health').json()['bill_count']==3


def test_selected_range_is_the_only_chat_ledger_context(workspace,monkeypatch):
    c,m=workspace
    bill(c,1000,'income','工资');bill(c,50,'expense','餐饮')
    bill(c,99999,'income','工资',day='2026-10-01')
    captured=[]
    monkeypatch.setattr(m,'api_chat',lambda messages,*a,**k:captured.extend(messages) or '核对完成')
    result=c.post('/chat',json={'messages':[{'role':'user','content':'这个月收支怎么样'}], 'ledger_range':{'start_date':'2026-09-01','end_date':'2026-09-30'}})
    assert result.status_code==200,result.text
    context='\n'.join(x['content'] for x in captured if x['role']=='system')
    assert '2026-09-30' in context and '1000.0' in context and '950.0' in context and '99999' not in context
    assert c.post('/chat',json={'messages':[], 'ledger_range':{'start_date':'2026-09-30','end_date':'2026-09-01'}}).status_code==422
    assert not c.get('/health').json()['capabilities']['ocr']


def test_bad_quotes_and_unverifiable_model_report_are_never_rendered():
    good={'date':'2025-12-04','open':'43','high':'45','low':'42','close':'44','volume':'361207'}
    bad={**good,'high':'43.22','low':'43.42'}
    assert valid_quote(good) and not valid_quote(bad)
    assert not valid_quote({**good,'close':'nan'})
    q=quote_quality([bad,good]);assert q['excluded']==1 and q['valid']==1
    sections=stock_facts('测试', [good],[],[],q)
    result=grounded_report(sections,lambda _:'2025年12月15日成交额36.12亿元')
    assert result['grounding_status']=='verified_summary'
    assert '36.12' not in result['reply'] and '2025-12-04' in result['reply']
    result=grounded_report(sections,lambda catalog:json.dumps({k:list(v)[:1] for k,v in catalog.items()}))
    assert result['grounding_status']=='model_grounded' and '不作单位换算' in result['reply']
