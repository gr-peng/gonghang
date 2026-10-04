from __future__ import annotations

import argparse
import json
from pathlib import Path

from scns_guard.planning_demo import run_planning_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a scripted clarification and mock-bank demo")
    parser.add_argument("--policy", type=Path, default=Path("configs/transfer_policy.yaml"))
    args = parser.parse_args()
    print(json.dumps(run_planning_demo(policy_path=args.policy), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
