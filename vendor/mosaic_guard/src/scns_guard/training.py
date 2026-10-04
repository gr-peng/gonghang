from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .dataset import PredicateTrainingRow, validate_group_isolation
from .predicates import LinearPredicateSpec


@dataclass(frozen=True)
class TrainingResult:
    model: LinearPredicateSpec
    metrics: dict[str, Any]


def _rates(labels, predictions) -> dict[str, float | None]:
    labels = list(int(value) for value in labels)
    predictions = list(int(value) for value in predictions)
    positives = sum(labels)
    negatives = len(labels) - positives
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions, strict=True))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions, strict=True))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predictions, strict=True))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions, strict=True))
    return {
        "tpr": None if positives == 0 else tp / positives,
        "fpr": None if negatives == 0 else fp / negatives,
        "precision": None if tp + fp == 0 else tp / (tp + fp),
        "accuracy": None if not labels else (tp + tn) / len(labels),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def _select_threshold(
    labels,
    probabilities,
    *,
    max_false_escalation_rate: float,
) -> tuple[float, dict[str, Any]]:
    candidates = sorted({0.0, 1.0, *[float(value) for value in probabilities]}, reverse=True)
    feasible: list[tuple[float, float, dict[str, Any]]] = []
    for threshold in candidates:
        predictions = [float(probability) >= threshold for probability in probabilities]
        rates = _rates(labels, predictions)
        fpr = rates["fpr"]
        tpr = rates["tpr"]
        if fpr is not None and fpr <= max_false_escalation_rate:
            feasible.append((float(tpr or 0.0), threshold, rates))
    if not feasible:
        threshold = 1.0
        rates = _rates(labels, [False for _ in probabilities])
        return threshold, {
            **rates,
            "constraint_feasible": False,
            "max_false_escalation_rate": max_false_escalation_rate,
        }
    # Maximize TPR; on a tie choose the larger, more conservative threshold.
    best_tpr, threshold, rates = max(feasible, key=lambda item: (item[0], item[1]))
    return threshold, {
        **rates,
        "selected_tpr": best_tpr,
        "constraint_feasible": True,
        "max_false_escalation_rate": max_false_escalation_rate,
    }


def train_linear_predicate(
    rows: Iterable[PredicateTrainingRow],
    *,
    model_id: str = "source-field-linear-probe",
    version: str = "0.1.0",
    c: float = 1.0,
    max_false_escalation_rate: float = 0.05,
    random_state: int = 7,
) -> TrainingResult:
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import average_precision_score, roc_auc_score
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("install the 'ml' extra to train a predicate") from exc

    materialized = tuple(rows)
    validate_group_isolation(list(materialized))
    feature_names = tuple(sorted({name for row in materialized for name in row.features}))
    if not feature_names:
        raise ValueError("no features found")

    def split(name: str):
        selected = [row for row in materialized if row.split == name]
        if not selected:
            raise ValueError(f"missing {name!r} rows")
        x = np.asarray(
            [[float(row.features.get(feature, 0.0)) for feature in feature_names] for row in selected],
            dtype=float,
        )
        y = np.asarray([row.label for row in selected], dtype=int)
        return selected, x, y

    train_rows, x_train, y_train = split("train")
    calibration_rows, x_cal, y_cal = split("calibration")
    test_rows, x_test, y_test = split("test")
    if len(set(y_train.tolist())) < 2:
        raise ValueError("training split must contain both labels")
    if len(set(y_cal.tolist())) < 2:
        raise ValueError("calibration split must contain both labels")

    estimator = LogisticRegression(
        C=c,
        solver="liblinear",
        random_state=random_state,
        max_iter=2000,
    )
    estimator.fit(x_train, y_train)
    cal_prob = estimator.predict_proba(x_cal)[:, 1]
    threshold, calibration_metrics = _select_threshold(
        y_cal,
        cal_prob,
        max_false_escalation_rate=max_false_escalation_rate,
    )
    test_prob = estimator.predict_proba(x_test)[:, 1]
    test_pred = test_prob >= threshold
    test_metrics = _rates(y_test, test_pred)
    if len(set(y_test.tolist())) == 2:
        test_metrics["auroc"] = float(roc_auc_score(y_test, test_prob))
        test_metrics["average_precision"] = float(
            average_precision_score(y_test, test_prob)
        )
    else:
        test_metrics["auroc"] = None
        test_metrics["average_precision"] = None

    model = LinearPredicateSpec(
        model_id=model_id,
        version=version,
        feature_names=feature_names,
        weights=tuple(float(value) for value in estimator.coef_[0]),
        bias=float(estimator.intercept_[0]),
        threshold=float(threshold),
        metadata={
            "training_rows": len(train_rows),
            "calibration_rows": len(calibration_rows),
            "test_rows": len(test_rows),
            "train_groups": len({row.group_id for row in train_rows}),
            "calibration_groups": len({row.group_id for row in calibration_rows}),
            "test_groups": len({row.group_id for row in test_rows}),
            "regularization_c": c,
            "random_state": random_state,
            "activation_policy": "audit_only_until_external_kill_gates_pass",
        },
    )
    metrics = {
        "evidence_status": "derived_from_supplied_rows_only",
        "label_kind": "paired_counterfactual_field_effect",
        "feature_names": feature_names,
        "calibration": calibration_metrics,
        "test": test_metrics,
        "group_isolation_checked": True,
    }
    return TrainingResult(model=model, metrics=metrics)


def save_training_result(
    result: TrainingResult,
    *,
    model_path: str | Path,
    metrics_path: str | Path,
) -> None:
    model_target = Path(model_path)
    metrics_target = Path(metrics_path)
    model_target.parent.mkdir(parents=True, exist_ok=True)
    metrics_target.parent.mkdir(parents=True, exist_ok=True)
    model_target.write_text(
        json.dumps(result.model.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    metrics_target.write_text(
        json.dumps(result.metrics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
