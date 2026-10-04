from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo import run_demo
from .experiment import run_synthetic_experiment
from .receipts import ReceiptLedger
from .replay import ReceiptReplayer
from .schemas import export_schemas
from .tokens import TokenAuthority
from .trust import FactAuthority


def _default_policy() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "transfer_policy.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mosaic-guard",
        description="Monotone neuro-symbolic safety control plane prototype",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run one complete transfer flow")
    demo.add_argument("--policy", type=Path, default=_default_policy())
    demo.add_argument("--receipts", type=Path, default=Path("artifacts/demo_receipts.jsonl"))

    experiment = sub.add_parser("experiment", help="run synthetic falsification harness")
    experiment.add_argument("--policy", type=Path, default=_default_policy())
    experiment.add_argument("--repeats", type=int, default=10)
    experiment.add_argument("--seed", type=int, default=7)
    experiment.add_argument("--output", type=Path, default=Path("artifacts/synthetic_results.json"))

    schemas = sub.add_parser("schemas", help="export JSON schemas")
    schemas.add_argument("--output-dir", type=Path, default=Path("schemas"))

    verify = sub.add_parser("verify-receipts", help="verify and replay a receipt chain")
    verify.add_argument("path", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        result = run_demo(policy_path=args.policy, receipt_path=args.receipts)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "experiment":
        result = run_synthetic_experiment(
            policy_path=args.policy,
            repeats_per_kind=args.repeats,
            seed=args.seed,
            output_path=args.output,
        )
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))
        return 0
    if args.command == "schemas":
        paths = export_schemas(args.output_dir)
        print("\n".join(str(path) for path in paths))
        return 0
    if args.command == "verify-receipts":
        ledger = ReceiptLedger(args.path)
        replayer = ReceiptReplayer(
            fact_authority=FactAuthority({"bank-core": b"prototype-bank-core-secret"}),
            token_authority=TokenAuthority(
                "obligation-service", b"prototype-token-secret"
            ),
        )
        results = replayer.replay_chain(ledger.receipts)
        print(
            json.dumps(
                [
                    {
                        "sequence": result.sequence,
                        "valid": result.valid,
                        "checks": result.checks,
                        "limitations": result.limitations,
                    }
                    for result in results
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return int(not all(result.valid for result in results))
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
