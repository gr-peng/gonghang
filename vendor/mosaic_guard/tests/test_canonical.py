from __future__ import annotations

import pytest

from scns_guard.canonical import canonical_json, sha256_hex
from scns_guard.enums import ObligationType, SourceKind
from scns_guard.policy import PolicyEngine


def test_unordered_collections_have_stable_canonical_encoding() -> None:
    left = {
        "kinds": frozenset({SourceKind.RAG, SourceKind.USER}),
        "obligations": {ObligationType.MFA, ObligationType.CONFIRMATION},
    }
    right = {
        "obligations": {ObligationType.CONFIRMATION, ObligationType.MFA},
        "kinds": frozenset({SourceKind.USER, SourceKind.RAG}),
    }
    assert canonical_json(left) == canonical_json(right)
    assert sha256_hex(left) == sha256_hex(right)


def test_policy_snapshot_round_trip_preserves_digest(root) -> None:
    policy = PolicyEngine.from_yaml(root / "configs" / "transfer_policy.yaml")
    reconstructed = PolicyEngine.from_mapping(policy.snapshot())
    assert reconstructed.spec.digest == policy.spec.digest
    assert reconstructed.snapshot() == policy.snapshot()


def test_canonical_json_rejects_stringified_key_collisions() -> None:
    with pytest.raises(ValueError, match="key collision"):
        canonical_json({1: "integer", "1": "string"})
