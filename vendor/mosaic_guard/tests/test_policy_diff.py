from __future__ import annotations

import copy

import pytest

from scns_guard.policy import PolicyEngine
from scns_guard.policy_diff import BoundedPolicyDiffer, PolicyDiffCase

from .helpers import action_from_trace, make_trace


def _move_boundary(policy, small_upper_minor: int):
    raw = copy.deepcopy(policy.snapshot())
    raw["version"] = f"boundary-{small_upper_minor}"
    for rule in raw["rules"]:
        if rule["rule_id"] == "transfer-small":
            for condition in rule["when"]["all"]:
                comparison = condition.get("compare")
                if comparison and isinstance(comparison["left"], dict) and "sum_minor" in comparison["left"] and comparison["op"] == "<=":
                    comparison["right"] = small_upper_minor
        if rule["rule_id"] == "transfer-large":
            for condition in rule["when"]["all"]:
                comparison = condition.get("compare")
                if comparison and isinstance(comparison["left"], dict) and "sum_minor" in comparison["left"] and comparison["op"] == ">":
                    comparison["right"] = small_upper_minor
    return PolicyEngine.from_mapping(raw)


def test_bounded_diff_detects_permission_expansion(components) -> None:
    policy, facts, _, ledger = components
    expanded = _move_boundary(policy, 200_000)
    action = action_from_trace(make_trace(amount_minor=150_000, trace_id="diff-expand"))
    finding = BoundedPolicyDiffer(facts).compare(
        policy,
        expanded,
        [PolicyDiffCase("amount-minor-150000", action, ledger.issue_facts("user-1", facts))],
    )[0]
    assert finding.expansion
    assert finding.new_level < finding.old_level


def test_bounded_diff_accepts_narrowing(components) -> None:
    policy, facts, _, ledger = components
    narrowed = _move_boundary(policy, 50_000)
    action = action_from_trace(make_trace(amount_minor=80_000, trace_id="diff-narrow"))
    finding = BoundedPolicyDiffer(facts).compare(
        policy,
        narrowed,
        [PolicyDiffCase("amount-minor-80000", action, ledger.issue_facts("user-1", facts))],
    )[0]
    assert not finding.expansion
    assert finding.new_level > finding.old_level


def test_z3_numeric_diff_respects_three_valued_missing_facts(components) -> None:
    pytest.importorskip("z3")

    from scns_guard.policy_diff import find_numeric_expansion_with_z3

    policy, _, _, _ = components
    action = action_from_trace(make_trace(amount_minor=20_000, trace_id="z3-unknown"))

    old_raw = copy.deepcopy(policy.snapshot())
    old_raw["version"] = "z3-old"
    old_raw["rules"] = []
    old = PolicyEngine.from_mapping(old_raw)

    new_raw = copy.deepcopy(old_raw)
    new_raw["version"] = "z3-new"
    new_raw["rules"] = [
        {
            "rule_id": "missing-negated-fact-must-remain-unknown",
            "action": "transfer",
            "reason": "absence cannot create permission",
            "effect": "green",
            "when": {
                "not": {
                    "fact_equals": {
                        "predicate": "special_block",
                        "subject": "$actor",
                        "value": True,
                    }
                }
            },
        }
    ]
    new = PolicyEngine.from_mapping(new_raw)
    assert (
        find_numeric_expansion_with_z3(
            old,
            new,
            action_template=action,
            verified_facts={},
            parameter="amount_minor",
            lower=1,
            upper=100_000,
        )
        is None
    )


def test_z3_minor_unit_domain_has_no_fractional_counterexamples(components) -> None:
    pytest.importorskip("z3")

    from scns_guard.policy_diff import find_numeric_expansion_with_z3

    policy, facts, _, ledger = components
    action = action_from_trace(make_trace(amount_minor=1, trace_id="z3-integer-domain"))
    verified = facts.verified_index(ledger.issue_facts("user-1", facts))

    old_raw = copy.deepcopy(policy.snapshot())
    old_raw["version"] = "z3-integer-old"
    old_raw["rules"] = []
    old = PolicyEngine.from_mapping(old_raw)

    new_raw = copy.deepcopy(old_raw)
    new_raw["version"] = "z3-integer-new"
    new_raw["rules"] = [
        {
            "rule_id": "fractional-only-expansion",
            "action": "transfer",
            "reason": "must be unsatisfiable over integer minor units",
            "effect": "green",
            "when": {
                "all": [
                    {
                        "compare": {
                            "left": "$params.amount_minor",
                            "op": ">",
                            "right": 0,
                        }
                    },
                    {
                        "compare": {
                            "left": "$params.amount_minor",
                            "op": "<",
                            "right": 1,
                        }
                    },
                ]
            },
        }
    ]
    new = PolicyEngine.from_mapping(new_raw)

    assert (
        find_numeric_expansion_with_z3(
            old,
            new,
            action_template=action,
            verified_facts=verified,
            parameter="amount_minor",
            lower=0,
            upper=1,
        )
        is None
    )
