from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scns_guard.enums import FactTrust, ObligationType, SourceKind
from scns_guard.models import TrustedFact
from scns_guard.tokens import TokenAuthority
from scns_guard.trust import FactAuthority

from .helpers import action_from_trace, make_trace


def test_only_signed_trusted_fact_verifies() -> None:
    authority = FactAuthority({"issuer": b"secret"})
    fact = TrustedFact(
        predicate="authenticated",
        subject="u",
        value=True,
        issuer="issuer",
    )
    assert not authority.verify(fact)
    signed = authority.sign(fact)
    assert authority.verify(signed)
    forged = signed.model_copy(update={"value": False})
    assert not authority.verify(forged)
    untrusted = fact.model_copy(update={"trust": FactTrust.UNTRUSTED})
    assert not authority.verify(untrusted)


def test_fact_expiry_is_enforced() -> None:
    now = datetime.now(timezone.utc)
    authority = FactAuthority({"issuer": b"secret"})
    signed = authority.sign(
        TrustedFact(
            predicate="x",
            subject="u",
            value=1,
            issuer="issuer",
            issued_at=now,
            expires_at=now + timedelta(seconds=1),
        )
    )
    assert authority.verify(signed, now=now)
    assert not authority.verify(signed, now=now + timedelta(seconds=2))


def test_tokens_are_bound_to_exact_action_session_and_actor() -> None:
    authority = TokenAuthority("tokens", b"secret")
    first = action_from_trace(make_trace(trace_id="token-a"))
    second = action_from_trace(make_trace(amount_minor=20_100, trace_id="token-a"))
    token = authority.issue(first, ObligationType.CONFIRMATION)
    assert authority.verify(token, first)
    assert not authority.verify(token, second)


def test_tokens_are_bound_to_security_relevant_provenance() -> None:
    authority = TokenAuthority("tokens", b"secret")
    direct = action_from_trace(
        make_trace(
            trace_id="token-provenance",
            extra_kind=SourceKind.RAG,
            extra_payload={"override_to": "acct-mallory"},
        )
    )
    laundered = direct.model_copy(
        update={
            "sources": tuple(source for source in direct.sources if source.kind is not SourceKind.RAG),
            "argument_bindings": tuple(
                binding.model_copy(
                    update={
                        "source_ids": tuple(
                            source_id
                            for source_id in binding.source_ids
                            if not source_id.startswith("extra-")
                        )
                    }
                )
                for binding in direct.argument_bindings
            ),
        }
    )
    assert direct.params == laundered.params
    token = authority.issue(direct, ObligationType.MFA)
    assert authority.verify(token, direct)
    assert not authority.verify(token, laundered)


def test_future_dated_fact_and_token_do_not_verify_early() -> None:
    now = datetime.now(timezone.utc)
    facts = FactAuthority({"issuer": b"secret"})
    future_fact = facts.sign(
        TrustedFact(
            predicate="x",
            subject="u",
            value=1,
            issuer="issuer",
            issued_at=now + timedelta(seconds=10),
            expires_at=now + timedelta(seconds=20),
        )
    )
    action = action_from_trace(make_trace(trace_id="future-token"))
    tokens = TokenAuthority("tokens", b"secret")
    future_token = tokens.issue(
        action,
        ObligationType.CONFIRMATION,
        now=now + timedelta(seconds=10),
    )
    assert not facts.verify(future_fact, now=now)
    assert not tokens.verify(future_token, action, now=now)


def test_fact_index_uses_signed_issue_time_not_caller_order() -> None:
    now = datetime.now(timezone.utc)
    authority = FactAuthority({"issuer": b"secret"})
    older = authority.sign(
        TrustedFact(
            predicate="limit",
            subject="u",
            value=100,
            issuer="issuer",
            issued_at=now - timedelta(seconds=2),
            expires_at=now + timedelta(seconds=20),
        )
    )
    newer = authority.sign(
        TrustedFact(
            predicate="limit",
            subject="u",
            value=50,
            issuer="issuer",
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=20),
        )
    )
    forward = authority.verified_index((older, newer), now=now)
    reverse = authority.verified_index((newer, older), now=now)
    assert [fact.value for fact in forward[("limit", "u")]] == [100, 50]
    assert [fact.value for fact in reverse[("limit", "u")]] == [100, 50]
