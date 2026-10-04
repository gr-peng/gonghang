from __future__ import annotations

import math
from typing import Any, Iterable

from pydantic import Field, model_validator

from .causal import InfluenceEstimate
from .enums import ArgumentRole, RiskLevel
from .models import DetectorEvidence, DetectorSignal, StrictModel
from .policy import PolicyEngine
from .models import ActionProposal


class LinearPredicateSpec(StrictModel):
    model_id: str
    version: str
    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    bias: float = 0.0
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    training_label: str = "paired_counterfactual_field_effect"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dimensions_match(self) -> "LinearPredicateSpec":
        if len(self.feature_names) != len(self.weights):
            raise ValueError("feature_names and weights must have equal length")
        return self

    def predict_proba(self, features: dict[str, float]) -> float:
        logit = self.bias + sum(
            weight * float(features.get(name, 0.0))
            for name, weight in zip(self.feature_names, self.weights, strict=True)
        )
        if logit >= 0:
            return 1.0 / (1.0 + math.exp(-logit))
        exp_logit = math.exp(logit)
        return exp_logit / (1.0 + exp_logit)


class PredicateInput(StrictModel):
    source_id: str
    field: str
    features: dict[str, float]


class NeuralSourcePredicateGuard:
    """Adapter for an offline-trained hidden-state/source predicate.

    The model predicts a counterfactual influence label. It does not infer
    authority and cannot discharge any hard-policy obligation.
    """

    detector_id = "neural-source-predicate"

    def __init__(
        self,
        spec: LinearPredicateSpec,
        *,
        audit_only: bool = True,
    ) -> None:
        self.spec = spec
        self.audit_only = audit_only

    def assess(
        self,
        action: ActionProposal,
        *,
        context: dict[str, Any],
    ) -> DetectorSignal:
        policy = context.get("policy")
        records = context.get("predicate_inputs", ())
        if not isinstance(policy, PolicyEngine):
            raise TypeError("detector context requires PolicyEngine under 'policy'")
        parsed = tuple(
            record if isinstance(record, PredicateInput) else PredicateInput.model_validate(record)
            for record in records
        )
        max_score = 0.0
        level = RiskLevel.GREEN
        evidence: list[DetectorEvidence] = []
        contract = policy.contract_for(action.action_type)

        for record in parsed:
            score = self.spec.predict_proba(record.features)
            if score < self.spec.threshold:
                continue
            if record.field == "__action_type__":
                delegated = policy.source_is_delegated_for_control(
                    action.action_type, record.source_id, action
                )
                candidate = RiskLevel.DENY
            else:
                delegated = policy.source_is_delegated_for_field(
                    action.action_type, record.field, record.source_id, action
                )
                field_contract = None if contract is None else contract.fields.get(record.field)
                role = ArgumentRole.OTHER if field_contract is None else field_contract.role
                candidate = (
                    RiskLevel.RED
                    if role
                    in {
                        ArgumentRole.TARGET,
                        ArgumentRole.AMOUNT,
                        ArgumentRole.COMMAND,
                        ArgumentRole.CREDENTIAL,
                    }
                    else RiskLevel.YELLOW
                )
            if delegated:
                continue
            level = max(level, candidate)
            max_score = max(max_score, score)
            evidence.append(
                DetectorEvidence(
                    source_id=record.source_id,
                    field=record.field,
                    score=score,
                    intervention="learned surrogate of paired counterfactual effect",
                    details={"model_id": self.spec.model_id},
                )
            )
        return DetectorSignal(
            detector_id=self.detector_id,
            detector_version=self.spec.version,
            level=level,
            score=max_score,
            reason=(
                "learned predicate found non-delegated causal support"
                if evidence
                else "no learned non-delegated causal support"
            ),
            evidence=tuple(evidence),
            audit_only=self.audit_only,
        )


def estimates_to_training_rows(
    estimates: Iterable[InfluenceEstimate],
    feature_rows: dict[tuple[str, str], dict[str, float]],
    *,
    label_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Join causal replay labels with precomputed model features."""

    rows: list[dict[str, Any]] = []
    for estimate in estimates:
        key = (estimate.source_id, estimate.field)
        if key not in feature_rows:
            continue
        rows.append(
            {
                "source_id": estimate.source_id,
                "field": estimate.field,
                "features": feature_rows[key],
                "label": int(estimate.mean_effect >= label_threshold),
                "effect": estimate.mean_effect,
                "replay_consistency": estimate.full_replay_consistency,
            }
        )
    return rows
