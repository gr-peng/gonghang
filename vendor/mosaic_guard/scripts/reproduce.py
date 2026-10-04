from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from export_examples import export_examples
from scns_guard.demo import run_demo
from scns_guard.planning_demo import run_planning_demo
from scns_guard.runtime_demo import run_runtime_demo
from scns_guard.execution_demo import run_execution_demo
from scns_guard.engineering_demo import run_engineering_demo
from scns_guard.experiment import run_synthetic_experiment
from scns_guard.receipts import ReceiptLedger
from scns_guard.replay import ReceiptReplayer
from scns_guard.reproducibility import preserved_model_manifests, verify_manifest, write_manifest
from scns_guard.schemas import export_schemas
from scns_guard.tokens import TokenAuthority
from scns_guard.trust import FactAuthority

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
POLICY = ROOT / "configs" / "transfer_policy.yaml"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_command(command: list[str], output: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = source if not existing else f"{source}{os.pathsep}{existing}"
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "$ " + " ".join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}); see {output}")
    return result


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for name in (
        "demo_receipts.jsonl",
        "demo_output.json",
        "synthetic_results.json",
        "synthetic_summary.json",
        "receipt_verification.json",
        "reproducibility_manifest.json",
        "reproduction_summary.json",
        "compile_report.txt",
        "test_report.txt",
        "coverage_test_report.txt",
        "coverage_report.txt",
        "planning_demo.json",
        "planning_demo_receipts.jsonl",
        "runtime_safety_demo.json",
        "runtime_safety_receipts.jsonl",
        "execution_once_demo.json",
        "execution_once_receipts.jsonl",
        "engineering_demo.json",
        "engineering_demo_receipts.jsonl",
    ):
        (ARTIFACTS / name).unlink(missing_ok=True)

    # Export deterministic interfaces and fixtures before testing them.
    export_schemas(ROOT / "schemas")
    export_examples(ROOT / "examples")

    run_command(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"],
        ARTIFACTS / "compile_report.txt",
    )
    run_command(
        [sys.executable, "-m", "pytest", "-q"],
        ARTIFACTS / "test_report.txt",
    )
    run_command(
        [sys.executable, "-m", "coverage", "erase"],
        ARTIFACTS / "coverage_test_report.txt",
    )
    coverage_run = run_command(
        [sys.executable, "-m", "coverage", "run", "--source=src/scns_guard", "-m", "pytest", "-q"],
        ARTIFACTS / "coverage_test_report.txt",
    )
    del coverage_run
    run_command(
        [sys.executable, "-m", "coverage", "report", "-m"],
        ARTIFACTS / "coverage_report.txt",
    )

    return produce_artifacts()


def produce_artifacts(*, evidence_mode: str = "single_full_command") -> int:
    """Final stage, also callable after independently completed test/coverage runs.

    The normal entrypoint still executes every step. Split callers must retain
    the current run's raw reports; this is not a cache or skip-tests CLI switch.
    """
    for name in ("test_report.txt", "coverage_test_report.txt"):
        report = (ARTIFACTS / name).read_text(encoding="utf-8")
        if " passed" not in report or " failed" in report or "ERROR " in report:
            raise SystemExit(f"current successful component report required: {name}")
    receipt_path = ARTIFACTS / "demo_receipts.jsonl"
    demo = run_demo(policy_path=POLICY, receipt_path=receipt_path)
    # Keep the captured output portable across extraction locations.
    demo["receipt_path"] = str(receipt_path.relative_to(ROOT))
    write_json(ARTIFACTS / "demo_output.json", demo)

    planning_demo = run_planning_demo(
        policy_path=POLICY, receipt_path=ARTIFACTS / "planning_demo_receipts.jsonl",
    )
    write_json(ARTIFACTS / "planning_demo.json", planning_demo)
    if not planning_demo["receipts_replay_valid"]:
        raise SystemExit("planning demo receipt replay failed")

    runtime_demo = run_runtime_demo(policy_path=POLICY, receipt_path=ARTIFACTS / "runtime_safety_receipts.jsonl")
    write_json(ARTIFACTS / "runtime_safety_demo.json", runtime_demo)
    if runtime_demo["passed"] != runtime_demo["attempts"] or not runtime_demo["receipts_replay_valid"]:
        raise SystemExit("runtime safety demo failed")

    execution_demo = run_execution_demo(policy_path=POLICY, receipt_path=ARTIFACTS / "execution_once_receipts.jsonl")
    write_json(ARTIFACTS / "execution_once_demo.json", execution_demo)
    if execution_demo["passed"] != execution_demo["attempts"] or not execution_demo["receipts_replay_valid"]:
        raise SystemExit("execution-once demo failed")

    engineering_demo = run_engineering_demo(policy_path=POLICY, receipt_path=ARTIFACTS / 'engineering_demo_receipts.jsonl')
    write_json(ARTIFACTS / 'engineering_demo.json', engineering_demo)
    if engineering_demo['passed'] != engineering_demo['attempts'] or not engineering_demo['receipts_replay_valid']:
        raise SystemExit('engineering integration demo failed')

    synthetic = run_synthetic_experiment(
        policy_path=POLICY,
        repeats_per_kind=20,
        seed=7,
        output_path=ARTIFACTS / "synthetic_results.json",
    )
    write_json(
        ARTIFACTS / "synthetic_summary.json",
        {key: value for key, value in synthetic.items() if key != "rows"},
    )

    ledger = ReceiptLedger(receipt_path)
    replay = ReceiptReplayer(
        fact_authority=FactAuthority({"bank-core": b"prototype-bank-core-secret"}),
        token_authority=TokenAuthority("obligation-service", b"prototype-token-secret"),
    ).replay_chain(ledger.receipts)
    replay_payload = [
        {
            "sequence": item.sequence,
            "valid": item.valid,
            "checks": item.checks,
            "limitations": item.limitations,
        }
        for item in replay
    ]
    write_json(ARTIFACTS / "receipt_verification.json", replay_payload)
    if not all(item.valid for item in replay):
        raise SystemExit("receipt replay failed")

    manifest_path = write_manifest(ROOT, ARTIFACTS / "reproducibility_manifest.json")
    manifest_verification = verify_manifest(ROOT, manifest_path)
    if not manifest_verification["valid"]:
        raise SystemExit("reproducibility manifest verification failed")
    write_json(
        ARTIFACTS / "reproduction_summary.json",
        {
            "status": "pass",
            "evidence_boundary": "tests_and_deterministic_synthetic_smoke_only",
            "execution_mode": evidence_mode,
            "real_llm_calls_in_this_reproduction": "not_run",
            "preserved_real_model_artifacts": preserved_model_manifests(ROOT),
            "test_report": str((ARTIFACTS / "test_report.txt").relative_to(ROOT)),
            "coverage_report": str((ARTIFACTS / "coverage_report.txt").relative_to(ROOT)),
            "demo_receipts": str(receipt_path.relative_to(ROOT)),
            "planning_demo": "artifacts/planning_demo.json",
            "runtime_safety_demo": "artifacts/runtime_safety_demo.json",
            "execution_once_demo": "artifacts/execution_once_demo.json",
            "engineering_demo": "artifacts/engineering_demo.json",
            "sandbox_integration_opt_in": os.environ.get('MOSAIC_SANDBOX_IMAGE'),
            "synthetic_results": str((ARTIFACTS / "synthetic_results.json").relative_to(ROOT)),
            "receipt_verification": str(
                (ARTIFACTS / "receipt_verification.json").relative_to(ROOT)
            ),
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_verification": manifest_verification,
        },
    )
    print(ARTIFACTS / "reproduction_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
