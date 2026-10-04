"""Runtime-owned, actor-scoped safety lock for the single-process prototype."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock

from .enums import DecisionStatus, ExecutionState, RiskLevel
from .models import ActionProposal, DecisionReceipt, DetectorEvidence, DetectorSignal


class SafetyCircuitBreaker:
    """Counts execution failures and denied execution attempts since manual reset.

    Query/preview decisions and pending obligations never increment the counters.
    Reset is an operator-only host API, deliberately absent from model tools.
    This class does not authenticate operators or persist state across restarts.
    """

    detector_id = "runtime-safety-circuit"

    def __init__(self, *, failure_threshold: int = 3, denial_threshold: int = 5,
                 state_store=None, namespace: str = 'safety'):
        for threshold in (failure_threshold, denial_threshold):
            if type(threshold) is not int or threshold <= 0:
                raise ValueError("circuit thresholds must be positive integers")
        self.failure_threshold = failure_threshold
        self.denial_threshold = denial_threshold
        self.lock = RLock()
        self._states: dict[str, dict] = {}
        self._events: list[dict] = []
        self.state_store = state_store
        self.namespace = namespace
        if state_store is not None:
            state_store.configure_circuit(namespace, failure_threshold, denial_threshold)

    def _state(self, actor_id: str) -> dict:
        return self._states.setdefault(actor_id, {"failures": 0, "denials": 0, "locked": False})

    def snapshot(self, actor_id: str) -> dict:
        if self.state_store is not None:
            return self.state_store.circuit_snapshot(self.namespace, actor_id)
        with self.lock:
            return {**self._state(actor_id), "failure_threshold": self.failure_threshold,
                    "denial_threshold": self.denial_threshold}

    @property
    def events(self) -> tuple[dict, ...]:
        if self.state_store is not None:
            return tuple(event for event in self.state_store.events() if event.get('namespace') == self.namespace)
        with self.lock:
            return tuple(deepcopy(self._events))

    def assess(self, action: ActionProposal, *, context: dict) -> DetectorSignal | None:
        del context
        state = self.snapshot(action.actor_id)
        if not state["locked"]:
            return None
        return DetectorSignal(
            detector_id=self.detector_id, detector_version="1", level=RiskLevel.DENY,
            score=1.0, reason="actor safety lock requires trusted operator review",
            evidence=(DetectorEvidence(details=state),),
        )

    def observe(self, receipt: DecisionReceipt) -> None:
        if self.state_store is not None:
            self.state_store.observe_circuit(self.namespace, receipt)
            return
        with self.lock:
            actor_id = receipt.formal.action.actor_id
            state = self._state(actor_id)
            if state["locked"]:
                return
            execution = receipt.formal.execution
            fact_failure = any(s.detector_id == "runtime-fact-supplier-error"
                               for s in receipt.formal.decision.detector_signals)
            if fact_failure or (execution is not None and execution.state is ExecutionState.FAILED):
                state["failures"] += 1
                event = "execution_failure"
            elif receipt.formal.decision.status is DecisionStatus.DENY:
                state["denials"] += 1
                event = "denied_attempt"
            else:
                return
            state["locked"] = (state["failures"] >= self.failure_threshold
                               or state["denials"] >= self.denial_threshold)
            self._events.append({"event": event, "actor_id": actor_id, **state,
                                 "receipt_hash": receipt.receipt_hash,
                                 "at": datetime.now(timezone.utc).isoformat()})

    def reset(self, actor_id: str, *, operator_id: str, reason: str) -> None:
        if self.state_store is not None:
            self.state_store.reset_circuit(self.namespace, actor_id, operator_id=operator_id, reason=reason)
            return
        if not operator_id.strip() or not reason.strip():
            raise ValueError("operator identity and review reason are required")
        with self.lock:
            before = self.snapshot(actor_id)
            self._states[actor_id] = {"failures": 0, "denials": 0, "locked": False}
            self._events.append({"event": "reset", "actor_id": actor_id,
                                 "operator_id": operator_id, "reason": reason, "before": before,
                                 "at": datetime.now(timezone.utc).isoformat()})
