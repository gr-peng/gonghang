import json
from pathlib import Path

from scns_guard.experiment import run_synthetic_experiment

ROOT = Path(__file__).resolve().parents[1]
result = run_synthetic_experiment(
    policy_path=ROOT / "configs" / "transfer_policy.yaml",
    repeats_per_kind=20,
    seed=7,
    output_path=ROOT / "artifacts" / "synthetic_results.json",
)
print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
