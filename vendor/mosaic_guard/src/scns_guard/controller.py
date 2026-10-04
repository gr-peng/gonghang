from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import nullcontext
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol

from .enums import DecisionStatus, ExecutionState, ObligationType, RiskLevel
from .lineage import LineageAuthority
from .lattice import obligations_for, risk_join, status_for
from .models import (
    ActionProposal,
    AuthorizationDecision,
    DecisionReceipt,
    DetectorEvidence,
    DetectorSignal,
    ExecutionRecord,
    FormalReceiptLayer,
    InternalEvidenceLayer,
    ObligationToken,
    TrustedFact,
)
from .policy import PolicyEngine
from .circuit import SafetyCircuitBreaker
from .outcomes import validate_tool_result
from .receipts import ReceiptLedger
from .state_machine import AuthorizationStateMachine
from .tokens import TokenAuthority
from .execution_store import RequestState
from .trust import FactAuthority


class RuntimeDetector(Protocol):
    detector_id: str

    def assess(
        self,
        action: ActionProposal,
        *,
        context: dict[str, Any],
    ) -> DetectorSignal | Sequence[DetectorSignal] | None: ...


class ActionTool(Protocol):
    name: str

    def snapshot(self) -> dict[str, Any]: ...

    def execute(self, action: ActionProposal) -> dict[str, Any]: ...


class SafetyController:
    """Sole authorization point for structured LLM actions.

    Detector outputs are joined with the hard-policy risk. The API never exposes
    a detector path that can reduce the base risk or discharge an obligation.
    """

    def __init__(
        self,
        *,
        policy: PolicyEngine,
        fact_authority: FactAuthority,
        token_authority: TokenAuthority,
        detectors: Iterable[RuntimeDetector] = (),
        receipt_ledger: ReceiptLedger | None = None,
        enforce_argument_contracts: bool = True,
        lineage_authority: LineageAuthority | None = None,
        circuit_breaker: SafetyCircuitBreaker | None = None,
    ) -> None:
        self.policy = policy
        self.fact_authority = fact_authority
        self.token_authority = token_authority
        self.detectors = tuple(detectors)
        self.receipt_ledger = receipt_ledger or ReceiptLedger()
        self.enforce_argument_contracts = enforce_argument_contracts
        self.lineage_authority = lineage_authority
        self.circuit_breaker = circuit_breaker
        if (circuit_breaker is not None and circuit_breaker.state_store is not None
                and circuit_breaker.state_store is not token_authority.state_store):
            raise ValueError('persistent circuit and tokens must share the same store')
        self._runtime_lock = circuit_breaker.lock if circuit_breaker is not None else RLock()

    def _collect_signals(
        self,
        action: ActionProposal,
        context: dict[str, Any],
        supplied: Iterable[DetectorSignal],
    ) -> tuple[DetectorSignal, ...]:
        signals = list(supplied)
        if self.circuit_breaker is not None:
            circuit_signal = self.circuit_breaker.assess(action, context={})
            if circuit_signal is not None:
                signals.append(circuit_signal)
        detector_context = dict(context)
        detector_context.setdefault("policy", self.policy)
        for detector in self.detectors:
            result = detector.assess(action, context=detector_context)
            if result is None:
                continue
            if isinstance(result, DetectorSignal):
                signals.append(result)
            else:
                signals.extend(result)
        return tuple(signals)

    def _evaluate(
        self,
        action: ActionProposal,
        facts: Sequence[TrustedFact],
        tokens: Sequence[ObligationToken],
        *,
        detector_context: dict[str, Any] | None = None,
        supplied_signals: Iterable[DetectorSignal] = (),
        now: datetime | None = None,
    ) -> tuple[
        AuthorizationDecision,
        tuple[TrustedFact, ...],
        tuple[ObligationToken, ...],
        tuple[DetectorSignal, ...],
    ]:
        now = now or datetime.now(timezone.utc)
        verified_facts = tuple(fact for fact in facts if self.fact_authority.verify(fact, now=now))
        fact_index = self.fact_authority.verified_index(verified_facts, now=now)
        verified_source_ids = (
            None
            if self.lineage_authority is None
            else self.lineage_authority.verified_source_ids(action.sources, now=now)
        )
        policy_evaluation = self.policy.evaluate(
            action,
            fact_index,
            enforce_argument_contracts=self.enforce_argument_contracts,
            verified_source_ids=verified_source_ids,
        )
        signals = self._collect_signals(
            action,
            detector_context or {},
            supplied_signals,
        )
        active_levels = [signal.level for signal in signals if not signal.audit_only]
        final_level = risk_join(policy_evaluation.base_level, *active_levels)

        verified_tokens = tuple(
            token for token in tokens if self.token_authority.verify_for_use(token, action, now=now)
        )
        discharged = frozenset(token.obligation for token in verified_tokens)
        required = obligations_for(final_level)
        missing = required - discharged
        status = status_for(final_level, discharged)

        decision = AuthorizationDecision(
            action_digest=action.digest,
            base_level=policy_evaluation.base_level,
            final_level=final_level,
            status=status,
            required_obligations=required,
            discharged_obligations=discharged,
            missing_obligations=missing,
            policy_evaluation=policy_evaluation,
            detector_signals=signals,
            fact_ids=tuple(fact.fact_id for fact in verified_facts),
            token_ids=tuple(token.token_id for token in verified_tokens),
            evaluated_at=now,
        )
        return decision, verified_facts, verified_tokens, signals

    def decide(
        self,
        action: ActionProposal,
        *,
        facts: Sequence[TrustedFact],
        tokens: Sequence[ObligationToken] = (),
        detector_context: dict[str, Any] | None = None,
        supplied_signals: Iterable[DetectorSignal] = (),
        now: datetime | None = None,
    ):
        decision, verified_facts, verified_tokens, signals = self._evaluate(
            action,
            facts,
            tokens,
            detector_context=detector_context,
            supplied_signals=supplied_signals,
            now=now,
        )
        formal = FormalReceiptLayer(
            action=action.model_copy(deep=True),
            trusted_facts=verified_facts,
            verified_tokens=verified_tokens,
            policy_snapshot=self.policy.snapshot(),
            controller_config={
                "enforce_argument_contracts": self.enforce_argument_contracts,
                "enforce_authenticated_lineage": self.lineage_authority is not None,
            },
            policy_evaluation=decision.policy_evaluation,
            decision=decision,
        )
        return self.receipt_ledger.append(
            formal,
            InternalEvidenceLayer(signals=signals),
        )

    def execute(
        self,
        action: ActionProposal,
        *,
        fact_supplier: Callable[[], Sequence[TrustedFact]],
        tool: ActionTool,
        tokens: Sequence[ObligationToken] = (),
        detector_context: dict[str, Any] | None = None,
        supplied_signals: Iterable[DetectorSignal] = (),
        now: datetime | None = None,
        request_id: str | None = None,
        verify_principal: Callable[[], object] | None = None,
    ) -> DecisionReceipt:
        """Re-evaluate inside the adapter's optional transaction boundary.

        BankLedger provides a shared lock. Other adapters without transaction()
        retain fresh-fact checks but do not gain atomic backend authorization.
        """
        action = action.model_copy(deep=True)
        store = self.token_authority.state_store
        contract = self.policy.contract_for(action.action_type)
        requires_reservation = action.write_action or contract is None or contract.write_action
        key = (store.request_key(self.token_authority.issuer, action,
                                 action.action_id if request_id is None else request_id)
               if requires_reservation else None)
        transaction = getattr(tool, "transaction", nullcontext)
        with self._runtime_lock:
            if key is not None:
                current = store.bind(key, action, tool.name)
                if current.status != 'pending':
                    return self._existing_request(action, key, current, now=now)
            with transaction():
                # Another controller may have finished while this one waited
                # for the backend transaction. Re-read before evaluating tokens.
                if key is not None:
                    current = store.bind(key, action, tool.name)
                    if current.status != 'pending':
                        return self._existing_request(action, key, current, now=now)
                return self._execute_locked(
                    action, fact_supplier=fact_supplier, tool=tool, tokens=tokens,
                    detector_context=detector_context, supplied_signals=supplied_signals, now=now,
                    request_key=key,
                    verify_principal=verify_principal,
                )

    def _existing_request(
        self, action: ActionProposal, key: str, current: RequestState, *, now: datetime | None,
    ) -> DecisionReceipt:
        if current.status == 'finished' and current.receipt is not None:
            # Historical result, not a fresh authorization or another dispatch.
            return current.receipt
        unresolved = current.status == 'started'
        signal = DetectorSignal(
            detector_id='runtime-execution-unresolved' if unresolved else 'runtime-request-rejected',
            detector_version='1', level=RiskLevel.DENY, score=1.0,
            reason=('previous execution attempt has no terminal result; do not redispatch'
                    if unresolved else f'request reservation rejected: {current.status}'),
            evidence=(DetectorEvidence(details={'request_key': key, 'state': current.status}),),
        )
        return self.decide(action, facts=(), supplied_signals=(signal,), now=now)

    def _execute_locked(
        self, action: ActionProposal, *, fact_supplier: Callable[[], Sequence[TrustedFact]],
        tool: ActionTool, tokens: Sequence[ObligationToken],
        detector_context: dict[str, Any] | None, supplied_signals: Iterable[DetectorSignal],
        now: datetime | None, request_key: str | None,
        verify_principal: Callable[[], object] | None,
    ) -> DecisionReceipt:

        machine = AuthorizationStateMachine()
        immutable_action = action.model_copy(deep=True)
        supplied_signals = tuple(supplied_signals)
        if verify_principal is not None:
            try:
                verify_principal()
            except (ValueError, PermissionError):
                # Expiry/revocation after waiting is not a bank-service failure
                # and must not poison a user's other sessions via the circuit.
                return self._existing_request(immutable_action, request_key or '',
                                              RequestState('identity_unavailable'), now=now)
        try:
            facts = tuple(fact_supplier())
        except Exception as exc:
            facts = ()
            supplied_signals += (DetectorSignal(
                detector_id="runtime-fact-supplier-error", detector_version="1",
                level=RiskLevel.DENY, score=1.0,
                reason=f"fresh trusted facts unavailable: {type(exc).__name__}",
            ),)
        decision, verified_facts, verified_tokens, signals = self._evaluate(
            immutable_action,
            facts,
            tokens,
            detector_context=detector_context,
            supplied_signals=supplied_signals,
            now=now,
        )
        machine.transition(ExecutionState.EVALUATED)

        config = {
            "enforce_argument_contracts": self.enforce_argument_contracts,
            "enforce_authenticated_lineage": self.lineage_authority is not None,
            "execution_protocol": "reserve-before-dispatch-v1",
            "request_key": request_key,
        }
        authorization = FormalReceiptLayer(
            action=immutable_action, trusted_facts=verified_facts, verified_tokens=verified_tokens,
            policy_snapshot=self.policy.snapshot(), controller_config=config,
            policy_evaluation=decision.policy_evaluation, decision=decision,
        )

        def authorization_current() -> bool:
            # Backend transaction() pins state, not wall time. Reverify the
            # exact signed evidence after each potentially blocking boundary.
            # Historical tokens are checked cryptographically here: reservation
            # legitimately makes them unavailable for any *other* execution.
            if self.policy.spec.digest != decision.policy_evaluation.policy_hash:
                return False
            # Complete all potentially blocking state checks before taking the
            # expiry timestamp. Re-authenticate the principal after those waits.
            if self.token_authority.state_store.session_revoked(action.actor_id, action.session_id):
                return False
            if self.circuit_breaker is not None and self.circuit_breaker.snapshot(action.actor_id)['locked']:
                return False
            if verify_principal is not None:
                try:
                    verify_principal()
                except (ValueError, PermissionError):
                    return False
            stamp = now or datetime.now(timezone.utc)
            if not all(self.fact_authority.verify(fact, now=stamp) for fact in verified_facts):
                return False
            if not all(self.token_authority.verify(token, immutable_action, now=stamp)
                       for token in verified_tokens
                       if token.obligation in decision.required_obligations):
                return False
            return True

        execution: ExecutionRecord | None = None
        if decision.status is DecisionStatus.ALLOW:
            if request_key is not None:
                used_tokens = tuple(token for token in verified_tokens
                                    if token.obligation in decision.required_obligations)
                reserved = self.token_authority.state_store.reserve(
                    request_key, immutable_action, tool.name, tokens=used_tokens, authorization=authorization,
                    verify_token=lambda token: self.token_authority.verify(token, immutable_action, now=now),
                    circuit_namespace=(self.circuit_breaker.namespace if self.circuit_breaker is not None
                                       and self.circuit_breaker.state_store is not None else None),
                    verify_principal=verify_principal,
                    verify_authorization=authorization_current,
                )
                if reserved.status != 'reserved':
                    return self._existing_request(immutable_action, request_key, reserved, now=now)
            elif verify_principal is not None:
                # Reads have no write reservation but still require a live identity.
                try:
                    verify_principal()
                except (ValueError, PermissionError):
                    return self._existing_request(immutable_action, '', RequestState('identity_unavailable'), now=now)
            machine.transition(ExecutionState.AUTHORIZED)
            before = {}
            try:
                before = tool.snapshot()
                # Snapshot and durable reservation can both block. Nothing that
                # can wait on an external adapter goes between this check and
                # dispatch. A failure after reservation remains a non-retryable
                # attempt with a conservative unknown result.
                if verify_principal is not None:
                    verify_principal()
                if not authorization_current():
                    raise PermissionError('authorization evidence expired or changed before dispatch')
                result = tool.execute(immutable_action)
                validate_tool_result(immutable_action, result)
                after = tool.snapshot()
                machine.transition(ExecutionState.EXECUTED)
                execution = ExecutionRecord(
                    action_digest=immutable_action.digest,
                    state=ExecutionState.EXECUTED,
                    decision_id=decision.decision_id,
                    tool_name=tool.name,
                    before_state=before,
                    after_state=after,
                    tool_result=result,
                )
            except Exception as exc:
                try:
                    after = tool.snapshot()
                except Exception as snapshot_exc:
                    after = {"snapshot_unavailable": type(snapshot_exc).__name__}
                machine.transition(ExecutionState.FAILED)
                execution = ExecutionRecord(
                    action_digest=immutable_action.digest,
                    state=ExecutionState.FAILED,
                    decision_id=decision.decision_id,
                    tool_name=tool.name,
                    before_state=before,
                    after_state=after,
                    tool_result={"error": type(exc).__name__, "message": str(exc)},
                )
        elif decision.status is DecisionStatus.DENY:
            machine.transition(ExecutionState.DENIED)
        else:
            machine.transition(ExecutionState.OBLIGATION_PENDING)

        formal = FormalReceiptLayer(
            action=immutable_action,
            trusted_facts=verified_facts,
            verified_tokens=verified_tokens,
            policy_snapshot=self.policy.snapshot(),
            controller_config=config,
            policy_evaluation=decision.policy_evaluation,
            decision=decision,
            execution=execution,
        )
        receipt = self.receipt_ledger.append(
            formal,
            InternalEvidenceLayer(signals=signals),
        )
        if request_key is not None and execution is not None:
            self.token_authority.state_store.finish(request_key, receipt)
        if self.circuit_breaker is not None:
            self.circuit_breaker.observe(receipt)
        return receipt
