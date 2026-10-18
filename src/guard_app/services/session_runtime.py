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
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
    valid_configuration,
)
from guard_app.storage.sqlite_store import SQLiteStore

# A-local proposal only: A's factory must use C's executable/config/model hash
# verification and separate app-server, then perform account/read and relay setup.
# Receipts and callbacks are A-local composition seams, not final B/C APIs.
type CodexSessionFactory = Callable[
    [TaskState, SessionOptions, TaskResources], Awaitable[CodexSession]
]

SESSION_CLEANUP_TIMEOUT_S = 0.5


class SessionRuntimeError(SessionStartError):
    pass


class SessionRuntime:
    def __init__(
        self,
        store: Store,
        *,
        session_factory: CodexSessionFactory | None = None,
        preparation: SessionPreparation | None = None,
        close_store: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self._session_factory = session_factory
        self._preparation = preparation
        self._startup_lock = asyncio.Lock()
        self._configuration: ConfigurationReceipt | None = None
        self._startup_attempted = False
        self._close_store = close_store
        self._runners: dict[str, CodexTaskRunner] = {}
        self._sessions: dict[str, CodexSession] = {}
        self._options: dict[str, SessionOptions] = {}
        self._codes: dict[str, str] = {}
        self._closed = False
        self._creating: set[asyncio.Task[object]] = set()
        self._creating_ids: dict[asyncio.Task[object], str] = {}
        self._defer_store_close = False
        self._cleanup_unconfirmed = False
        self._resources: dict[str, TaskResources] = {}
        self._cleanup_started: set[str] = set()
        self._cleanup_outcomes: dict[str, asyncio.Future[bool]] = {}
        self._cleanup_operations: set[asyncio.Task[None]] = set()

    def runner_for_task(self, task_id: str) -> CodexTaskRunner:
        if self._closed or task_id not in self._runners:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        return self._runners[task_id]

    def _check_creation_allowed(self) -> None:
        current = asyncio.current_task()
        if self._closed or (current is not None and current.cancelling()):
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        if self._cleanup_unconfirmed:
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

    def creation_blocker(self) -> str | None:
        """What is already known to refuse a new session, in creation order.

        None does not promise success: L2 and per-Task checks run on create.
        """
        if self._closed:
            return "SESSION_UNAVAILABLE"
        if self._cleanup_unconfirmed:
            return "CODEX_CLEANUP_UNCONFIRMED"
        preparation = self._preparation
        if type(preparation) is not SessionPreparation:
            return "CODEX_CONFIG_UNAVAILABLE"
        if self._startup_attempted and self._configuration is None:
            return "CODEX_PREPARATION_FAILED"
        if self._session_factory is None:
            return "CODEX_SESSION_UNAVAILABLE"
        return None

    @staticmethod
    async def _prepared[Result](action: Callable[[], Awaitable[Result]]) -> Result:
        """Translate callback faults without swallowing cancellation or raw text."""

        async def invoke() -> Result:
            return await action()

        operation = asyncio.create_task(invoke())
        try:
            return await operation
        finally:
            if operation.done() and not operation.cancelled() and operation.exception() is not None:
                raise SessionRuntimeError("CODEX_PREPARATION_FAILED") from None

    async def _check_startup(self) -> ConfigurationReceipt:
        async with self._startup_lock:
            self._check_creation_allowed()
            preparation = self._preparation
            if type(preparation) is not SessionPreparation or not all(
                callable(callback)
                for callback in (
                    preparation.prepare_startup,
                    preparation.verify_configuration,
                    preparation.prepare_task,
                    preparation.confirm_exit,
                    preparation.cleanup_task,
                )
            ):
                raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
            if self._configuration is None:
                if self._startup_attempted:
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                self._startup_attempted = True
                receipt = await self._prepared(preparation.prepare_startup)
                self._check_creation_allowed()
                if type(receipt) is not StartupReceipt or not valid_configuration(
                    receipt.configuration
                ):
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                if receipt.l2_configuration is None:
                    raise SessionRuntimeError("CODEX_L2_UNAVAILABLE")
                if receipt.l2_passed is not True:
                    raise SessionRuntimeError("CODEX_L2_FAILED")
                if not valid_configuration(receipt.l2_configuration):
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                if receipt.configuration != receipt.l2_configuration:
                    raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")
                self._configuration = receipt.configuration
            return self._configuration

    async def _verify_configuration(self, expected: ConfigurationReceipt) -> None:
        preparation = self._preparation
        if preparation is None:
            raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
        actual = await self._prepared(lambda: preparation.verify_configuration(expected))
        self._check_creation_allowed()
        if not valid_configuration(actual):
            raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
        if actual != expected:
            raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")

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
            self._creating_ids.pop(current, None)
            if self._defer_store_close and not self._creating:
                self._close_store_once()

    async def _create_session(self, options: SessionOptions) -> TaskProjection:
        expected = await self._check_startup()
        await self._verify_configuration(expected)
        factory = self._session_factory
        if factory is None:
            raise SessionRuntimeError("CODEX_SESSION_UNAVAILABLE")
        state = TaskState(
            task_id=f"session-{uuid4().hex}",
            epoch=1,
            profile_id="codex-pending-profile",
            policy_version="codex-pending-v1",
            status=TaskStatus.RUNNING,
        )
        current = asyncio.current_task()
        if current is not None:
            self._creating_ids[current] = state.task_id
        preparation = self._preparation
        if preparation is None:
            raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
        created = adopted = False
        try:
            resources = await self._prepared(
                lambda: preparation.prepare_task(state.task_id, expected)
            )
            if (
                type(resources) is not TaskResources
                or resources.task_id != state.task_id
                or type(resources.owner_token) is not str
                or not resources.owner_token
                or len(resources.owner_token) > 256
                or resources.handle is None
                or any(
                    resources.owner_token == owned.owner_token or resources.handle is owned.handle
                    for owned in self._resources.values()
                )
            ):
                # A mismatched/shared receipt never grants ownership to erase it.
                # Its partial resource ownership is nevertheless unconfirmed;
                # reject additional allocations until the app is restarted.
                self._cleanup_unconfirmed = True
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._resources[state.task_id] = resources
            self._check_creation_allowed()
            if not valid_configuration(resources.configuration):
                raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
            if resources.configuration != expected:
                raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")
            # Recheck after resource setup, immediately before session creation.
            await self._verify_configuration(expected)
            # No Store RUNNING row exists while C's secure launch is unconfirmed.
            session = await self._prepared(lambda: factory(state, options, resources))
            if any(session is existing for existing in self._sessions.values()):
                raise SessionRuntimeError("CODEX_SESSION_INVALID")
            self._sessions[state.task_id] = session
            if not all(
                callable(getattr(session, name, None)) for name in ("send", "interrupt", "aclose")
            ):
                raise SessionRuntimeError("CODEX_SESSION_INVALID")
            self._check_creation_allowed()
            await self.store.create_task(state, created_at_utc=datetime.now(UTC))
            created = True
            confirmed = await self.store.load_task(state.task_id)
            if confirmed != state:
                raise StoreError()
            self._check_creation_allowed()
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
            self._check_creation_allowed()
            adopted = True
            return snapshot
        finally:
            if not adopted:
                discarded = self._runners.pop(state.task_id, None)
                if discarded is not None:
                    discarded.close()
                self._options.pop(state.task_id, None)
                self._codes.pop(state.task_id, None)
                try:
                    await self._close_session(state.task_id)
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
            label=state.high_watermark,
            unclassified=state.unclassified,
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

    async def _bounded_cleanup(self, action: Callable[[], Awaitable[object]]) -> bool:
        async def invoke() -> None:
            if await action() is not None:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

        operation = asyncio.create_task(invoke())
        self._cleanup_operations.add(operation)
        operation.add_done_callback(self._consume_cleanup)
        try:
            done, _ = await asyncio.wait({operation}, timeout=SESSION_CLEANUP_TIMEOUT_S)
            return bool(done) and not operation.cancelled() and operation.exception() is None
        finally:
            if not operation.done():
                operation.cancel()

    def _consume_cleanup(self, operation: asyncio.Task[None]) -> None:
        self._cleanup_operations.discard(operation)
        if not operation.cancelled():
            operation.exception()

    async def _close_session(self, task_id: str, *, interrupt: bool = False) -> None:
        session = self._sessions.get(task_id)
        resources = self._resources.get(task_id)
        if session is None and resources is None:
            return
        running = self._cleanup_outcomes.get(task_id)
        if running is not None:
            # A concurrent stop/close adopts the in-flight cleanup's result
            # instead of reporting it unconfirmed or running it a second time.
            if not await asyncio.shield(running):
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            return
        if task_id in self._cleanup_started:
            # An earlier cleanup ended unconfirmed; it is never retried.
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        self._cleanup_started.add(task_id)
        outcome = asyncio.get_running_loop().create_future()
        self._cleanup_outcomes[task_id] = outcome
        complete = False
        try:
            interrupted = not interrupt or session is None
            closed = session is None
            if session is not None:
                interrupt_session = getattr(session, "interrupt", None)
                close_session = getattr(session, "aclose", None)
                try:
                    if interrupt and callable(interrupt_session):
                        interrupted = await self._bounded_cleanup(interrupt_session)
                finally:
                    if callable(close_session):
                        closed = await self._bounded_cleanup(close_session)
            confirmed = cleaned = resources is None
            preparation = self._preparation
            if resources is not None and preparation is not None:

                async def confirm() -> None:
                    nonlocal confirmed
                    confirmed = await preparation.confirm_exit(resources) is True

                confirmation_finished = await self._bounded_cleanup(confirm)
                # No removal until termination is positively confirmed. If a
                # timed-out callback later returns True, this path stays closed.
                if closed and confirmation_finished and confirmed:
                    cleaned = await self._bounded_cleanup(
                        lambda: preparation.cleanup_task(resources)
                    )
                    if cleaned:
                        self._resources.pop(task_id, None)
            complete = interrupted and closed and confirmed and cleaned
            if not complete:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._sessions.pop(task_id, None)
            self._cleanup_started.discard(task_id)
        finally:
            self._cleanup_outcomes.pop(task_id, None)
            outcome.set_result(complete)
            if not complete:
                self._cleanup_unconfirmed = True
                self._codes[task_id] = "CODEX_CLEANUP_UNCONFIRMED"

    async def close(self) -> None:
        if self._closed:
            if self._cleanup_unconfirmed:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
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
                    for task_id in tuple(self._sessions.keys() | self._resources.keys())
                    # A pending factory can still launch a process. Its creator
                    # exclusively owns cleanup until it returns/fails, even if
                    # shutdown's bounded wait has already elapsed.
                    if task_id not in self._creating_ids.values()
                ),
                return_exceptions=True,
            )
            if (
                self._cleanup_unconfirmed
                or unfinished_creation
                or any(isinstance(result, BaseException) for result in results)
            ):
                self._cleanup_unconfirmed = True
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
            session_blocker=self.creation_blocker,
        )


async def bootstrap_product(
    db_path: str,
    *,
    session_factory: CodexSessionFactory | None = None,
    preparation: SessionPreparation | None = None,
) -> BackendBindings:
    store = SQLiteStore(db_path, audit_id_factory=lambda: f"audit-{uuid4().hex}")
    runtime = SessionRuntime(
        store,
        session_factory=session_factory,
        preparation=preparation,
        close_store=store.close,
    )
    adopted = False
    try:
        if preparation is not None:
            await runtime._check_startup()
        bindings = runtime.bindings()
        adopted = True
        return bindings
    finally:
        if not adopted:
            await runtime.close()
