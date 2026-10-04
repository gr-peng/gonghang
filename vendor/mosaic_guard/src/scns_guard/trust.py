from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from .canonical import canonical_json
from .enums import FactTrust
from .models import TrustedFact


class TrustError(ValueError):
    pass


class FactAuthority:
    """HMAC-backed fact issuer used by the prototype trust boundary.

    HMAC is sufficient for the local prototype. A production deployment should
    replace this with an authenticated service and asymmetric signatures/HSMs.
    """

    def __init__(self, issuer_secrets: dict[str, bytes]) -> None:
        if not issuer_secrets:
            raise ValueError("At least one trusted issuer is required")
        self._issuer_secrets = dict(issuer_secrets)

    def sign(self, fact: TrustedFact) -> TrustedFact:
        secret = self._issuer_secrets.get(fact.issuer)
        if secret is None:
            raise TrustError(f"Unknown trusted issuer: {fact.issuer}")
        if fact.trust is not FactTrust.TRUSTED:
            raise TrustError("Only trusted facts can be signed by FactAuthority")
        signature = hmac.new(
            secret,
            canonical_json(fact.unsigned_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return fact.model_copy(update={"signature": signature})

    def verify(self, fact: TrustedFact, *, now: datetime | None = None) -> bool:
        if fact.trust is not FactTrust.TRUSTED or not fact.signature:
            return False
        secret = self._issuer_secrets.get(fact.issuer)
        if secret is None:
            return False
        now = now or datetime.now(timezone.utc)
        if now < fact.issued_at:
            return False
        if fact.expires_at is not None and now >= fact.expires_at:
            return False
        expected = hmac.new(
            secret,
            canonical_json(fact.unsigned_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(fact.signature, expected)

    def verified_index(
        self,
        facts: list[TrustedFact] | tuple[TrustedFact, ...],
        *,
        now: datetime | None = None,
    ) -> dict[tuple[str, str], list[TrustedFact]]:
        index: dict[tuple[str, str], list[TrustedFact]] = {}
        for fact in facts:
            if self.verify(fact, now=now):
                index.setdefault((fact.predicate, fact.subject), []).append(fact)
        for rows in index.values():
            rows.sort(key=lambda item: (item.issued_at, item.fact_id))
        return index


def fact_value(
    verified_index: dict[tuple[str, str], list[TrustedFact]],
    predicate: str,
    subject: str,
    *,
    default: Any = None,
) -> Any:
    from .value_types import latest_fact_value
    value = latest_fact_value(predicate, verified_index.get((predicate, subject), []))
    return default if value is None else value
