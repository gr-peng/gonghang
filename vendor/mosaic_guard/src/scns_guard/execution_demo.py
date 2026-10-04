"""Deterministic delivery/restart/fault checks against the mock bank."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .causal import AgentTrace, SourceMessage
from .controller import SafetyController
from .enums import ObligationType, SourceKind, SourceTrust
from .execution_store import ExecutionStore
from .experiment import default_components
from .lineage import LineageAuthority, VerifiedPayloadProvenanceResolver
from .outcomes import outcome_from_receipt
from .planning import PLANNER_VERSION, PlanningLLMAdapter
from .receipts import ReceiptLedger
from .replay import ReceiptReplayer
from .tokens import TokenAuthority


def run_execution_demo(*, policy_path: str | Path, receipt_path: str | Path | None = None) -> dict[str, Any]:
    policy, facts, _, bank = default_components(policy_path)
    lineage = LineageAuthority({'demo-runtime': b'prototype-lineage-secret'})
    params = {'from_account': 'acct-user', 'to_account': 'acct-alice', 'amount_minor': 20_000}
    content = {'text': '从 acct-user 向 acct-alice 转账 20000 分。', 'payload': params}
    source = lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
                                 producer_id='demo-runtime', source_id='execution-demo-user', content=content)
    trace = AgentTrace(trace_id='execution-demo', session_id='execution-demo-session', actor_id='user-1',
                       messages=(SourceMessage(source=source, **content),))
    def generator(messages, seed):
        del messages, seed
        return json.dumps({'schema_version': PLANNER_VERSION, 'kind': 'task',
                           'action_type': 'transfer', 'params': params})
    action = PlanningLLMAdapter(generator, provenance_resolver=VerifiedPayloadProvenanceResolver(lineage)).plan(trace, seed=0).action
    assert action is not None
    receipts = ReceiptLedger(receipt_path)
    checks = []
    initial = bank.snapshot()

    with TemporaryDirectory(prefix='mosaic-execution-check-') as directory:
        database = Path(directory) / 'execution.sqlite3'
        def connect():
            store = ExecutionStore(database)
            authority = TokenAuthority('obligation-service', b'prototype-token-secret', state_store=store)
            controller = SafetyController(policy=policy, fact_authority=facts, token_authority=authority,
                receipt_ledger=receipts, lineage_authority=lineage)
            return store, authority, controller
        store, authority, controller = connect()

        def submit(case, request_id, expected_status, expected_transfers, *, tokens=(), tool=None):
            receipt = controller.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts),
                                          tool=bank if tool is None else tool, tokens=tokens, request_id=request_id)
            outcome = outcome_from_receipt(receipt, action=action)
            checks.append({'case': case, 'request_id': request_id,
                'outcome': outcome.model_dump(mode='json'), 'transfer_count': len(bank.execution_log),
                'passed': outcome.status == expected_status and len(bank.execution_log) == expected_transfers})
            return receipt

        submit('pending_confirmation', 'payment-a', 'needs_confirmation', 0)
        confirmation = authority.issue(action, ObligationType.CONFIRMATION)
        original = submit('first_execution', 'payment-a', 'succeeded', 1, tokens=(confirmation,))
        repeated = submit('duplicate_delivery', 'payment-a', 'succeeded', 1, tokens=(confirmation,))
        assert repeated.receipt_hash == original.receipt_hash
        submit('used_token_new_request', 'token-reuse', 'needs_confirmation', 1, tokens=(confirmation,))
        submit('distinct_approved_payment', 'payment-b', 'succeeded', 2,
               tokens=(authority.issue(action, ObligationType.CONFIRMATION),))
        cancelled = authority.issue(action, ObligationType.CONFIRMATION)
        authority.revoke(cancelled, operator_id='trusted-fixture-operator', reason='fixture cancellation')
        submit('revoked_confirmation', 'cancelled-payment', 'needs_confirmation', 2, tokens=(cancelled,))
        store.close()
        store, authority, controller = connect()
        restarted = submit('duplicate_after_reopen', 'payment-a', 'succeeded', 2, tokens=(confirmation,))
        assert restarted.receipt_hash == original.receipt_hash

        class ResponseLost:
            name = bank.name
            snapshot = bank.snapshot
            transaction = bank.transaction
            def execute(self, action):
                bank.execute(action)
                raise TimeoutError('fixture: bank posted but response was lost')

        timeout_token = authority.issue(action, ObligationType.CONFIRMATION)
        unknown = submit('posted_then_timeout', 'timeout-payment', 'unknown', 3,
                         tokens=(timeout_token,), tool=ResponseLost())
        repeat_unknown = submit('timeout_duplicate', 'timeout-payment', 'unknown', 3, tokens=(timeout_token,))
        assert unknown.receipt_hash == repeat_unknown.receipt_hash

        class SimulatedProcessExit(BaseException):
            pass
        class ExitAfterPost(ResponseLost):
            def execute(self, action):
                bank.execute(action)
                raise SimulatedProcessExit()
        crash_token = authority.issue(action, ObligationType.CONFIRMATION)
        try:
            controller.execute(action, fact_supplier=lambda: bank.issue_facts('user-1', facts),
                tool=ExitAfterPost(), tokens=(crash_token,), request_id='crashed-payment')
        except SimulatedProcessExit:
            pass
        store.close()
        store, authority, controller = connect()
        submit('unfinished_after_reopen', 'crashed-payment', 'unknown', 4,
               tokens=(authority.issue(action, ObligationType.CONFIRMATION),))
        lifecycle_events = store.events()
        replay = ReceiptReplayer(fact_authority=facts, token_authority=authority,
                                 lineage_authority=lineage).replay_chain(receipts.receipts)
        store.close()

    return {'evidence_boundary': 'scripted_planner_mock_bank_and_simulated_process_exit_not_real_llm',
        'token_source': 'explicit_test_fixtures_not_model_claims', 'checks': checks,
        'attempts': len(checks), 'passed': sum(check['passed'] for check in checks),
        'initial_bank': initial, 'final_bank': bank.snapshot(), 'action': action.model_dump(mode='json'),
        'lifecycle_events': lifecycle_events, 'receipt_count': len(receipts.receipts),
        'receipts_replay_valid': all(result.valid for result in replay),
        'boundary': 'SQLite reopens retain reservations; mock bank stays in memory; no real process kill or distributed bank transaction'}
