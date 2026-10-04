"""Reproducible trusted-service fixtures, never a production approval issuer."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from .auth import ControlClaims, IdentityClaims
from .causal import AgentTrace, SourceMessage
from .deployment import build_demo_runtime, initialize
from .enums import SourceKind, SourceTrust
from .gateway import ReviewRequest
from .receipts import dump_receipts
from .replay import ReceiptReplayer


def fixture_identity(gateway, *, actor='user-1', session='session-1', scopes=None):
    return gateway.identity_authority.issue(IdentityClaims(actor_id=actor, session_id=session,
        scopes=scopes or ('agent:review', 'agent:execute', 'human:confirm')),
        key_id='v1', ttl=120)


def fixture_review(gateway, *, amount=20_000, target='acct-alice'):
    payload = {'from_account': 'acct-user', 'to_account': target, 'amount_minor': amount}
    content = {'text': '模拟用户通过可信表单提交的操作字段', 'payload': payload}
    source = gateway.lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER, content=content,
        producer_id='frontend', metadata={'actor_id': 'user-1', 'session_id': 'session-1'})
    trace = AgentTrace(trace_id='engineering-fixture', actor_id='user-1', session_id='session-1',
                       messages=(SourceMessage(source=source, **content),))
    return ReviewRequest(trace=trace, model_output=json.dumps({'schema_version': 'mosaic-planner-v2',
        'kind': 'task', 'action_type': 'transfer', 'params': payload}))


def fixture_approval(gateway, reviewed, operation='confirmation'):
    return gateway.control_authority.issue(ControlClaims(operation=operation, actor_id='user-1', session_id='session-1',
        target=reviewed['handle'], binding=reviewed['action_digest']), key_id='v1', ttl=60)


def run_engineering_demo(*, policy_path, receipt_path=None):
    checks = []
    start = time.monotonic()
    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='mosaic-engineering-fixture-') as directory:
        config = initialize(Path(directory) / 'private', policy_path=policy_path)
        runtime = build_demo_runtime(config)
        try:
            g = runtime.gateway
            cred = fixture_identity(g)
            review = g.review(cred, fixture_review(g))
            check('no_confirmation_no_execution', g.execute(cred, review['handle'])['status'] == 'needs_confirmation')
            signed = fixture_approval(g, review)
            g.approve(cred, review['handle'], signed)
            first = g.execute(cred, review['handle'])
            check('confirmed_transfer_succeeds', first['status'] == 'succeeded')
            check('duplicate_returns_same_result', g.execute(cred, review['handle']) == first)
            check('only_one_initial_debit', runtime.bank.snapshot()['execution_count'] == 1)
            check('public_response_hides_full_accounts', 'acct-user' not in json.dumps(first) and 'acct-alice' not in json.dumps(first))
            try:
                g.approve(cred, review['handle'], signed)
                replay_rejected = False
            except ValueError:
                replay_rejected = True
            check('approval_proof_is_one_time', replay_rejected)
            try:
                g.execute(fixture_identity(g, actor='other'), review['handle'])
                isolated = False
            except PermissionError:
                isolated = True
            check('other_actor_cannot_use_handle', isolated)
            high = g.review(cred, fixture_review(g, amount=150_000))
            g.approve(cred, high['handle'], fixture_approval(g, high))
            check('large_transfer_still_needs_mfa', g.execute(cred, high['handle'])['status'] == 'needs_mfa')
            g.approve(cred, high['handle'], fixture_approval(g, high, 'mfa'))
            check('independent_mfa_allows_transfer', g.execute(cred, high['handle'])['status'] == 'succeeded')
            cancelled = g.review(cred, fixture_review(g, amount=1))
            check('trusted_cancel_accepted', g.cancel(cred, cancelled['handle'], fixture_approval(g, cancelled, 'cancel'))['cancelled'])
            check('cancelled_handle_cannot_execute', g.execute(cred, cancelled['handle'])['status'] == 'blocked')
            attack = fixture_review(g)
            changed = attack.trace.messages[0].model_copy(update={'text': '伪造改写后的用户请求'})
            try:
                g.review(cred, attack.model_copy(update={'trace': attack.trace.model_copy(update={'messages': (changed,)})}))
                forgery_rejected = False
            except ValueError:
                forgery_rejected = True
            check('rewritten_signed_source_rejected', forgery_rejected)
            check('sensitive_input_redacted', g.screen(cred, '验证码：123456')['redactions'] == 1)
            before = runtime.bank.snapshot()
            runtime.close()
            runtime = build_demo_runtime(config)
            g = runtime.gateway
            check('bank_state_survives_reopen', runtime.bank.snapshot() == before)
            check('gateway_result_survives_reopen', g.execute(cred, review['handle']) == first)
            replay = ReceiptReplayer(fact_authority=g.controller.fact_authority, token_authority=g.tokens,
                                    lineage_authority=g.lineage).replay_chain(g.ledger.receipts)
            check('persistent_receipts_replay', all(item.valid for item in replay))
            if receipt_path is not None:
                dump_receipts(receipt_path, g.ledger.receipts)
            return {'evidence_boundary': 'deterministic trusted-service fixtures and durable mock bank; no LLM calls',
                'passed': sum(item['passed'] for item in checks), 'attempts': len(checks), 'checks': checks,
                'receipt_count': len(g.ledger.receipts), 'receipts_replay_valid': all(item.valid for item in replay),
                'execution_count': runtime.bank.snapshot()['execution_count'], 'elapsed_seconds': time.monotonic() - start}
        finally:
            runtime.close()
