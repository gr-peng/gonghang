from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from scns_guard.enums import RiskLevel
from scns_guard.models import ActionProposal, TrustedFact
from scns_guard.money import MAX_MINOR_UNITS
from scns_guard.policy import PolicyEngine, PolicyError
from scns_guard.simulator import BankAccount, BankLedger


def transfer_action(amount_minor: object) -> ActionProposal:
    return ActionProposal(
        session_id="money-session",
        actor_id="user-1",
        action_type="transfer",
        params={
            "from_account": "acct-user",
            "to_account": "acct-alice",
            "amount_minor": amount_minor,
        },
    )


@pytest.mark.parametrize(
    "invalid",
    [
        0.1 + 0.2,
        -0.0,
        Decimal("1.001"),
        True,
        0,
        -1,
        2**63,
    ],
)
def test_transfer_amount_requires_positive_bounded_integer_minor_units(invalid: object) -> None:
    with pytest.raises(ValidationError):
        transfer_action(invalid)


def test_minor_unit_action_digest_survives_json_round_trip() -> None:
    action = transfer_action(150_000)
    restored = ActionProposal.model_validate_json(action.model_dump_json())

    assert restored.params["amount_minor"] == 150_000
    assert isinstance(restored.params["amount_minor"], int)
    assert restored.digest == action.digest


@pytest.mark.parametrize("invalid", [100.0, -0.0, Decimal("0.01"), 2**63])
def test_monetary_trusted_facts_require_bounded_integer_minor_units(invalid: object) -> None:
    with pytest.raises(ValidationError):
        TrustedFact(
            predicate="account_balance_minor",
            subject="acct-user",
            value=invalid,
            issuer="bank-core",
        )


def test_zero_monetary_trusted_fact_is_valid() -> None:
    fact = TrustedFact(
        predicate="daily_remaining_minor",
        subject="user-1",
        value=0,
        issuer="bank-core",
    )
    assert fact.value == 0


def test_policy_rejects_binary_float_monetary_threshold(components) -> None:
    policy, _, _, _ = components
    raw = policy.snapshot()
    raw["version"] = "float-money-threshold"
    for rule in raw["rules"]:
        if rule["rule_id"] != "transfer-small":
            continue
        for condition in rule["when"]["all"]:
            comparison = condition.get("compare")
            if comparison and comparison["left"] == "$params.amount_minor":
                comparison["right"] = 1000.0
                with pytest.raises(PolicyError, match="integer minor units"):
                    PolicyEngine.from_mapping(raw)
                return
    raise AssertionError("transfer-small monetary comparison not found")


def test_policy_boundary_is_exact_in_minor_units(components) -> None:
    policy, facts, _, ledger = components
    fact_index = facts.verified_index(ledger.issue_facts("user-1", facts))

    at_boundary = policy.evaluate(
        transfer_action(100_000), fact_index, enforce_argument_contracts=False
    )
    above_boundary = policy.evaluate(
        transfer_action(100_001), fact_index, enforce_argument_contracts=False
    )

    assert at_boundary.base_level is RiskLevel.YELLOW
    assert above_boundary.base_level is RiskLevel.RED


def test_ledger_accumulates_minor_units_exactly() -> None:
    ledger = BankLedger(
        [
            BankAccount("acct-user", "user-1", 100),
            BankAccount("acct-alice", "alice", 0),
        ],
        daily_limit_minor=100,
    )

    ledger.execute(transfer_action(10))
    ledger.execute(transfer_action(20))

    assert ledger.accounts["acct-user"].balance_minor == 70
    assert ledger.accounts["acct-alice"].balance_minor == 30
    assert ledger.daily_spent_minor["user-1"] == 30


def test_ledger_rejects_minor_unit_overflow_before_mutation() -> None:
    ledger = BankLedger(
        [
            BankAccount("acct-user", "user-1", 1),
            BankAccount("acct-alice", "alice", MAX_MINOR_UNITS),
        ],
        daily_limit_minor=MAX_MINOR_UNITS,
    )

    with pytest.raises(OverflowError):
        ledger.execute(transfer_action(1))

    assert ledger.accounts["acct-user"].balance_minor == 1
    assert ledger.accounts["acct-alice"].balance_minor == MAX_MINOR_UNITS
