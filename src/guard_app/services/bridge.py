"""Qt-thread command ingress and a separate asyncio backend lifetime.

This is an A-owned bridge kernel. Bootstrap runs in the backend thread and
must supply TaskExecution instances and B's human-only capability. It
does not create Tasks, authorize operators, or infer durable Task state.
Frozen lifecycle/command metadata and ephemeral public conversation events
cross into Qt signals. Raw tool results never cross this boundary.

This module keeps the Qt facade, the backend thread and its startup and
shutdown. The parts it composes:

- ``bridge_types``: the public values and fixed codes, without Qt.
- ``bridge_ingress``: command validation, the bounded ingress and the ledger.
- ``bridge_lanes``: backend-loop queues and workers.
- ``bridge_bindings``: ownership of the bootstrap result until it is closed.
- ``bridge_streams``: the streamed answer of a running command (A90).
- ``bridge_commands``: what each command does against the bindings.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import math
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from guard_app.services import catalog_sources
from guard_app.services.bridge_bindings import BindingsOwner, usable
from guard_app.services.bridge_commands import (
    AccountCommands,
    BridgeOutlet,
    CommandExecutor,
    TaskViews,
)
from guard_app.services.bridge_ingress import (
    BridgeCommand,
    CommandIngress,
    CommandLedger,
    IngressItem,
    account_command,
    control_command,
    ingress_lane,
    message_command,
    policy_command,
    run_command,
    session_command,
)
from guard_app.services.bridge_lanes import CommandLanes
from guard_app.services.bridge_streams import MAX_PARTIAL_CHARS as MAX_PARTIAL_CHARS
from guard_app.services.bridge_streams import PARTIAL_FLUSH_S as PARTIAL_FLUSH_S
from guard_app.services.bridge_streams import AnswerStreams
from guard_app.services.bridge_types import CODEX_DEFAULT_EFFORT as CODEX_DEFAULT_EFFORT
from guard_app.services.bridge_types import CODEX_MODEL_ID as CODEX_MODEL_ID
from guard_app.services.bridge_types import CODEX_MODELS as CODEX_MODELS
from guard_app.services.bridge_types import CODEX_REASONING_EFFORTS as CODEX_REASONING_EFFORTS
from guard_app.services.bridge_types import ApprovalReview as ApprovalReview
from guard_app.services.bridge_types import BackendBindings as BackendBindings
from guard_app.services.bridge_types import BackendCleanupError as BackendCleanupError
from guard_app.services.bridge_types import BridgeState as BridgeState
from guard_app.services.bridge_types import CommandEvent as CommandEvent
from guard_app.services.bridge_types import CommandStage as CommandStage
from guard_app.services.bridge_types import ControlAction as ControlAction
from guard_app.services.bridge_types import ConversationEvent as ConversationEvent
from guard_app.services.bridge_types import LifecycleEvent as LifecycleEvent
from guard_app.services.bridge_types import SessionCreated as SessionCreated
from guard_app.services.bridge_types import SessionOptions as SessionOptions
from guard_app.services.bridge_types import SessionStartError as SessionStartError
from guard_app.services.bridge_types import TaskProjection as TaskProjection
from guard_app.services.codex_account import AccountView as AccountView  # UI re-export
from guard_app.services.policy_view import PolicyServer as PolicyServer  # UI re-export
from guard_app.services.policy_view import PolicyTool as PolicyTool  # UI re-export
from guard_app.services.policy_view import PolicyView as PolicyView  # UI re-export


def valid_server_id(server_id: object) -> bool:
    """Forward B's pure syntax result, including before backend startup/after close.

    This local hint neither normalizes input nor grants Registry acceptance.
    Registry remains responsible for validating every submitted registration.
    """

    return catalog_sources.valid_server_id(server_id)


class _ProcessStartGate:
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


_PROCESS_START_GATE = _ProcessStartGate()


def _checked_limits(counts: tuple[int, ...], timeouts: tuple[float, ...]) -> tuple[float, ...]:
    if any(type(value) is not int or value < 1 for value in counts) or any(
        type(value) not in (int, float) for value in timeouts
    ):
        raise ValueError("Bridge limits are invalid")
    try:
        seconds = tuple(float(value) for value in timeouts)
    except OverflowError:
        raise ValueError("Bridge limits are invalid") from None
    if any(not math.isfinite(value) or value <= 0 for value in seconds):
        raise ValueError("Bridge limits are invalid")
    return seconds


class BackendBridge(QObject):
    """Call public methods on the Qt thread; never wait there for backend work.

    A command's ``BACKEND_RETURNED`` only records an API return. In
    particular, cancel/emergency_halt require a later authoritative Store
    snapshot before the UI can display an applied Task transition.

    ``control_workers`` serves APPROVE/REJECT only. CANCEL/EMERGENCY_HALT
    have a separate bounded queue and two reserved workers, even when
    ``control_workers=1``. Completed command IDs are cached only for this
    process lifetime; C/B must provide durable replay protection.
    """

    lifecycle_changed = Signal(object)
    command_changed = Signal(object)
    task_changed = Signal(object)
    review_ready = Signal(object)
    conversation_ready = Signal(object)
    # The whole answer so far for one run command; replaces the previous one.
    conversation_partial = Signal(object)
    session_created = Signal(object)
    # str: fixed blocker code, or "" when new sessions may be requested.
    session_capability_changed = Signal(str)
    account_changed = Signal(object)
    policy_changed = Signal(object)
    settings_changed = Signal(object)

    def __init__(
        self,
        bootstrap: Callable[[], Awaitable[BackendBindings]],
        *,
        run_queue_size: int = 16,
        control_queue_size: int = 16,
        control_workers: int = 4,
        max_command_ids: int = 4096,
        startup_timeout: float = 10.0,
        shutdown_timeout: float = 3.0,
        start_gate: _ProcessStartGate | None = None,
    ) -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise ValueError("Bridge requires the Qt application thread")
        if not callable(bootstrap):
            raise TypeError("Bridge bootstrap is invalid")
        startup_timeout, shutdown_timeout = _checked_limits(
            (run_queue_size, control_queue_size, control_workers, max_command_ids),
            (startup_timeout, shutdown_timeout),
        )
        super().__init__()
        self._bootstrap = bootstrap
        self._start_gate = _PROCESS_START_GATE if start_gate is None else start_gate
        self._start_owner = object()
        self._state_code = "NEW"
        self._run_queue_size = run_queue_size
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
        self._ledger = CommandLedger(max_command_ids)
        self._ingress = CommandIngress(run_queue_size, control_queue_size)
        self._bindings = BindingsOwner(shutdown_timeout, self._consume_task)
        stopped = self._stop_requested.is_set
        outlet = BridgeOutlet(
            task_changed=self.task_changed.emit,
            review_ready=self.review_ready.emit,
            conversation_ready=self.conversation_ready.emit,
            session_created=self.session_created.emit,
            session_capability=self.session_capability_changed.emit,
            account_changed=self.account_changed.emit,
            policy_changed=self.policy_changed.emit,
        )
        self._streams = AnswerStreams(self.conversation_partial.emit, stopped)
        self._views = TaskViews(self._bindings, outlet, stopped, self._consume_task)
        self._accounts = AccountCommands(self._views, outlet, stopped, self._consume_task)
        self._commands = CommandExecutor(
            self._bindings, self._views, self._accounts, self._streams, outlet
        )
        self._lanes = CommandLanes(
            run_size=run_queue_size,
            control_size=control_queue_size,
            control_workers=control_workers,
            perform=self._perform,
            runs_for=self._ledger.runs_for,
            consume=self._consume_task,
        )

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
        self._require_qt_thread()
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

    def request_run(self, task_id: str, *, command_id: str | None = None) -> str:
        self._require_qt_thread()
        return self._submit(run_command(task_id, command_id))

    def request_control(
        self,
        action: ControlAction,
        target_id: str,
        *,
        command_id: str | None = None,
        epoch: int | None = None,
    ) -> str:
        self._require_qt_thread()
        return self._submit(control_command(action, target_id, command_id, epoch))

    def request_message(
        self,
        task_id: str,
        epoch: int,
        text: str,
        *,
        command_id: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Queue a user turn through the existing Runner, without new sessions.

        `effort` is the reasoning level picked in the composer; None keeps the
        session's current level."""
        self._require_qt_thread()
        return self._submit(message_command(task_id, epoch, text, command_id, effort))

    def request_account(
        self, action: str, *, target_id: str = "account", secret: str | None = None
    ) -> str:
        """Codex login commands. `secret` is an API key, never kept or shown."""

        self._require_qt_thread()
        return self._submit(account_command(action, target_id, secret))

    def request_policy_access(
        self, server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
    ) -> str:
        """B3.10: replace one server's allow/deny rules from the activation dialog."""

        self._require_qt_thread()
        return self._submit(policy_command(server_id, allowed, denied))

    def request_new_session(
        self, draft_key: str, options: SessionOptions, *, command_id: str | None = None
    ) -> str:
        self._require_qt_thread()
        return self._submit(session_command(draft_key, options, command_id))

    def close(self) -> None:
        """Request bounded backend cleanup without joining from the UI thread."""

        self._require_qt_thread()
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
            self.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSED, closed_code))
            return
        self._stop_requested.set()
        self.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSING, "CLOSING"))
        self._reject_ingress("SHUTTING_DOWN")
        if loop is not None and stop_async is not None:
            # The backend loop may already have completed.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop_async.set)

    @staticmethod
    def _require_qt_thread() -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Bridge method requires the Qt thread")

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
        self.lifecycle_changed.emit(LifecycleEvent(state, code))

    # -- commands: Qt thread to the backend loop --------------------------------

    def _submit(self, command: BridgeCommand) -> str:
        with self._state_lock:
            state, loop = self._state, self._loop
        if state is not BridgeState.READY or loop is None:
            code = "CLOSING" if state in (BridgeState.CLOSING, BridgeState.CLOSED) else "NOT_READY"
            rejection: str | None = code
        else:
            schedule = functools.partial(loop.call_soon_threadsafe, self._drain_ingress)
            rejection = self._ingress.offer(command, self._ledger, schedule)
        if rejection is not None:
            self._emit_untracked_rejection(command, rejection)
        return command.command_id

    def _drain_ingress(self) -> None:
        # Re-schedule after a small batch so stop/worker callbacks can run.
        # Every batch selects urgent commands first, including ones received
        # after a normal-command drain began.
        try:
            for _ in range(8):
                item = self._ingress.take()
                if item is None:
                    return
                self._admit_item(item)
        finally:
            if self._ingress.more_waiting():
                asyncio.get_running_loop().call_soon(self._drain_ingress)

    def _admit_item(self, item: IngressItem) -> None:
        command = item.command
        completed = False
        try:
            if item.replay is not None:
                current = self._ledger.previous(command.command_id)
                self._replay_or_conflict(command, current if current is not None else item.replay)
            else:
                self._admit(command)
            completed = True
        finally:
            try:
                if not completed:
                    self._loop_error.set()
                    if item.replay is not None:
                        self.command_changed.emit(
                            command.event(CommandStage.UNCONFIRMED, "REPLAY_UNCONFIRMED")
                        )
                    else:
                        self._emit_command(
                            command, CommandStage.UNCONFIRMED, "ADMISSION_UNCONFIRMED"
                        )
            finally:
                self._ingress.release(command.command_id)

    def _reject_ingress(self, code: str) -> None:
        for command in self._ingress.close():
            self._emit_untracked_rejection(command, code)

    def _emit_untracked_rejection(self, command: BridgeCommand, code: str) -> None:
        self.command_changed.emit(command.event(CommandStage.REJECTED, code))

    def _admit(self, command: BridgeCommand) -> None:
        previous = self._ledger.previous(command.command_id)
        if previous is not None:
            self._replay_or_conflict(command, previous)
            return
        lane = ingress_lane(command)
        queue = self._lanes.queue(lane)
        if queue is None:
            raise RuntimeError("BRIDGE_QUEUE_UNAVAILABLE")
        accepted: CommandEvent | None = None
        rejection = "CLOSING"
        with self._state_lock:
            if self._state is BridgeState.READY:
                try:
                    if lane == "run" and self._ledger.active_runs() >= self._run_queue_size + 1:
                        raise asyncio.QueueFull
                    queue.put_nowait(command)
                except asyncio.QueueFull:
                    rejection = "QUEUE_FULL"
                else:
                    accepted = command.event(CommandStage.ACCEPTED, "QUEUED")
                    self._ledger.record(accepted)
        if accepted is None:
            self._emit_command(command, CommandStage.REJECTED, rejection)
        else:
            self.command_changed.emit(accepted)

    def _replay_or_conflict(self, command: BridgeCommand, previous: CommandEvent) -> None:
        if (
            previous.lane,
            previous.action,
            previous.target_id,
        ) == (command.lane, command.action, command.target_id):
            self.command_changed.emit(previous)
        else:
            self._emit_untracked_rejection(command, "COMMAND_ID_CONFLICT")

    def _emit_command(self, command: BridgeCommand, stage: CommandStage, code: str) -> None:
        event = command.event(stage, code)
        self._ledger.record(event)
        self.command_changed.emit(event)

    async def _perform(self, command: BridgeCommand) -> None:
        self._emit_command(command, CommandStage.PROCESSING, "BACKEND_STARTED")
        completed = False
        try:
            code = await self._commands.execute(command)
            completed = True
            if self._ledger.stage(command.command_id) is CommandStage.PROCESSING:
                self._emit_command(command, CommandStage.BACKEND_RETURNED, code)
        finally:
            if not completed:
                self._emit_command(command, CommandStage.UNCONFIRMED, "BACKEND_UNCONFIRMED")

    # -- backend thread lifetime ------------------------------------------------

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
        self._reject_ingress(
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
        self._lanes.open()
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
        if boot not in done or boot.cancelled() or boot.exception() is not None:
            self._end_startup(await self._bindings.discard(boot), "STARTUP_FAILED")
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
        if projection in ready and not projection.cancelled() and projection.exception() is None:
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
        self.account_changed.emit(
            AccountView("UNAVAILABLE" if bindings.account is None else "UNKNOWN")
        )
        self.policy_changed.emit(bindings.policy)
        status = bindings.settings_status
        self.settings_changed.emit(status if type(status) is str else None)
        if bindings.task_events is not None:
            bindings.task_events(self._views.task_event)
        if bindings.answer_partials is not None:
            bindings.answer_partials(self._streams.add)
        self._set_state(BridgeState.READY, "READY")

    async def _shut_down(self) -> None:
        self._set_state(BridgeState.CLOSING, "CLOSING")
        self._reject_ingress("SHUTTING_DOWN")
        self._lanes.reject_waiting(
            lambda command: self._emit_command(command, CommandStage.REJECTED, "SHUTTING_DOWN")
        )
        self._lanes.cancel_operations()
        self._accounts.cancel_waits()
        self._lanes.cancel_workers()
        active = self._lanes.active()
        unfinished = False
        if active:
            _, pending = await asyncio.wait(active, timeout=self._shutdown_timeout)
            unfinished = bool(pending)
        for event in self._ledger.unsettled():
            self._emit_command(
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
        if not task.cancelled():
            task.exception()  # Suppress default raw exception logging; events use fixed codes.
