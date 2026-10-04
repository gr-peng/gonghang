from concurrent.futures import ThreadPoolExecutor

import pytest

from scns_guard.durable_bank import DurableBankLedger
from scns_guard.simulator import BankAccount

from .helpers import action_from_trace, make_trace


def bank_at(path):
    return DurableBankLedger(path, [BankAccount('acct-user', 'user-1', 500_000),
                                   BankAccount('acct-alice', 'alice', 10_000)])


def test_durable_bank_reopen_and_exact_backend_idempotency(tmp_path):
    path = tmp_path / 'bank.db'
    bank = bank_at(path)
    action = action_from_trace(make_trace())
    first = bank.execute(action)
    bank.close()
    bank = bank_at(path)
    assert bank.snapshot()['accounts']['acct-user']['balance_minor'] == 480_000
    assert bank.execute(action) == first
    assert bank.snapshot()['execution_count'] == 1
    assert bank.reconcile(action) == first
    assert bank.snapshot()['daily_spent_minor']['user-1'] == 20_000
    with pytest.raises(ValueError):
        bank.execute(action.model_copy(update={'params': {**action.params, 'amount_minor': 1}}))
    bank.close()


def test_independent_backend_connections_serialize_and_keep_balance(tmp_path):
    path = tmp_path / 'bank.db'
    banks = [bank_at(path), bank_at(path)]
    actions = [action_from_trace(make_trace(trace_id=f'transfer-{i}')) for i in range(10)]
    def submit(i):
        return banks[i % 2].execute(actions[i])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(submit, range(10)))
    assert banks[0].snapshot()['accounts']['acct-user']['balance_minor'] == 300_000
    assert banks[1].snapshot()['execution_count'] == 10
    for bank in banks:
        bank.close()
