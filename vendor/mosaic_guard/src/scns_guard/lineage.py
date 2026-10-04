from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .canonical import canonical_json, sha256_hex
from .enums import ArgumentRole, SourceKind, SourceTrust
from .models import ArgumentBinding, FieldDerivationEdge, SourceRef
from .value_types import strict_equal


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

_DEFAULT_ROLES = {
    "from_account": ArgumentRole.IDENTIFIER,
    "to_account": ArgumentRole.TARGET,
    "amount_minor": ArgumentRole.AMOUNT,
    "memo": ArgumentRole.CONTENT,
    "account_id": ArgumentRole.IDENTIFIER,
}


class LineageError(ValueError):
    pass


class LineageAuthority:
    """Issue and verify runtime-owned source-lineage records.

    HMAC is a prototype trust boundary. Production deployments should use
    authenticated service identity, key rotation, and asymmetric verification.
    """

    def __init__(self, producer_secrets: Mapping[str, bytes]) -> None:
        if not producer_secrets:
            raise ValueError("at least one lineage producer is required")
        self._producer_secrets = dict(producer_secrets)

    def _sign(self, record: SourceRef) -> SourceRef:
        if record.producer_id is None:
            raise LineageError("lineage producer_id is required")
        secret = self._producer_secrets.get(record.producer_id)
        if secret is None:
            raise LineageError(f"unknown lineage producer: {record.producer_id}")
        signature = hmac.new(
            secret,
            canonical_json(record.unsigned_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return record.model_copy(update={"signature": signature})

    def issue_root(
        self,
        *,
        kind: SourceKind,
        trust: SourceTrust,
        content: Any,
        producer_id: str,
        source_id: str | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceRef:
        record = SourceRef(
            source_id=source_id or str(uuid4()),
            kind=kind,
            trust=trust,
            content_hash=sha256_hex(content),
            transformation="origin",
            producer_id=producer_id,
            created_at=created_at or datetime.now(timezone.utc),
            metadata=dict(metadata or {}),
        )
        return self._sign(record)

    def derive(
        self,
        *,
        kind: SourceKind,
        content: Any,
        producer_id: str,
        transformation: str,
        parents: Sequence[SourceRef],
        field_derivations: Sequence[FieldDerivationEdge] = (),
        source_id: str | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceRef:
        if not parents:
            raise LineageError("derived lineage requires at least one parent")
        if not transformation.strip() or transformation == "origin":
            raise LineageError("derived lineage requires a non-origin transformation")
        parent_ids = tuple(parent.source_id for parent in parents)
        if len(parent_ids) != len(set(parent_ids)):
            raise LineageError("derived lineage parents must be unique")
        for parent in parents:
            if not self._signature_valid(parent):
                raise LineageError(f"parent lineage does not verify: {parent.source_id}")
        ancestor_ids = tuple(
            sorted(
                {
                    ancestor_id
                    for parent in parents
                    for ancestor_id in (parent.source_id, *parent.ancestor_source_ids)
                }
            )
        )
        allowed_edge_sources = set(ancestor_ids)
        for edge in field_derivations:
            if not set(edge.parent_source_ids) <= allowed_edge_sources:
                raise LineageError(
                    f"field derivation for {edge.output_field!r} escapes parent closure"
                )
        record = SourceRef(
            source_id=source_id or str(uuid4()),
            kind=kind,
            trust=min(parent.trust for parent in parents),
            content_hash=sha256_hex(content),
            parent_source_ids=parent_ids,
            ancestor_source_ids=ancestor_ids,
            transformation=transformation,
            producer_id=producer_id,
            created_at=created_at or datetime.now(timezone.utc),
            field_derivations=tuple(field_derivations),
            metadata=dict(metadata or {}),
        )
        return self._sign(record)

    def _signature_valid(self, record: SourceRef) -> bool:
        if (
            record.producer_id is None
            or record.created_at is None
            or record.content_hash is None
            or record.transformation is None
            or record.signature is None
            or not _HEX_64.fullmatch(record.content_hash)
            or not _HEX_64.fullmatch(record.signature)
        ):
            return False
        secret = self._producer_secrets.get(record.producer_id)
        if secret is None:
            return False
        expected = hmac.new(
            secret,
            canonical_json(record.unsigned_payload()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(record.signature, expected)

    MAX_RECORDS = 128

    def verify(
        self, record: SourceRef, *, catalog: Mapping[str, SourceRef] | None = None,
        now: datetime | None = None,
    ) -> bool:
        if catalog is not None and (len(catalog) > self.MAX_RECORDS
                or catalog.get(record.source_id) != record):
            return False
        return self._verify(record, catalog=catalog, now=now or datetime.now(timezone.utc),
                            visiting=set(), memo={})

    def _verify(
        self, record: SourceRef, *, catalog: Mapping[str, SourceRef] | None,
        now: datetime, visiting: set[str], memo: dict[str, bool],
    ) -> bool:
        key = record.source_id
        if key in visiting:
            return False
        if key in memo:
            return memo[key]
        memo[key] = False
        if len(visiting) >= self.MAX_RECORDS or not self._signature_valid(record):
            return False
        if record.created_at is None or record.created_at.tzinfo is None or record.created_at > now:
            return False
        if not record.parent_source_ids:
            memo[key] = (record.transformation == 'origin' and not record.ancestor_source_ids
                         and not record.field_derivations)
            return memo[key]
        if catalog is None or record.transformation == 'origin':
            return False
        visiting.add(key)
        try:
            parents = []
            for parent_id in record.parent_source_ids:
                parent = catalog.get(parent_id)
                if (parent is None or parent.source_id != parent_id
                        or not self._verify(parent, catalog=catalog, now=now, visiting=visiting, memo=memo)
                        or parent.created_at is None or parent.created_at > record.created_at):
                    return False
                parents.append(parent)
            expected = tuple(sorted({ancestor for parent in parents
                                     for ancestor in (parent.source_id, *parent.ancestor_source_ids)}))
            if (record.ancestor_source_ids != expected
                    or record.trust != min(parent.trust for parent in parents)):
                return False
            memo[key] = all(set(edge.parent_source_ids) <= set(expected)
                            for edge in record.field_derivations)
            return memo[key]
        finally:
            visiting.remove(key)

    def verified_source_ids(
        self, records: Sequence[SourceRef], *, now: datetime | None = None,
    ) -> frozenset[str]:
        if len(records) > self.MAX_RECORDS:
            return frozenset()
        catalog = {record.source_id: record for record in records}
        if len(catalog) != len(records):
            return frozenset()
        memo: dict[str, bool] = {}
        stamp = now or datetime.now(timezone.utc)
        return frozenset(record.source_id for record in records
                         if self._verify(record, catalog=catalog, now=stamp, visiting=set(), memo=memo))

    @staticmethod
    def content_matches(record: SourceRef, content: Any) -> bool:
        return record.content_hash == sha256_hex(content)


class VerifiedPayloadProvenanceResolver:
    """Strong explicit-provenance baseline over signed runtime payload metadata.

    Exact field/value matches are deliberately conservative. Paraphrase recovery
    belongs to the learned source-field experiment, not this trusted resolver.
    """

    def __init__(
        self,
        authority: LineageAuthority,
        *,
        role_by_field: Mapping[str, ArgumentRole] | None = None,
    ) -> None:
        self.authority = authority
        self.role_by_field = {**_DEFAULT_ROLES, **dict(role_by_field or {})}

    def __call__(
        self,
        output: Any,
        trace: Any,
        active_source_ids: tuple[str, ...],
    ) -> tuple[ArgumentBinding, ...]:
        active_set = set(active_source_ids)
        catalog = trace.source_catalog()
        verified = self.authority.verified_source_ids(tuple(catalog.values()))
        bindings: list[ArgumentBinding] = []
        for field, value in output.params.items():
            matched_messages = [
                message
                for message in trace.messages
                if message.source.source_id in active_set
                and field in message.payload
                and strict_equal(message.payload[field], value) is True
            ]
            lineage_ids: list[str] = []
            content_matches = True
            for message in matched_messages:
                source_id = message.source.source_id
                record = catalog[source_id]
                content_matches = content_matches and self.authority.content_matches(
                    record,
                    {"text": message.text, "payload": message.payload},
                )
                for lineage_id in (source_id, *record.ancestor_source_ids):
                    if lineage_id not in lineage_ids:
                        lineage_ids.append(lineage_id)
            complete = bool(lineage_ids) and content_matches and all(
                lineage_id in verified
                for lineage_id in lineage_ids
            )
            bindings.append(
                ArgumentBinding(
                    field=field,
                    source_ids=tuple(lineage_ids) if complete else (),
                    role=self.role_by_field.get(field, ArgumentRole.OTHER),
                    observed_provenance_complete=complete,
                )
            )
        return tuple(bindings)


__all__ = [
    "FieldDerivationEdge",
    "LineageAuthority",
    "LineageError",
    "VerifiedPayloadProvenanceResolver",
]
