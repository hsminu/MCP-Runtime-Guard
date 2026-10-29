"""Qt-free backend thread, startup and confirmed bounded shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from guard_app.domain.enums import StorePrepareStep
from guard_app.domain.errors import StorePrepareError
from guard_app.domain.models import RecoveryItemPage, RecoveryReport
from guard_app.services.bridge_bindings import BindingsOwner, usable
from guard_app.services.bridge_commands import (
    AccountCommands,
    BridgeOutlet,
    CommandExecutor,
    TaskViews,
)
from guard_app.services.bridge_context import CommandContext
from guard_app.services.bridge_ingress import BridgeCommand
from guard_app.services.bridge_queries import QueryLane
from guard_app.services.bridge_streams import AnswerStreams
from guard_app.services.bridge_types import (
    BackendBindings,
    BridgeState,
    CommandEvent,
    CommandStage,
    ConversationEvent,
    LifecycleEvent,
    QueryReply,
    QueryRequest,
)
from guard_app.services.codex_account import AccountView
from guard_app.services.policy_view import PolicyView


class ProcessStartGate:
    """정리 결과와 실제 스레드 종료를 확인하는 프로세스 단위 시작 게이트."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tainted = False
        self._active: object | None = None
        self._finished = False
        self._thread: threading.Thread | None = None

    def try_start(self, owner: object) -> str | None:
        with self._lock:
            if self._tainted:
                return "PROCESS_RESTART_REQUIRED"
            if self._active is not None and (
                not self._finished or (self._thread is not None and self._thread.is_alive())
            ):
                return "BACKEND_ALREADY_ACTIVE"
            self._active = owner
            self._finished = False
            self._thread = None
            return None

    def finish(self, owner: object, code: str, thread: threading.Thread | None) -> None:
        with self._lock:
            if self._active is not owner or self._finished:
                return
            # BACKEND_FAILURE도 _serve의 정리를 확인하지 못했으므로 영구 오염이다.
            if code not in {
                "CLOSED",
                "STARTUP_FAILED",
                "INITIAL_SNAPSHOT_FAILED",
                "START_THREAD_FAILED",
            }:
                self._tainted = True
            self._finished = True
            self._thread = thread


@dataclass(frozen=True, slots=True)
class LifecycleOutlet:
    lifecycle: Callable[[LifecycleEvent], None]
    command: Callable[[CommandEvent], None]
    partial: Callable[[ConversationEvent], None]
    settings: Callable[[str | None], None]
    recovery: Callable[[RecoveryReport | None], None]
    recovery_page: Callable[[RecoveryItemPage | None], None]
    policy: Callable[[PolicyView | None], None]
    # B6-05: whether this run answers screen queries (at READY), and each answer.
    query_source: Callable[[bool], None]
    query: Callable[[QueryReply], None]


class BackendHost:
    def __init__(
        self,
        bootstrap: Callable[[], Awaitable[BackendBindings]],
        outlet: BridgeOutlet,
        lifecycle: LifecycleOutlet,
        *,
        run_queue_size: int,
        control_queue_size: int,
        control_workers: int,
        max_command_ids: int,
        startup_timeout: float,
        shutdown_timeout: float,
        start_gate: ProcessStartGate,
    ) -> None:
        self._bootstrap = bootstrap
        self._recovery_loading = False
        self._start_gate = start_gate
        self._start_owner = object()
        self._state_code = "NEW"
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._state_lock = threading.Lock()
        self._state = BridgeState.NEW
        self._cleanup_unconfirmed = False
        self._stop_requested = threading.Event()
        self._thread_done = threading.Event()
        self._loop_error = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._bindings = BindingsOwner(shutdown_timeout, self._consume_task)
        stopped = self._stop_requested.is_set
        self._streams = AnswerStreams(lifecycle.partial, stopped)
        self._views = TaskViews(self._bindings, outlet, stopped, self._consume_task)
        self._accounts = AccountCommands(self._views, outlet, stopped, self._consume_task)
        self._commands = CommandExecutor(
            self._bindings, self._views, self._accounts, self._streams, outlet
        )
        self.commands = CommandContext(
            state_lock=self._state_lock,
            state=lambda: self._state,
            loop=lambda: self._loop,
            failed=self._loop_error.set,
            emit=lifecycle.command,
            commands=self._commands,
            run_size=run_queue_size,
            control_size=control_queue_size,
            control_workers=control_workers,
            max_ids=max_command_ids,
            consume=self._consume_task,
        )
        self._out = outlet
        self._events = lifecycle
        self._queries = QueryLane(lifecycle.query, self._consume_task)

    @property
    def state(self) -> BridgeState:
        with self._state_lock:
            return self._state

    @property
    def backend_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def thread_done(self) -> bool:
        return self._thread_done.is_set()

    @property
    def cleanup_unconfirmed(self) -> bool:
        with self._state_lock:
            return self._cleanup_unconfirmed

    @property
    def shutdown_timeout(self) -> float:
        return self._shutdown_timeout

    def wait_done(self, timeout_s: float) -> bool:
        """After the Qt loop has ended: wait at most `timeout_s` for the backend thread."""
        return self._thread_done.wait(max(0.0, timeout_s))

    def start(self) -> None:
        with self._state_lock:
            if self._state is not BridgeState.NEW:
                raise ValueError("Bridge has already started")
        rejection = self._start_gate.try_start(self._start_owner)
        if rejection is not None:
            self._set_state(BridgeState.FAILED, rejection)
            self._thread_done.set()
            return
        self._set_state(BridgeState.STARTING, "STARTING")
        try:
            self._thread = threading.Thread(target=self._thread_main, name="guard-backend")
            self._thread.start()
        except (RuntimeError, OSError):
            self._set_state(BridgeState.FAILED, "START_THREAD_FAILED")
            self._start_gate.finish(self._start_owner, "START_THREAD_FAILED", self._thread)
            self._thread_done.set()

    def close(self) -> None:
        """Request bounded backend cleanup without joining from the UI thread."""

        emit_closed = False
        closed_code = "CLOSED"
        with self._state_lock:
            state = self._state
            if state in (BridgeState.CLOSING, BridgeState.CLOSED):
                return
            if state is BridgeState.NEW or self._thread_done.is_set():
                self._state = BridgeState.CLOSED
                if self._cleanup_unconfirmed:
                    closed_code = "CLEANUP_UNCONFIRMED"
                self._thread_done.set()
                emit_closed = True
            else:
                self._state = BridgeState.CLOSING
                loop, stop_async = self._loop, self._stop_async
        if emit_closed:
            self._events.lifecycle(LifecycleEvent(BridgeState.CLOSED, closed_code))
            return
        self._stop_requested.set()
        self._events.lifecycle(LifecycleEvent(BridgeState.CLOSING, "CLOSING"))
        self.commands.reject_ingress("SHUTTING_DOWN")
        if loop is not None and stop_async is not None:
            # The backend loop may already have completed.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop_async.set)

    def _set_state(self, state: BridgeState, code: str) -> None:
        with self._state_lock:
            if self._state is BridgeState.CLOSING and state is BridgeState.READY:
                return
            if code.endswith("CLEANUP_UNCONFIRMED") or code == "BACKEND_FAILURE":
                self._cleanup_unconfirmed = True
            elif self._cleanup_unconfirmed and state is BridgeState.CLOSED:
                code = "CLEANUP_UNCONFIRMED"
            self._state = state
            self._state_code = code
        self._events.lifecycle(LifecycleEvent(state, code))

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        # The asyncio default handler may print raw exception text or task reprs.
        # Root/command futures are inspected below; any other loop error is
        # reported only as a fixed cleanup code.
        loop.set_exception_handler(lambda _loop, _context: self._loop_error.set())
        asyncio.set_event_loop(loop)
        with self._state_lock:
            self._loop = loop
        root = loop.create_task(self._serve())
        loop.run_until_complete(asyncio.wait({root}))
        if root.exception() is not None:
            if self._stop_requested.is_set():
                self._set_state(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")
            else:
                self._set_state(BridgeState.FAILED, "BACKEND_FAILURE")
        self.commands.reject_ingress(
            "SHUTTING_DOWN" if self._stop_requested.is_set() else "BACKEND_FAILURE"
        )
        self._cancel_leftovers(loop)
        loop.close()
        asyncio.set_event_loop(None)
        if self._loop_error.is_set():
            self._mark_cleanup_unconfirmed()
        with self._state_lock:
            self._loop = None
        if self._stop_requested.is_set() and self.state is BridgeState.CLOSING:
            self._set_state(BridgeState.CLOSED, "CLOSED")
        self._start_gate.finish(self._start_owner, self._state_code, self._thread)
        self._thread_done.set()

    def _cancel_leftovers(self, loop: asyncio.AbstractEventLoop) -> None:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if not pending:
            return
        done, unfinished = loop.run_until_complete(asyncio.wait(pending, timeout=0.1))
        for task in done:
            self._consume_task(task)
        if unfinished:
            self._mark_cleanup_unconfirmed()

    def _mark_cleanup_unconfirmed(self) -> None:
        if self.state is BridgeState.CLOSED:
            self._set_state(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")
        elif self.state is BridgeState.FAILED:
            self._set_state(BridgeState.FAILED, "STARTUP_CLEANUP_UNCONFIRMED")

    async def _serve(self) -> None:
        self._stop_async = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop_async.set()
        boot = asyncio.ensure_future(self._bootstrap())
        boot.add_done_callback(self._consume_task)
        stop = asyncio.create_task(self._stop_async.wait())
        bindings = await self._start_backend(boot, stop)
        if bindings is None:
            stop.cancel()
            return
        # A finished stop task implies a stop request.
        if self._stop_requested.is_set() or stop.done():
            self._set_state(BridgeState.CLOSING, "CLOSING")
            cleaned = await self._bindings.close()
            self._set_state(BridgeState.CLOSED, "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED")
            return
        self.commands.lanes.open()
        self._announce_ready(bindings)
        await stop
        await self._shut_down()

    async def _start_backend(
        self, boot: asyncio.Task[BackendBindings], stop: asyncio.Task[Any]
    ) -> BackendBindings | None:
        """Adopt usable bindings and show the first Task, or clean up and fail."""
        done, _ = await asyncio.wait(
            {boot, stop},
            timeout=self._startup_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        error = _task_error(boot) if boot.done() else None
        if boot not in done or boot.cancelled() or error is not None:
            code = error.code.value if isinstance(error, StorePrepareError) else "STARTUP_FAILED"
            self._end_startup(await self._bindings.discard(boot), code)
            return None
        adopted = self._bindings.adopt(boot)
        bindings = self._bindings.current
        if not adopted or bindings is None or not usable(bindings):
            cleaned = await self._bindings.close() if adopted else False
            self._end_startup(cleaned, "STARTUP_FAILED")
            return None
        if bindings.initial_task_id is not None and not await self._first_view(
            bindings.initial_task_id, stop
        ):
            self._end_startup(await self._bindings.close(), "INITIAL_SNAPSHOT_FAILED")
            return None
        return bindings

    async def _first_view(self, task_id: str, stop: asyncio.Task[Any]) -> bool:
        projection = asyncio.create_task(self._views.publish(task_id))
        projection.add_done_callback(self._consume_task)
        ready, _ = await asyncio.wait(
            {projection, stop},
            timeout=self._startup_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if projection in ready and not projection.cancelled() and _task_error(projection) is None:
            return True
        projection.cancel()
        return False

    def _end_startup(self, cleaned: bool, failed_code: str) -> None:
        if self._stop_requested.is_set():
            self._set_state(BridgeState.CLOSED, "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED")
        else:
            self._set_state(
                BridgeState.FAILED, failed_code if cleaned else "STARTUP_CLEANUP_UNCONFIRMED"
            )

    def _announce_ready(self, bindings: BackendBindings) -> None:
        self._views.publish_capability()
        self._out.account_changed(
            AccountView("UNAVAILABLE" if bindings.account is None else "UNKNOWN")
        )
        self._events.policy(bindings.policy)
        status = bindings.settings_status
        self._events.recovery(bindings.recovery_report)
        self._events.recovery_page(bindings.recovery_page)
        self._events.settings(status if type(status) is str else None)
        if bindings.task_events is not None:
            bindings.task_events(self._views.task_event)
        if bindings.answer_partials is not None:
            bindings.answer_partials(self._streams.add)
        self._queries.open(bindings.queries)
        self._set_state(BridgeState.READY, "READY")
        self._events.query_source(bindings.queries is not None)

    def codex_progress(self, code: str) -> None:
        """Only the fixed startup phase crosses to Qt."""
        if code == "CODEX_L2_CHECK" and self.state is BridgeState.STARTING:
            self._events.lifecycle(LifecycleEvent(BridgeState.STARTING, code))

    def store_progress(self, step: StorePrepareStep) -> None:
        """Backend-loop callback; fixed progress values only, no state transition."""
        if isinstance(step, StorePrepareStep) and self.state is BridgeState.STARTING:
            self._events.lifecycle(LifecycleEvent(BridgeState.STARTING, step.value))

    def query(self, kind: str, token: int, request: QueryRequest) -> None:
        """Qt thread: queue one validated read on the backend loop while READY."""
        loop = self._loop
        if loop is not None and self.state is BridgeState.READY:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._queries.submit, kind, token, request)
                return
        self._events.query(QueryReply(kind, token, None, failed=True))

    def load_recovery_page(self) -> None:
        loop = self._loop
        if loop is not None and self.state is BridgeState.READY:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._start_recovery_page)

    def _start_recovery_page(self) -> None:
        bindings = self._bindings.current
        if self._recovery_loading or bindings is None or bindings.recovery_next is None:
            return
        self._recovery_loading = True
        task = asyncio.ensure_future(bindings.recovery_next())

        def finished(result: asyncio.Future[RecoveryItemPage]) -> None:
            self._recovery_loading = False
            if result.cancelled() or _task_error(result) is not None:
                self._events.recovery_page(None)
            elif self.state is BridgeState.READY:
                self._events.recovery_page(result.result())

        task.add_done_callback(finished)

    async def _shut_down(self) -> None:
        self._set_state(BridgeState.CLOSING, "CLOSING")
        self.commands.reject_ingress("SHUTTING_DOWN")
        self.commands.lanes.reject_waiting(
            lambda command: self.commands.emit_command(
                command, CommandStage.REJECTED, "SHUTTING_DOWN"
            )
        )
        self.commands.lanes.cancel_operations()
        self._accounts.cancel_waits()
        self.commands.lanes.cancel_workers()
        # Reads end before the bindings close the Store under them.
        unfinished = not await self._queries.close(self._shutdown_timeout)
        active = self.commands.lanes.active()
        if active:
            _, pending = await asyncio.wait(active, timeout=self._shutdown_timeout)
            unfinished = unfinished or bool(pending)
        for event in self.commands.ledger.unsettled():
            self.commands.emit_command(
                BridgeCommand(event.command_id, event.lane, event.action, event.target_id),
                CommandStage.UNCONFIRMED,
                "SHUTDOWN_UNCONFIRMED",
            )
        cleaned = await self._bindings.close()
        self._set_state(
            BridgeState.CLOSED,
            "CLOSED" if cleaned and not unfinished else "CLEANUP_UNCONFIRMED",
        )

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        _task_error(task)


def _task_error(task: asyncio.Future[Any]) -> BaseException | None:
    """Consume a finished future without logging; callers decide cleanup success.

    Cancellation remains a separate check at every confirmation site. Awaited
    cancellation is never caught here.
    """
    return None if task.cancelled() else task.exception()
