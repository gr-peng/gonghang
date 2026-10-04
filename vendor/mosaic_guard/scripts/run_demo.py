from pathlib import Path

from scns_guard.demo import print_demo

ROOT = Path(__file__).resolve().parents[1]
print_demo(
    policy_path=ROOT / "configs" / "transfer_policy.yaml",
    receipt_path=ROOT / "artifacts" / "demo_receipts.jsonl",
)
