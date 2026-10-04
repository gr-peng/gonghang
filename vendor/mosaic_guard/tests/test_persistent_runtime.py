from concurrent.futures import ThreadPoolExecutor

import pytest

from scns_guard.circuit import SafetyCircuitBreaker
from scns_guard.controller import SafetyController
from scns_guard.enums import ObligationType
from scns_guard.receipts import ReceiptLedger
from scns_guard.runtime_store import RuntimeStore, SQLiteReceiptLedger
from scns_guard.tokens import TokenAuthority

from .helpers import action_from_trace, make_trace


def runtime(components, path):
    policy, facts, _, bank = components
    store = RuntimeStore(path)
    circuit = SafetyCircuitBreaker(state_store=store, failure_threshold=1)
    tokens = TokenAuthority('persistent', b'test-only', state_store=store)
    ledger = SQLiteReceiptLedger(store, circuit_namespace=circuit.namespace)
    controller = SafetyController(policy=policy, fact_authority=facts, token_authority=tokens,
                                  receipt_ledger=ledger, circuit_breaker=circuit)
    return store, circuit, tokens, controller, bank, facts


def test_failure_lock_and_chain_survive_restart_and_observation_is_idempotent(components, tmp_path):
    path = tmp_path / 'state.db'
    store, circuit, tokens, controller, bank, facts = runtime(components, path)
    action = action_from_trace(make_trace())
    class Broken:
        name = bank.name
        snapshot = bank.snapshot
        def execute(self, action):
            raise OSError('fixture failure')
    receipt = controller.execute(action, fact_supplier=lambda: bank.issue_facts(action.actor_id, facts),
                                 tool=Broken(), tokens=(tokens.issue(action, ObligationType.CONFIRMATION),))
    circuit.observe(receipt)
    assert circuit.snapshot(action.actor_id)['failures'] == 1
    store.close()
    store, circuit, tokens, controller, bank, facts = runtime(components, path)
    assert circuit.snapshot(action.actor_id)['locked']
    assert len(controller.receipt_ledger.receipts) == 1
    ReceiptLedger.verify_chain(controller.receipt_ledger.receipts)
    receipt = controller.execute(action, fact_supplier=lambda: bank.issue_facts(action.actor_id, facts),
        tool=bank, tokens=(tokens.issue(action, ObligationType.CONFIRMATION),), request_id='after-restart')
    assert receipt.formal.execution is None
    assert not bank.execution_log
    generation = circuit.snapshot(action.actor_id)['generation']
    circuit.reset(action.actor_id, operator_id='trusted-op', reason='reviewed')
    assert circuit.snapshot(action.actor_id)['generation'] > generation
    assert not circuit.snapshot(action.actor_id)['locked']
    store.close()


def test_persistent_threshold_cannot_silently_change(components, tmp_path):
    store, *_ = runtime(components, tmp_path / 'state.db')
    with pytest.raises(ValueError, match='configuration'):
        SafetyCircuitBreaker(state_store=store, failure_threshold=100)
    store.close()


def test_multiple_connections_append_one_chain(components, tmp_path):
    path = tmp_path / 'state.db'
    runtimes = [runtime(components, path) for _ in range(2)]
    action = action_from_trace(make_trace())
    def append(i):
        _, _, _, controller, bank, facts = runtimes[i % 2]
        return controller.decide(action, facts=bank.issue_facts(action.actor_id, facts))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(20)))
    chain = runtimes[0][3].receipt_ledger.receipts
    assert len(chain) == 20
    ReceiptLedger.verify_chain(chain)
    assert not runtimes[0][1].snapshot(action.actor_id)['locked']
    for item in runtimes:
        item[0].close()


@pytest.mark.parametrize('barrier', ['cancel', 'lock'])
def test_cancel_or_lock_between_evaluation_and_reservation(components, tmp_path, barrier):
    store, circuit, tokens, controller, bank, facts = runtime(components, tmp_path / 'state.db')
    action = action_from_trace(make_trace())
    request_id = 'race'
    key = store.request_key(tokens.issuer, action, request_id)
    original = store.reserve
    def intervene(*args, **kwargs):
        if barrier == 'cancel':
            assert store.cancel(key, action, bank.name, operator_id='user-1', reason='cancelled')
        else:
            store._db.execute("INSERT INTO circuit_state VALUES (?,?,?,?,?,?)",
                              (circuit.namespace, action.actor_id, 1, 0, 1, 1))
        return original(*args, **kwargs)
    store.reserve = intervene
    result = controller.execute(action, fact_supplier=lambda: bank.issue_facts(action.actor_id, facts),
        tool=bank, tokens=(tokens.issue(action, ObligationType.CONFIRMATION),), request_id=request_id)
    assert result.formal.execution is None
    assert not bank.execution_log
    store.close()
