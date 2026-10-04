"""Inventory arithmetic, missing data, concurrency and separation from money movement."""
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from test_finance_workspace import workspace
from wealth import today


def account(client, **overrides):
    body = dict(kind='asset', category='cash', name='日常账户', amount='20000',
                available='18000', emergency='6000', goal='2000', other_reserved='1000',
                as_of=today().isoformat(), request_id='account-test-1')
    body.update(overrides)
    response = client.post('/finance/wealth/accounts', json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def confirm(client):
    view = client.get('/finance/wealth').json()
    response = client.post('/finance/wealth/confirm', json={'expected_revision': view['revision'], 'complete': True})
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_is_unknown_until_explicit_zero_inventory(workspace):
    client, _ = workspace
    view = client.get('/finance/wealth').json()
    assert view['net_minor'] is None and view['available_minor'] is None
    assert confirm(client)['available_minor'] == 0


def test_net_worth_liquidity_reservations_and_no_bank_or_ledger_effect(workspace):
    client, _ = workspace
    before = client.get('/finance/overview').json()
    bills = client.get('/bills').json()
    account(client)
    account(client, name='基金估值', category='fund', amount='7000', available=None,
            emergency='0', goal='0', other_reserved='0', request_id='fund-account')
    account(client, name='信用卡', kind='liability', category='credit', amount='4000',
            available=None, due='1500', emergency='0', goal='0', other_reserved='0', request_id='debt-account')
    pending = client.get('/finance/wealth').json()
    assert pending['net_minor'] == 2300000 and pending['available_minor'] is None
    view = confirm(client)
    assert view['available_minor'] == 750000 and view['shortfall_minor'] == 0
    after = client.get('/finance/overview').json()
    assert after['wealth']['planning']['emergency_target_minor'] is None  # Demo ledger cannot fund personal wealth.
    for field in ['cash_minor', 'assets_minor', 'investable_minor', 'products', 'operations', 'planning']:
        assert after[field] == before[field]
    assert client.get('/bills').json() == bills


def test_shortfall_and_negative_net_worth_are_not_hidden(workspace):
    client, _ = workspace
    account(client, amount='20', available='20', emergency='0', goal='0', other_reserved='0')
    account(client, name='贷款', kind='liability', category='loan', amount='1000', due='80',
            available=None, emergency='0', goal='0', other_reserved='0', request_id='debt-account')
    view = confirm(client)
    assert view['net_minor'] == -98000 and view['available_minor'] == 0 and view['shortfall_minor'] == 6000


@pytest.mark.parametrize('fields', [
    {'available': None, 'emergency': '0', 'goal': '0', 'other_reserved': '0'},
    {'kind': 'liability', 'category': 'loan', 'available': None, 'emergency': '0', 'goal': '0', 'other_reserved': '0'},
    {'as_of': (today()-timedelta(days=31)).isoformat()},
])
def test_missing_or_stale_never_releases_cash(workspace, fields):
    client, _ = workspace
    account(client, **fields)
    view = confirm(client)
    assert not view['ready'] and view['available_minor'] is None


def test_confirmation_expires_and_survives_restart(workspace, monkeypatch):
    client, module = workspace
    account(client)
    assert confirm(client)['ready']
    module.finance_router.reset()
    assert client.get('/finance/wealth').json()['ready']
    import wealth
    current = today()
    monkeypatch.setattr(wealth, 'today', lambda: current+timedelta(days=31))
    view = client.get('/finance/wealth').json()
    assert not view['current'] and view['available_minor'] is None


def test_idempotency_duplicate_alias_cas_delete_and_invalidation(workspace):
    client, _ = workspace
    entry, body = account(client)
    assert client.post('/finance/wealth/accounts', json=body).json()['id'] == entry['id']
    assert client.post('/finance/wealth/accounts', json={**body, 'amount':'20001'}).status_code == 409
    assert client.post('/finance/wealth/accounts', json={**body, 'request_id':'duplicate-2', 'name':' 日常账户 '}).status_code == 409
    revision = confirm(client)['revision']
    edit = {k:v for k,v in body.items() if k != 'request_id'}
    edit.update(amount='21000', expected_version=entry['version'])
    url = '/finance/wealth/accounts/'+entry['id']
    result = client.put(url, json=edit)
    assert result.status_code == 200
    assert result.json()['available_minor'] is None
    assert client.put(url, json=edit).status_code == 409
    assert client.post('/finance/wealth/confirm', json={'expected_revision': revision, 'complete': True}).status_code == 409
    assert client.request('DELETE', url, json={'expected_version': 1}).status_code == 409
    assert client.request('DELETE', url, json={'expected_version': 2}).status_code == 200
    assert client.get('/finance/wealth').json()['net_minor'] is None
    assert client.post('/finance/wealth/accounts', json=body).status_code == 409


@pytest.mark.parametrize('override', [
    {'amount': -1}, {'amount': 1.5}, {'amount': '0.001'}, {'amount': '-1'},
    {'amount': 'NaN'}, {'available':'21000'}, {'emergency':'18001'},
    {'as_of': (today()+timedelta(days=1)).isoformat()}, {'category': 'credit'},
    {'category': 'fund'}, {'due':'1'}, {'name':'   '}, {'source':'bank'},
])
def test_invalid_inventory_is_rejected(workspace, override):
    client, _ = workspace
    body = dict(kind='asset', category='cash', name='账户', amount='20000',
                available='18000', as_of=today().isoformat(), request_id='invalid-account')
    assert client.post('/finance/wealth/accounts', json={**body, **override}).status_code == 422
    assert not client.get('/finance/wealth').json()['accounts']


def test_scenario_decimal_rounding_and_no_side_effect(workspace):
    client, _ = workspace
    before = client.get('/finance/overview').json()
    result = client.post('/finance/wealth/scenario', json={'principal':'10000', 'change_percent':'-10', 'fee_percent':'0.5'})
    assert result.status_code == 200
    view = result.json()
    assert (view['fee_minor'], view['invested_minor'], view['final_minor'], view['change_minor']) == (5000,995000,895500,-104500)
    assert client.get('/finance/overview').json() == before
    half = client.post('/finance/wealth/scenario', json={'principal':'1', 'change_percent':'0', 'fee_percent':'0.5'}).json()
    assert half['fee_minor'] == 1 and half['final_minor'] == 99
    for value in ['-100.01','101','NaN']:
        assert client.post('/finance/wealth/scenario', json={'principal':'1', 'change_percent':value, 'fee_percent':'0'}).status_code == 422


def test_authenticated_origin_bound_and_grounded_chat(workspace):
    client, module = workspace
    other = TestClient(module.app)
    try:
        assert other.get('/finance/wealth').status_code == 401
    finally:
        other.close()
    client.headers['Origin'] = 'https://foreign.example'
    assert client.post('/finance/wealth/confirm', json={'expected_revision':0, 'complete':True}).status_code == 403
    client.headers['Origin'] = 'http://testserver'
    account(client)
    confirm(client)
    reply = client.post('/finance/assistant', json={'messages':[{'role':'user','content':'我的净资产和可动用资金是多少？'}]}).json()
    assert reply['draft'] is None and reply['plan_next'] == 'wealth'
    assert '20,000.00' in reply['reply'] and '9,000.00' in reply['reply']


def personal_plan(client):
    from finance_workspace import date, month_shift
    for offset in [-3,-2,-1]:
        for kind, category, amount in [('income','工资',4000),('expense','住房',3000)]:
            r=client.post('/bills',json={'event_date':month_shift(date.today(),offset).isoformat(),
                'amount':amount,'type':kind,'category':category,'currency':'CNY','description':'个人隔离记录'})
            assert r.status_code == 201
    profile=client.get('/finance/overview').json()['profile']
    r=client.post('/finance/profile',json={**profile,'ledger_scope':'personal','cashflow_confirmed':True,'reserve_months':3})
    assert r.status_code == 200
    # Switching ledgers intentionally invalidates the previous completeness
    # declaration. Confirm the newly selected personal ledger separately.
    assert r.json()['profile']['cashflow_confirmed'] is False
    r=client.post('/finance/profile',json={**r.json()['profile'],'cashflow_confirmed':True})
    assert r.status_code == 200


def test_personal_ledger_links_reserve_gap_without_adding_flows(workspace):
    client, _ = workspace
    account(client)
    confirm(client)
    personal_plan(client)
    view=client.get('/finance/wealth').json()
    assert view['planning']['emergency_target_minor'] == 900000
    assert view['planning']['emergency_gap_minor'] == 300000
    assert view['net_minor'] == 2000000 and view['available_minor'] == 900000
    profile=client.get('/finance/overview').json()['profile']
    client.post('/finance/profile',json={**profile,'cashflow_confirmed':False})
    assert client.get('/finance/wealth').json()['planning']['emergency_target_minor'] is None


def test_plan_explanation_uses_declared_reserve_without_sending_accounts_to_model(workspace, monkeypatch):
    import json
    import finance_workspace
    client, _ = workspace
    account(client, name='私有资产简称')
    confirm(client)
    personal_plan(client)
    seen=[]
    def model(messages, **kwargs):
        seen.append(messages)
        return json.dumps({'intent':'plan','recipient':None,'product_id':None,'amount':None})
    monkeypatch.setattr(finance_workspace,'api_chat',model)
    reply=client.post('/finance/assistant',json={'messages':[{'role':'user','content':'应急金准备得怎样？'}]}).json()
    assert '手动登记已预留 6,000.00 元，尚差 3,000.00 元' in reply['reply']
    assert '实际已预留金额尚未核对' not in reply['reply']
    assert '私有资产简称' not in json.dumps(seen,ensure_ascii=False)
    assert 'wealth' not in json.dumps(seen)
