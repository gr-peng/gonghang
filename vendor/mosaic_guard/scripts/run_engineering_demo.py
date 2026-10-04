import argparse
import json

from scns_guard.engineering_demo import run_engineering_demo


parser = argparse.ArgumentParser()
parser.add_argument('--policy', default='configs/transfer_policy.yaml')
parser.add_argument('--receipts', default='artifacts/engineering_demo_receipts.jsonl')
args = parser.parse_args()
print(json.dumps(run_engineering_demo(policy_path=args.policy, receipt_path=args.receipts), ensure_ascii=False, indent=2))
