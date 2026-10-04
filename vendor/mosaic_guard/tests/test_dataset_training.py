from __future__ import annotations

import pytest

from scns_guard.dataset import PredicateTrainingRow, validate_group_isolation
from scns_guard.training import train_linear_predicate


def _rows():
    rows = []
    counter = 0
    for split, offset in [("train", 0.0), ("calibration", 0.1), ("test", -0.1)]:
        for label in [0, 1]:
            for group_index in range(3):
                counter += 1
                rows.append(
                    PredicateTrainingRow(
                        row_id=f"r{counter}",
                        group_id=f"{split}-g{label}-{group_index}",
                        split=split,
                        source_id=f"s{counter}",
                        field="to_account",
                        features={
                            "source_field_alignment": float(label) * 2.0 + offset,
                            "position_shortcut": float(group_index % 2),
                        },
                        label=label,
                        effect=float(label),
                        replay_consistency=1.0,
                        intervention_ids=("source_ablation", "source_relocation"),
                    )
                )
    return rows


def test_group_leakage_is_rejected() -> None:
    rows = _rows()
    rows[1] = rows[1].model_copy(update={"group_id": rows[0].group_id, "split": "test"})
    with pytest.raises(ValueError, match="group leakage"):
        validate_group_isolation(rows)


def test_linear_training_pipeline_uses_grouped_splits() -> None:
    result = train_linear_predicate(
        _rows(),
        max_false_escalation_rate=0.34,
        random_state=3,
    )
    assert result.model.feature_names
    assert 0.0 <= result.model.threshold <= 1.0
    assert result.metrics["group_isolation_checked"] is True
    assert result.metrics["label_kind"] == "paired_counterfactual_field_effect"
