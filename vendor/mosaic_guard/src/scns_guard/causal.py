from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from pydantic import Field

from .enums import ArgumentRole, RiskLevel
from .models import (
    ActionProposal,
    DetectorEvidence,
    DetectorSignal,
    SourceRef,
    StrictModel,
)
from .policy import PolicyEngine


class SourceMessage(StrictModel):
    source: SourceRef
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTrace(StrictModel):
    trace_id: str
    session_id: str
    actor_id: str
    messages: tuple[SourceMessage, ...]
    lineage_sources: tuple[SourceRef, ...] = ()
    # Hidden ancestor content is retained for controlled transformation replay,
    # but is never rendered as a visible model message.
    lineage_messages: tuple[SourceMessage, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    def source_ids(self) -> tuple[str, ...]:
        return tuple(message.source.source_id for message in self.messages)

    def source_catalog(self) -> dict[str, SourceRef]:
        catalog: dict[str, SourceRef] = {}
        for source in (
            *(message.source for message in self.messages),
            *(message.source for message in self.lineage_messages),
            *self.lineage_sources,
        ):
            previous = catalog.get(source.source_id)
            if previous is not None and previous != source:
                raise ValueError(f"conflicting lineage records for source {source.source_id!r}")
            catalog[source.source_id] = source
        return catalog

    def source_closure(self, source_ids: Iterable[str]) -> tuple[SourceRef, ...]:
        catalog = self.source_catalog()
        ordered: list[SourceRef] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(source_id: str) -> None:
            if source_id in visited:
                return
            if source_id in visiting:
                raise ValueError(f"cyclic source lineage at {source_id!r}")
            source = catalog.get(source_id)
            if source is None:
                return
            visiting.add(source_id)
            for parent_id in source.parent_source_ids:
                visit(parent_id)
            visiting.remove(source_id)
            visited.add(source_id)
            ordered.append(source)

        for source_id in source_ids:
            visit(source_id)
        return tuple(ordered)

    def source_depends_on(self, source_id: str, candidate_ancestors: frozenset[str]) -> bool:
        if source_id in candidate_ancestors:
            return True
        source = self.source_catalog().get(source_id)
        if source is None:
            return False
        return bool(set(source.ancestor_source_ids) & candidate_ancestors)


class AgentAdapter(Protocol):
    def propose(
        self,
        trace: AgentTrace,
        *,
        disabled_source_ids: frozenset[str],
        seed: int,
    ) -> ActionProposal | None: ...


class CallableAgentAdapter:
    def __init__(
        self,
        fn: Callable[[AgentTrace, frozenset[str], int], ActionProposal | None],
    ) -> None:
        self._fn = fn

    def propose(
        self,
        trace: AgentTrace,
        *,
        disabled_source_ids: frozenset[str],
        seed: int,
    ) -> ActionProposal | None:
        return self._fn(trace, disabled_source_ids, seed)


class InfluenceEstimate(StrictModel):
    source_id: str
    field: str
    mean_effect: float = Field(ge=0.0, le=1.0)
    changed_pairs: int = Field(ge=0)
    total_pairs: int = Field(gt=0)
    full_replay_consistency: float = Field(ge=0.0, le=1.0)
    intervention: str = "source_ablation"
    examples: tuple[dict[str, Any], ...] = ()


class CausalAttributionReport(StrictModel):
    trace_id: str
    action_type: str
    target_action_digest: str
    estimates: tuple[InfluenceEstimate, ...]
    seeds: tuple[int, ...]
    intervention_note: str = (
        "paired common-random-number source ablation; causal interpretation is "
        "conditional on intervention validity"
    )

    def by_source_field(self) -> dict[tuple[str, str], InfluenceEstimate]:
        return {(item.source_id, item.field): item for item in self.estimates}


def _value_effect(left: Any, right: Any) -> float:
    if left == right:
        return 0.0
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not (math.isfinite(float(left)) and math.isfinite(float(right))):
            return 1.0
        scale = max(abs(float(left)), abs(float(right)), 1.0)
        return min(1.0, abs(float(left) - float(right)) / scale)
    return 1.0


def _action_consistency(candidate: ActionProposal | None, target: ActionProposal) -> float:
    if candidate is None:
        return 0.0
    if candidate.action_type != target.action_type:
        return 0.0
    fields = set(candidate.params) | set(target.params)
    if not fields:
        return 1.0
    return 1.0 - sum(
        _value_effect(candidate.params.get(field), target.params.get(field)) for field in fields
    ) / len(fields)


class CounterfactualSourceAttributor:
    """Black-box source-to-field attribution through paired shadow replay.

    This is an auditable baseline and label generator. It is not assumed to be
    an online-efficient detector, and source ablation alone is not treated as a
    sufficient causal design for a paper claim.
    """

    def __init__(
        self,
        adapter: AgentAdapter,
        *,
        seeds: Sequence[int] = (0, 1, 2),
        min_effect: float = 1e-9,
        max_examples_per_estimate: int = 2,
    ) -> None:
        if not seeds:
            raise ValueError("at least one replay seed is required")
        self.adapter = adapter
        self.seeds = tuple(int(seed) for seed in seeds)
        self.min_effect = min_effect
        self.max_examples_per_estimate = max_examples_per_estimate

    def attribute(
        self,
        trace: AgentTrace,
        target_action: ActionProposal,
        *,
        source_ids: Iterable[str] | None = None,
    ) -> CausalAttributionReport:
        selected_sources = tuple(source_ids or trace.source_ids())
        accumulator: dict[tuple[str, str], list[float]] = defaultdict(list)
        changed: dict[tuple[str, str], int] = defaultdict(int)
        examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        consistency: dict[str, list[float]] = defaultdict(list)

        for source_id in selected_sources:
            for seed in self.seeds:
                full = self.adapter.propose(
                    trace, disabled_source_ids=frozenset(), seed=seed
                )
                counterfactual = self.adapter.propose(
                    trace,
                    disabled_source_ids=frozenset({source_id}),
                    seed=seed,
                )
                consistency[source_id].append(_action_consistency(full, target_action))
                action_type_effect = float(
                    full is None
                    or counterfactual is None
                    or full.action_type != counterfactual.action_type
                )
                key = (source_id, "__action_type__")
                accumulator[key].append(action_type_effect)
                changed[key] += int(action_type_effect > self.min_effect)
                if action_type_effect and len(examples[key]) < self.max_examples_per_estimate:
                    examples[key].append(
                        {
                            "seed": seed,
                            "full": None if full is None else full.action_type,
                            "counterfactual": (
                                None if counterfactual is None else counterfactual.action_type
                            ),
                        }
                    )

                full_params = {} if full is None else full.params
                cf_params = {} if counterfactual is None else counterfactual.params
                for field in sorted(set(full_params) | set(cf_params) | set(target_action.params)):
                    effect = _value_effect(full_params.get(field), cf_params.get(field))
                    key = (source_id, field)
                    accumulator[key].append(effect)
                    changed[key] += int(effect > self.min_effect)
                    if effect > self.min_effect and len(examples[key]) < self.max_examples_per_estimate:
                        examples[key].append(
                            {
                                "seed": seed,
                                "full": full_params.get(field),
                                "counterfactual": cf_params.get(field),
                            }
                        )

        estimates: list[InfluenceEstimate] = []
        for (source_id, field), values in sorted(accumulator.items()):
            estimates.append(
                InfluenceEstimate(
                    source_id=source_id,
                    field=field,
                    mean_effect=sum(values) / len(values),
                    changed_pairs=changed[(source_id, field)],
                    total_pairs=len(values),
                    full_replay_consistency=(
                        sum(consistency[source_id]) / len(consistency[source_id])
                    ),
                    examples=tuple(examples[(source_id, field)]),
                )
            )
        return CausalAttributionReport(
            trace_id=trace.trace_id,
            action_type=target_action.action_type,
            target_action_digest=target_action.digest,
            estimates=tuple(estimates),
            seeds=self.seeds,
        )


_HIGH_AUTHORITY_ROLES = {
    ArgumentRole.TARGET,
    ArgumentRole.AMOUNT,
    ArgumentRole.COMMAND,
    ArgumentRole.CREDENTIAL,
}


class FieldCausalGuard:
    """Maps unauthorized field-level causal support to risk obligations.

    The mapping is deliberately one-way: it can emit GREEN/YELLOW/RED/DENY,
    but the controller only joins the result with the hard-policy risk.
    """

    detector_id = "field-causal-guard"

    def __init__(
        self,
        attributor: CounterfactualSourceAttributor,
        *,
        detector_version: str = "0.1.0",
        effect_threshold: float = 0.5,
        min_full_replay_consistency: float = 0.8,
        audit_only: bool = True,
    ) -> None:
        self.attributor = attributor
        self.detector_version = detector_version
        self.effect_threshold = effect_threshold
        self.min_full_replay_consistency = min_full_replay_consistency
        self.audit_only = audit_only

    def assess(
        self,
        action: ActionProposal,
        *,
        context: dict[str, Any],
    ) -> DetectorSignal:
        policy = context.get("policy")
        trace = context.get("trace")
        if not isinstance(policy, PolicyEngine):
            raise TypeError("detector context requires a PolicyEngine under 'policy'")
        if not isinstance(trace, AgentTrace):
            raise TypeError("detector context requires an AgentTrace under 'trace'")
        report = context.get("causal_report")
        if not isinstance(report, CausalAttributionReport):
            report = self.attributor.attribute(trace, action)

        level = RiskLevel.GREEN
        max_score = 0.0
        evidence: list[DetectorEvidence] = []
        reasons: list[str] = []
        contract = policy.contract_for(action.action_type)

        for estimate in report.estimates:
            if estimate.mean_effect < self.effect_threshold:
                continue
            if estimate.full_replay_consistency < self.min_full_replay_consistency:
                reasons.append(
                    f"unstable replay for source={estimate.source_id}; treated conservatively"
                )
                level = max(level, RiskLevel.YELLOW)
                max_score = max(max_score, 1.0 - estimate.full_replay_consistency)
                continue

            if estimate.field == "__action_type__":
                delegated = policy.source_is_delegated_for_control(
                    action.action_type, estimate.source_id, action
                )
                role = ArgumentRole.COMMAND
            else:
                delegated = policy.source_is_delegated_for_field(
                    action.action_type,
                    estimate.field,
                    estimate.source_id,
                    action,
                )
                field_contract = None if contract is None else contract.fields.get(estimate.field)
                role = ArgumentRole.OTHER if field_contract is None else field_contract.role
                if field_contract is not None and not field_contract.causal_sensitive:
                    continue

            if delegated:
                continue

            if estimate.field == "__action_type__":
                candidate_level = RiskLevel.DENY
            elif role in _HIGH_AUTHORITY_ROLES:
                candidate_level = RiskLevel.RED
            else:
                candidate_level = RiskLevel.YELLOW
            level = max(level, candidate_level)
            max_score = max(max_score, estimate.mean_effect)
            reasons.append(
                f"non-delegated source {estimate.source_id!r} causally changes "
                f"{estimate.field!r}"
            )
            evidence.append(
                DetectorEvidence(
                    source_id=estimate.source_id,
                    field=estimate.field,
                    score=estimate.mean_effect,
                    intervention=estimate.intervention,
                    details={
                        "changed_pairs": estimate.changed_pairs,
                        "total_pairs": estimate.total_pairs,
                        "full_replay_consistency": estimate.full_replay_consistency,
                        "examples": list(estimate.examples),
                    },
                )
            )

        reason = "; ".join(reasons) if reasons else "no unauthorized causal support detected"
        return DetectorSignal(
            detector_id=self.detector_id,
            detector_version=self.detector_version,
            level=level,
            score=max_score,
            reason=reason,
            evidence=tuple(evidence),
            audit_only=self.audit_only,
        )
