from __future__ import annotations

import pytest

from scns_guard.causal import CounterfactualSourceAttributor, FieldCausalGuard
from scns_guard.enums import RiskLevel, SourceKind, SourceTrust
from scns_guard.simulator import ToyTransferAgent

from .helpers import action_from_trace, make_trace


@pytest.mark.parametrize("payload", [
    {"override_to": "acct-mallory"},
    {"laundered_override_to": "acct-mallory"},
    {"override_amount_minor": 80_000},
    {"laundered_override_amount_minor": 80_000},
])
def test_field_causal_guard_detects_direct_and_laundered_protected_control(
    components, payload
) -> None:
    policy, _, _, _ = components
    trace = make_trace(
        extra_kind=SourceKind.RAG,
        extra_payload=payload,
        trace_id=str(sorted(payload)),
    )
    action = action_from_trace(trace)
    guard = FieldCausalGuard(
        CounterfactualSourceAttributor(ToyTransferAgent(), seeds=(0, 1)),
        audit_only=False,
    )
    signal = guard.assess(action, context={"policy": policy, "trace": trace})
    assert signal.level is RiskLevel.RED
    assert signal.evidence


def test_benign_rag_memo_is_not_treated_as_authority(components) -> None:
    policy, _, _, _ = components
    trace = make_trace(
        extra_kind=SourceKind.RAG,
        extra_payload={"append_memo": "invoice reference"},
        trace_id="memo",
    )
    action = action_from_trace(trace)
    signal = FieldCausalGuard(
        CounterfactualSourceAttributor(ToyTransferAgent(), seeds=(0,)),
        audit_only=False,
    ).assess(action, context={"policy": policy, "trace": trace})
    assert signal.level is RiskLevel.GREEN


def test_trusted_service_is_within_delegation_mask(components) -> None:
    policy, _, _, _ = components
    trace = make_trace(
        extra_kind=SourceKind.TRUSTED_SERVICE,
        extra_trust=SourceTrust.TRUSTED,
        extra_payload={"override_to": "acct-payroll"},
        trace_id="trusted-service",
    )
    action = action_from_trace(trace)
    signal = FieldCausalGuard(
        CounterfactualSourceAttributor(ToyTransferAgent(), seeds=(0,)),
        audit_only=False,
    ).assess(action, context={"policy": policy, "trace": trace})
    assert signal.level is RiskLevel.GREEN
