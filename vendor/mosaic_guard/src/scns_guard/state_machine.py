from __future__ import annotations

from dataclasses import dataclass

from .enums import ExecutionState


class InvalidTransition(RuntimeError):
    pass


_ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PROPOSED: frozenset({ExecutionState.EVALUATED}),
    ExecutionState.EVALUATED: frozenset(
        {
            ExecutionState.OBLIGATION_PENDING,
            ExecutionState.AUTHORIZED,
            ExecutionState.DENIED,
        }
    ),
    ExecutionState.OBLIGATION_PENDING: frozenset(
        {ExecutionState.EVALUATED, ExecutionState.DENIED}
    ),
    ExecutionState.AUTHORIZED: frozenset(
        {ExecutionState.EXECUTED, ExecutionState.FAILED}
    ),
    ExecutionState.EXECUTED: frozenset(),
    ExecutionState.DENIED: frozenset(),
    ExecutionState.FAILED: frozenset(),
}


@dataclass
class AuthorizationStateMachine:
    state: ExecutionState = ExecutionState.PROPOSED

    def transition(self, target: ExecutionState) -> None:
        if target not in _ALLOWED[self.state]:
            raise InvalidTransition(f"illegal transition: {self.state.value} -> {target.value}")
        self.state = target
