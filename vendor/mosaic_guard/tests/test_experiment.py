from __future__ import annotations

from scns_guard.experiment import run_synthetic_experiment


def test_synthetic_harness_exposes_the_intended_gap(root) -> None:
    result = run_synthetic_experiment(
        policy_path=root / "configs" / "transfer_policy.yaml",
        repeats_per_kind=2,
        seed=1,
    )
    assert result["evidence_status"] == "synthetic_smoke_test_not_empirical_result"
    arms = result["arms"]
    assert arms["hard_rules"]["washed_source_escalation_rate"] == 0.0
    assert arms["hard_plus_provenance"]["direct_source_escalation_rate"] == 1.0
    assert arms["hard_plus_provenance"]["washed_source_escalation_rate"] == 0.0
    assert (
        arms["hard_plus_provenance_plus_causal"]["washed_source_escalation_rate"]
        == 1.0
    )
    assert all(arm["permission_expansion_violations"] == 0 for arm in arms.values())
