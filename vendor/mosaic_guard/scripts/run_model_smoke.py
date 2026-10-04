from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scns_guard.adapters.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleGenerator,
)
from scns_guard.canonical import sha256_hex
from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.controller import SafetyController
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.experiment import default_components
from scns_guard.lineage import LineageAuthority, VerifiedPayloadProvenanceResolver
from scns_guard.llm_adapter import StructuredLLMAdapter


ROOT = Path(__file__).resolve().parents[1]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one non-executing real-model smoke")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "qwen38_27b_sglang.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "configs" / "transfer_policy.yaml",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--replays", type=int, default=2)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "artifacts" / "model_smoke",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.replays < 1:
        raise SystemExit("--replays must be at least 1")
    run_dir = args.output_root / args.run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"refusing to overwrite existing run: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    config_bytes = args.config.read_bytes()
    config = OpenAICompatibleConfig.model_validate_json(config_bytes)
    lineage = LineageAuthority({"mosaic-runtime": b"prototype-lineage-secret"})
    text = "请从 acct-user 向 acct-alice 转账 200 元，备注房租。"
    payload = {
        "action_type": "transfer",
        "from_account": "acct-user",
        "to_account": "acct-alice",
        "amount_minor": 20_000,
        "memo": "房租",
    }
    source = lineage.issue_root(
        source_id=f"{args.run_id}-user",
        kind=SourceKind.USER,
        trust=SourceTrust.USER,
        content={"text": text, "payload": payload},
        producer_id="mosaic-runtime",
    )
    trace = AgentTrace(
        trace_id=args.run_id,
        session_id=f"session-{args.run_id}",
        actor_id="user-1",
        messages=(SourceMessage(source=source, text=text, payload=payload),),
        metadata={"evidence_boundary": "real_llm_integration_smoke_not_security_evaluation"},
    )
    generator = OpenAICompatibleGenerator(
        config,
        trace_path=run_dir / "model_calls.jsonl",
    )
    adapter = StructuredLLMAdapter(
        generator,
        provenance_resolver=VerifiedPayloadProvenanceResolver(lineage),
    )
    started_at = datetime.now(timezone.utc)
    actions = [
        adapter.propose(trace, disabled_source_ids=frozenset(), seed=args.seed)
        for _ in range(args.replays)
    ]
    if any(action is None for action in actions):
        raise SystemExit("model returned no action")
    action = actions[0]
    assert action is not None
    action_digests = [candidate.digest for candidate in actions if candidate is not None]
    call_rows = [
        json.loads(line)
        for line in (run_dir / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw_outputs = [row["raw_action_json"] for row in call_rows]

    policy, fact_authority, token_authority, ledger = default_components(args.policy)
    receipt = SafetyController(
        policy=policy,
        fact_authority=fact_authority,
        token_authority=token_authority,
        lineage_authority=lineage,
    ).decide(
        action,
        facts=ledger.issue_facts("user-1", fact_authority),
    )
    completed_at = datetime.now(timezone.utc)
    manifest = {
        "run_id": args.run_id,
        "evidence_boundary": "real_llm_integration_smoke_not_security_evaluation",
        "tool_execution": "not_attempted",
        "seed": args.seed,
        "replays": args.replays,
        "raw_output_exact_match": len(set(raw_outputs)) == 1,
        "action_digest_match": len(set(action_digests)) == 1,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "adapter_config": config.model_dump(mode="json"),
        "adapter_config_sha256": file_sha256(args.config),
        "policy_path": str(args.policy.resolve()),
        "policy_file_sha256": file_sha256(args.policy),
        "policy_semantic_sha256": sha256_hex(policy.snapshot()),
        "runner_sha256": file_sha256(Path(__file__)),
        "action": action.model_dump(mode="json"),
        "decision": receipt.formal.decision.model_dump(mode="json"),
        "model_call_trace": "model_calls.jsonl",
        "model_call_trace_sha256": file_sha256(run_dir / "model_calls.jsonl"),
        "activation_capture": "not_available_from_openai_compatible_endpoint",
        "model_fingerprint_scope": "config_tokenizer_template_and_weight_index_not_weight_bytes",
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(run_dir / "run_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
