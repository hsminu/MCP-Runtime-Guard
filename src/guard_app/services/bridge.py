"""Qt-thread command ingress and a separate asyncio backend lifetime.

This is an A-owned bridge kernel. Bootstrap runs in the backend thread and
must supply existing TaskRunner instances and B's human-only capability. It
does not create Tasks, authorize operators, or infer durable Task state.
Only frozen, public lifecycle/command metadata crosses into Qt signals.
"""

from __future__ import annotations

import asyncio
import math
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    Withheld,
)
from guard_app.services.approvals import HumanControl
from guard_app.services.runner import RunUpdate, TaskRunner


class BridgeState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


class ControlAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    EMERGENCY_HALT = "emergency_halt"


class CommandStage(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    BACKEND_RETURNED = "backend_returned"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    state: BridgeState
    code: str


@dataclass(frozen=True, slots=True)
class CommandEvent:
    command_id: str
    lane: str
    action: str
    target_id: str
    stage: CommandStage
    code: str


@dataclass(frozen=True, slots=True)
class BackendBindings:
    """Construct only in the backend thread from trusted app composition.

    ``runner_for_task`` must resolve a Store-created Task's TaskRunner. The
    bridge invokes only ``TaskRunner.advance``; it accepts no user-supplied
    coroutine or direct transport operation. ``human_control`` is the B
    capability obtained from ``GuardService.human_control``.
    """

    runner_for_task: Callable[[str], TaskRunner]
    human_control: HumanControl
    close: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class _Command:
    command_id: str
    lane: str
    action: str
    target_id: str


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
    ) -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise ValueError("Bridge requires the Qt application thread")
        if not callable(bootstrap):
            raise TypeError("Bridge bootstrap is invalid")
        if any(
            type(value) is not int or value < 1
            for value in (
                run_queue_size,
                control_queue_size,
                control_workers,
                max_command_ids,
            )
        ) or any(
            type(value) not in (int, float)
            for value in (startup_timeout, shutdown_timeout)
        ):
            raise ValueError("Bridge limits are invalid")
        try:
            startup_timeout = float(startup_timeout)
            shutdown_timeout = float(shutdown_timeout)
        except OverflowError:
            raise ValueError("Bridge limits are invalid") from None
        if any(
            not math.isfinite(value) or value <= 0
            for value in (startup_timeout, shutdown_timeout)
        ):
            raise ValueError("Bridge limits are invalid")
        super().__init__()
        self._bootstrap = bootstrap
        self._run_queue_size = run_queue_size
        self._control_queue_size = control_queue_size
        self._control_workers = control_workers
        self._max_command_ids = max_command_ids
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._state_lock = threading.Lock()
        self._state = BridgeState.NEW
        self._stop_requested = threading.Event()
        self._thread_done = threading.Event()
        self._loop_error = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._run_queue: asyncio.Queue[_Command] | None = None
        self._control_queue: asyncio.Queue[_Command] | None = None
        self._urgent_queue: asyncio.Queue[_Command] | None = None
        self._bindings: BackendBindings | None = None
        self._records: dict[str, CommandEvent] = {}
        self._recent: OrderedDict[str, CommandEvent] = OrderedDict()
        self._operations: dict[str, asyncio.Task[object]] = {}

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

    def start(self) -> None:
        self._require_qt_thread()
        with self._state_lock:
            if self._state is not BridgeState.NEW:
                raise ValueError("Bridge has already started")
            self._state = BridgeState.STARTING
        self.lifecycle_changed.emit(LifecycleEvent(BridgeState.STARTING, "STARTING"))
        self._thread = threading.Thread(target=self._thread_main, name="guard-backend")
        try:
            self._thread.start()
        except (RuntimeError, OSError):
            self._set_state(BridgeState.FAILED, "START_THREAD_FAILED")
            self._thread_done.set()

    def request_run(self, task_id: str, *, command_id: str | None = None) -> str:
        self._require_qt_thread()
        command = _Command(
            self._id(command_id, generate=True), "run", "advance", self._id(task_id)
        )
        self._submit(command)
        return command.command_id

    def request_control(
        self,
        action: ControlAction,
        target_id: str,
        *,
        command_id: str | None = None,
    ) -> str:
        self._require_qt_thread()
        if type(action) is not ControlAction:
            raise ValueError("Control action is invalid")
        command = _Command(
            self._id(command_id, generate=True),
            "control",
            action.value,
            self._id(target_id),
        )
        self._submit(command)
        return command.command_id

    def close(self) -> None:
        """Request bounded backend cleanup without joining from the UI thread."""

        self._require_qt_thread()
        emit_closed = False
        with self._state_lock:
            state = self._state
            if state in (BridgeState.CLOSING, BridgeState.CLOSED):
                return
            if state is BridgeState.NEW or self._thread_done.is_set():
                self._state = BridgeState.CLOSED
                self._thread_done.set()
                emit_closed = True
            else:
                self._state = BridgeState.CLOSING
                loop, stop_async = self._loop, self._stop_async
        if emit_closed:
            self.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSED, "CLOSED"))
            return
        self._stop_requested.set()
        self.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSING, "CLOSING"))
        if loop is not None and stop_async is not None:
            try:
                loop.call_soon_threadsafe(stop_async.set)
            except RuntimeError:
                pass  # Backend loop has already completed.

    @staticmethod
    def _id(value: str | None, *, generate: bool = False) -> str:
        if value is None and generate:
            return uuid4().hex
        if (
            type(value) is not str
            or not value
            or len(value) > 256
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        ):
            raise ValueError("Bridge identifier is invalid")
        return value

    @staticmethod
    def _require_qt_thread() -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Bridge method requires the Qt thread")

    def _set_state(self, state: BridgeState, code: str) -> None:
        with self._state_lock:
            if self._state is BridgeState.CLOSING and state is BridgeState.READY:
                return
            self._state = state
        self.lifecycle_changed.emit(LifecycleEvent(state, code))

    def _submit(self, command: _Command) -> None:
        with self._state_lock:
            state, loop = self._state, self._loop
        if state is not BridgeState.READY or loop is None:
            code = (
                "CLOSING"
                if state in (BridgeState.CLOSING, BridgeState.CLOSED)
                else "NOT_READY"
            )
            self.command_changed.emit(
                CommandEvent(
                    command.command_id,
                    command.lane,
                    command.action,
                    command.target_id,
                    CommandStage.REJECTED,
                    code,
                )
            )
            return
        try:
            loop.call_soon_threadsafe(self._admit, command)
        except RuntimeError:
            self.command_changed.emit(
                CommandEvent(
                    command.command_id,
                    command.lane,
                    command.action,
                    command.target_id,
                    CommandStage.REJECTED,
                    "CLOSING",
                )
            )

    def _admit(self, command: _Command) -> None:
        previous = self._records.get(command.command_id) or self._recent.get(
            command.command_id
        )
        if previous is not None:
            if (
                previous.lane,
                previous.action,
                previous.target_id,
            ) == (command.lane, command.action, command.target_id):
                self.command_changed.emit(previous)
            else:
                self.command_changed.emit(
                    CommandEvent(
                        command.command_id,
                        command.lane,
                        command.action,
                        command.target_id,
                        CommandStage.REJECTED,
                        "COMMAND_ID_CONFLICT",
                    )
                )
            return
        with self._state_lock:
            ready = self._state is BridgeState.READY
        if not ready:
            self._emit_command(command, CommandStage.REJECTED, "CLOSING")
            return
        if command.lane == "run":
            queue = self._run_queue
        elif command.action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            queue = self._urgent_queue
        else:
            queue = self._control_queue
        assert queue is not None
        try:
            queue.put_nowait(command)
        except asyncio.QueueFull:
            self._emit_command(command, CommandStage.REJECTED, "QUEUE_FULL")
            return
        self._emit_command(command, CommandStage.ACCEPTED, "QUEUED")

    def _emit_command(self, command: _Command, stage: CommandStage, code: str) -> None:
        event = CommandEvent(
            command.command_id,
            command.lane,
            command.action,
            command.target_id,
            stage,
            code,
        )
        if stage in (CommandStage.ACCEPTED, CommandStage.PROCESSING):
            self._records[command.command_id] = event
        elif self._records.pop(command.command_id, None) is not None:
            self._recent[command.command_id] = event
            self._recent.move_to_end(command.command_id)
            if len(self._recent) > self._max_command_ids:
                self._recent.popitem(last=False)
        self.command_changed.emit(event)

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
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            done, unfinished = loop.run_until_complete(
                asyncio.wait(pending, timeout=0.1)
            )
            for task in done:
                self._consume_task(task)
            if unfinished:
                if self.state is BridgeState.CLOSED:
                    self._set_state(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")
                elif self.state is BridgeState.FAILED:
                    self._set_state(BridgeState.FAILED, "STARTUP_CLEANUP_UNCONFIRMED")
        loop.close()
        asyncio.set_event_loop(None)
        if self._loop_error.is_set():
            if self.state is BridgeState.CLOSED:
                self._set_state(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")
            elif self.state is BridgeState.FAILED:
                self._set_state(BridgeState.FAILED, "STARTUP_CLEANUP_UNCONFIRMED")
        with self._state_lock:
            self._loop = None
        if self._stop_requested.is_set() and self.state is BridgeState.CLOSING:
            self._set_state(BridgeState.CLOSED, "CLOSED")
        self._thread_done.set()

    async def _serve(self) -> None:
        self._stop_async = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop_async.set()
        boot = asyncio.ensure_future(self._bootstrap())
        boot.add_done_callback(self._consume_task)
        stop = asyncio.create_task(self._stop_async.wait())
        done, _ = await asyncio.wait(
            {boot, stop},
            timeout=self._startup_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if boot not in done or boot.cancelled() or boot.exception() is not None:
            boot.cancel()
            if not self._stop_requested.is_set():
                self._set_state(BridgeState.FAILED, "STARTUP_FAILED")
            else:
                self._set_state(BridgeState.CLOSED, "CLOSED")
            stop.cancel()
            return
        bindings = boot.result()
        if (
            type(bindings) is not BackendBindings
            or not callable(bindings.runner_for_task)
            or not isinstance(bindings.human_control, HumanControl)
            or (bindings.close is not None and not callable(bindings.close))
        ):
            self._set_state(BridgeState.FAILED, "STARTUP_FAILED")
            stop.cancel()
            return
        self._bindings = bindings
        if self._stop_requested.is_set() or stop in done:
            self._set_state(BridgeState.CLOSING, "CLOSING")
            cleaned = await self._cleanup_bindings()
            self._set_state(
                BridgeState.CLOSED, "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED"
            )
            return
        self._run_queue = asyncio.Queue(maxsize=self._run_queue_size)
        self._control_queue = asyncio.Queue(maxsize=self._control_queue_size)
        self._urgent_queue = asyncio.Queue(maxsize=self._control_queue_size)
        workers = [asyncio.create_task(self._worker("run"))]
        workers.extend(
            asyncio.create_task(self._worker("control"))
            for _ in range(self._control_workers)
        )
        workers.extend(asyncio.create_task(self._worker("urgent")) for _ in range(2))
        self._set_state(BridgeState.READY, "READY")
        await stop
        self._set_state(BridgeState.CLOSING, "CLOSING")
        for queue in (self._run_queue, self._control_queue, self._urgent_queue):
            while not queue.empty():
                command = queue.get_nowait()
                self._emit_command(command, CommandStage.REJECTED, "SHUTTING_DOWN")
                queue.task_done()
        for task in self._operations.values():
            task.cancel()
        for worker in workers:
            worker.cancel()
        active = tuple(self._operations.values()) + tuple(workers)
        unfinished = False
        if active:
            _, pending = await asyncio.wait(active, timeout=self._shutdown_timeout)
            unfinished = bool(pending)
        for command_id, event in tuple(self._records.items()):
            if event.stage is CommandStage.PROCESSING:
                self._emit_command(
                    _Command(command_id, event.lane, event.action, event.target_id),
                    CommandStage.UNCONFIRMED,
                    "SHUTDOWN_UNCONFIRMED",
                )
        cleaned = await self._cleanup_bindings()
        self._set_state(
            BridgeState.CLOSED,
            "CLOSED" if cleaned and not unfinished else "CLEANUP_UNCONFIRMED",
        )

    async def _cleanup_bindings(self) -> bool:
        bindings = self._bindings
        if bindings is None or bindings.close is None:
            return True
        cleanup = asyncio.ensure_future(bindings.close())
        cleanup.add_done_callback(self._consume_task)
        done, _ = await asyncio.wait({cleanup}, timeout=self._shutdown_timeout)
        if cleanup not in done:
            cleanup.cancel()
            return False
        return not cleanup.cancelled() and cleanup.exception() is None

    async def _worker(self, lane: str) -> None:
        if lane == "run":
            queue = self._run_queue
        elif lane == "urgent":
            queue = self._urgent_queue
        else:
            queue = self._control_queue
        assert queue is not None
        while True:
            command = await queue.get()
            self._emit_command(command, CommandStage.PROCESSING, "BACKEND_STARTED")
            operation = asyncio.create_task(self._execute(command))
            operation.add_done_callback(self._consume_task)
            self._operations[command.command_id] = operation
            try:
                await asyncio.wait({operation})
                if operation.cancelled() or operation.exception() is not None:
                    self._emit_command(
                        command, CommandStage.UNCONFIRMED, "BACKEND_UNCONFIRMED"
                    )
                elif self._records[command.command_id].stage is CommandStage.PROCESSING:
                    self._emit_command(
                        command, CommandStage.BACKEND_RETURNED, operation.result()
                    )
            finally:
                self._operations.pop(command.command_id, None)
                queue.task_done()

    async def _execute(self, command: _Command) -> str:
        bindings = self._bindings
        assert bindings is not None
        if command.lane == "run":
            runner = bindings.runner_for_task(command.target_id)
            if (
                not isinstance(runner, TaskRunner)
                or runner.task_id != command.target_id
            ):
                return "RUNNER_UNAVAILABLE"
            update = await runner.advance()
            if type(update) is not RunUpdate or update.task_id != command.target_id:
                return "RUNNER_UNCONFIRMED"
            return f"RUN_{update.status.upper()}_QUERY_REQUIRED"
        control = bindings.human_control
        if command.action == ControlAction.APPROVE:
            result = await control.approve(command.target_id)
        elif command.action == ControlAction.REJECT:
            result = await control.reject(command.target_id)
        elif command.action == ControlAction.CANCEL:
            result = await control.cancel(command.target_id)
        else:
            result = await control.emergency_halt(command.target_id)
        if isinstance(result, (Denied, Pending, Released, Withheld, OutcomeUnknown)):
            return f"CONTROL_{result.kind.upper()}_QUERY_REQUIRED"
        return "CONTROL_UNCONFIRMED"

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()  # Suppress default raw exception logging; events use fixed codes.
