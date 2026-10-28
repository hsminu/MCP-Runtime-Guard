"""SEQ-02 실행 권한 snapshot의 불변성·I9 경계를 검증한다."""

import inspect

import pytest
from pydantic import ValidationError

from guard_app.domain.enums import CommitOutcome, StorePhase
from guard_app.domain.models import StoreExecutionState
from guard_app.domain.reasons import Reason
from guard_app.ports import StoreLifecycle


def make_state() -> StoreExecutionState:
    return StoreExecutionState(
        run_id="private-run-id",
        phase=StorePhase.FAULTED,
        execution_enabled=False,
        state_revision=1,
        reason=Reason.STATE_COMMIT_FAILED,
        commit_outcome=CommitOutcome.UNKNOWN,
        last_committed_run_state="private-run-state",
        requires_restart=True,
    )


def test_execution_state_is_immutable_and_hides_strings_in_repr():
    state = make_state()
    assert "private-run-id" not in repr(state)
    assert "private-run-state" not in repr(state)
    assert state.run_id == "private-run-id"
    assert state.last_committed_run_state == "private-run-state"
    with pytest.raises(ValidationError):
        state.execution_enabled = True
    with pytest.raises(ValidationError):
        state.model_copy(update={"state_revision": -1})
    with pytest.raises(ValidationError):
        state.model_copy(update={"execution_enabled": 1})


def test_execution_state_serializes_fixed_phase_and_outcome_values():
    state = make_state()
    data = state.model_dump(mode="json")
    assert data["phase"] == "FAULTED"
    assert data["commit_outcome"] == "UNKNOWN"
    assert StoreExecutionState.model_validate_json(state.model_dump_json()) == state
    assert {phase.value for phase in StorePhase} == {
        "CLOSED",
        "MIGRATING",
        "RECOVERING",
        "RECOVERED",
        "READY",
        "FAULTED",
    }
    assert {outcome.value for outcome in CommitOutcome} == {
        "NOT_STARTED",
        "ROLLED_BACK",
        "COMMITTED",
        "UNKNOWN",
    }


def test_lifecycle_exposes_synchronous_control_and_async_close_observation():
    assert not inspect.iscoroutinefunction(StoreLifecycle.execution_state)
    assert not inspect.iscoroutinefunction(StoreLifecycle.halt_execution)
    assert not inspect.iscoroutinefunction(StoreLifecycle.close)
    assert inspect.iscoroutinefunction(StoreLifecycle.wait_closed)
