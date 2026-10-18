"""A-local injectable Codex composition pending B/C agreement.

C supplies startup L2 evidence and secure process/path/cleanup capabilities;
A composes those with its JSON-RPC and Guard relay into the session factory.
Missing capabilities reject before Store Task creation. No launcher, login
persistence, model API, Guard protocol or replacement B port is implemented here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.bridge import (
    BackendBindings,
    ControlAction,
    SessionOptions,
    SessionStartError,
    TaskProjection,
)
from guard_app.services.runner import CodexSession, CodexTaskRunner
from guard_app.storage.sqlite_store import SQLiteStore

# A-local proposal only: A's factory must use C's executable/config/model hash
# verification and separate app-server, then perform account/read and relay setup.
# The L2 callback runs once per app lifetime and must return exactly True.
type CodexSessionFactory = Callable[[TaskState, SessionOptions], Awaitable[CodexSession]]
type StartupL2Check = Callable[[], Awaitable[bool]]

SESSION_CLEANUP_TIMEOUT_S = 0.5


class SessionRuntimeError(SessionStartError):
    pass


class SessionRuntime:
    def __init__(
        self,
        store: Store,
        *,
        session_factory: CodexSessionFactory | None = None,
        startup_l2_check: StartupL2Check | None = None,
        close_store: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self._session_factory = session_factory
        self._startup_l2_check = startup_l2_check
        self._startup_lock = asyncio.Lock()
        self._l2_ready = False
        self._close_store = close_store
        self._runners: dict[str, CodexTaskRunner] = {}
        self._sessions: dict[str, CodexSession] = {}
        self._options: dict[str, SessionOptions] = {}
        self._codes: dict[str, str] = {}
        self._closed = False
        self._creating: set[asyncio.Task[object]] = set()
        self._defer_store_close = False
        self._cleanup_unconfirmed = False

    def runner_for_task(self, task_id: str) -> CodexTaskRunner:
        if self._closed or task_id not in self._runners:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        return self._runners[task_id]

    async def _check_startup(self) -> None:
        async with self._startup_lock:
            if self._closed:
                raise SessionRuntimeError("SESSION_UNAVAILABLE")
            if not self._l2_ready:
                if self._startup_l2_check is None:
                    raise SessionRuntimeError("CODEX_L2_UNAVAILABLE")
                if await self._startup_l2_check() is not True:
                    raise SessionRuntimeError("CODEX_L2_FAILED")
                self._l2_ready = True
            if self._closed:
                raise SessionRuntimeError("SESSION_UNAVAILABLE")

    async def create_session(self, options: SessionOptions) -> TaskProjection:
        if self._closed or type(options) is not SessionOptions:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        current = asyncio.current_task()
        if current is None:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        self._creating.add(current)
        try:
            return await self._create_session(options)
        finally:
            self._creating.discard(current)
            if self._defer_store_close and not self._creating:
                self._close_store_once()

    async def _create_session(self, options: SessionOptions) -> TaskProjection:
        await self._check_startup()
        if self._session_factory is None:
            raise SessionRuntimeError("CODEX_SESSION_UNAVAILABLE")
        state = TaskState(
            task_id=f"session-{uuid4().hex}",
            epoch=1,
            profile_id="codex-pending-profile",
            policy_version="codex-pending-v1",
            status=TaskStatus.RUNNING,
        )
        # No Store RUNNING row exists while C's secure launch is unconfirmed.
        session = await self._session_factory(state, options)
        if any(session is existing for existing in self._sessions.values()):
            # Do not close another Task's handle when a factory violates isolation.
            raise SessionRuntimeError("CODEX_SESSION_INVALID")
        created = adopted = False
        try:
            if not all(
                callable(getattr(session, name, None)) for name in ("send", "interrupt", "aclose")
            ):
                raise SessionRuntimeError("CODEX_SESSION_INVALID")
            if self._closed:
                raise SessionRuntimeError("SESSION_UNAVAILABLE")
            await self.store.create_task(state, created_at_utc=datetime.now(UTC))
            created = True
            confirmed = await self.store.load_task(state.task_id)
            if confirmed != state:
                raise StoreError()
            if self._closed:
                raise SessionRuntimeError("SESSION_UNAVAILABLE")
            runner = CodexTaskRunner(
                state.task_id,
                state.epoch,
                session,
                lambda code: self._fail_session(state.task_id, code),
            )
            self._runners[state.task_id] = runner
            self._sessions[state.task_id] = session
            self._options[state.task_id] = options
            self._codes[state.task_id] = "READY_CODEX"
            snapshot = await self.snapshot(state.task_id)
            if self._closed:
                raise SessionRuntimeError("SESSION_UNAVAILABLE")
            adopted = True
            return snapshot
        finally:
            if not adopted:
                discarded = self._runners.pop(state.task_id, None)
                if discarded is not None:
                    discarded.close()
                self._sessions.pop(state.task_id, None)
                self._options.pop(state.task_id, None)
                self._codes.pop(state.task_id, None)
                try:
                    close = getattr(session, "aclose", None)
                    if callable(close) and not await self._bounded_cleanup(close):
                        self._cleanup_unconfirmed = True
                        raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
                finally:
                    if created:
                        await self._halt_store_task(state.task_id)

    async def snapshot(self, task_id: str) -> TaskProjection:
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != runner.epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        options = self._options[task_id]
        return TaskProjection(
            task_id,
            state.epoch,
            state.status,
            model_mode=options.mode,
            model_id=options.model,
            model_ready=self._codes[task_id] == "READY_CODEX"
            and state.status is TaskStatus.RUNNING,
            connection_code=self._codes[task_id],
            tools_available=False,
            can_advance=runner.can_advance,
            can_accept_message=runner.can_accept_message and state.status is TaskStatus.RUNNING,
            runner_state=runner.runtime_status,
        )

    async def _halt_store_task(self, task_id: str) -> None:
        state = await self.store.load_task(task_id)
        if state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            return
        updated = await self.store.set_task_status(
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

    async def _fail_session(self, task_id: str, code: str) -> None:
        self._codes[task_id] = code
        runner = self._runners[task_id]
        runner.close()
        try:
            await self._halt_store_task(task_id)
        finally:
            await self._close_session(task_id, interrupt=True)

    async def stop_session(self, task_id: str, epoch: int, action: ControlAction) -> None:
        if action not in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            raise SessionRuntimeError("CONTROL_UNAVAILABLE")
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != epoch or runner.epoch != epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        runner.close()  # Invalidate late responses without waiting for the send lock.
        try:
            await self._halt_store_task(task_id)
        finally:
            await self._close_session(task_id, interrupt=True)

    @staticmethod
    async def _bounded_cleanup(action: Callable[[], Awaitable[None]]) -> bool:
        async def invoke() -> None:
            await action()

        operation = asyncio.create_task(invoke())
        operation.add_done_callback(SessionRuntime._consume_cleanup)
        try:
            done, _ = await asyncio.wait({operation}, timeout=SESSION_CLEANUP_TIMEOUT_S)
            return bool(done) and not operation.cancelled() and operation.exception() is None
        finally:
            if not operation.done():
                operation.cancel()

    @staticmethod
    def _consume_cleanup(operation: asyncio.Task[None]) -> None:
        if not operation.cancelled():
            operation.exception()

    async def _close_session(self, task_id: str, *, interrupt: bool = False) -> None:
        session = self._sessions.pop(task_id, None)
        if session is not None:
            interrupted = closed = False
            try:
                interrupted = not interrupt or await self._bounded_cleanup(session.interrupt)
            finally:
                closed = await self._bounded_cleanup(session.aclose)
            if not interrupted or not closed:
                self._cleanup_unconfirmed = True
                self._sessions[task_id] = session
                self._codes[task_id] = "CODEX_CLEANUP_UNCONFIRMED"
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Bridge normally cancels creation first; direct callers get the same
        # ownership rule so no late factory result can outlive the Store.
        creating = tuple(task for task in self._creating if task is not asyncio.current_task())
        for task in creating:
            task.cancel()
        unfinished_creation = False
        if creating:
            _, pending = await asyncio.wait(creating, timeout=SESSION_CLEANUP_TIMEOUT_S)
            unfinished_creation = bool(pending)
            self._defer_store_close = unfinished_creation
        try:
            for runner in self._runners.values():
                runner.close()
            # Start every cleanup independently so a stalled Task cannot stop
            # another session from receiving interrupt/close.
            results = await asyncio.gather(
                *(
                    self._close_session(task_id, interrupt=True)
                    for task_id in tuple(self._sessions)
                ),
                return_exceptions=True,
            )
            if (
                self._cleanup_unconfirmed
                or unfinished_creation
                or any(isinstance(result, BaseException) for result in results)
            ):
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        finally:
            self._runners.clear()
            self._options.clear()
            self._codes.clear()
            if not self._defer_store_close or not self._creating:
                self._close_store_once()

    def _close_store_once(self) -> None:
        close, self._close_store = self._close_store, None
        if close is not None:
            close()

    def bindings(self) -> BackendBindings:
        return BackendBindings(
            self.runner_for_task,
            None,
            close=self.close,
            snapshot=self.snapshot,
            create_session=self.create_session,
            stop_session=self.stop_session,
        )


async def bootstrap_product(
    db_path: str,
    *,
    session_factory: CodexSessionFactory | None = None,
    startup_l2_check: StartupL2Check | None = None,
) -> BackendBindings:
    store = SQLiteStore(db_path, audit_id_factory=lambda: f"audit-{uuid4().hex}")
    runtime = SessionRuntime(
        store,
        session_factory=session_factory,
        startup_l2_check=startup_l2_check,
        close_store=store.close,
    )
    adopted = False
    try:
        if startup_l2_check is not None:
            await runtime._check_startup()
        bindings = runtime.bindings()
        adopted = True
        return bindings
    finally:
        if not adopted:
            await runtime.close()
