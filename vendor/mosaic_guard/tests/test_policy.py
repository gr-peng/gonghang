from __future__ import annotations

import pytest

from scns_guard.enums import RiskLevel, SourceKind
from scns_guard.simulator import ToyTransferAgent

from .helpers import action_from_trace, make_trace


def test_clean_small_transfer_is_yellow(components) -> None:
    policy, facts, _, ledger = components
    action = action_from_trace(make_trace())
    evaluation = policy.evaluate(action, facts.verified_index(ledger.issue_facts("user-1", facts)))
    assert evaluation.base_level is RiskLevel.YELLOW
    assert evaluation.argument_contract_violations == ()


def test_large_transfer_is_red(components) -> None:
    policy, facts, _, ledger = components
    action = action_from_trace(make_trace(amount_minor=150_000))
    evaluation = policy.evaluate(action, facts.verified_index(ledger.issue_facts("user-1", facts)))
    assert evaluation.base_level is RiskLevel.RED


def test_missing_trusted_facts_fails_closed(components) -> None:
    policy, _, _, _ = components
    action = action_from_trace(make_trace())
    evaluation = policy.evaluate(action, {})
    assert evaluation.base_level is RiskLevel.DENY


def test_external_provenance_upgrades_direct_target_control(components) -> None:
    policy, facts, _, ledger = components
    direct = make_trace(
        extra_kind=SourceKind.RAG,
        extra_payload={"override_to": "acct-mallory"},
        trace_id="direct",
    )
    action = action_from_trace(direct)
    evaluation = policy.evaluate(action, facts.verified_index(ledger.issue_facts("user-1", facts)))
    assert evaluation.base_level is RiskLevel.RED
    assert any("non-delegated source" in item for item in evaluation.argument_contract_violations)


def test_laundered_provenance_is_not_magically_recovered_by_contract(components) -> None:
    policy, facts, _, ledger = components
    washed = make_trace(
        extra_kind=SourceKind.RAG,
        extra_payload={"laundered_override_to": "acct-mallory"},
        trace_id="washed",
    )
    action = action_from_trace(washed)
    evaluation = policy.evaluate(action, facts.verified_index(ledger.issue_facts("user-1", facts)))
    assert evaluation.base_level is RiskLevel.YELLOW
    assert evaluation.argument_contract_violations == ()


def test_unknown_action_is_denied(components) -> None:
    policy, facts, _, ledger = components
    trace = make_trace(trace_id="unknown")
    action = ToyTransferAgent().propose(trace, disabled_source_ids=frozenset(), seed=0)
    assert action is not None
    action = action.model_copy(update={"action_type": "delete_account"})
    evaluation = policy.evaluate(action, facts.verified_index(ledger.issue_facts("user-1", facts)))
    assert evaluation.base_level is RiskLevel.DENY


def test_negating_a_missing_trusted_fact_does_not_create_permission(components) -> None:
    policy, _, _, _ = components
    raw = policy.snapshot()
    raw["version"] = "negative-missing-test"
    raw["rules"] = [
        {
            "rule_id": "unsafe-negation-shape",
            "action": "transfer",
            "reason": "must not match when trusted fact is absent",
            "effect": "green",
            "when": {
                "not": {
                    "fact_equals": {
                        "predicate": "special_approval",
                        "subject": "$actor",
                        "value": True,
                    }
                }
            },
        }
    ]
    from scns_guard.policy import PolicyEngine

    test_policy = PolicyEngine.from_mapping(raw)
    action = action_from_trace(make_trace(trace_id="missing-negation"))
    evaluation = test_policy.evaluate(
        action,
        {},
        enforce_argument_contracts=False,
    )
    assert evaluation.base_level is RiskLevel.DENY


def test_action_rejects_ambiguous_or_unknown_provenance() -> None:
    from pydantic import ValidationError

    action = action_from_trace(make_trace(trace_id="bad-provenance"))
    duplicate_source = action.sources + (action.sources[0],)
    with pytest.raises(ValidationError):
        action.model_copy(update={"sources": duplicate_source}).model_validate(
            action.model_copy(update={"sources": duplicate_source}).model_dump()
        )

    binding = action.argument_bindings[0].model_copy(update={"source_ids": ("missing",)})
    bad_bindings = (binding,) + action.argument_bindings[1:]
    with pytest.raises(ValidationError):
        type(action).model_validate(
            action.model_copy(update={"argument_bindings": bad_bindings}).model_dump()
        )


def test_policy_rejects_pseudo_facts_and_malformed_operators(components) -> None:
    from scns_guard.policy import PolicyEngine, PolicyError

    policy, _, _, _ = components
    raw = policy.snapshot()
    raw["version"] = "invalid-default"
    raw["rules"] = [
        {
            "rule_id": "pseudo-fact",
            "action": "transfer",
            "reason": "missing trusted facts must not receive permissive defaults",
            "effect": "green",
            "when": {
                "equals": {
                    "left": {
                        "fact": {
                            "predicate": "authenticated",
                            "subject": "$actor",
                            "default": True,
                        }
                    },
                    "right": True,
                }
            },
        }
    ]
    with pytest.raises(PolicyError):
        PolicyEngine.from_mapping(raw)

    raw["rules"][0]["when"] = {
        "compare": {"left": "$params.amount_minor", "op": "~=", "right": 1}
    }
    with pytest.raises(PolicyError):
        PolicyEngine.from_mapping(raw)


def test_not_exists_missing_trusted_fact_remains_unknown(components) -> None:
    from scns_guard.policy import PolicyEngine

    policy, _, _, _ = components
    raw = policy.snapshot()
    raw["version"] = "not-exists-missing"
    raw["rules"] = [
        {
            "rule_id": "absence-is-not-authority",
            "action": "transfer",
            "reason": "missing approval must not become approval under negation",
            "effect": "green",
            "when": {
                "not": {
                    "exists": {
                        "value": {
                            "fact": {
                                "predicate": "blocked",
                                "subject": "$actor",
                            }
                        }
                    }
                }
            },
        }
    ]
    test_policy = PolicyEngine.from_mapping(raw)
    action = action_from_trace(make_trace(trace_id="not-exists-missing"))
    evaluation = test_policy.evaluate(action, {}, enforce_argument_contracts=False)
    assert evaluation.base_level is RiskLevel.DENY


def test_policy_requires_default_deny_and_unambiguous_rule_ids(components) -> None:
    from scns_guard.policy import PolicyEngine, PolicyError

    policy, _, _, _ = components

    permissive = policy.snapshot()
    permissive["version"] = "permissive-default"
    permissive["default_level"] = "green"
    with pytest.raises(PolicyError, match="default_level must be deny"):
        PolicyEngine.from_mapping(permissive)

    duplicate = policy.snapshot()
    duplicate["version"] = "duplicate-rule-id"
    duplicate["rules"][1]["rule_id"] = duplicate["rules"][0]["rule_id"]
    with pytest.raises(PolicyError, match="rule_id values must be unique"):
        PolicyEngine.from_mapping(duplicate)

    typo = policy.snapshot()
    typo["version"] = "unknown-action-rule"
    typo["rules"][0]["action"] = "tranfer"
    with pytest.raises(PolicyError, match="without contracts"):
        PolicyEngine.from_mapping(typo)


def test_yaml_policy_loader_rejects_duplicate_keys(tmp_path, components) -> None:
    from scns_guard.policy import PolicyEngine, PolicyError

    policy, _, _, _ = components
    text = """
policy_id: duplicate-key
version: 1
version: 2
default_level: deny
action_contracts: {}
rules: []
"""
    path = tmp_path / "duplicate.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PolicyError, match="duplicate YAML mapping key"):
        PolicyEngine.from_yaml(path)


def test_policy_rejects_unknown_reference_syntax(components) -> None:
    from scns_guard.policy import PolicyEngine, PolicyError

    policy, _, _, _ = components
    raw = policy.snapshot()
    raw["version"] = "bad-reference"
    raw["rules"][0]["when"] = {
        "equals": {"left": "$param.account_id", "right": "acct-user"}
    }
    with pytest.raises(PolicyError, match="Unsupported policy reference"):
        PolicyEngine.from_mapping(raw)
