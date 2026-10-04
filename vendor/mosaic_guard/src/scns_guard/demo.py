from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .causal import AgentTrace, CounterfactualSourceAttributor, FieldCausalGuard, SourceMessage
from .controller import SafetyController
from .enums import ObligationType, SourceKind, SourceTrust
from .models import SourceRef
from .policy import PolicyEngine
from .receipts import ReceiptLedger
from .simulator import BankAccount, BankLedger, ToyTransferAgent
from .tokens import TokenAuthority
from .trust import FactAuthority


def run_demo(
    *,
    policy_path: str | Path,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    policy = PolicyEngine.from_yaml(policy_path)
    facts = FactAuthority({"bank-core": b"prototype-bank-core-secret"})
    tokens = TokenAuthority("obligation-service", b"prototype-token-secret")
    ledger = BankLedger(
        [
            BankAccount("acct-user", "user-1", 500_000),
            BankAccount("acct-alice", "alice", 10_000),
            BankAccount("acct-mallory", "mallory", 10_000),
        ]
    )
    agent = ToyTransferAgent()
    user_source = SourceRef(
        source_id="demo-user",
        kind=SourceKind.USER,
        trust=SourceTrust.USER,
    )
    trace = AgentTrace(
        trace_id="demo-clean-trace",
        session_id="demo-session",
        actor_id="user-1",
        messages=(
            SourceMessage(
                source=user_source,
                text="Transfer 1500 to Alice.",
                payload={
                    "action_type": "transfer",
                    "from_account": "acct-user",
                    "to_account": "acct-alice",
                    "amount_minor": 150_000,
                    "memo": "rent",
                },
            ),
        ),
    )
    action = agent.propose(trace, disabled_source_ids=frozenset(), seed=0)
    assert action is not None
    guard = FieldCausalGuard(
        CounterfactualSourceAttributor(agent, seeds=(0, 1)),
        audit_only=True,
    )
    receipt_ledger = ReceiptLedger(receipt_path)
    controller = SafetyController(
        policy=policy,
        fact_authority=facts,
        token_authority=tokens,
        detectors=(guard,),
        receipt_ledger=receipt_ledger,
    )

    first = controller.decide(
        action,
        facts=ledger.issue_facts("user-1", facts),
        detector_context={"trace": trace},
    )
    confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
    mfa = tokens.issue(action, ObligationType.MFA)
    executed = controller.execute(
        action,
        fact_supplier=lambda: ledger.issue_facts("user-1", facts),
        tool=ledger,
        tokens=(confirmation, mfa),
        detector_context={"trace": trace},
    )
    return {
        "first_decision": first.formal.decision.model_dump(mode="json"),
        "execution_decision": executed.formal.decision.model_dump(mode="json"),
        "execution": None
        if executed.formal.execution is None
        else executed.formal.execution.model_dump(mode="json"),
        "final_ledger": ledger.snapshot(),
        "receipt_count": len(receipt_ledger.receipts),
        "receipt_path": None if receipt_path is None else str(receipt_path),
    }


def print_demo(**kwargs: Any) -> None:
    print(json.dumps(run_demo(**kwargs), indent=2, ensure_ascii=False))
