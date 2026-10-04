from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import sha256_hex
from .enums import (
    ArgumentRole,
    DecisionStatus,
    ExecutionState,
    FactTrust,
    ObligationType,
    RiskLevel,
    SourceKind,
    SourceTrust,
)
from .money import MAX_MINOR_UNITS, is_minor_unit_name, validate_minor_units


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class FieldDerivationEdge(StrictModel):
    output_field: str
    parent_source_ids: tuple[str, ...]
    operation: str

    @model_validator(mode="after")
    def validate_edge(self) -> "FieldDerivationEdge":
        if not self.output_field.strip():
            raise ValueError("field derivation output_field must be non-empty")
        if not self.operation.strip():
            raise ValueError("field derivation operation must be non-empty")
        if not self.parent_source_ids:
            raise ValueError("field derivation requires at least one parent source")
        if len(self.parent_source_ids) != len(set(self.parent_source_ids)):
            raise ValueError("field derivation parent_source_ids must be unique")
        return self


class SourceRef(StrictModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, frozen=True)

    source_id: str
    kind: SourceKind
    trust: SourceTrust
    content_hash: str | None = None
    parent_source_ids: tuple[str, ...] = ()
    ancestor_source_ids: tuple[str, ...] = ()
    transformation: str | None = None
    producer_id: str | None = None
    created_at: datetime | None = None
    field_derivations: tuple[FieldDerivationEdge, ...] = ()
    signature: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lineage_shape(self) -> "SourceRef":
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")
        if len(self.parent_source_ids) != len(set(self.parent_source_ids)):
            raise ValueError("parent_source_ids must be unique")
        if len(self.ancestor_source_ids) != len(set(self.ancestor_source_ids)):
            raise ValueError("ancestor_source_ids must be unique")
        if self.source_id in self.parent_source_ids or self.source_id in self.ancestor_source_ids:
            raise ValueError("a source cannot be its own parent or ancestor")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("source created_at must be timezone-aware")
        return self

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"signature"}, exclude_none=True)


class ArgumentBinding(StrictModel):
    field: str
    source_ids: tuple[str, ...] = ()
    role: ArgumentRole = ArgumentRole.OTHER
    observed_provenance_complete: bool = False


class TransferParams(StrictModel):
    from_account: str
    to_account: str
    amount_minor: int = Field(strict=True, gt=0, le=MAX_MINOR_UNITS)
    memo: str | None = None


class ActionProposal(StrictModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    actor_id: str
    action_type: str
    params: dict[str, Any]
    sources: tuple[SourceRef, ...] = ()
    argument_bindings: tuple[ArgumentBinding, ...] = ()
    proposed_at: datetime = Field(default_factory=utcnow)
    write_action: bool = True

    @field_validator("params")
    @classmethod
    def nonempty_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("params must be an object")
        for field, item in value.items():
            if is_minor_unit_name(field):
                validate_minor_units(
                    item,
                    field_name=f"action parameter {field!r}",
                    allow_zero=field != "amount_minor",
                )
        return value

    @model_validator(mode="after")
    def validate_security_envelope(self) -> "ActionProposal":
        if self.action_type == "transfer":
            TransferParams.model_validate(self.params)
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id values must be unique within an action")
        binding_fields = [binding.field for binding in self.argument_bindings]
        if len(binding_fields) != len(set(binding_fields)):
            raise ValueError("each action field may have at most one provenance binding")
        known_sources = set(source_ids)
        for source in self.sources:
            missing_parents = set(source.parent_source_ids) - known_sources
            missing_ancestors = set(source.ancestor_source_ids) - known_sources
            if missing_parents or missing_ancestors:
                raise ValueError(
                    f"source {source.source_id!r} is missing lineage closure: "
                    f"{sorted(missing_parents | missing_ancestors)!r}"
                )
            lineage_ids = set(source.parent_source_ids) | set(source.ancestor_source_ids)
            for edge in source.field_derivations:
                unknown_edge_sources = set(edge.parent_source_ids) - lineage_ids
                if unknown_edge_sources:
                    raise ValueError(
                        f"field derivation for source {source.source_id!r} refers to "
                        f"unknown lineage sources: {sorted(unknown_edge_sources)!r}"
                    )
        for binding in self.argument_bindings:
            if binding.field not in self.params:
                raise ValueError(
                    f"provenance binding refers to absent parameter: {binding.field}"
                )
            unknown = set(binding.source_ids) - known_sources
            if unknown:
                raise ValueError(
                    f"provenance binding {binding.field!r} refers to unknown sources: "
                    f"{sorted(unknown)!r}"
                )
        return self

    @property
    def digest(self) -> str:
        payload = {
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "params": self.params,
            "write_action": self.write_action,
            # Provenance and source trust affect authorization. Binding tokens to
            # this security envelope prevents a caller from reusing a token after
            # replacing or laundering the recorded source context.
            "sources": self.sources,
            "argument_bindings": self.argument_bindings,
        }
        return sha256_hex(payload)

    def binding_for(self, field: str) -> ArgumentBinding | None:
        return next((binding for binding in self.argument_bindings if binding.field == field), None)

    def source_by_id(self, source_id: str) -> SourceRef | None:
        return next((source for source in self.sources if source.source_id == source_id), None)


class TrustedFact(StrictModel):
    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    predicate: str
    subject: str
    value: Any
    issuer: str
    trust: FactTrust = FactTrust.TRUSTED
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    signature: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def expiry_follows_issue(self) -> "TrustedFact":
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("fact expires_at must be later than issued_at")
        if is_minor_unit_name(self.predicate):
            validate_minor_units(
                self.value,
                field_name=f"trusted fact {self.predicate!r}",
                allow_zero=True,
            )
        return self

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"signature"}, exclude_none=True)


class ObligationToken(StrictModel):
    token_id: str = Field(default_factory=lambda: str(uuid4()))
    obligation: ObligationType
    action_digest: str
    session_id: str
    actor_id: str
    issuer: str
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    signature: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def expiry_follows_issue(self) -> "ObligationToken":
        if self.expires_at <= self.issued_at:
            raise ValueError("token expires_at must be later than issued_at")
        return self

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"signature"}, exclude_none=True)


class DetectorEvidence(StrictModel):
    source_id: str | None = None
    field: str | None = None
    score: float | None = None
    intervention: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DetectorSignal(StrictModel):
    detector_id: str
    detector_version: str
    level: RiskLevel
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: tuple[DetectorEvidence, ...] = ()
    audit_only: bool = False
    produced_at: datetime = Field(default_factory=utcnow)


class RuleTrace(StrictModel):
    rule_id: str
    matched: bool
    effect: RiskLevel | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluation(StrictModel):
    policy_id: str
    policy_version: str
    policy_hash: str
    base_level: RiskLevel
    matched_rule_ids: tuple[str, ...]
    trace: tuple[RuleTrace, ...]
    argument_contract_violations: tuple[str, ...] = ()


class AuthorizationDecision(StrictModel):
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    action_digest: str
    base_level: RiskLevel
    final_level: RiskLevel
    status: DecisionStatus
    required_obligations: frozenset[ObligationType]
    discharged_obligations: frozenset[ObligationType]
    missing_obligations: frozenset[ObligationType]
    policy_evaluation: PolicyEvaluation
    detector_signals: tuple[DetectorSignal, ...] = ()
    fact_ids: tuple[str, ...] = ()
    token_ids: tuple[str, ...] = ()
    evaluated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def check_monotonicity(self) -> "AuthorizationDecision":
        if self.final_level < self.base_level:
            raise ValueError("final_level may not be less restrictive than base_level")
        if self.required_obligations - self.discharged_obligations != self.missing_obligations:
            raise ValueError("missing_obligations is inconsistent")
        return self


class ExecutionRecord(StrictModel):
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    action_digest: str
    state: ExecutionState
    decision_id: str
    tool_name: str
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    tool_result: dict[str, Any]
    executed_at: datetime = Field(default_factory=utcnow)


class FormalReceiptLayer(StrictModel):
    action: ActionProposal
    trusted_facts: tuple[TrustedFact, ...]
    verified_tokens: tuple[ObligationToken, ...]
    policy_snapshot: dict[str, Any]
    controller_config: dict[str, Any] = Field(default_factory=dict)
    policy_evaluation: PolicyEvaluation
    decision: AuthorizationDecision
    execution: ExecutionRecord | None = None

    @model_validator(mode="after")
    def check_internal_consistency(self) -> "FormalReceiptLayer":
        if self.decision.action_digest != self.action.digest:
            raise ValueError("receipt decision is not bound to the recorded action")
        if self.decision.policy_evaluation != self.policy_evaluation:
            raise ValueError("receipt policy evaluations disagree")
        if self.decision.base_level != self.policy_evaluation.base_level:
            raise ValueError("receipt decision base level disagrees with policy evaluation")
        if self.decision.fact_ids != tuple(fact.fact_id for fact in self.trusted_facts):
            raise ValueError("receipt fact IDs disagree with recorded trusted facts")
        if self.decision.token_ids != tuple(token.token_id for token in self.verified_tokens):
            raise ValueError("receipt token IDs disagree with recorded verified tokens")
        if self.execution is not None:
            if self.decision.status is not DecisionStatus.ALLOW:
                raise ValueError("receipt contains execution for a non-allow decision")
            if self.execution.action_digest != self.action.digest:
                raise ValueError("receipt execution is not bound to the recorded action")
            if self.execution.decision_id != self.decision.decision_id:
                raise ValueError("receipt execution is not bound to the recorded decision")
            if self.execution.state not in {ExecutionState.EXECUTED, ExecutionState.FAILED}:
                raise ValueError("receipt execution has an invalid terminal state")
        return self


class InternalEvidenceLayer(StrictModel):
    signals: tuple[DetectorSignal, ...]
    note: Literal[
        "internal evidence is risk evidence, not authorization proof"
    ] = "internal evidence is risk evidence, not authorization proof"


class DecisionReceipt(StrictModel):
    receipt_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = Field(ge=1)
    previous_receipt_hash: str | None = None
    formal: FormalReceiptLayer
    internal: InternalEvidenceLayer
    created_at: datetime = Field(default_factory=utcnow)
    receipt_hash: str | None = None

    @model_validator(mode="after")
    def check_evidence_layers(self) -> "DecisionReceipt":
        if self.internal.signals != self.formal.decision.detector_signals:
            raise ValueError("formal decision and internal evidence signals disagree")
        return self

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"receipt_hash"}, exclude_none=True)
