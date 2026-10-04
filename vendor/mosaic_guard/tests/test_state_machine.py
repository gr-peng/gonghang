from __future__ import annotations

import pytest

from scns_guard.enums import ExecutionState
from scns_guard.state_machine import AuthorizationStateMachine, InvalidTransition


def test_state_machine_rejects_execute_without_authorization() -> None:
    machine = AuthorizationStateMachine()
    with pytest.raises(InvalidTransition):
        machine.transition(ExecutionState.EXECUTED)
    machine.transition(ExecutionState.EVALUATED)
    machine.transition(ExecutionState.AUTHORIZED)
    machine.transition(ExecutionState.EXECUTED)
    assert machine.state is ExecutionState.EXECUTED
