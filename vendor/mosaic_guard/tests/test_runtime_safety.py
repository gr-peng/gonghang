from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from scns_guard.controller import SafetyController
from scns_guard.enums import DecisionStatus, ExecutionState, ObligationType, RiskLevel
from scns_guard.money import MAX_MINOR_UNITS
from scns_guard.policy import ConditionEvaluator, PolicyEngine, PolicyError
from scns_guard.replay import ReceiptReplayer
from scns_guard.simulator import BankAccount, BankLedger

from .helpers import action_from_trace, make_trace


def controller_for(components, **kwargs):
    policy, facts, tokens, _ = components
    return SafetyController(policy=policy, fact_authority=facts, token_authority=tokens, **kwargs)


@pytest.mark.parametrize('spent,amount,level', [
    (0, 100_000, RiskLevel.YELLOW), (60_000, 40_000, RiskLevel.YELLOW),
    (60_000, 40_001, RiskLevel.RED), (60_000, 60_000, RiskLevel.RED),
    (100_000, 1, RiskLevel.RED), (1_000_000, 1, RiskLevel.DENY),
])
def test_cumulative_daily_risk(components, spent, amount, level):
    _, facts, _, bank = components
    bank.daily_spent_minor['user-1'] = spent
    action = action_from_trace(make_trace(amount_minor=amount))
    receipt = controller_for(components).decide(action, facts=bank.issue_facts('user-1', facts))
    assert receipt.formal.decision.base_level is level


def test_missing_daily_spend_fails_closed(components):
    _, facts, _, bank = components
    action = action_from_trace(make_trace())
    supplied = [f for f in bank.issue_facts('user-1', facts) if f.predicate != 'daily_spent_minor']
    assert controller_for(components).decide(action, facts=supplied).formal.decision.status is DecisionStatus.DENY


def test_confirmed_split_rechecks_cumulative_and_requires_mfa(components):
    _, facts, tokens, bank = components
    c = controller_for(components)
    actions = [action_from_trace(make_trace(amount_minor=60_000, trace_id=f'split-{i}')) for i in range(2)]
    confirmations = [tokens.issue(a, ObligationType.CONFIRMATION) for a in actions]
    first = c.execute(actions[0], fact_supplier=lambda: bank.issue_facts('user-1', facts),
                      tool=bank, tokens=(confirmations[0],))
    second = c.execute(actions[1], fact_supplier=lambda: bank.issue_facts('user-1', facts),
                       tool=bank, tokens=(confirmations[1],))
    assert first.formal.execution.state is ExecutionState.EXECUTED
    assert second.formal.decision.status is DecisionStatus.REQUIRE_MFA
    assert second.formal.execution is None
    assert bank.daily_spent_minor['user-1'] == 60_000
    final = c.execute(actions[1], fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=bank,
                      tokens=(confirmations[1], tokens.issue(actions[1], ObligationType.MFA)))
    assert final.formal.execution.state is ExecutionState.EXECUTED
    assert bank.daily_spent_minor['user-1'] == 120_000
    assert all(r.valid for r in ReceiptReplayer(fact_authority=facts, token_authority=tokens).replay_chain(c.receipt_ledger.receipts))


def test_concurrent_split_uses_bank_transaction(components):
    _, facts, tokens, bank = components
    # Separate controllers share the same bank transaction boundary.
    def attempt(i):
        action = action_from_trace(make_trace(amount_minor=60_000, trace_id=f'parallel-{i}'))
        return controller_for(components).execute(action,
            fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=bank,
            tokens=(tokens.issue(action, ObligationType.CONFIRMATION),))
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(attempt, range(8)))
    assert sum(r.formal.execution is not None for r in receipts) == 1
    assert bank.daily_spent_minor['user-1'] == 60_000


def test_daily_rollover_uses_shanghai_day_and_expires_old_facts(components):
    _, facts, _, _ = components
    current = [datetime(2026, 9, 3, 15, 59, 59, tzinfo=timezone.utc)]
    bank = BankLedger([BankAccount('acct-user', 'user-1', 500_000),
                       BankAccount('acct-alice', 'alice', 0)], clock=lambda: current[0])
    bank.daily_spent_minor['user-1'] = 60_000
    prior = bank.issue_facts('user-1', facts)
    spend = next(f for f in prior if f.predicate == 'daily_spent_minor')
    assert spend.value == 60_000
    assert spend.expires_at <= current[0] + timedelta(seconds=1)
    current[0] += timedelta(seconds=2)
    fresh = bank.issue_facts('user-1', facts)
    assert next(f for f in fresh if f.predicate == 'daily_spent_minor').value == 0
    assert bank.snapshot()['business_date'] == '2026-09-04'


@pytest.mark.parametrize('terms', [[MAX_MINOR_UNITS, 1], [True, 1], [-1, 1], [1.0, 1]])
def test_sum_minor_invalid_values_fail_closed(terms):
    action = action_from_trace(make_trace())
    condition = {'not': {'compare': {'left': {'sum_minor': terms}, 'op': '>', 'right': 0}}}
    # Invalid literals are rejected at policy loading, including under negation.
    from scns_guard.policy import validate_condition
    with pytest.raises((PolicyError, ValueError)):
        validate_condition(condition)


def test_sum_minor_missing_and_runtime_overflow_remain_unknown():
    action = action_from_trace(make_trace(amount_minor=2))
    evaluator = ConditionEvaluator(action, {})
    for terms in [[{'fact': {'predicate': 'daily_spent_minor', 'subject': '$actor'}}, 1],
                  ['$params.amount_minor', MAX_MINOR_UNITS]]:
        condition = {'not': {'compare': {'left': {'sum_minor': terms}, 'op': '>', 'right': 0}}}
        assert not evaluator.evaluate(condition)


def test_circuit_locks_after_failures_and_not_pending_confirmation(components):
    from scns_guard.circuit import SafetyCircuitBreaker
    breaker = SafetyCircuitBreaker(failure_threshold=2)
    c = controller_for(components, circuit_breaker=breaker)
    _, facts, tokens, bank = components
    action = action_from_trace(make_trace())
    for _ in range(4):
        c.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=bank)
    assert not breaker.snapshot('user-1')['locked']

    class FailingTool:
        name = 'failing-mock'
        def snapshot(self):
            return {}
        def execute(self, action):
            raise TimeoutError('untrusted secret error details')

    # Independent fixture-approved attempts, not duplicate deliveries of one request.
    for index in range(2):
        confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
        r = c.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts),
                      tool=FailingTool(), tokens=(confirmation,), request_id=f'fault-{index}')
        assert r.formal.execution.state is ExecutionState.FAILED
    assert breaker.snapshot('user-1')['locked']
    # Same actor in another session cannot bypass a safety lock, even with MFA.
    other = action_from_trace(make_trace(trace_id='new-session'))
    r = c.execute(other, fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=bank,
                  tokens=tuple(tokens.issue(other, o) for o in (ObligationType.CONFIRMATION, ObligationType.MFA)))
    assert r.formal.decision.status is DecisionStatus.DENY
    assert not bank.execution_log
    assert not breaker.snapshot('user-2')['locked']
    assert all(x.valid for x in ReceiptReplayer(fact_authority=facts, token_authority=tokens).replay_chain(c.receipt_ledger.receipts))
    breaker.reset('user-1', operator_id='trusted-test-operator', reason='backend checked')
    assert not breaker.snapshot('user-1')['locked']
    assert breaker.events[-1]['event'] == 'reset'


def test_repeated_denied_attempts_lock_actor(components):
    from scns_guard.circuit import SafetyCircuitBreaker
    breaker = SafetyCircuitBreaker(denial_threshold=2)
    c = controller_for(components, circuit_breaker=breaker)
    _, facts, _, bank = components
    action = action_from_trace(make_trace(to_account='nonexistent'))
    for _ in range(2):
        c.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=bank)
    assert breaker.snapshot('user-1')['locked']


def test_false_tool_result_is_unknown_not_success(components):
    from scns_guard.outcomes import outcome_from_receipt
    _, facts, tokens, bank = components
    action = action_from_trace(make_trace())
    class LyingTool:
        name = 'invalid-mock-response'
        def snapshot(self):
            return {}
        def execute(self, action):
            return {'status': 'posted', 'action_digest': action.digest,
                    'from_account': 'acct-user', 'to_account': 'acct-mallory', 'amount_minor': 20_000}
    r = controller_for(components).execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts),
                 tool=LyingTool(), tokens=(tokens.issue(action, ObligationType.CONFIRMATION),))
    assert r.formal.execution.state is ExecutionState.FAILED
    out = outcome_from_receipt(r, action=action)
    assert out.status == 'unknown'
    assert out.data == {}
    assert '成功' not in out.message


def test_public_outcomes_only_use_matching_execution_receipt(components):
    from scns_guard.outcomes import outcome_from_receipt
    _, facts, tokens, bank = components
    action = action_from_trace(make_trace())
    c = controller_for(components)
    pending = c.decide(action, facts=bank.issue_facts('user-1', facts))
    assert outcome_from_receipt(pending, action=action).status == 'needs_confirmation'
    confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
    allowed = c.decide(action, facts=bank.issue_facts('user-1', facts), tokens=(confirmation,))
    assert outcome_from_receipt(allowed, action=action).status == 'authorized_not_executed'
    executed = c.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts),
                         tool=bank, tokens=(confirmation,))
    out = outcome_from_receipt(executed, action=action)
    assert out.status == 'succeeded'
    assert out.data['amount_minor'] == action.params['amount_minor']
    wrong = action_from_trace(make_trace(trace_id='foreign', amount_minor=1))
    with pytest.raises(ValueError, match='action'):
        outcome_from_receipt(executed, action=wrong)
    tampered = executed.model_copy(update={'receipt_hash': 'forged'})
    with pytest.raises(ValueError, match='hash'):
        outcome_from_receipt(tampered, action=action)


@pytest.mark.parametrize('stage', ['facts', 'before_snapshot', 'after_snapshot'])
def test_adapter_faults_leave_receipt_and_count_toward_lock(components, stage):
    from scns_guard.circuit import SafetyCircuitBreaker
    from scns_guard.outcomes import outcome_from_receipt
    _, facts, tokens, bank = components
    breaker = SafetyCircuitBreaker(failure_threshold=1)
    c = controller_for(components, circuit_breaker=breaker)
    action = action_from_trace(make_trace())
    class FaultTool:
        name = 'fault-mock'
        executed = False
        def snapshot(self):
            if stage == 'before_snapshot' or (stage == 'after_snapshot' and self.executed):
                raise TimeoutError('state unavailable')
            return bank.snapshot()
        def execute(self, action):
            self.executed = True
            return bank.execute(action)
    def supplier():
        if stage == 'facts':
            raise TimeoutError('facts unavailable')
        return bank.issue_facts('user-1', facts)
    r = c.execute(action, fact_supplier=supplier, tool=FaultTool(),
                  tokens=(tokens.issue(action, ObligationType.CONFIRMATION),))
    assert outcome_from_receipt(r, action=action).status != 'succeeded'
    assert breaker.snapshot('user-1')['locked']
    assert len(bank.execution_log) == (1 if stage == 'after_snapshot' else 0)


def test_self_transfer_conserves_money(components):
    _, _, _, bank = components
    action = action_from_trace(make_trace(to_account='acct-user'))
    before = bank.accounts['acct-user'].balance_minor
    bank.execute(action)
    assert bank.accounts['acct-user'].balance_minor == before


def test_smt_supports_daily_sum_and_rejects_overflow_domain(components):
    pytest.importorskip('z3')
    from scns_guard.policy_diff import find_numeric_expansion_with_z3
    from .test_policy_diff import _move_boundary
    policy, facts, _, bank = components
    bank.daily_spent_minor['user-1'] = 60_000
    verified = facts.verified_index(bank.issue_facts('user-1', facts))
    args = dict(action_template=action_from_trace(make_trace()), verified_facts=verified,
                parameter='amount_minor', lower=1, upper=100_000)
    assert find_numeric_expansion_with_z3(policy, _move_boundary(policy, 200_000), **args) is not None
    with pytest.raises(ValueError, match='overflow'):
        find_numeric_expansion_with_z3(policy, policy, **{**args, 'upper': MAX_MINOR_UNITS})


def test_policy_update_only_tightens_original_per_transfer_rule(components):
    from scns_guard.policy_diff import BoundedPolicyDiffer, PolicyDiffCase
    policy, facts, _, bank = components
    old_raw = policy.snapshot()
    old_raw['version'] = 'test-reconstructed-single-transfer-boundary'
    for rule in old_raw['rules']:
        for condition in rule['when'].get('all', []):
            comparison = condition.get('compare', {})
            if isinstance(comparison.get('left'), dict) and 'sum_minor' in comparison['left']:
                comparison['left'] = '$params.amount_minor'
    old = PolicyEngine.from_mapping(old_raw)
    cases = []
    for spent in (0, 60_000, 100_000, 900_000):
        bank.daily_spent_minor['user-1'] = spent
        for amount in (1, 40_000, 40_001, 60_000, 100_000, 100_001, 1_000_000):
            action = action_from_trace(make_trace(amount_minor=amount))
            cases.append(PolicyDiffCase(f'{spent}-{amount}', action, bank.issue_facts('user-1', facts)))
    BoundedPolicyDiffer(facts).assert_no_expansion(old, policy, cases)


def test_scripted_runtime_demo():
    from scns_guard.runtime_demo import run_runtime_demo
    result = run_runtime_demo(policy_path='configs/transfer_policy.yaml')
    assert result['passed'] == result['attempts'] == 10
    assert result['final_bank']['daily_spent_minor']['user-1'] == 120_000
    assert result['final_bank']['execution_count'] == 2
    assert result['circuit_state']['locked']
    assert result['receipts_replay_valid']
