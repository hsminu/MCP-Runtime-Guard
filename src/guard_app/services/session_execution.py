"""SEQ-02: synchronous authority checks, independent of the SQLite queue."""

from collections.abc import Callable

from guard_app.domain.models import StoreExecutionState


class ExecutionGate:
    def __init__(self, read: Callable[[], StoreExecutionState]) -> None:
        self._read = read
        self._revision = read().state_revision
        self._refused = False

    def allowed(self) -> bool:
        state = self._read()
        self._refused |= not state.execution_enabled or state.state_revision != self._revision
        return not self._refused
