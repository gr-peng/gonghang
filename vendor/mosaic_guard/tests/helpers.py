from __future__ import annotations

from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.models import SourceRef
from scns_guard.simulator import ToyTransferAgent


def make_trace(
    *,
    amount_minor: int = 20_000,
    to_account: str = "acct-alice",
    extra_kind: SourceKind | None = None,
    extra_trust: SourceTrust = SourceTrust.EXTERNAL,
    extra_payload: dict | None = None,
    trace_id: str = "test-trace",
) -> AgentTrace:
    user = SourceMessage(
        source=SourceRef(
            source_id=f"user-{trace_id}",
            kind=SourceKind.USER,
            trust=SourceTrust.USER,
        ),
        text="user transfer request",
        payload={
            "action_type": "transfer",
            "from_account": "acct-user",
            "to_account": to_account,
            "amount_minor": amount_minor,
            "memo": "test",
        },
    )
    messages = [user]
    if extra_kind is not None:
        messages.append(
            SourceMessage(
                source=SourceRef(
                    source_id=f"extra-{trace_id}",
                    kind=extra_kind,
                    trust=extra_trust,
                ),
                text="extra source",
                payload=extra_payload or {},
            )
        )
    return AgentTrace(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        actor_id="user-1",
        messages=tuple(messages),
    )


def action_from_trace(trace: AgentTrace):
    action = ToyTransferAgent().propose(
        trace,
        disabled_source_ids=frozenset(),
        seed=0,
    )
    assert action is not None
    return action
