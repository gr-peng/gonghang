from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .enums import DecisionStatus
from .lineage import LineageAuthority
from .lattice import obligations_for, risk_join, status_for
from .models import DecisionReceipt
from .policy import PolicyEngine
from .receipts import ReceiptIntegrityError, ReceiptLedger
from .tokens import TokenAuthority
from .trust import FactAuthority


@dataclass(frozen=True)
class ReplayResult:
    sequence: int
    valid: bool
    checks: tuple[str, ...]
    limitations: tuple[str, ...]


class ReceiptReplayer:
    """Replays the formal decision conditional on recorded detector outputs.

    Detector inference itself is not recomputed because that requires the exact
    model, weights, runtime, and counterfactual environment. The receipt keeps
    those outputs in a separate internal-evidence layer to avoid confusing a
    policy replay with a proof that the attribution was true.
    """

    def __init__(
        self,
        *,
        fact_authority: FactAuthority,
        token_authority: TokenAuthority,
        lineage_authority: LineageAuthority | None = None,
    ) -> None:
        self.fact_authority = fact_authority
        self.token_authority = token_authority
        self.lineage_authority = lineage_authority

    def replay_one(self, receipt: DecisionReceipt) -> ReplayResult:
        checks: list[str] = []
        failures: list[str] = []
        formal = receipt.formal
        action = formal.action

        if action.digest == formal.decision.action_digest:
            checks.append("action digest matches decision")
        else:
            failures.append("action digest mismatch")

        policy = PolicyEngine.from_mapping(formal.policy_snapshot)
        if policy.spec.digest == formal.policy_evaluation.policy_hash:
            checks.append("policy snapshot hash matches")
        else:
            failures.append("policy snapshot hash mismatch")

        replay_time = formal.decision.evaluated_at
        verified_facts = tuple(
            fact
            for fact in formal.trusted_facts
            if self.fact_authority.verify(fact, now=replay_time)
        )
        if len(verified_facts) == len(formal.trusted_facts):
            checks.append("all recorded trusted facts verify")
        else:
            failures.append("one or more trusted facts fail verification")
        fact_index = self.fact_authority.verified_index(verified_facts, now=replay_time)
        enforce_contracts = bool(
            formal.controller_config.get("enforce_argument_contracts", True)
        )
        enforce_lineage = bool(
            formal.controller_config.get("enforce_authenticated_lineage", False)
        )
        verified_source_ids = None
        if enforce_lineage:
            if self.lineage_authority is None:
                failures.append("authenticated lineage cannot be replayed without its authority")
                verified_source_ids = frozenset()
            else:
                verified_source_ids = self.lineage_authority.verified_source_ids(
                    action.sources,
                    now=replay_time,
                )
                if len(verified_source_ids) == len(action.sources):
                    checks.append("all recorded source-lineage records verify")
                else:
                    failures.append("one or more source-lineage records fail verification")
        reevaluated = policy.evaluate(
            action,
            fact_index,
            enforce_argument_contracts=enforce_contracts,
            verified_source_ids=verified_source_ids,
        )
        if reevaluated == formal.policy_evaluation:
            checks.append("hard policy evaluation reproduces exactly")
        else:
            failures.append("hard policy evaluation differs")

        verified_tokens = tuple(
            token
            for token in formal.verified_tokens
            if self.token_authority.verify(token, action, now=replay_time)
        )
        if len(verified_tokens) == len(formal.verified_tokens):
            checks.append("all recorded obligation tokens verify")
        else:
            failures.append("one or more obligation tokens fail verification")

        if tuple(formal.decision.detector_signals) == tuple(receipt.internal.signals):
            checks.append("internal-evidence layer matches recorded decision inputs")
        else:
            failures.append("detector signal layers disagree")

        active_levels = [
            signal.level for signal in receipt.internal.signals if not signal.audit_only
        ]
        final_level = risk_join(reevaluated.base_level, *active_levels)
        discharged = frozenset(token.obligation for token in verified_tokens)
        required = obligations_for(final_level)
        replay_status = status_for(final_level, discharged)
        decision = formal.decision
        if (
            final_level == decision.final_level
            and required == decision.required_obligations
            and discharged == decision.discharged_obligations
            and required - discharged == decision.missing_obligations
            and replay_status == decision.status
        ):
            checks.append("lattice join, obligations, and final status reproduce")
        else:
            failures.append("final decision does not reproduce")

        if formal.execution is not None:
            if formal.execution.action_digest == action.digest:
                checks.append("execution record is bound to the same action")
            else:
                failures.append("execution/action digest mismatch")
            if decision.status is not DecisionStatus.ALLOW:
                failures.append("execution exists for a non-allow decision")

        return ReplayResult(
            sequence=receipt.sequence,
            valid=not failures,
            checks=tuple(checks + [f"FAIL: {item}" for item in failures]),
            limitations=(
                "replay is conditional on the recorded detector outputs",
                "replay proves rule execution under recorded facts, not real-world fact truth",
            ),
        )

    def replay_chain(self, receipts: Iterable[DecisionReceipt]) -> tuple[ReplayResult, ...]:
        materialized = tuple(receipts)
        ReceiptLedger.verify_chain(materialized)
        return tuple(self.replay_one(receipt) for receipt in materialized)

    def replay_file(self, path: str) -> tuple[ReplayResult, ...]:
        receipts = ReceiptLedger.load_jsonl(path)
        try:
            return self.replay_chain(receipts)
        except ReceiptIntegrityError:
            raise
