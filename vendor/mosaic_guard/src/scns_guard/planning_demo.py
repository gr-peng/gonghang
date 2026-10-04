from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .causal import AgentTrace, SourceMessage
from .controller import SafetyController
from .enums import ObligationType, SourceKind, SourceTrust
from .lineage import LineageAuthority, VerifiedPayloadProvenanceResolver
from .planning import PLANNER_VERSION, PlanningLLMAdapter, PlanningService
from .policy import PolicyEngine
from .receipts import ReceiptLedger
from .replay import ReceiptReplayer
from .simulator import BankAccount, BankLedger
from .tokens import TokenAuthority
from .trust import FactAuthority


def run_planning_demo(
    *, policy_path: str | Path, receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """脚本规划器与模拟账本演示；确认令牌只由显式测试夹具签发。"""
    lineage = LineageAuthority({"demo-runtime": b"prototype-lineage-secret"})
    facts = FactAuthority({"bank-core": b"prototype-bank-core-secret"})
    tokens = TokenAuthority("obligation-service", b"prototype-token-secret")
    bank = BankLedger([BankAccount("acct-user", "user-1", 100_000),
                       BankAccount("acct-alice", "alice", 0)])
    receipts = ReceiptLedger(receipt_path)
    controller = SafetyController(policy=PolicyEngine.from_yaml(policy_path),
                                  fact_authority=facts, token_authority=tokens,
                                  lineage_authority=lineage, receipt_ledger=receipts)

    def user_message(source_id: str, text: str, payload: dict[str, Any]) -> SourceMessage:
        content = {"text": text, "payload": payload}
        source = lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
                                    content=content, producer_id="demo-runtime",
                                    source_id=source_id)
        return SourceMessage(source=source, **content)

    first = user_message("demo-request", "请从 acct-user 转账 1000 分，收款账户稍后补充。",
                         {"from_account": "acct-user", "amount_minor": 1000})
    reply = user_message("demo-reply", "收款账户是 acct-alice。", {"to_account": "acct-alice"})
    outputs = iter([first.payload, {**first.payload, **reply.payload}])

    def scripted_generator(messages, seed):
        del messages, seed
        return json.dumps({"schema_version": PLANNER_VERSION, "kind": "task",
                           "action_type": "transfer", "params": next(outputs)})

    service = PlanningService(PlanningLLMAdapter(
        scripted_generator, provenance_resolver=VerifiedPayloadProvenanceResolver(lineage)),
        controller)
    trace = AgentTrace(trace_id="planning-demo", session_id="demo-session", actor_id="user-1",
                       messages=(first,))
    supplier = lambda: bank.issue_facts("user-1", facts)
    before = bank.snapshot()
    first_turn = service.handle(trace, fact_supplier=supplier, seed=0)
    completed_turn = service.handle(trace.model_copy(update={"messages": (first, reply)}),
                                    fact_supplier=supplier, seed=0)
    action = completed_turn.planning.action
    assert action is not None
    without_confirmation = controller.execute(action, fact_supplier=supplier, tool=bank)
    assert bank.snapshot() == before
    # 仅演示可信确认事件；真实应用必须由已认证、展示确切参数的确认界面触发。
    confirmation = tokens.issue(action, ObligationType.CONFIRMATION)
    confirmed = controller.execute(action, fact_supplier=supplier, tool=bank,
                                    tokens=(confirmation,))
    replay = ReceiptReplayer(fact_authority=facts, token_authority=tokens,
                             lineage_authority=lineage).replay_chain(receipts.receipts)
    return {
        "evidence_boundary": "scripted_planner_with_mock_bank_not_real_llm",
        "first_turn": first_turn.model_dump(mode="json"),
        "completed_turn": completed_turn.model_dump(mode="json"),
        "without_confirmation_executed": without_confirmation.formal.execution is not None,
        "confirmation_source": "explicit_test_fixture_not_model_output",
        "confirmed_execution": (None if confirmed.formal.execution is None
                                else confirmed.formal.execution.model_dump(mode="json")),
        "initial_ledger": before,
        "final_ledger": bank.snapshot(),
        "receipt_count": len(receipts.receipts),
        "receipts_replay_valid": all(item.valid for item in replay),
    }
