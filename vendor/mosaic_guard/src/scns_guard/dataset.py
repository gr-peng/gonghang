from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from .causal import AgentTrace
from .enums import RiskLevel
from .llm_adapter import ModelActionOutput
from .models import StrictModel


SplitName = Literal["train", "calibration", "test"]


class DelegationGrant(StrictModel):
    source_id: str
    action_type: str
    field: str
    authorized: bool
    rationale: str


class ResearchScenario(StrictModel):
    scenario_id: str
    group_id: str
    split: SplitName
    trace: AgentTrace
    authorized_action: ModelActionOutput
    protected_effect: dict[str, Any]
    delegation: tuple[DelegationGrant, ...]
    attack: bool
    source_laundered: bool = False
    transformation_ids: tuple[str, ...] = ()
    expected_min_level: RiskLevel = RiskLevel.GREEN
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_min_level", mode="before")
    @classmethod
    def parse_level(cls, value: Any) -> RiskLevel:
        return RiskLevel.parse(value)


class PredicateTrainingRow(StrictModel):
    row_id: str
    group_id: str
    split: SplitName
    source_id: str
    field: str
    features: dict[str, float]
    label: int = Field(ge=0, le=1)
    effect: float = Field(ge=0.0, le=1.0)
    replay_consistency: float = Field(ge=0.0, le=1.0)
    intervention_ids: tuple[str, ...]
    label_kind: Literal["paired_counterfactual_field_effect"] = (
        "paired_counterfactual_field_effect"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_group_isolation(rows: tuple[PredicateTrainingRow, ...] | list[PredicateTrainingRow]) -> None:
    seen: dict[str, str] = {}
    for row in rows:
        previous = seen.setdefault(row.group_id, row.split)
        if previous != row.split:
            raise ValueError(
                f"group leakage: {row.group_id!r} appears in both {previous!r} and {row.split!r}"
            )


def load_predicate_rows(path: str | Path) -> tuple[PredicateTrainingRow, ...]:
    rows: list[PredicateTrainingRow] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(PredicateTrainingRow.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid predicate row at line {line_number}: {exc}") from exc
    validate_group_isolation(rows)
    return tuple(rows)


def write_jsonl(path: str | Path, rows: tuple[StrictModel, ...] | list[StrictModel]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.model_dump(mode="json"), ensure_ascii=False))
            handle.write("\n")


def authorization_equivalent(left: ResearchScenario, right: ResearchScenario) -> bool:
    """Check the minimum programmatic equality used for matched benign pairs."""

    return (
        left.authorized_action == right.authorized_action
        and left.protected_effect == right.protected_effect
        and left.delegation == right.delegation
    )
