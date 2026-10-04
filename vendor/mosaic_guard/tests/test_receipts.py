from __future__ import annotations

import json

import pytest

from scns_guard.controller import SafetyController
from scns_guard.enums import ObligationType
from scns_guard.receipts import ReceiptIntegrityError, ReceiptLedger
from scns_guard.replay import ReceiptReplayer

from .helpers import action_from_trace, make_trace


def test_receipt_chain_and_conditional_replay(tmp_path, components) -> None:
    policy, facts, tokens, ledger = components
    path = tmp_path / "receipts.jsonl"
    receipt_ledger = ReceiptLedger(path)
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        receipt_ledger=receipt_ledger,
    )
    action = action_from_trace(make_trace(trace_id="receipt"))
    confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
    controller.execute(
        action,
        fact_supplier=lambda: ledger.issue_facts("user-1", facts),
        tool=ledger,
        tokens=(confirmation,),
    )
    ReceiptLedger.verify_chain(receipt_ledger.receipts)
    result = ReceiptReplayer(
        fact_authority=facts,
        token_authority=tokens,
    ).replay_chain(receipt_ledger.receipts)
    assert all(item.valid for item in result), result
    assert result[0].limitations


def test_receipt_tampering_breaks_hash_chain(tmp_path, components) -> None:
    policy, facts, tokens, ledger = components
    path = tmp_path / "receipts.jsonl"
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        receipt_ledger=ReceiptLedger(path),
    )
    action = action_from_trace(make_trace(trace_id="tamper"))
    controller.decide(action, facts=ledger.issue_facts("user-1", facts))
    row = json.loads(path.read_text(encoding="utf-8"))
    row["formal"]["action"]["params"]["amount_minor"] = 999_900
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptIntegrityError):
        ReceiptLedger(path)


def test_receipt_models_reject_inconsistent_action_and_evidence(components) -> None:
    from pydantic import ValidationError

    from scns_guard.models import DecisionReceipt, DetectorSignal, FormalReceiptLayer, InternalEvidenceLayer
    from scns_guard.enums import RiskLevel

    policy, facts, tokens, ledger = components
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
    )
    action = action_from_trace(make_trace(trace_id="receipt-consistency"))
    receipt = controller.decide(action, facts=ledger.issue_facts("user-1", facts))

    mutated_action = action.model_copy(
        update={"params": {**action.params, "amount_minor": 99_900}}
    )
    with pytest.raises(ValidationError, match="not bound to the recorded action"):
        FormalReceiptLayer.model_validate(
            {**receipt.formal.model_dump(mode="python"), "action": mutated_action}
        )

    extra_signal = DetectorSignal(
        detector_id="test",
        detector_version="1",
        level=RiskLevel.YELLOW,
        score=1.0,
        reason="test mismatch",
    )
    with pytest.raises(ValidationError, match="signals disagree"):
        DecisionReceipt.model_validate(
            {
                **receipt.model_dump(mode="python"),
                "internal": InternalEvidenceLayer(signals=(extra_signal,)),
            }
        )
