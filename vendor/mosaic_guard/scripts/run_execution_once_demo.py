import json
from pathlib import Path

from scns_guard.execution_demo import run_execution_demo

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    print(json.dumps(run_execution_demo(policy_path=ROOT / 'configs/transfer_policy.yaml'),
                     indent=2, ensure_ascii=False))
