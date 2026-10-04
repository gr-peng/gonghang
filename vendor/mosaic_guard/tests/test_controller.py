from __future__ import annotations

from scns_guard.controller import SafetyController
from scns_guard.enums import DecisionStatus, FactTrust, ObligationType, RiskLevel
from scns_guard.models import DetectorSignal, TrustedFact
from scns_guard.receipts import ReceiptLedger

from .helpers import action_from_trace, make_trace


class FixedDetector:
    detector_id = "fixed"

    def __init__(self, level: RiskLevel) -> None:
        self.level = level

    def assess(self, action, *, context):
        del action, context
        return DetectorSignal(
            detector_id=self.detector_id,
            detector_version="test",
            level=self.level,
            score=1.0,
            reason="test signal",
        )


def test_detector_cannot_lower_hard_policy(components) -> None:
    policy, facts, tokens, ledger = components
    action = action_from_trace(make_trace(amount_minor=150_000, trace_id="cannot-lower"))
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        detectors=(FixedDetector(RiskLevel.GREEN),),
    )
    decision = controller.decide(
        action,
        facts=ledger.issue_facts("user-1", facts),
    ).formal.decision
    assert decision.base_level is RiskLevel.RED
    assert decision.final_level is RiskLevel.RED


def test_detector_can_only_add_obligations(components) -> None:
    policy, facts, tokens, ledger = components
    action = action_from_trace(make_trace(trace_id="upgrade"))
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        detectors=(FixedDetector(RiskLevel.RED),),
    )
    decision = controller.decide(
        action,
        facts=ledger.issue_facts("user-1", facts),
    ).formal.decision
    assert decision.base_level is RiskLevel.YELLOW
    assert decision.final_level is RiskLevel.RED
    assert decision.status is DecisionStatus.REQUIRE_MFA


def test_untrusted_fact_cannot_discharge_policy_precondition(components) -> None:
    policy, facts, tokens, ledger = components
    action = action_from_trace(make_trace(trace_id="untrusted-fact"))
    signed = list(ledger.issue_facts("user-1", facts))
    signed = [fact for fact in signed if fact.predicate != "authenticated"]
    signed.append(
        TrustedFact(
            predicate="authenticated",
            subject="user-1",
            value=True,
            issuer="llm",
            trust=FactTrust.UNTRUSTED,
        )
    )
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
    )
    decision = controller.decide(action, facts=signed).formal.decision
    assert decision.final_level is RiskLevel.DENY
    assert decision.status is DecisionStatus.DENY


def test_full_redecision_prevents_stale_balance_execution(components) -> None:
    policy, facts, tokens, ledger = components
    action = action_from_trace(make_trace(amount_minor=40_000, trace_id="stale"))
    confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
    # A prior snapshot would allow the action.
    before = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
    ).decide(
        action,
        facts=ledger.issue_facts("user-1", facts),
        tokens=(confirmation,),
    )
    assert before.formal.decision.status is DecisionStatus.ALLOW

    # State changes before execution; execute() obtains fresh signed facts.
    ledger.accounts["acct-user"].balance_minor = 10_000
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        receipt_ledger=ReceiptLedger(),
    )
    receipt = controller.execute(
        action,
        fact_supplier=lambda: ledger.issue_facts("user-1", facts),
        tool=ledger,
        tokens=(confirmation,),
    )
    assert receipt.formal.decision.status is DecisionStatus.DENY
    assert receipt.formal.execution is None
    assert ledger.accounts["acct-user"].balance_minor == 10_000
