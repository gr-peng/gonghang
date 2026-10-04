import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from scns_guard.audit import verify_checkpoint
from scns_guard.auth import ControlClaims
from scns_guard.content_safety import screen_text, validate_action_text

from .test_gateway import gateway, identity, proof, review_request


def test_signed_checkpoint_detects_truncation_and_tampering(gateway):
    g, auth, _, lineage, _ = gateway
    g.audit_secret = b'a' * 32
    cred = identity(auth)
    g.review(cred, review_request(lineage))
    auditor = identity(auth, actor='auditor', scopes=('audit:read',))
    checkpoint = g.checkpoint(auditor)
    assert verify_checkpoint(checkpoint, g.ledger.receipts, g.store.events(), g.audit_secret)
    assert not verify_checkpoint(checkpoint, (), g.store.events(), g.audit_secret)
    assert not verify_checkpoint({**checkpoint, 'receipt_count': 0}, (), g.store.events(), g.audit_secret)
    g.review(cred, review_request(lineage, amount=1))
    assert verify_checkpoint(checkpoint, g.ledger.receipts, g.store.events(), g.audit_secret)
    with pytest.raises(PermissionError):
        g.checkpoint(cred)


def test_session_revocation_prevents_new_dispatch_after_evaluation(gateway):
    g, auth, proofs, lineage, bank = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage))
    g.approve(cred, review['handle'], proof(proofs, review))
    operator = identity(auth, actor='op', session='op-session', scopes=('operator:revoke',))
    signed = proofs.issue(ControlClaims(operation='revoke_session', actor_id='op', session_id='op-session',
        target='user-1', binding=g.session_binding('user-1', 'session-1')), key_id='v1')
    reserve = g.store.reserve
    def revoke_then_reserve(*args, **kwargs):
        g.revoke_session(operator, 'user-1', 'session-1', signed)
        return reserve(*args, **kwargs)
    g.store.reserve = revoke_then_reserve
    assert g.execute(cred, review['handle'])['status'] == 'blocked'
    assert not bank.execution_log
    with pytest.raises(PermissionError):
        g.review(cred, review_request(lineage))


def test_concurrent_approval_replay_only_mints_one_token(gateway):
    g, auth, proofs, lineage, _ = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage))
    signed = proof(proofs, review)
    def accept(_):
        try:
            g.approve(cred, review['handle'], signed)
            return 1
        except ValueError:
            return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(accept, range(16))) == 1
    assert g.execute(cred, review['handle'])['status'] == 'succeeded'


def test_rate_limit_is_shared_persistent_and_recovers_next_window(gateway):
    g, *_ = gateway
    for _ in range(3):
        assert g.store.admit('principal', limit=3, now=120)
    assert not g.store.admit('principal', limit=3, now=120)
    assert g.store.admit('principal', limit=3, now=180)


def test_sensitive_text_is_redacted_but_action_not_silently_rewritten():
    for secret in ('api_key=abcdef123456', '验证码：123456', 'bearer abcdefghijk', '13812345678', '123456789012345678'):
        assert screen_text(secret)['redactions']
        with pytest.raises(ValueError):
            validate_action_text({'memo': secret})
    with pytest.raises(ValueError):
        validate_action_text({'to_account': 'acct-\u202euser'})
    # Account identifiers remain exact in the trusted confirmation view.
    validate_action_text({'to_account': '123456789012345678'})


def test_sandbox_scope_disabled_runtime_and_audit_fail_closed(gateway):
    g, auth, *_ = gateway
    with pytest.raises(PermissionError):
        g.run_code(identity(auth), 'print(1)')
    with pytest.raises(RuntimeError):
        g.run_code(identity(auth, scopes=('sandbox:run',)), 'print(1)')
