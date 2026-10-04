from __future__ import annotations

import argparse
import json
from pathlib import Path

from scns_guard.dataset import load_predicate_rows
from scns_guard.training import save_training_result, train_linear_predicate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train a grouped linear source-field predicate from causal-effect rows"
    )
    parser.add_argument("rows", type=Path)
    parser.add_argument("--model-output", type=Path, default=Path("artifacts/predicate_model.json"))
    parser.add_argument("--metrics-output", type=Path, default=Path("artifacts/predicate_metrics.json"))
    parser.add_argument("--max-false-escalation", type=float, default=0.05)
    parser.add_argument("--c", type=float, default=1.0)
    args = parser.parse_args()

    result = train_linear_predicate(
        load_predicate_rows(args.rows),
        c=args.c,
        max_false_escalation_rate=args.max_false_escalation,
    )
    save_training_result(
        result,
        model_path=args.model_output,
        metrics_path=args.metrics_output,
    )
    print(json.dumps(result.metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
