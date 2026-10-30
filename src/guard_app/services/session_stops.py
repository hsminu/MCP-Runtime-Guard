"""Where a session's end is recorded (STOP-01).

With Guard assembled, Guard's system channel stops the Task under its Task lock
and audits it. A never writes HALTED itself then: a competing write would fail
Guard's commit and halt every Task. Without Guard nothing else stops the Store
row, so it is halted here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from guard_app.domain.contracts import TaskIdentity
from guard_app.domain.enums import SystemStopReason, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.guard import SystemControl, SystemControlError
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_cleanup import BackgroundWork

# A session stop code -> STOP-01 reason (PR #28 review mapping, agreed by B).
# A token refresh conflict arrives as `unauthorized` like any auth failure, so
# AUTH_REFRESH_CONFLICT is not chosen until C's T46 finds a signal.
_STOP_REASONS = {
    "CODEX_L3_VIOLATION": SystemStopReason.L3_VIOLATION,
    "CODEX_WATCHDOG_TIMEOUT": SystemStopReason.WATCHDOG_EXCEEDED,
    "CODEX_AUTH_FAILED": SystemStopReason.AUTH_FAILED,
    "CODEX_USAGE_LIMIT": SystemStopReason.USAGE_LIMIT,
    "CODEX_TOOL_CALL_INVALID": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_SERVER_REQUEST_REFUSED": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_ANSWER_TOO_LARGE": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_INPUT_INVALID": SystemStopReason.PROTOCOL_FAILED,
}


def stop_reason(code: str) -> SystemStopReason:
    """Process exit, turn/start failures and anything unlisted count as PROCESS_FAILED."""
    return _STOP_REASONS.get(code, SystemStopReason.PROCESS_FAILED)


class StopChannel:
    def __init__(
        self,
        store: Store,
        system: SystemControl | None,
        admission: SessionAdmission,
        work: BackgroundWork,
    ) -> None:
        self._store = store
        self._system = system
        self._admission = admission
        self._work = work

    def replace_system(self, system: SystemControl | None) -> None:
        """Only after the previous channel's stop records have settled."""
        self._system = system

    async def halt_row(self, task_id: str) -> None:
        """Write HALTED to the Store row; only where Guard does not stop the Task."""
        state = await self._store.load_task(task_id)
        if state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            return
        updated = await self._store.set_task_status(
            task_id,
            TaskStatus.HALTED,
            expected_row_revision=state.row_revision,
            at_utc=datetime.now(UTC),
            reason=Reason.TASK_NOT_RUNNING,
        )
        if (
            updated.task_id != task_id
            or updated.epoch != state.epoch
            or updated.status is not TaskStatus.HALTED
        ):
            raise SessionRuntimeError("SESSION_STOP_UNCONFIRMED")

    async def stop(
        self, identity: TaskIdentity, code: str, settled: Callable[[bool], None]
    ) -> None:
        """STOP-01: Guard stops the Task; the session is closed after this call.

        `stop_task` blocks dispatch before it waits for the Task lock, so an
        in-flight (shielded) submit is refused at its next check. Its Store write
        and audit happen under that lock. The wait runs in the background and
        `settled(unconfirmed)` runs when it ends. If it fails, Guard is halted
        globally and the Task must show that the stop was not confirmed rather
        than applied.
        """
        system = self._system
        if system is None:
            await self.halt_row(identity.task_id)
            return

        async def stop() -> None:
            unconfirmed = False
            try:
                await system.stop_task(identity, stop_reason(code))
            except (SystemControlError, StoreError):
                unconfirmed = True
                raise
            finally:
                settled(unconfirmed)

        self._work.start(stop())
        await asyncio.sleep(0)  # let stop_task block dispatch now

    def record_user_input(self, identity: TaskIdentity) -> None:
        """Audit that Codex asked the user (answered empty); never its text."""
        system = self._system
        if system is None or self._admission.closed:
            return
        self._work.start(system.record_request_user_input(identity))
