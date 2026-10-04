from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from scns_guard.adapters.openai_compatible import OpenAICompatibleGenerator
from scns_guard.task_suite import build_task_specs
from scns_guard.run_audit import audit_intervention_run


def test_runner_smoke_uses_fake_endpoint_and_writes_complete_evidence(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("pilot_runner_test", root / "scripts/run_multi_intervention_pilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads((root / "configs/qwen38_27b_sglang.json").read_text())
    config["capture_token_spans"] = False
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    def fake_post(self, endpoint, payload):
        messages = payload["messages"]
        blocks = [json.loads(s) for s in re.findall(r"<MOSAIC_SOURCE>\n(.*?)\n</MOSAIC_SOURCE>",
                                                    messages[1]["content"], re.DOTALL)]
        if self.config.prompt_template_id == "mosaic-summary-v1":
            content = json.dumps({"summary": "reported: " + blocks[0]["text"]})
        else:
            user = next(b for b in blocks if b["source_kind"] == "user")
            params = {k: v for k, v in user["payload"].items() if k != "action_type"}
            for b in blocks:
                if b["source_kind"] == "trusted_service":
                    params.update(b["payload"])
            content = json.dumps({"action_type": "transfer", "params": params, "write_action": True})
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    monkeypatch.setattr(OpenAICompatibleGenerator, "_post_json", fake_post)
    monkeypatch.setattr(module, "build_task_specs", lambda data_seed: build_task_specs(data_seed=data_seed)[:1])
    monkeypatch.setattr(sys, "argv", ["runner", "--run-id", "fake-smoke", "--config", str(config_path),
                                      "--output-root", str(tmp_path), "--modes", "full"])
    assert module.main() == 0
    out = tmp_path / "fake-smoke"
    result = json.loads((out / "summary.json").read_text())
    assert result["planner_outcomes"] == 24
    # The intentionally simple fake emits incomplete actions after the only
    # delegated field source is removed. These must remain recorded failures.
    assert result["errors"] == 2
    assert len((out / "summary_calls.jsonl").read_text().splitlines()) == 3
    assert len((out / "planner_calls_full.jsonl").read_text().splitlines()) == 24
    assert json.loads((out / "manifest.json").read_text())["status"] == "complete"
    audit = audit_intervention_run(out)
    assert audit["valid"], audit
    assert audit["recorded_model_errors"] == 2
    assert audit["verified_receipts"] == 22
    manifest_text = (out / "manifest.json").read_text()
    summary_text = (out / "summary.json").read_text()
    wrong_summary = json.loads(summary_text)
    wrong_summary["errors"] = 999
    (out / "summary.json").write_text(json.dumps(wrong_summary))
    manifest = json.loads(manifest_text)
    manifest["file_sha256"]["summary.json"] = __import__("hashlib").sha256((out / "summary.json").read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))
    with __import__("pytest").raises(ValueError, match="summary counts"):
        audit_intervention_run(out)
    (out / "summary.json").write_text(summary_text)
    (out / "manifest.json").write_text(manifest_text)
    with (out / "outcomes.jsonl").open("a") as handle:
        handle.write('{}\n')
    with __import__("pytest").raises(ValueError, match="hash mismatch"):
        audit_intervention_run(out)
