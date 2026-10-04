from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Any, Literal

from pydantic import Field, model_validator

from .canonical import canonical_json
from .models import StrictModel
from .causal import AgentTrace


class ReplayOutcome(StrictModel):
    status: Literal["action", "no_action", "error"]
    action_type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    write_action: bool | None = None
    error: str | None = None

    @model_validator(mode="after")
    def coherent_status(self):
        if self.status == "action" and (not self.action_type or self.write_action is None):
            raise ValueError("action outcome requires action_type and write_action")
        if self.status != "action" and (self.action_type is not None or self.params):
            raise ValueError("non-action outcome cannot carry action parameters")
        return self


def protected_effect_preserved(outcome: ReplayOutcome, expected: dict[str, Any] | None) -> bool | None:
    if expected is None:
        return None
    return (outcome.status == "action" and outcome.action_type == expected["action_type"]
            and outcome.write_action == expected.get("write_action", True)
            and all(key in outcome.params and canonical_json(outcome.params[key]) == canonical_json(value)
                    for key, value in expected.items() if key not in ("action_type", "write_action")))


def compare_outcomes(full: ReplayOutcome, altered: ReplayOutcome, *, fields: tuple[str, ...],
                     expected: dict[str, Any] | None) -> dict[str, Any]:
    both_actions = full.status == altered.status == "action"
    comparable = both_actions and full.action_type == altered.action_type
    effects = {field: (int(canonical_json(full.params[field]) != canonical_json(altered.params[field]))
                       if comparable and field in full.params and field in altered.params else None)
               for field in fields}
    return {
        "comparable_actions": comparable,
        "field_effects": effects,
        "action_presence_changed": ((full.status == "action") != (altered.status == "action")
                                    if "error" not in (full.status, altered.status) else None),
        "action_type_changed": full.action_type != altered.action_type if both_actions else None,
        "full_status": full.status, "altered_status": altered.status,
        "full_authorized_effect": protected_effect_preserved(full, expected),
        "altered_authorized_effect": protected_effect_preserved(altered, expected),
    }


def summarize_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, dict[str, list[int | None]]] = defaultdict(lambda: defaultdict(list))
    field_means: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for field, value in row["field_effects"].items():
            cells[row["intervention_id"]][field].append(value)
    output = {}
    for iid, fields in cells.items():
        output[iid] = {}
        for field, all_values in fields.items():
            values = [value for value in all_values if value is not None]
            avg = mean(values) if values else None
            output[iid][field] = {
                "comparable_pairs": len(values), "unscorable_pairs": len(all_values) - len(values),
                "mean_effect": avg, "sample_std": stdev(values) if len(values) > 1 else None,
            }
            if avg is not None:
                field_means[field].append(avg)
    return {
        "by_intervention": output,
        "heterogeneous_fields": sorted(f for f, values in field_means.items() if len(set(values)) > 1),
        "pooled_causal_label": None,
        "interpretation": "interventions target different perturbations; heterogeneous effects are retained, not pooled into an authority or causal label",
        "uncertainty": "sample variation only; repeated deterministic calls are not independent task evidence",
    }


@dataclass(frozen=True)
class PreparedVariant:
    intervention_id: str
    trace: AgentTrace
    expected: dict[str, Any] | None
    authorization_equivalent: bool
    validity_assumptions: tuple[str, ...]


def run_paired_replays(
    trace: AgentTrace, variants: Sequence[PreparedVariant],
    run: Callable[[AgentTrace, int], ReplayOutcome], *, seeds: Sequence[int],
    fields: tuple[str, ...], original_expected: dict[str, Any],
    on_pair: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("replay seeds must be nonempty and unique")
    if len({v.intervention_id for v in variants}) != len(variants):
        raise ValueError("intervention IDs must be unique")
    rows = []
    for seed in seeds:
        full = run(trace, seed)
        for variant in variants:
            altered = run(variant.trace, seed)
            pair = compare_outcomes(full, altered, fields=fields, expected=variant.expected)
            pair["full_authorized_effect"] = protected_effect_preserved(full, original_expected)
            row = {"intervention_id": variant.intervention_id, "seed": seed,
                   "authorization_equivalent": variant.authorization_equivalent,
                   "validity_assumptions": list(variant.validity_assumptions),
                   "full": full.model_dump(mode="json"), "altered": altered.model_dump(mode="json"),
                   **pair}
            rows.append(row)
            if on_pair is not None:
                on_pair(row)
    return rows
