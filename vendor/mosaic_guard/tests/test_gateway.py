import json
from datetime import datetime, timedelta, timezone

import pytest

from scns_guard.auth import CredentialAuthority, IdentityClaims, ControlClaims
from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.gateway import SafetyGateway, ReviewRequest
from scns_guard.lineage import LineageAuthority
from scns_guard.runtime_store import RuntimeStore


@pytest.fixture
def gateway(components, tmp_path):
    policy, facts, _, bank = components
    auth = CredentialAuthority(issuer='login', audience='guard', keys={'v1': b'i' * 32}, max_ttl=300)
    proofs = CredentialAuthority(issuer='approval', audience='guard', keys={'v1': b'p' * 32}, max_ttl=120)
    lineage = LineageAuthority({'frontend': b'l' * 32})
    store = RuntimeStore(tmp_path / 'gateway.db')
    gateway = SafetyGateway(store=store, policy=policy, fact_authority=facts, tool=bank,
        fact_supplier=lambda actor: bank.issue_facts(actor, facts), lineage_authority=lineage,
        identity_authority=auth, control_authority=proofs, token_secret=b't' * 32)
    yield gateway, auth, proofs, lineage, bank
    store.close()


def identity(auth, actor='user-1', session='session-1', scopes=('agent:review', 'agent:execute', 'human:confirm')):
    return auth.issue(IdentityClaims(actor_id=actor, session_id=session, scopes=scopes), key_id='v1', ttl=120)


def review_request(lineage, *, amount=20_000, actor='user-1', session='session-1', target='acct-alice'):
    payload = dict(from_account='acct-user', to_account=target, amount_minor=amount)
    content = {'text': '用户结构化测试输入', 'payload': payload}
    source = lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER, content=content,
        producer_id='frontend', metadata={'actor_id': actor, 'session_id': session})
    trace = AgentTrace(trace_id='test', actor_id=actor, session_id=session,
        messages=(SourceMessage(source=source, **content),))
    return ReviewRequest(trace=trace, model_output=json.dumps(dict(schema_version='mosaic-planner-v2',
        kind='task', action_type='transfer', params=payload)))


def proof(proofs, review, *, operation='confirmation', actor='user-1', session='session-1'):
    return proofs.issue(ControlClaims(operation=operation, actor_id=actor, session_id=session,
        target=review['handle'], binding=review['action_digest']), key_id='v1', ttl=60)


def test_gateway_normal_confirmation_exactly_once_and_no_internal_data(gateway):
    g, auth, proofs, lineage, bank = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage))
    assert review['outcome']['status'] == 'needs_confirmation'
    assert g.execute(cred, review['handle'])['status'] == 'needs_confirmation'
    human = g.confirmation_view(cred, review['handle'])
    assert human['params']['to_account'] == 'acct-alice'
    signed = proof(proofs, review)
    g.approve(cred, review['handle'], signed)
    first = g.execute(cred, review['handle'])
    assert first['status'] == 'succeeded'
    assert g.execute(cred, review['handle']) == first
    assert len(bank.execution_log) == 1
    serialized = json.dumps(first)
    for secret in ('acct-user', 'acct-alice', 'signature', 'trusted_facts', 'before_state', 'memo'):
        assert secret not in serialized
    with pytest.raises(ValueError, match='used'):
        g.approve(cred, review['handle'], signed)


def test_large_action_requires_independent_mfa(gateway):
    g, auth, proofs, lineage, bank = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage, amount=150_000))
    g.approve(cred, review['handle'], proof(proofs, review))
    assert g.execute(cred, review['handle'])['status'] == 'needs_mfa'
    g.approve(cred, review['handle'], proof(proofs, review, operation='mfa'))
    assert g.execute(cred, review['handle'])['status'] == 'succeeded'
    assert len(bank.execution_log) == 1


def test_model_cannot_inject_authority_or_rewrite_signed_source(gateway):
    g, auth, _, lineage, bank = gateway
    cred = identity(auth)
    request = review_request(lineage)
    raw = json.loads(request.model_output)
    raw['actor_id'] = 'admin'
    with pytest.raises(ValueError):
        g.review(cred, request.model_copy(update={'model_output': json.dumps(raw)}))
    changed = request.trace.messages[0].model_copy(update={'payload': {'amount_minor': 1}})
    with pytest.raises(ValueError, match='source'):
        g.review(cred, request.model_copy(update={'trace': request.trace.model_copy(update={'messages': (changed,)})}))
    assert not bank.execution_log


def test_owner_and_scope_isolation_and_no_public_token_issuer(gateway):
    g, auth, proofs, lineage, _ = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage))
    for other in (identity(auth, actor='other'), identity(auth, session='other'), identity(auth, scopes=('audit:read',))):
        with pytest.raises(PermissionError):
            g.execute(other, review['handle'])
    with pytest.raises(PermissionError):
        g.confirmation_view(identity(auth, scopes=('agent:review',)), review['handle'])
    with pytest.raises(ValueError):
        g.approve(cred, review['handle'], identity(auth))
    different = g.review(cred, review_request(lineage, amount=21_000))
    with pytest.raises(PermissionError):
        g.approve(cred, different['handle'], proof(proofs, review))


def test_cancel_is_a_trusted_control_event_not_model_no_action(gateway):
    g, auth, proofs, lineage, bank = gateway
    cred = identity(auth)
    review = g.review(cred, review_request(lineage))
    g.approve(cred, review['handle'], proof(proofs, review))
    request = review_request(lineage).model_copy(update={'model_output': json.dumps({
        'schema_version': 'mosaic-planner-v2', 'kind': 'no_action', 'reason': 'cancelled'})})
    assert g.review(cred, request)['status'] == 'no_action'
    assert g.cancel(cred, review['handle'], proof(proofs, review, operation='cancel'))['cancelled']
    assert g.execute(cred, review['handle'])['status'] == 'blocked'
    assert not bank.execution_log


def test_credentials_reject_expired_forged_and_overlong_lifetime(gateway):
    g, auth, _, lineage, _ = gateway
    claims = IdentityClaims(actor_id='user-1', session_id='session-1', scopes=('agent:review',))
    expired = auth.issue(claims, key_id='v1', ttl=1, now=datetime.now(timezone.utc) - timedelta(seconds=2))
    for credential in (expired, identity(auth) + 'x', 'not-a-credential'):
        with pytest.raises(ValueError):
            g.review(credential, review_request(lineage))
    with pytest.raises(ValueError):
        auth.issue(claims, key_id='v1', ttl=301)


def test_operator_reset_requires_role_fresh_generation_and_distinct_proof(gateway):
    g, auth, proofs, lineage, _ = gateway
    cred = identity(auth)
    g.circuit.reset('user-1', operator_id='fixture', reason='setup')
    generation = g.circuit.snapshot('user-1')['generation']
    signed = proofs.issue(ControlClaims(operation='reset', actor_id='operator', session_id='op-session',
        target='user-1', binding=g.reset_binding('user-1', generation)), key_id='v1', ttl=60)
    with pytest.raises(PermissionError):
        g.reset(cred, 'user-1', signed)
    op = identity(auth, actor='operator', session='op-session', scopes=('operator:reset',))
    assert g.reset(op, 'user-1', signed)['locked'] is False
    with pytest.raises((ValueError, PermissionError)):
        g.reset(op, 'user-1', signed)
