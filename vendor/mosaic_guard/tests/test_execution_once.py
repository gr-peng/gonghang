from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from scns_guard.controller import SafetyController
from scns_guard.enums import DecisionStatus, ExecutionState, ObligationType
from scns_guard.outcomes import outcome_from_receipt
from scns_guard.receipts import ReceiptLedger
from scns_guard.replay import ReceiptReplayer
from scns_guard.tokens import TokenAuthority

from .helpers import action_from_trace, make_trace


def setup(components, *, authority=None):
    policy, facts, tokens, bank = components
    tokens = authority or tokens
    controller = SafetyController(policy=policy, fact_authority=facts, token_authority=tokens)
    return controller, facts, tokens, bank


def execute(controller, facts, bank, action, tokens=(), **kwargs):
    return controller.execute(action, fact_supplier=lambda: bank.issue_facts(action.actor_id, facts, now=kwargs.get('now')),
                              tool=bank, tokens=tokens, **kwargs)


def test_default_same_action_executes_once_and_returns_original_receipt(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    first = execute(c, facts, bank, a, (confirmation,))
    again = execute(c, facts, bank, a, (confirmation,))
    assert len(bank.execution_log) == 1
    assert again.receipt_hash == first.receipt_hash
    assert len(c.receipt_ledger.receipts) == 1
    # History remains verifiable after consumption; current authority is gone.
    assert tokens.verify(confirmation, a)
    assert not tokens.verify_for_use(confirmation, a)
    assert all(r.valid for r in ReceiptReplayer(fact_authority=facts, token_authority=tokens).replay_chain(c.receipt_ledger.receipts))


def test_pending_mfa_does_not_consume_confirmation(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace(amount_minor=150_000))
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    pending = execute(c, facts, bank, a, (confirmation,), request_id='approval-flow')
    assert pending.formal.decision.status is DecisionStatus.REQUIRE_MFA
    assert tokens.verify_for_use(confirmation, a)
    done = execute(c, facts, bank, a, (confirmation, tokens.issue(a, ObligationType.MFA)), request_id='approval-flow')
    assert done.formal.execution.state is ExecutionState.EXECUTED
    assert not tokens.verify_for_use(confirmation, a)


def test_used_token_cannot_be_reused_with_new_request_or_new_action_id(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    execute(c, facts, bank, a, (confirmation,), request_id='first')
    copied = a.model_copy(update={'action_id': 'new-action-id'})
    denied = execute(c, facts, bank, copied, (confirmation,), request_id='second')
    assert denied.formal.execution is None
    assert denied.formal.decision.status is not DecisionStatus.ALLOW
    assert len(bank.execution_log) == 1


def test_fresh_approval_can_authorize_distinct_identical_payment(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    for key in ('intent-1', 'intent-2'):
        execute(c, facts, bank, a, (tokens.issue(a, ObligationType.CONFIRMATION),), request_id=key)
    assert len(bank.execution_log) == 2


@pytest.mark.parametrize('field,value', [('amount_minor', 123), ('to_account', 'acct-mallory')])
def test_request_key_cannot_be_rebound_after_pending_or_completed(components, field, value):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    execute(c, facts, bank, a, request_id='fixed')
    changed = a.model_copy(update={'params': {**a.params, field: value}})
    receipt = execute(c, facts, bank, changed,
                      (tokens.issue(changed, ObligationType.CONFIRMATION),), request_id='fixed')
    assert receipt.formal.decision.status is DecisionStatus.DENY
    assert not bank.execution_log


def test_concurrent_retries_charge_once_with_shared_store(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    def attempt(_):
        other, _, _, _ = setup(components)
        return execute(other, facts, bank, a, (confirmation,), request_id='parallel-retry')
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(attempt, range(16)))
    assert len(bank.execution_log) == 1
    assert all(r.formal.execution is None or r.formal.execution.action_digest == a.digest for r in receipts)


def test_posted_then_timeout_is_not_retried(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    class TimeoutAfterPost:
        name = bank.name
        snapshot = bank.snapshot
        transaction = bank.transaction
        def execute(self, action):
            bank.execute(action)
            raise TimeoutError('response lost after bank commit')
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    first = c.execute(a, fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=TimeoutAfterPost(),
                      tokens=(confirmation,), request_id='lost-response')
    again = execute(c, facts, bank, a, (confirmation,), request_id='lost-response')
    assert outcome_from_receipt(first, action=a).status == 'unknown'
    assert again.receipt_hash == first.receipt_hash
    assert len(bank.execution_log) == 1


def test_sqlite_reopen_preserves_consumption_and_cached_receipt(components, tmp_path):
    from scns_guard.execution_store import ExecutionStore
    path = tmp_path / 'execution.sqlite3'
    state = ExecutionStore(path)
    authority = TokenAuthority('persisted-tokens', b'test-secret', state_store=state)
    c, facts, _, bank = setup(components, authority=authority)
    a = action_from_trace(make_trace())
    confirmation = authority.issue(a, ObligationType.CONFIRMATION)
    first = execute(c, facts, bank, a, (confirmation,), request_id='restart')
    state.close()
    reopened = ExecutionStore(path)
    second_authority = TokenAuthority('persisted-tokens', b'test-secret', state_store=reopened)
    other, _, _, _ = setup(components, authority=second_authority)
    cached = execute(other, facts, bank, a, (confirmation,), request_id='restart')
    assert cached.receipt_hash == first.receipt_hash
    assert not second_authority.verify_for_use(confirmation, a)
    assert len(bank.execution_log) == 1
    reopened.close()


@pytest.mark.parametrize('post_before_crash', [False, True])
def test_crash_without_terminal_receipt_remains_uncertain_after_restart(components, tmp_path, post_before_crash):
    from scns_guard.execution_store import ExecutionStore
    path = tmp_path / 'crash.sqlite3'
    state = ExecutionStore(path)
    authority = TokenAuthority('persisted-tokens', b'test-secret', state_store=state)
    c, facts, _, bank = setup(components, authority=authority)
    a = action_from_trace(make_trace())
    class SimulatedProcessExit(BaseException):
        pass
    class CrashingTool:
        name = bank.name
        snapshot = bank.snapshot
        transaction = bank.transaction
        def execute(self, action):
            if post_before_crash:
                bank.execute(action)
            raise SimulatedProcessExit()
    confirmation = authority.issue(a, ObligationType.CONFIRMATION)
    with pytest.raises(SimulatedProcessExit):
        c.execute(a, fact_supplier=lambda: bank.issue_facts('user-1', facts), tool=CrashingTool(),
                  tokens=(confirmation,), request_id='crash')
    state.close()
    reopened = ExecutionStore(path)
    other_authority = TokenAuthority('persisted-tokens', b'test-secret', state_store=reopened)
    other, _, _, _ = setup(components, authority=other_authority)
    retry = execute(other, facts, bank, a, (other_authority.issue(a, ObligationType.CONFIRMATION),), request_id='crash')
    assert outcome_from_receipt(retry, action=a).status == 'unknown'
    assert len(bank.execution_log) == int(post_before_crash)
    assert not other_authority.verify_for_use(confirmation, a)
    reopened.close()


def test_revoke_prevents_execution_but_not_historical_signature_validation(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    assert tokens.revoke(confirmation, operator_id='trusted-test-operator', reason='user cancelled')
    assert tokens.verify(confirmation, a)
    assert not tokens.verify_for_use(confirmation, a)
    receipt = execute(c, facts, bank, a, (confirmation,))
    assert receipt.formal.execution is None
    assert not bank.execution_log
    assert tokens.state_store.events()[-1]['event'] == 'token_revoked'


def test_forged_token_cannot_revoke_real_token(components):
    _, _, tokens, _ = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    with pytest.raises(ValueError):
        tokens.revoke(confirmation.model_copy(update={'signature': 'forged'}),
                      operator_id='op', reason='invalid')
    assert tokens.verify_for_use(confirmation, a)


def test_duplicate_token_entries_are_consumed_atomically_once(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    receipt = execute(c, facts, bank, a, (confirmation, confirmation))
    assert receipt.formal.execution.state is ExecutionState.EXECUTED
    assert not tokens.verify_for_use(confirmation, a)


def test_expired_token_cannot_authorize_new_request_but_cache_is_history(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    now = datetime.now(timezone.utc)
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION, ttl_seconds=1, now=now)
    first = execute(c, facts, bank, a, (confirmation,), request_id='original', now=now)
    assert first.formal.execution is not None
    future = now + timedelta(seconds=5)
    cached = execute(c, facts, bank, a, (confirmation,), request_id='original', now=future)
    assert cached.receipt_hash == first.receipt_hash
    pending = execute(c, facts, bank, a, (confirmation,), request_id='new', now=future)
    assert pending.formal.execution is None
    assert len(bank.execution_log) == 1


def test_receipt_persistence_failure_does_not_release_execution_reservation(components, tmp_path):
    from scns_guard.execution_store import ExecutionStore
    state = ExecutionStore(tmp_path / 'receipt-failure.sqlite3')
    authority = TokenAuthority('persisted-tokens', b'secret', state_store=state)
    c, facts, _, bank = setup(components, authority=authority)
    a = action_from_trace(make_trace())
    confirmation = authority.issue(a, ObligationType.CONFIRMATION)
    class BrokenReceiptLedger(ReceiptLedger):
        def append(self, formal, internal):
            raise OSError('audit disk unavailable')
    c.receipt_ledger = BrokenReceiptLedger()
    with pytest.raises(OSError):
        execute(c, facts, bank, a, (confirmation,), request_id='receipt-failure')
    c.receipt_ledger = ReceiptLedger()
    retry = execute(c, facts, bank, a, (confirmation,), request_id='receipt-failure')
    assert outcome_from_receipt(retry, action=a).status == 'unknown'
    assert len(bank.execution_log) == 1
    state.close()


def test_independent_sqlite_connections_race_for_one_token(components, tmp_path):
    from scns_guard.execution_store import ExecutionStore
    path = tmp_path / 'concurrent.sqlite3'
    stores = [ExecutionStore(path), ExecutionStore(path)]
    authorities = [TokenAuthority('shared-issuer', b'secret', state_store=store) for store in stores]
    a = action_from_trace(make_trace())
    token = authorities[0].issue(a, ObligationType.CONFIRMATION)
    barrier = Barrier(2)
    controllers = [setup(components, authority=authority)[0] for authority in authorities]
    _, facts, _, bank = components
    class UnlockedTool:
        name = bank.name
        snapshot = bank.snapshot
        execute = bank.execute
    for store in stores:
        original = store.reserve
        def synchronized_reserve(*args, _original=original, **kwargs):
            barrier.wait(timeout=5)
            return _original(*args, **kwargs)
        store.reserve = synchronized_reserve
    def attempt(i):
        return controllers[i].execute(a, fact_supplier=lambda: bank.issue_facts('user-1', facts),
            tool=UnlockedTool(), tokens=(token,), request_id=f'competing-{i}')
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(attempt, range(2)))
    assert sum(r.formal.execution is not None for r in receipts) == 1
    assert len(bank.execution_log) == 1
    for store in stores:
        store.close()


def test_revocation_between_evaluation_and_reservation_stops_dispatch(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    original = tokens.state_store.reserve
    def revoke_then_reserve(*args, **kwargs):
        tokens.revoke(confirmation, operator_id='trusted-op', reason='cancelled before dispatch')
        return original(*args, **kwargs)
    tokens.state_store.reserve = revoke_then_reserve
    receipt = execute(c, facts, bank, a, (confirmation,))
    assert receipt.formal.decision.status is DecisionStatus.DENY
    assert not bank.execution_log


def test_unavailable_state_store_fails_closed(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    tokens.state_store.close()
    import sqlite3
    with pytest.raises(sqlite3.Error):
        execute(c, facts, bank, a, (confirmation,))
    assert not bank.execution_log


def test_consumed_token_revocation_does_not_undo_or_release_payment(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    execute(c, facts, bank, a, (confirmation,))
    assert not tokens.revoke(confirmation, operator_id='op', reason='too late')
    assert not tokens.verify_for_use(confirmation, a)
    assert len(bank.execution_log) == 1


def test_revocation_survives_store_reopen(components, tmp_path):
    from scns_guard.execution_store import ExecutionStore
    path = tmp_path / 'revocation.sqlite3'
    first_store = ExecutionStore(path)
    authority = TokenAuthority('issuer', b'secret', state_store=first_store)
    a = action_from_trace(make_trace())
    confirmation = authority.issue(a, ObligationType.CONFIRMATION)
    authority.revoke(confirmation, operator_id='op', reason='cancelled')
    first_store.close()
    next_store = ExecutionStore(path)
    next_authority = TokenAuthority('issuer', b'secret', state_store=next_store)
    assert not next_authority.verify_for_use(confirmation, a)
    assert next_store.events()[-1]['reason'] == 'cancelled'
    next_store.close()


@pytest.mark.parametrize('request_id', ['', True, 'x' * 201])
def test_invalid_request_id_fails_before_execution(components, request_id):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    with pytest.raises(ValueError):
        execute(c, facts, bank, a, (tokens.issue(a, ObligationType.CONFIRMATION),), request_id=request_id)
    assert not bank.execution_log


def test_cached_receipt_corruption_never_triggers_reexecution(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    confirmation = tokens.issue(a, ObligationType.CONFIRMATION)
    receipt = execute(c, facts, bank, a, (confirmation,))
    bad = receipt.model_copy(update={'receipt_hash': 'tampered'})
    tokens.state_store._db.execute('UPDATE execution_requests SET receipt_json=?', (bad.model_dump_json(),))
    with pytest.raises(ValueError, match='integrity'):
        execute(c, facts, bank, a, (confirmation,))
    assert len(bank.execution_log) == 1


def test_same_request_id_does_not_cross_session_or_actor(components):
    c, facts, tokens, bank = setup(components)
    a = action_from_trace(make_trace())
    token = tokens.issue(a, ObligationType.CONFIRMATION)
    original = execute(c, facts, bank, a, (token,), request_id='same-id')
    for changed in (a.model_copy(update={'session_id': 'another-session'}),
                    a.model_copy(update={'actor_id': 'other-user'})):
        response = execute(c, facts, bank, changed, (token,), request_id='same-id')
        assert response.receipt_hash != original.receipt_hash
        assert response.formal.execution is None
    assert len(bank.execution_log) == 1


def test_read_queries_are_not_served_from_payment_cache(components):
    c, facts, _, bank = setup(components)
    a = action_from_trace(make_trace())
    read = a.model_copy(update={'action_type': 'read_balance', 'params': {'account_id': 'acct-user'},
        'write_action': False, 'argument_bindings': (a.binding_for('from_account').model_copy(update={'field': 'account_id'}),)})
    first = execute(c, facts, bank, read)
    bank.accounts['acct-user'].balance_minor -= 1
    second = execute(c, facts, bank, read)
    assert second.formal.execution.tool_result['balance_minor'] == first.formal.execution.tool_result['balance_minor'] - 1


def test_execution_once_demo_is_replayable():
    from scns_guard.execution_demo import run_execution_demo
    result = run_execution_demo(policy_path='configs/transfer_policy.yaml')
    assert result['passed'] == result['attempts'] == 10
    assert result['final_bank']['execution_count'] == 4
    assert result['receipt_count'] == 7
    assert result['receipts_replay_valid']
