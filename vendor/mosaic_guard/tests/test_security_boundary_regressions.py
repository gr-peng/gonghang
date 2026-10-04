"""Adversarial regressions for the 2026-09-05 code audit."""
from datetime import datetime, timedelta, timezone

import pytest

from scns_guard.auth import ControlClaims, IdentityClaims
from scns_guard.enums import RiskLevel
from scns_guard.policy import ConditionEvaluator
from .helpers import action_from_trace, make_trace
from .test_gateway import gateway, identity, proof, review_request
from .test_service import service, request


@pytest.mark.parametrize('predicate,value', [
    ('authenticated', 1), ('authenticated', 1.0),
    ('recipient_allowed', 1), ('account_active', 1),
    ('owned_accounts', 'prefix-acct-user-suffix'),
    ('owned_accounts', {'acct-user': True}),
    ('owned_accounts', ['acct-user', 1]),
])
def test_malformed_signed_fact_never_authorizes(gateway, predicate, value):
    g, auth, proofs, lineage, bank = gateway
    supply = g.fact_supplier
    def malformed(actor):
        return tuple(g.controller.fact_authority.sign(f.model_copy(update={'value': value}))
                     if f.predicate == predicate else f for f in supply(actor))
    g.fact_supplier = malformed
    cred = identity(auth)
    reviewed = g.review(cred, review_request(lineage))
    assert reviewed['outcome']['status'] == 'blocked'
    g.approve(cred, reviewed['handle'], proof(proofs, reviewed))
    assert g.execute(cred, reviewed['handle'])['status'] == 'blocked'
    assert not bank.execution_log


@pytest.mark.parametrize('condition', [
    {'equals': {'left': 1, 'right': True}},
    {'equals': {'left': [1], 'right': [True]}},
    {'contains': {'collection': 'prefix-acct-user-suffix', 'item': 'acct-user'}},
    {'in': {'collection': {'literal': {'acct-user': False}}, 'item': 'acct-user'}},
    {'compare': {'left': True, 'op': '==', 'right': 1}},
])
def test_type_errors_stay_unknown_under_negation(condition):
    evaluator = ConditionEvaluator(action_from_trace(make_trace()), {})
    assert not evaluator.evaluate(condition)
    assert not evaluator.evaluate({'not': condition})


def test_revoked_credential_cannot_lock_current_session(service):
    _, runtime, server = service
    g = runtime.gateway
    old = identity(g.identity_authority)
    operator = identity(g.identity_authority, actor='op', session='ops', scopes=('operator:revoke',))
    signed = g.control_authority.issue(ControlClaims(operation='revoke_session', actor_id='op',
        session_id='ops', target='user-1', binding=g.session_binding('user-1', 'session-1')), key_id='v1')
    g.revoke_session(operator, 'user-1', 'session-1', signed)
    for _ in range(5):
        assert request(server, '/v1/screen', {'text': 'hello'}, old)[0] == 403
    assert g.circuit.snapshot('user-1')['denials'] == 0
    new = identity(g.identity_authority, session='new')
    reviewed = g.review(new, review_request(g.lineage, session='new'))
    assert reviewed['outcome']['status'] == 'needs_confirmation'


@pytest.mark.parametrize('expire_at', ['before_facts', 'facts', 'reservation'])
def test_identity_expiry_at_dispatch_leaves_tokens_and_request_pending(gateway, monkeypatch, expire_at):
    g, auth, proofs, lineage, bank = gateway
    cred = identity(auth)
    reviewed = g.review(cred, review_request(lineage))
    g.approve(cred, reviewed['handle'], proof(proofs, reviewed))
    clock = [datetime.now(timezone.utc)]
    original_verify = auth.verify
    def verify(raw, claim_type, **kwargs):
        return original_verify(raw, claim_type, now=clock[0])
    monkeypatch.setattr(auth, 'verify', verify)
    if expire_at == 'before_facts':
        original = g.controller._execute_locked
        def delayed(*args, **kwargs):
            clock[0] += timedelta(minutes=10)
            return original(*args, **kwargs)
        monkeypatch.setattr(g.controller, '_execute_locked', delayed)
    elif expire_at == 'facts':
        original = g.fact_supplier
        def delayed(actor):
            clock[0] += timedelta(minutes=10)
            return original(actor)
        g.fact_supplier = delayed
    else:
        original = g.store.reserve
        def delayed(*args, **kwargs):
            clock[0] += timedelta(minutes=10)
            return original(*args, **kwargs)
        monkeypatch.setattr(g.store, 'reserve', delayed)
    result = g.execute(cred, reviewed['handle'])
    assert result['status'] == 'blocked'
    assert not bank.execution_log
    row = g.store._db.execute('SELECT state FROM execution_requests').fetchone()
    assert row[0] == 'pending'
    assert g.store._db.execute('SELECT count(*) FROM token_lifecycle').fetchone()[0] == 0
    assert not g.circuit.snapshot('user-1')['locked']
    assert g.circuit.snapshot('user-1')['failures'] == 0


def test_approval_rechecks_identity_after_waiting_for_state_transaction(gateway, monkeypatch):
    from contextlib import contextmanager
    g, auth, proofs, lineage, _ = gateway
    cred = identity(auth)
    reviewed = g.review(cred, review_request(lineage))
    signed = proof(proofs, reviewed)
    clock = [datetime.now(timezone.utc)]
    original_verify, atomic = auth.verify, g.store._atomic
    monkeypatch.setattr(auth, 'verify', lambda raw, typ, **kw: original_verify(raw, typ, now=clock[0]))
    @contextmanager
    def delayed():
        with atomic():
            clock[0] += timedelta(minutes=10)
            yield
    monkeypatch.setattr(g.store, '_atomic', delayed)
    with pytest.raises(ValueError):
        g.approve(cred, reviewed['handle'], signed)
    assert g.store._db.execute('SELECT count(*) FROM gateway_tokens').fetchone()[0] == 0
    assert g.store._db.execute('SELECT count(*) FROM gateway_proofs').fetchone()[0] == 0
