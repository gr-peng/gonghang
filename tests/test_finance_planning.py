"""Ledger-derived planning is inspectable and never creates banking authority."""
from datetime import date
import json

import pytest

from test_bank_import import workspace


@pytest.fixture()
def planning_workspace(workspace, monkeypatch):
    import finance_workspace

    class PlanningDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 3)

    monkeypatch.setattr(finance_workspace, 'date', PlanningDate)
    return workspace


def bill(client, amount, kind, category, *, day='2026-09-15', source='manual', currency='CNY', movement='cashflow'):
    result = client.post('/bills', json={
        'event_date': day, 'amount': amount, 'type': kind, 'category': category,
        'description': '隔离测试账单', 'source': source, 'currency': currency,
        'metadata': {'movement': movement},
    })
    assert result.status_code == 201, result.text


def seed_months(client, income=4000, expense=1000):
    for month in (7, 8, 9):
        bill(client, income, 'income', '工资', day=f'2026-{month:02d}-01')
        bill(client, expense, 'expense', '住房', day=f'2026-{month:02d}-05')


def profile(client, **changes):
    current = client.get('/finance/overview').json()['profile']
    result = client.post('/finance/profile', json={**current, 'cashflow_confirmed':True, **changes})
    assert result.status_code == 200, result.text
    return result.json()


def test_plan_uses_ledger_monthly_flow_and_keeps_emergency_funding_unknown(planning_workspace):
    client, module = planning_workspace
    seed_months(client)
    result = profile(client, goal_name='旅行', goal_amount='1200', goal_months=6)
    plan = result['planning']
    assert plan['currency'] == 'CNY' and plan['bank_effect'] is False
    assert plan['basis'] == {
        'kind': 'observed_monthly_average', 'scope': 'demo', 'start_date': '2026-04-01',
        'end_date': '2026-09-30', 'observed_months': 3,
    }
    assert plan['monthly'] == {
        'income_minor': 400000, 'net_expense_minor': 100000, 'gross_expense_minor': 100000,
        'net_minor': 300000, 'commitment_extra_minor': 0, 'debt_minor': 0, 'available_minor': 300000,
    }
    assert plan['goal'] == {
        'target_minor': 120000, 'recorded_minor': 0, 'remaining_minor': 120000, 'months': 6,
        'monthly_required_minor': 20000, 'monthly_allocated_minor': 20000,
        'shortfall_minor': 0, 'feasible': True,
    }
    assert plan['emergency'] == {
        'target_minor': 300000, 'recorded_minor': None, 'gap_minor': None, 'status': 'unconfirmed',
    }
    assert plan['after_goal_minor'] == 280000
    assert result['cash_minor'] == 5000000  # Independent initialized simulation stock.
    assert result['goal_monthly_minor'] == plan['goal']['monthly_required_minor']
    assert module.finance_router.workspace().bank.snapshot()['execution_count'] == 0


def test_goal_progress_reduces_monthly_need_and_completed_goal_stops_reserving(planning_workspace):
    client, module = planning_workspace
    seed_months(client, income=1000, expense=800)
    initial = profile(client, goal_name='学费', goal_amount='1200.01', goal_months=3)
    assert initial['goal_monthly_minor'] == 40001  # Round up to a sufficient whole cent.
    assert initial['planning']['goal']['monthly_allocated_minor'] == 20000
    assert initial['planning']['goal']['shortfall_minor'] == 20001
    assert initial['goal_feasible'] is False
    assert any(r['id'] == 'goal_capacity' for r in initial['reasons'])
    progress = client.post('/finance/goal/progress', json={'amount': '600.01', 'request_id': 'plan-progress-first'})
    assert progress.status_code == 200 and progress.json()['bank_effect'] is False
    partial = client.get('/finance/overview').json()
    assert partial['goal_monthly_minor'] == 20000 and partial['goal_feasible'] is True
    assert partial['planning']['goal']['remaining_minor'] == 60000
    assert not any(r['id'] == 'goal_capacity' for r in partial['reasons'])
    client.post('/finance/goal/progress', json={'amount': '700', 'request_id': 'plan-progress-over-target'})
    complete = client.get('/finance/overview').json()
    assert complete['planning']['goal']['recorded_minor'] == 130001
    assert complete['planning']['goal']['remaining_minor'] == 0
    assert complete['goal_monthly_minor'] == 0 and complete['goal_feasible'] is True
    assert complete['planning']['after_goal_minor'] == 20000
    assert not any(r['id'] in {'goal', 'goal_capacity'} for r in complete['reasons'])
    assert complete['goal_buffer_minor'] == initial['goal_buffer_minor'] == 120001
    assert complete['cash_minor'] == initial['cash_minor'] == 5000000
    assert module.finance_router.workspace().bank.snapshot()['execution_count'] == 0


@pytest.mark.parametrize('commitment,extra,available', [('0', 0, 300000), ('800', 0, 300000),
                                                     ('1000', 0, 300000), ('1500', 50000, 250000)])
def test_monthly_commitment_only_adds_the_amount_not_already_in_expenses(planning_workspace, commitment, extra, available):
    client, _ = planning_workspace
    seed_months(client)
    result = profile(client, commitment_amount=commitment, goal_name='目标', goal_amount='8400', goal_months=3)
    plan = result['planning']
    assert plan['monthly']['commitment_extra_minor'] == extra
    assert plan['monthly']['available_minor'] == available
    assert plan['goal']['feasible'] is (available >= 280000)
    assert result['goal_feasible'] == plan['goal']['feasible']
    assert any(r['id'] == 'goal_capacity' for r in result['reasons']) is (available < 280000)
    assert plan['after_goal_minor'] == max(0, available - 280000)


def test_refunds_cash_principal_currency_and_current_month_have_distinct_meanings(planning_workspace):
    client, _ = planning_workspace
    bill(client, 1000, 'income', '工资')
    bill(client, 200, 'expense', '住房')
    bill(client, 500, 'income', '退款')
    bill(client, 100000, 'income', '其他收入', movement='internal_transfer')
    bill(client, 9000, 'expense', '其他', movement='principal')
    foreign = client.post('/bills', json={
        'event_date': '2026-09-15', 'amount': 900000, 'type': 'income',
        'category': '工资', 'currency': 'USD', 'description': '外币不得混入人民币计划',
    })
    assert foreign.status_code == 422
    bill(client, 990000, 'income', '工资', day='2026-10-01')
    plan = client.get('/finance/overview').json()['planning']
    assert plan['basis']['observed_months'] == 1
    assert plan['monthly']['income_minor'] == 100000
    assert plan['monthly']['net_expense_minor'] == -30000
    assert plan['monthly']['gross_expense_minor'] == 20000
    assert plan['monthly']['net_minor'] == plan['monthly']['available_minor'] == 130000
    assert plan['emergency']['target_minor'] == 60000
    assert plan['after_goal_minor'] == 130000


def test_personal_scope_excludes_synthetic_income_and_new_bills_update_planning(planning_workspace):
    client, module = planning_workspace
    seed_months(client, income=6000, expense=1000)
    for source in ('synthetic_v2', 'synthetic_demo', 'jsonl'):
        bill(client, 99999, 'income', '工资', source=source)
    all_rows = client.get('/finance/overview').json()
    personal = profile(client, ledger_scope='personal')
    assert all_rows['planning']['monthly']['income_minor'] > 600000
    assert personal['planning']['basis']['scope'] == 'personal'
    assert personal['planning']['monthly']['income_minor'] == 600000
    assert personal['planning']['monthly']['net_minor'] == 500000
    bill(client, 300, 'expense', '购物')
    updated = client.get('/finance/overview').json()
    assert updated['planning']['monthly']['net_minor'] == 490000
    assert updated['cash_minor'] == personal['cash_minor'] == 5000000
    assert module.finance_router.workspace().bank.snapshot()['execution_count'] == 0


def test_no_history_or_negative_cashflow_never_invents_monthly_funding(planning_workspace):
    client, _ = planning_workspace
    empty = profile(client, goal_name='目标', goal_amount='60', goal_months=3)
    assert empty['planning']['basis']['observed_months'] == 0
    assert empty['planning']['monthly']['available_minor'] == 0
    assert empty['planning']['goal']['shortfall_minor'] == 2000
    assert empty['goal_feasible'] is False
    seed_months(client, income=500, expense=800)
    negative = client.get('/finance/overview').json()['planning']
    assert negative['monthly']['net_minor'] == -30000
    assert negative['monthly']['available_minor'] == 0
    assert negative['goal']['monthly_allocated_minor'] == negative['after_goal_minor'] == 0


def test_manual_progress_cannot_raise_trade_limit_and_bank_actions_do_not_change_ledger_plan(planning_workspace):
    client, module = planning_workspace
    seed_months(client, income=5000, expense=1000)
    before = profile(client, goal_name='长期目标', goal_amount='40000', goal_months=2, risk_answers=[2, 2, 2, 2])
    assert before['investable_minor'] == 700000 and before['products'][1]['eligible'] is True
    assert before['planning']['goal']['feasible'] is False
    response = client.post('/finance/goal/progress', json={'amount': '40000', 'request_id': 'manual-completion-no-authority'})
    assert response.status_code == 200
    after = client.get('/finance/overview').json()
    assert after['goal_monthly_minor'] == 0 and after['goal_feasible'] is True
    assert after['investable_minor'] == before['investable_minor']
    assert after['goal_buffer_minor'] == before['goal_buffer_minor'] == 4000000
    excessive = client.post('/finance/operations/review', json={
        'kind': 'subscribe', 'product_id': 'growth', 'amount': '8000', 'request_id': 'over-conservative-limit'})
    assert excessive.status_code == 409
    response = client.post('/finance/operations/review', json={
        'kind': 'subscribe', 'product_id': 'growth', 'amount': '200', 'request_id': 'isolated-allowed-investment'})
    assert response.status_code == 200, response.text
    op = response.json()
    assert op['status'] == 'needs_confirmation'
    assert client.get('/finance/overview').json()['cash_minor'] == 5000000
    result = client.post(f"/finance/operations/{op['handle']}/confirm", json={'challenge': op['challenge']})
    assert result.status_code == 200 and result.json()['status'] == 'succeeded'
    final = client.get('/finance/overview').json()
    assert final['cash_minor'] == 4980000 and final['assets_minor'] == 5000000
    assert final['planning'] == after['planning']
    assert module.finance_router.workspace().bank.snapshot()['execution_count'] == 1


def plan_reply_without_financial_writes(client, module, monkeypatch):
    import finance_workspace
    from finance_schema import plan_facts, plan_target

    snapshot = client.get('/finance/overview').json()
    before_bills = [tuple(row) for row in module._db_conn.execute('SELECT * FROM bills ORDER BY id')]
    before_bank = module.finance_router.workspace().bank.snapshot()
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"intent":"plan","recipient":null,"product_id":null,"amount":null}'
        facts = json.loads(messages[-1]['content'])
        assert facts == plan_facts(snapshot)  # Keep the existing model protocol.
        assert 'planning' not in facts and 'goal_name' not in facts
        return json.dumps(plan_target(facts))

    monkeypatch.setattr(finance_workspace, 'api_chat', model)
    response = client.post('/finance/assistant', json={'messages': [{'role': 'user', 'content': '帮我看一下目标和应急金安排'}]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(calls) == 2 and data['explanation_status'] == 'model_grounded'
    assert data['draft'] is None
    assert data['plan_next'] == plan_target(plan_facts(snapshot))['next_step']
    assert [tuple(row) for row in module._db_conn.execute('SELECT * FROM bills ORDER BY id')] == before_bills
    assert module.finance_router.workspace().bank.snapshot() == before_bank
    assert client.get('/finance/overview').json()['planning'] == snapshot['planning']
    return data, snapshot


def test_plan_reply_uses_completed_goal_and_distinguishes_monthly_flow_from_simulation(planning_workspace, monkeypatch):
    client, module = planning_workspace
    seed_months(client, income=5000, expense=1000)
    profile(client, goal_name='目标', goal_amount='40000', goal_recorded_amount='40000', goal_action='carry',
            goal_months=2, risk_answers=[2, 2, 2, 2])
    data, snapshot = plan_reply_without_financial_writes(client, module, monkeypatch)
    reply = data['reply']
    assert snapshot['planning']['goal']['monthly_required_minor'] == 0
    assert '目标剩余 0.00 元' in reply and '每月需预留 0.00 元' in reply
    assert '按设置的 2 个月规划' in reply
    assert '目标预留后月均结余为 4,000.00 元' in reply
    assert '应急金目标为 3,000.00 元，实际已预留金额尚未核对' in reply
    assert '尚未扣减待核对的应急金缺口' in reply
    assert '模拟账户：应急金与目标资金预留后' in reply
    assert '模拟账户的长期资金测算额为 7,000.00 元' in reply
    assert '不计入上述账本结余' in reply
    assert snapshot['goal_buffer_minor'] == 4000000


@pytest.mark.parametrize('observed_months', [0, 1])
def test_plan_reply_does_not_present_sparse_history_as_reliable_monthly_funding(planning_workspace, monkeypatch, observed_months):
    client, module = planning_workspace
    if observed_months:
        bill(client, 5000, 'income', '工资')
        bill(client, 1000, 'expense', '住房')
    profile(client, goal_name='目标', goal_amount='1200', goal_months=3)
    data, snapshot = plan_reply_without_financial_writes(client, module, monkeypatch)
    reply = data['reply']
    assert snapshot['planning']['basis']['observed_months'] == observed_months
    assert data['plan_next'] == 'import'
    assert '目标剩余 1,200.00 元' in reply and '每月需预留 400.00 元' in reply
    assert '先补充至少三个月收支，暂不据此判断可长期安排的月度金额' in reply
    assert '目标预留后月均结余为' not in reply
    assert '实际已预留金额尚未核对' in reply
    assert '模拟账户的长期资金测算额为 0.00 元' in reply
    if not observed_months:
        assert '目前没有可用于估算月均结余的完整月份账单' in reply
        assert '应急金目标需补充开销后再估算' in reply
    else:
        assert '1 个有记录月份' in reply and '月均结余 4,000.00 元' in reply
