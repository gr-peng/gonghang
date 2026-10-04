"""Independent fixed-seed/fault-injection regressions; no network or model service."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

import pytest

from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.lineage import LineageAuthority
from scns_guard.llm_adapter import parse_model_json
from scns_guard.models import ObligationToken
from .test_gateway import gateway, identity, proof, review_request


def _approved(gateway):
    g, auth, proofs, lineage, bank = gateway
    credential = identity(auth)
    reviewed = g.review(credential, review_request(lineage))
    g.approve(credential, reviewed['handle'], proof(proofs, reviewed))
    return g, bank, credential, reviewed


def _clock(monkeypatch):
    import scns_guard.auth as auth
    import scns_guard.controller as controller
    import scns_guard.gateway as gateway_module
    import scns_guard.simulator as simulator
    import scns_guard.tokens as tokens
    import scns_guard.trust as trust
    clock = [datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    for module in (auth, controller, gateway_module, simulator, tokens, trust):
        monkeypatch.setattr(module, 'datetime', Clock)
    return clock


@pytest.mark.parametrize('phase', ['reservation_wait', 'after_reservation', 'snapshot_wait'])
def test_fact_expiry_is_rechecked_at_mutation_and_dispatch(gateway, monkeypatch, phase):
    g, bank, credential, reviewed = _approved(gateway)
    clock = _clock(monkeypatch)
    supply = g.fact_supplier
    def short_facts(actor):
        return tuple(g.controller.fact_authority.sign(f.model_copy(update={
            'expires_at': clock[0] + timedelta(seconds=1)})) for f in supply(actor))
    g.fact_supplier = short_facts
    if phase == 'snapshot_wait':
        original = bank.snapshot
        calls = [0]
        def delayed():
            value = original()
            calls[0] += 1
            if calls[0] == 1:
                clock[0] += timedelta(seconds=2)
            return value
        monkeypatch.setattr(bank, 'snapshot', delayed)
    else:
        original = g.store.reserve
        def delayed(*args, **kwargs):
            if phase == 'reservation_wait':
                clock[0] += timedelta(seconds=2)
            result = original(*args, **kwargs)
            if phase == 'after_reservation':
                clock[0] += timedelta(seconds=2)
            return result
        monkeypatch.setattr(g.store, 'reserve', delayed)
    outcome = g.execute(credential, reviewed['handle'])
    assert not bank.execution_log, 'expired trusted facts still reached the bank'
    expected = 'blocked' if phase == 'reservation_wait' else 'unknown'
    assert outcome['status'] == expected
    consumed = g.store._db.execute('SELECT count(*) FROM token_lifecycle').fetchone()[0]
    assert consumed == (0 if phase == 'reservation_wait' else 1)
    if phase != 'reservation_wait':
        assert g.execute(credential, reviewed['handle']) == outcome


@pytest.mark.parametrize('kind', ['identity', 'token'])
def test_expiry_during_snapshot_does_not_dispatch(gateway, monkeypatch, kind):
    g, bank, credential, reviewed = _approved(gateway)
    clock = _clock(monkeypatch)
    if kind == 'token':
        # Keep the identity valid; only the exact approval expires.
        row = g.store._db.execute('SELECT payload_json FROM gateway_tokens').fetchone()
        token = ObligationToken.model_validate_json(row[0])
        token = token.model_copy(update={'expires_at': clock[0] + timedelta(seconds=1)})
        token = token.model_copy(update={'signature': g.tokens._sign(token)})
        with g.store._atomic():
            g.store._db.execute('UPDATE gateway_tokens SET payload_json=?', (token.model_dump_json(),))
    original = bank.snapshot
    first = [True]
    def delayed():
        value = original()
        if first[0]:
            first[0] = False
            clock[0] += timedelta(seconds=121 if kind == 'identity' else 2)
        return value
    monkeypatch.setattr(bank, 'snapshot', delayed)
    assert g.execute(credential, reviewed['handle'])['status'] == 'unknown'
    assert not bank.execution_log


def test_same_timestamp_conflicting_facts_cannot_be_selected_by_uuid(gateway):
    g, auth, _, lineage, bank = gateway
    supply = g.fact_supplier
    def contradictory(actor):
        facts = list(supply(actor))
        fact = next(f for f in facts if f.predicate == 'authenticated')
        facts = [f for f in facts if f.predicate != 'authenticated']
        facts.extend(g.controller.fact_authority.sign(fact.model_copy(update={'fact_id': key, 'value': val}))
                     for key, val in [('a-deny', False), ('z-permit', True)])
        return tuple(facts)
    g.fact_supplier = contradictory
    reviewed = g.review(identity(auth), review_request(lineage))
    assert reviewed['outcome']['status'] == 'blocked'
    assert not bank.execution_log


def test_failed_cancellation_rolls_back_proof_consumption(gateway, monkeypatch):
    g, bank, credential, reviewed = _approved(gateway)
    signed = proof(g.control_authority, reviewed, operation='cancel')
    original = g.store._event
    def disk_fault(event, **details):
        if event in ('request_cancelled', 'cancellation_too_late'):
            raise OSError('synthetic audit storage failure')
        return original(event, **details)
    monkeypatch.setattr(g.store, '_event', disk_fault)
    with pytest.raises(OSError):
        g.cancel(credential, reviewed['handle'], signed)
    monkeypatch.setattr(g.store, '_event', original)
    # The same still-valid signed cancellation can be safely resubmitted: neither
    # the cancellation nor its proof nonce may commit alone.
    assert g.cancel(credential, reviewed['handle'], signed)['cancelled']
    assert g.execute(credential, reviewed['handle'])['status'] == 'blocked'
    assert not bank.execution_log


def test_cancel_rechecks_identity_inside_final_mutation(gateway, monkeypatch):
    g, bank, credential, reviewed = _approved(gateway)
    signed = proof(g.control_authority, reviewed, operation='cancel')
    clock = _clock(monkeypatch)
    original = g.store.cancel
    def delayed(*args, **kwargs):
        clock[0] += timedelta(minutes=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(g.store, 'cancel', delayed)
    with pytest.raises((ValueError, PermissionError)):
        g.cancel(credential, reviewed['handle'], signed)
    assert g.store._db.execute('SELECT count(*) FROM cancelled_requests').fetchone()[0] == 0


def test_approval_token_never_outlives_its_control_proof(gateway, monkeypatch):
    g, auth, proofs, lineage, _ = gateway
    clock = _clock(monkeypatch)
    clock[0] = clock[0].replace(microsecond=800000)
    credential = identity(auth)
    reviewed = g.review(credential, review_request(lineage))
    signed = proof(proofs, reviewed)
    from scns_guard.auth import ControlClaims
    _, envelope = proofs.verify(signed, ControlClaims)
    g.approve(credential, reviewed['handle'], signed)
    token = ObligationToken.model_validate_json(g.store._db.execute('SELECT payload_json FROM gateway_tokens').fetchone()[0])
    assert token.expires_at.timestamp() <= envelope.expires_at


def _diamond(depth=8):
    authority = LineageAuthority({'fixture': b'l' * 32})
    root = authority.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
        content={}, producer_id='fixture', source_id='root')
    nodes, parents = [root], [root]
    for level in range(depth):
        pair = [authority.derive(kind=SourceKind.MEMORY, content={}, producer_id='fixture',
            transformation='summary', parents=parents, source_id=f'{level}-{branch}') for branch in range(2)]
        nodes.extend(pair)
        parents = pair
    return authority, nodes


def test_shared_ancestry_verification_is_not_exponential(monkeypatch):
    authority, nodes = _diamond()
    original, count = authority._signature_valid, [0]
    def counted(record):
        count[0] += 1
        return original(record)
    monkeypatch.setattr(authority, '_signature_valid', counted)
    assert authority.verified_source_ids(nodes) == frozenset(n.source_id for n in nodes)
    assert count[0] <= 2 * len(nodes), f'{len(nodes)} nodes required {count[0]} signature verifications'


@pytest.mark.parametrize('raw', ['{"x":1e999}', '{"x":-1e999}', '{"x":[1e999]}'])
def test_json_exponent_overflow_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_model_json(raw)


def test_deep_json_is_bounded_and_reports_value_error():
    raw = '{"x":' + '[' * 1500 + '0' + ']' * 1500 + '}'
    with pytest.raises(ValueError):
        parse_model_json(raw)

@pytest.mark.parametrize('kind', ['identity', 'fact'])
def test_final_authorization_clock_is_read_after_waiting_for_state(gateway, monkeypatch, kind):
    """The final guard's OWN state-lock waits must not freeze its expiry clock."""
    import sys
    g, bank, credential, reviewed = _approved(gateway)
    clock = _clock(monkeypatch)
    if kind == 'fact':
        supply = g.fact_supplier
        def short_facts(actor):
            return tuple(g.controller.fact_authority.sign(f.model_copy(update={
                'expires_at': clock[0] + timedelta(seconds=1)})) for f in supply(actor))
        g.fact_supplier = short_facts
    original_snapshot, original_check = bank.snapshot, g.store.session_revoked
    ready, injected = [False], [False]
    def snapshot():
        value = original_snapshot()
        ready[0] = True
        return value
    def waited(actor, session):
        if (ready[0] and not injected[0]
                and sys._getframe(1).f_code.co_name == 'authorization_current'):
            injected[0] = True
            clock[0] += timedelta(seconds=121 if kind == 'identity' else 2)
        return original_check(actor, session)
    monkeypatch.setattr(bank, 'snapshot', snapshot)
    monkeypatch.setattr(g.store, 'session_revoked', waited)
    outcome = g.execute(credential, reviewed['handle'])
    assert injected[0], 'fault injection must actually reach the final state check'
    assert not bank.execution_log
    assert outcome['status'] == 'unknown'
