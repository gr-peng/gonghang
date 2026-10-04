from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .adapters.openai_compatible import GenerationTrace
from .canonical import canonical_json, sha256_hex
from .causal import AgentTrace
from .controlled_replay import ReplayOutcome, compare_outcomes, protected_effect_preserved
from .lineage import LineageAuthority
from .receipts import ReceiptLedger
from .replay import ReceiptReplayer
from .tokens import TokenAuthority
from .trust import FactAuthority
from .task_suite import TransferTaskSpec, validate_task_isolation


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _outcome(value: dict[str, Any]) -> ReplayOutcome:
    return ReplayOutcome.model_validate(value)


def _validate_trace(trace: AgentTrace, authority: LineageAuthority) -> None:
    catalog = trace.source_catalog()
    for message in (*trace.messages, *trace.lineage_messages):
        if not authority.content_matches(message.source, {"text": message.text, "payload": message.payload}):
            raise ValueError(f"trace source content mismatch: {message.source.source_id}")
    if not all(authority.verify(source, catalog=catalog) for source in catalog.values()):
        raise ValueError("trace contains invalid signed lineage")


def audit_intervention_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("run manifest is not complete")
    for relative, expected in manifest.get("file_sha256", {}).items():
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("run manifest path escapes run directory") from exc
        if not candidate.is_file() or _hash(candidate) != expected:
            raise ValueError(f"run artifact hash mismatch: {relative}")
    protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    tasks = [TransferTaskSpec.model_validate(row) for row in json.loads((root / "task_specs.json").read_text())]
    validate_task_isolation(tasks)
    task_map = {task.group_id: task for task in tasks}
    outcomes = _rows(root / "outcomes.jsonl")
    pairs = _rows(root / "pairs.jsonl")
    variants = _rows(root / "variants.jsonl")
    expected_count = protocol["expected_planner_calls"]
    if len(outcomes) != expected_count:
        raise ValueError(f"outcome count mismatch: {len(outcomes)} != {expected_count}")
    keys: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in outcomes:
        if row.get("tool_execution") != "not_attempted":
            raise ValueError("a run outcome does not declare non-execution")
        key = (row["scenario_id"], row["mode"], row["seed"], row["intervention_id"])
        if key in keys:
            raise ValueError(f"duplicate outcome key: {key}")
        keys[key] = row
        task = task_map[row["group_id"]]
        if row["split"] != task.split or row["family_id"] != task.family_id:
            raise ValueError("outcome group/template split disagrees with authored task")
        expected = dict(task.reference)
        if row["condition"] == "delegated":
            if row["intervention_id"] in ("source_ablation", "control_neutralization"):
                expected = None
            elif row["intervention_id"] == "field_replacement":
                expected[task.field] = task.replacement
        if canonical_json(expected) != canonical_json(row["reference"]):
            raise ValueError("outcome reference differs from authored task/intervention contract")
        if protected_effect_preserved(_outcome(row["outcome"]), expected) != row["authorized_effect_preserved"]:
            raise ValueError("outcome protected-effect score does not replay")
    authority = LineageAuthority({"mosaic-runtime": b"prototype-lineage-secret"})
    for row in _rows(root / "scenarios.jsonl"):
        _validate_trace(AgentTrace.model_validate(row["trace"]), authority)
    for row in variants:
        _validate_trace(AgentTrace.model_validate(row["trace"]), authority)

    calls: dict[str, list[GenerationTrace]] = {}
    for mode in protocol["modes"]:
        path = root / f"planner_calls_{mode}.jsonl"
        calls[mode] = [GenerationTrace.model_validate(row) for row in _rows(path)]
        indices = sorted(row["model_call_index"] for row in outcomes
                         if row["mode"] == mode and row["model_call_index"] is not None)
        if indices != list(range(len(calls[mode]))):
            raise ValueError(f"non-contiguous model call indices for mode {mode}")
        for row in (r for r in outcomes if r["mode"] == mode and r["model_call_index"] is not None):
            call = calls[mode][row["model_call_index"]]
            if row["request_digest"] != call.request_digest or row["seed"] != call.seed:
                raise ValueError("outcome/model-call linkage mismatch")
            if call.response_digest != sha256_hex(call.raw_response):
                raise ValueError("model response digest mismatch")
            outcome = _outcome(row["outcome"])
            try:
                decoded = json.loads(call.raw_action_json)
            except json.JSONDecodeError:
                decoded = None
            if outcome.status == "action" and (not isinstance(decoded, dict)
                    or decoded.get("action_type") != outcome.action_type
                    or canonical_json(decoded.get("params")) != canonical_json(outcome.params)
                    or decoded.get("write_action", True) != outcome.write_action):
                raise ValueError("recorded action differs from raw model output")

    receipts = ReceiptLedger.load_jsonl(root / "receipts.jsonl")
    ReceiptLedger.verify_chain(receipts)
    receipt_map = {receipt.sequence: receipt for receipt in receipts}
    replay = ReceiptReplayer(
        fact_authority=FactAuthority({"bank-core": b"prototype-bank-core-secret"}),
        token_authority=TokenAuthority("obligation-service", b"prototype-token-secret"),
        lineage_authority=authority,
    ).replay_chain(receipts)
    if not all(result.valid for result in replay):
        raise ValueError("one or more formal receipts do not replay")
    for row in outcomes:
        sequence = row.get("receipt_sequence")
        outcome = _outcome(row["outcome"])
        if outcome.status == "action":
            if sequence not in receipt_map or receipt_map[sequence].formal.action.params != outcome.params:
                raise ValueError("action outcome and formal receipt disagree")
            receipt = receipt_map[sequence]
            decision = receipt.formal.decision
            if (row["action_digest"] != receipt.formal.action.digest
                    or row["base_level"] != decision.base_level.label()
                    or row["final_level"] != decision.final_level.label()
                    or row["decision_status"] != decision.status.value):
                raise ValueError("outcome decision metadata differs from formal receipt")
            if receipt.formal.execution is not None:
                raise ValueError("non-executing pilot contains an execution record")
            if receipt.formal.decision.final_level < receipt.formal.decision.base_level:
                raise ValueError("non-expansion invariant violated")

    for row in pairs:
        base = keys[(row["scenario_id"], row["mode"], row["seed"], "baseline")]
        altered = keys[(row["scenario_id"], row["mode"], row["seed"], row["intervention_id"])]
        recomputed = compare_outcomes(_outcome(base["outcome"]), _outcome(altered["outcome"]),
                                      fields=tuple(row["field_effects"]), expected=altered["reference"])
        for field in ("comparable_actions", "field_effects", "action_presence_changed",
                      "action_type_changed", "altered_authorized_effect"):
            if recomputed[field] != row[field]:
                raise ValueError(f"paired comparison mismatch: {field}")
    repeat_count = 0
    repeat_exact = 0
    opaque_identity_checks = 0
    for key, row in keys.items():
        if row["mode"] == "opaque" and row["intervention_id"] == "identity_rename":
            base = keys[key[:-1] + ("baseline",)]
            if row.get("prompt_digest") != base.get("prompt_digest"):
                raise ValueError("opaque identity rename unexpectedly changed the prompt")
            opaque_identity_checks += 1
        if key[-1] == "same_seed_repeat":
            base = keys[key[:-1] + ("baseline",)]
            if row.get("prompt_digest") != base.get("prompt_digest"):
                raise ValueError("same-seed repeat changed the input prompt")
            repeat_count += 1
            repeat_exact += int(row["outcome"] == base["outcome"])
    summary = json.loads((root / "summary.json").read_text())
    if (summary["planner_outcomes"] != len(outcomes) or summary["paired_comparisons"] != len(pairs)
            or summary["errors"] != sum(r["outcome"]["status"] == "error" for r in outcomes)):
        raise ValueError("summary counts disagree with individual outcomes")
    cells = defaultdict(list)
    for row in outcomes:
        cells[(row["mode"], row["condition"], row["intervention_id"])].append(row)
    if len(summary["cells"]) != len(cells):
        raise ValueError("summary cell count mismatch")
    for cell in summary["cells"]:
        rows = cells[(cell["mode"], cell["condition"], cell["intervention_id"])]
        recomputed = {
            "attempts": len(rows), "status_counts": dict(Counter(r["outcome"]["status"] for r in rows)),
            "reference_available": sum(r["reference"] is not None for r in rows),
            "authorized_effect_preserved": sum(r["authorized_effect_preserved"] is True for r in rows),
            "action_reference_mismatch": sum(r["reference"] is not None and r["outcome"]["status"] == "action"
                                              and r["authorized_effect_preserved"] is False for r in rows),
            "decision_statuses": dict(Counter(r.get("decision_status", "unavailable") for r in rows)),
        }
        if any(cell[k] != v for k, v in recomputed.items()):
            raise ValueError("summary cell does not reproduce from outcomes")
    return {
        "valid": True, "verified_files": len(manifest["file_sha256"]),
        "verified_outcomes": len(outcomes), "verified_pairs": len(pairs),
        "verified_receipts": len(receipts),
        "same_seed_repeats": repeat_count, "same_seed_exact_outcomes": repeat_exact,
        "group_and_template_isolation": True,
        "opaque_identity_prompt_noops_checked": opaque_identity_checks,
        "recorded_model_errors": sum(r["outcome"]["status"] == "error" for r in outcomes),
        "tool_execution": "not_attempted",
        "limitations": ["integrity hashes are not publisher authentication",
                        "model weights are identified by the run manifest boundary, not rehashed here",
                        "paired scoring replays saved outcomes and does not rerun inference"],
    }
