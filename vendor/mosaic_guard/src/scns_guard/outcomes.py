"""Deterministic result contracts and a receipt-only public result interface."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .canonical import sha256_hex
from .enums import DecisionStatus, ExecutionState
from .models import ActionProposal, DecisionReceipt, StrictModel
from .money import validate_minor_units


class ToolResultError(ValueError):
    pass


def validate_tool_result(action: ActionProposal, result: dict[str, Any]) -> dict[str, Any]:
    """Check trusted adapter output; never infer backend truth from model text.

    Returns only allowlisted public fields. A matching response is conditional on
    adapter integrity; it is not proof against a compromised banking service.
    """
    if not isinstance(result, dict):
        raise ToolResultError("tool result must be an object")
    if action.action_type == "transfer":
        expected = {"status": "posted", "action_digest": action.digest,
                    **{key: action.params[key] for key in ("from_account", "to_account", "amount_minor")}}
        for key, value in expected.items():
            if type(result.get(key)) is not type(value) or result.get(key) != value:
                raise ToolResultError(f"transfer result does not match action field: {key}")
        validate_minor_units(result["amount_minor"], field_name="tool amount_minor", allow_zero=False)
        return expected
    if action.action_type == "read_balance":
        if result.get("account_id") != action.params["account_id"]:
            raise ToolResultError("balance result does not match action account")
        balance = validate_minor_units(result.get("balance_minor"), field_name="tool balance_minor", allow_zero=True)
        return {"account_id": result["account_id"], "balance_minor": balance}
    raise ToolResultError("no result contract for action type")


class PublicOutcome(StrictModel):
    status: Literal['blocked', 'needs_confirmation', 'needs_mfa',
                    'authorized_not_executed', 'succeeded', 'unknown']
    action_digest: str
    receipt_hash: str
    data: dict[str, Any] = Field(default_factory=dict)
    message: str


def outcome_from_receipt(receipt: DecisionReceipt, *, action: ActionProposal) -> PublicOutcome:
    """Call only with a receipt fetched from the trusted runtime, never from LLM JSON."""
    receipt = DecisionReceipt.model_validate(receipt.model_dump(mode="python"))
    if receipt.formal.action.digest != action.digest:
        raise ValueError("receipt belongs to a different action")
    if receipt.receipt_hash != sha256_hex(receipt.unsigned_payload()):
        raise ValueError("receipt hash mismatch")
    formal = receipt.formal
    data: dict[str, Any] = {}
    if any(signal.detector_id == 'runtime-execution-unresolved' and not signal.audit_only
           for signal in formal.decision.detector_signals):
        status, message = 'unknown', '此前已有执行尝试，但结果尚未确认；请人工核对，不要重新提交。'
    elif formal.execution is None:
        status, message = {
            DecisionStatus.DENY: ('blocked', '安全检查未通过，本次请求未执行。'),
            DecisionStatus.REQUIRE_CONFIRMATION: ('needs_confirmation', '需要核对并确认具体参数，尚未执行。'),
            DecisionStatus.REQUIRE_MFA: ('needs_mfa', '需要完成参数确认和多因素验证，尚未执行。'),
            DecisionStatus.ALLOW: ('authorized_not_executed', '授权检查通过，但尚未执行。'),
        }[formal.decision.status]
    elif formal.execution.state is ExecutionState.FAILED:
        status, message = 'unknown', '无法确认操作结果，请人工核对账本；不要自动重试。'
    else:
        try:
            data = validate_tool_result(formal.action, formal.execution.tool_result)
            status, message = 'succeeded', '受信任工具已返回与本次请求一致的完成结果。'
        except (TypeError, ValueError):
            status, message = 'unknown', '工具结果与请求不一致，请人工核对账本；不要自动重试。'
    return PublicOutcome(status=status, action_digest=action.digest,
                         receipt_hash=receipt.receipt_hash, data=data, message=message)
