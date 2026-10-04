"""Short-lived signed service credentials; issuance is a trusted-host API only.

HMAC is suitable for a single controlled trust domain. Identity and approval
issuers require independent keys. This does not perform login or real MFA.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .canonical import canonical_json
from .llm_adapter import parse_model_json
from .models import StrictModel


class IdentityClaims(StrictModel):
    kind: Literal['identity'] = 'identity'
    actor_id: str = Field(strict=True, min_length=1, max_length=128)
    session_id: str = Field(strict=True, min_length=1, max_length=128)
    scopes: tuple[Literal['agent:review', 'agent:execute', 'human:confirm', 'operator:reset',
                          'audit:read', 'sandbox:run', 'operator:revoke'], ...]


class ControlClaims(StrictModel):
    kind: Literal['control'] = 'control'
    operation: Literal['confirmation', 'mfa', 'cancel', 'reset', 'revoke_session']
    actor_id: str = Field(strict=True, min_length=1, max_length=128)
    session_id: str = Field(strict=True, min_length=1, max_length=128)
    target: str = Field(strict=True, min_length=1, max_length=128)
    binding: str = Field(strict=True, pattern='^[0-9a-f]{64}$')


class SignedCredential(StrictModel):
    version: Literal[1] = 1
    issuer: str
    audience: str
    key_id: str
    issued_at: int = Field(strict=True)
    expires_at: int = Field(strict=True)
    nonce: str = Field(min_length=1, max_length=128)
    claims: dict
    signature: str = Field(pattern='^[0-9a-f]{64}$')


class CredentialAuthority:
    def __init__(self, *, issuer: str, audience: str, keys: dict[str, bytes], max_ttl: int = 300):
        if not issuer or not audience or not keys or any(len(key) < 32 for key in keys.values()):
            raise ValueError('named issuer/audience and at least 256-bit keys required')
        if type(max_ttl) is not int or not 1 <= max_ttl <= 3600:
            raise ValueError('invalid maximum credential lifetime')
        self.issuer, self.audience, self.keys, self.max_ttl = issuer, audience, dict(keys), max_ttl

    def _signature(self, data: dict, key_id: str) -> str:
        return hmac.new(self.keys[key_id], canonical_json(data).encode(), hashlib.sha256).hexdigest()

    def issue(self, claims: IdentityClaims | ControlClaims, *, key_id: str, ttl: int = 60,
              now: datetime | None = None) -> str:
        if type(ttl) is not int or not 1 <= ttl <= self.max_ttl:
            raise ValueError('credential lifetime exceeds configured limit')
        issued = int((now or datetime.now(timezone.utc)).timestamp())
        data = dict(version=1, issuer=self.issuer, audience=self.audience, key_id=key_id,
                    issued_at=issued, expires_at=issued + ttl, nonce=str(uuid4()), claims=claims.model_dump(mode='json'))
        data['signature'] = self._signature(data, key_id)
        return base64.urlsafe_b64encode(canonical_json(data).encode()).decode()

    def verify(self, raw: str, claim_type: type[IdentityClaims] | type[ControlClaims], *,
               now: datetime | None = None) -> tuple[IdentityClaims | ControlClaims, SignedCredential]:
        try:
            if not isinstance(raw, str) or not 1 <= len(raw) <= 16_384:
                raise ValueError('credential size')
            data = parse_model_json(base64.b64decode(raw, altchars=b'-_', validate=True).decode('utf-8'))
            envelope = SignedCredential.model_validate(data)
            unsigned = envelope.model_dump(exclude={'signature'})
            stamp = int((now or datetime.now(timezone.utc)).timestamp())
            if (envelope.issuer != self.issuer or envelope.audience != self.audience
                    or not envelope.issued_at <= stamp < envelope.expires_at
                    or not 1 <= envelope.expires_at - envelope.issued_at <= self.max_ttl
                    or not hmac.compare_digest(envelope.signature, self._signature(unsigned, envelope.key_id))):
                raise ValueError('credential verification')
            return claim_type.model_validate(envelope.claims), envelope
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValueError('invalid or expired signed credential') from exc
