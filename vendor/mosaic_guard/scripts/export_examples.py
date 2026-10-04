from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scns_guard.canonical import canonical_data
from scns_guard.experiment import generate_scenarios
from scns_guard.simulator import ToyTransferAgent

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples"
SELECTED = {
    "clean",
    "direct_target",
    "washed_target",
    "benign_rag_memo",
    "trusted_service_target",
}
FIXED_TIME = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def export_examples(output_dir: Path = OUTPUT) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    agent = ToyTransferAgent()
    paths: list[Path] = []
    for scenario in generate_scenarios(repeats_per_kind=1, seed=7):
        if scenario.kind not in SELECTED:
            continue
        action = agent.propose(
            scenario.trace,
            disabled_source_ids=frozenset(),
            seed=0,
        )
        if action is None:
            raise RuntimeError(f"example scenario produced no action: {scenario.scenario_id}")
        action = action.model_copy(
            update={
                "action_id": f"example-action-{scenario.kind}",
                "proposed_at": FIXED_TIME,
            }
        )
        payload = {
            "evidence_status": "deterministic_structured_example_not_llm_evidence",
            "scenario_id": scenario.scenario_id,
            "kind": scenario.kind,
            "description": scenario.description,
            "attack": scenario.attack,
            "source_laundered": scenario.washed,
            "source_escalation_expected": scenario.source_escalation_expected,
            "trace": scenario.trace,
            "proposed_action": action,
            "note": (
                "Payload keys drive the toy agent deterministically. They are test fixtures, "
                "not natural-language attack results."
            ),
        }
        path = output_dir / f"{scenario.kind}.json"
        path.write_text(
            json.dumps(canonical_data(payload), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return tuple(sorted(paths))


if __name__ == "__main__":
    for exported in export_examples():
        print(exported)
