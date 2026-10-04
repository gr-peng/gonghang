from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from .canonical import canonical_json
from .enums import ObligationType
from .models import ActionProposal, ObligationToken
from .execution_store import ExecutionStore


class TokenAuthority:
    """Issues action-bound confirmation and MFA tokens."""

    def __init__(self, issuer: str, secret: bytes, *, state_store: ExecutionStore | None = None) -> None:
        self.issuer = issuer
        self._secret = secret
        self.state_store = state_store if state_store is not None else ExecutionStore()

    def issue(
        self,
        action: ActionProposal,
        obligation: ObligationType,
        *,
        ttl_seconds: int = 300,
        now: datetime | None = None,
        metadata: dict[str, object] | None = None,
        expires_at: datetime | None = None,
    ) -> ObligationToken:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        now = now or datetime.now(timezone.utc)
        expiry = now + timedelta(seconds=ttl_seconds)
        if expires_at is not None:
            expiry = min(expiry, expires_at)
        unsigned = ObligationToken(
            obligation=obligation,
            action_digest=action.digest,
            session_id=action.session_id,
            actor_id=action.actor_id,
            issuer=self.issuer,
            issued_at=now,
            expires_at=expiry,
            signature="pending",
            metadata=metadata or {},
        )
        signature = self._sign(unsigned)
        return unsigned.model_copy(update={"signature": signature})

    def _sign(self, token: ObligationToken) -> str:
        return hmac.new(
            self._secret,
            canonical_json(token.unsigned_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify(
        self,
        token: ObligationToken,
        action: ActionProposal,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Signature/binding/time validation, deliberately independent of live state.

        Historical receipt replay uses this method. Live authorization must use
        verify_for_use() and atomic reservation before backend dispatch.
        """
        now = now or datetime.now(timezone.utc)
        if token.issuer != self.issuer:
            return False
        if now < token.issued_at or now >= token.expires_at:
            return False
        if token.action_digest != action.digest:
            return False
        if token.session_id != action.session_id or token.actor_id != action.actor_id:
            return False
        return hmac.compare_digest(token.signature, self._sign(token))

    def verify_for_use(self, token: ObligationToken, action: ActionProposal, *, now: datetime | None = None) -> bool:
        return self.verify(token, action, now=now) and self.state_store.token_available(token)

    def revoke(self, token: ObligationToken, *, operator_id: str, reason: str) -> bool:
        """Trusted host API only; does not authenticate the operator or undo transfers."""
        if token.issuer != self.issuer or not hmac.compare_digest(token.signature, self._sign(token)):
            raise ValueError('cannot revoke an invalid or foreign token')
        return self.state_store.revoke(token, operator_id=operator_id, reason=reason)
