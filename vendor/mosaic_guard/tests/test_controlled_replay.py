from scns_guard.controlled_replay import ReplayOutcome, compare_outcomes, summarize_pairs
from scns_guard.controlled_replay import PreparedVariant, run_paired_replays
from scns_guard.causal import AgentTrace


def action(recipient="A", amount=100):
    return ReplayOutcome(status="action", action_type="transfer", write_action=True,
                         params={"to_account": recipient, "amount_minor": amount})


def test_two_no_actions_do_not_create_field_effect_or_success():
    pair = compare_outcomes(ReplayOutcome(status="no_action"), ReplayOutcome(status="no_action"),
                            fields=("to_account",), expected={"action_type": "transfer", "to_account": "A"})
    assert pair["field_effects"]["to_account"] is None
    assert pair["action_presence_changed"] is False
    assert pair["altered_authorized_effect"] is False


def test_error_and_disappearance_are_not_field_substitution():
    for outcome in [ReplayOutcome(status="error", error="invalid JSON"), ReplayOutcome(status="no_action")]:
        pair = compare_outcomes(action(), outcome, fields=("to_account",), expected=None)
        assert pair["field_effects"]["to_account"] is None
        assert pair["comparable_actions"] is False


def test_type_exact_field_comparison_and_missing_field():
    pair = compare_outcomes(action(amount=1), action(amount=True), fields=("amount_minor", "absent"), expected=None)
    assert pair["field_effects"] == {"amount_minor": 1, "absent": None}


def test_heterogeneous_interventions_not_averaged_into_causal_label():
    rows = []
    for iid, cf in [("ablate", action("A")), ("relocate", action("B"))]:
        rows.append({"intervention_id": iid, "seed": 23,
                     **compare_outcomes(action("B"), cf, fields=("to_account",), expected=None)})
    report = summarize_pairs(rows)
    assert report["heterogeneous_fields"] == ["to_account"]
    assert report["pooled_causal_label"] is None
    assert report["by_intervention"]["ablate"]["to_account"]["sample_std"] is None


def test_multi_intervention_replay_preserves_common_seeds_and_variant_references():
    trace = AgentTrace(trace_id="t", session_id="s", actor_id="a", messages=())
    altered = trace.model_copy(update={"trace_id": "cf"})
    calls = []
    def run(t, seed):
        calls.append((t.trace_id, seed))
        return action("A" if t.trace_id == "t" else "B")
    variants = [PreparedVariant("replace", altered, {"action_type": "transfer", "to_account": "B"},
                                 False, ("authorized service value changed",))]
    rows = run_paired_replays(trace, variants, run, seeds=(23, 47), fields=("to_account",),
                              original_expected={"action_type": "transfer", "to_account": "A"})
    assert calls == [("t", 23), ("cf", 23), ("t", 47), ("cf", 47)]
    assert len(rows) == 2
    assert all(r["field_effects"]["to_account"] == 1 for r in rows)
    assert all(r["full_authorized_effect"] and r["altered_authorized_effect"] for r in rows)
    assert all(r["authorization_equivalent"] is False for r in rows)
