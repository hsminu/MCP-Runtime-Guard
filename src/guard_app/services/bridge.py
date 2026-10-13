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
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from guard_app.core.snapshot import ApprovalSnapshot
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    Withheld,
)
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.services.approvals import HumanControl
from guard_app.services.runner import RunUpdate, TaskRunner


class BackendCleanupError(StoreError):
    """A 자원 정리에서 발생한 StoreError. 원문 없이 정리 미확인을 전달한다."""


class BridgeState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


class ControlAction(StrEnum):
    REVIEW = "review"
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
    ``discard_queued`` settles a Task's queued, not-yet-active transport
    commands before wire; it is called synchronously right before a human
    cancel or emergency halt starts.
    """

    runner_for_task: Callable[[str], TaskRunner]
    human_control: HumanControl
    close: Callable[[], Awaitable[None]] | None = None
    snapshot: Callable[[str], Awaitable[TaskProjection]] | None = None
    initial_task_id: str | None = None
    discard_queued: Callable[[str], int] | None = None


@dataclass(frozen=True, slots=True)
class TaskProjection:
    task_id: str
    epoch: int
    status: TaskStatus
    approval_id: str | None = None
    host_dispatch: int | None = None
    receiver_effect: int | None = None
    agt_mode: str = "unconfigured"
    model_mode: str = "unconfigured"
    can_advance: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalReview:
    command_id: str
    approval_id: str
    task_id: str
    epoch: int
    receiving_boundaries: str
    arguments_bytes: bytes


@dataclass(frozen=True, slots=True)
class _Command:
    command_id: str
    lane: str
    action: str
    target_id: str


@dataclass(frozen=True, slots=True)
class _IngressItem:
    command: _Command
    replay: CommandEvent | None = None


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
        ) or any(type(value) not in (int, float) for value in (startup_timeout, shutdown_timeout)):
            raise ValueError("Bridge limits are invalid")
        try:
            startup_timeout = float(startup_timeout)
            shutdown_timeout = float(shutdown_timeout)
        except OverflowError:
            raise ValueError("Bridge limits are invalid") from None
        if any(
            not math.isfinite(value) or value <= 0 for value in (startup_timeout, shutdown_timeout)
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
        self._cleanup_unconfirmed = False
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
        self._command_lock = threading.Lock()
        self._records: dict[str, CommandEvent] = {}
        self._recent: OrderedDict[str, CommandEvent] = OrderedDict()
        self._operations: dict[str, asyncio.Task[object]] = {}
        self._pending_runs: dict[str, tuple[str, int]] = {}
        # Bound commands before they become asyncio ready callbacks. A normal
        # command flood must leave an independent ingress slot for urgent work.
        self._ingress_lock = threading.Lock()
        self._ingress: dict[str, deque[_IngressItem]] = {
            "run": deque(),
            "control": deque(),
            "urgent": deque(),
            "replay": deque(),
        }
        self._ingress_limits = {
            "run": run_queue_size,
            "control": control_queue_size,
            "urgent": control_queue_size,
            "replay": max(run_queue_size, control_queue_size),
        }
        self._ingress_pending: dict[str, _Command] = {}
        self._ingress_scheduled = False
        self._ingress_closed = False

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
        command = _Command(self._id(command_id, generate=True), "run", "advance", self._id(task_id))
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
            if code.endswith("CLEANUP_UNCONFIRMED"):
                self._cleanup_unconfirmed = True
            elif self._cleanup_unconfirmed and state is BridgeState.CLOSED:
                code = "CLEANUP_UNCONFIRMED"
            self._state = state
        self.lifecycle_changed.emit(LifecycleEvent(state, code))

    def _submit(self, command: _Command) -> None:
        with self._state_lock:
            state, loop = self._state, self._loop
        if state is not BridgeState.READY or loop is None:
            code = "CLOSING" if state in (BridgeState.CLOSING, BridgeState.CLOSED) else "NOT_READY"
            self._emit_untracked_rejection(command, code)
            return

        rejection: str | None = None
        with self._ingress_lock:
            in_flight = self._ingress_pending.get(command.command_id)
            if self._ingress_closed:
                rejection = "CLOSING"
            elif in_flight is not None:
                if in_flight != command:
                    rejection = "COMMAND_ID_CONFLICT"
            else:
                with self._command_lock:
                    previous = self._records.get(command.command_id) or self._recent.get(
                        command.command_id
                    )
                lane = "replay" if previous is not None else self._ingress_lane(command)
                pending = self._ingress[lane]
                if len(pending) >= self._ingress_limits[lane]:
                    rejection = "INGRESS_FULL"
                else:
                    pending.append(_IngressItem(command, previous))
                    self._ingress_pending[command.command_id] = command
                    if not self._ingress_scheduled:
                        self._ingress_scheduled = True
                        try:
                            loop.call_soon_threadsafe(self._drain_ingress)
                        except RuntimeError:
                            pending.pop()
                            self._ingress_pending.pop(command.command_id)
                            self._ingress_scheduled = False
                            rejection = "CLOSING"
        if rejection is not None:
            self._emit_untracked_rejection(command, rejection)

    @staticmethod
    def _ingress_lane(command: _Command) -> str:
        if command.lane == "run":
            return "run"
        if command.action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            return "urgent"
        return "control"

    def _drain_ingress(self) -> None:
        # Re-schedule after a small batch so stop/worker callbacks can run.
        # Every batch selects urgent commands first, including ones received
        # after a normal-command drain began.
        try:
            for _ in range(8):
                with self._ingress_lock:
                    item = None
                    for lane in ("urgent", "replay", "control", "run"):
                        if self._ingress[lane]:
                            item = self._ingress[lane].popleft()
                            break
                    if item is None:
                        return
                completed = False
                try:
                    if item.replay is not None:
                        with self._command_lock:
                            current = self._records.get(
                                item.command.command_id
                            ) or self._recent.get(item.command.command_id)
                        self._replay_or_conflict(
                            item.command,
                            current if current is not None else item.replay,
                        )
                    else:
                        self._admit(item.command)
                    completed = True
                finally:
                    try:
                        if not completed:
                            self._loop_error.set()
                            if item.replay is not None:
                                self.command_changed.emit(
                                    CommandEvent(
                                        item.command.command_id,
                                        item.command.lane,
                                        item.command.action,
                                        item.command.target_id,
                                        CommandStage.UNCONFIRMED,
                                        "REPLAY_UNCONFIRMED",
                                    )
                                )
                            else:
                                self._emit_command(
                                    item.command,
                                    CommandStage.UNCONFIRMED,
                                    "ADMISSION_UNCONFIRMED",
                                )
                    finally:
                        with self._ingress_lock:
                            self._ingress_pending.pop(item.command.command_id, None)
        finally:
            with self._ingress_lock:
                if any(self._ingress.values()):
                    asyncio.get_running_loop().call_soon(self._drain_ingress)
                else:
                    self._ingress_scheduled = False

    def _reject_ingress(self, code: str) -> None:
        with self._ingress_lock:
            self._ingress_closed = True
            pending = [
                item.command
                for lane in ("urgent", "replay", "control", "run")
                for item in self._ingress[lane]
            ]
            for queue in self._ingress.values():
                queue.clear()
            self._ingress_pending.clear()
            self._ingress_scheduled = False
        for command in pending:
            self._emit_untracked_rejection(command, code)

    def _emit_untracked_rejection(self, command: _Command, code: str) -> None:
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

    def _admit(self, command: _Command) -> None:
        with self._command_lock:
            previous = self._records.get(command.command_id) or self._recent.get(command.command_id)
        if previous is not None:
            self._replay_or_conflict(command, previous)
            return
        lane = self._ingress_lane(command)
        if lane == "run":
            queue = self._run_queue
        elif lane == "urgent":
            queue = self._urgent_queue
        else:
            queue = self._control_queue
        assert queue is not None
        accepted: CommandEvent | None = None
        rejection = "CLOSING"
        with self._state_lock:
            if self._state is BridgeState.READY:
                try:
                    queue.put_nowait(command)
                except asyncio.QueueFull:
                    rejection = "QUEUE_FULL"
                else:
                    accepted = CommandEvent(
                        command.command_id,
                        command.lane,
                        command.action,
                        command.target_id,
                        CommandStage.ACCEPTED,
                        "QUEUED",
                    )
                    self._record_command_event(accepted)
        if accepted is None:
            self._emit_command(command, CommandStage.REJECTED, rejection)
        else:
            self.command_changed.emit(accepted)

    def _replay_or_conflict(self, command: _Command, previous: CommandEvent) -> None:
        if (
            previous.lane,
            previous.action,
            previous.target_id,
        ) == (command.lane, command.action, command.target_id):
            self.command_changed.emit(previous)
        else:
            self._emit_untracked_rejection(command, "COMMAND_ID_CONFLICT")

    def _emit_command(self, command: _Command, stage: CommandStage, code: str) -> None:
        event = CommandEvent(
            command.command_id,
            command.lane,
            command.action,
            command.target_id,
            stage,
            code,
        )
        self._record_command_event(event)
        self.command_changed.emit(event)

    def _record_command_event(self, event: CommandEvent) -> None:
        with self._command_lock:
            if event.stage in (CommandStage.ACCEPTED, CommandStage.PROCESSING):
                self._records[event.command_id] = event
            elif (
                self._records.pop(event.command_id, None) is not None
                or event.stage is CommandStage.UNCONFIRMED
            ):
                self._recent[event.command_id] = event
                self._recent.move_to_end(event.command_id)
                if len(self._recent) > self._max_command_ids:
                    self._recent.popitem(last=False)

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
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            done, unfinished = loop.run_until_complete(asyncio.wait(pending, timeout=0.1))
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
            cleaned = await self._discard_bootstrap(boot)
            if self._stop_requested.is_set():
                self._set_state(
                    BridgeState.CLOSED,
                    "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED",
                )
            else:
                self._set_state(
                    BridgeState.FAILED,
                    "STARTUP_FAILED" if cleaned else "STARTUP_CLEANUP_UNCONFIRMED",
                )
            stop.cancel()
            return
        adopted = self._adopt_bootstrap_result(boot)
        bindings = self._bindings
        if (
            not adopted
            or bindings is None
            or not callable(bindings.runner_for_task)
            or not isinstance(bindings.human_control, HumanControl)
            or (bindings.close is not None and not callable(bindings.close))
            or (bindings.discard_queued is not None and not callable(bindings.discard_queued))
        ):
            cleaned = await self._cleanup_bindings() if adopted else False
            if self._stop_requested.is_set():
                self._set_state(
                    BridgeState.CLOSED,
                    "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED",
                )
            else:
                self._set_state(
                    BridgeState.FAILED,
                    "STARTUP_FAILED" if cleaned else "STARTUP_CLEANUP_UNCONFIRMED",
                )
            stop.cancel()
            return
        if bindings.initial_task_id is not None:
            projection = asyncio.create_task(self._publish_task(bindings.initial_task_id))
            projection.add_done_callback(self._consume_task)
            ready, _ = await asyncio.wait(
                {projection, stop},
                timeout=self._startup_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if (
                projection not in ready
                or projection.cancelled()
                or projection.exception() is not None
            ):
                projection.cancel()
                cleaned = await self._cleanup_bindings()
                stopping = self._stop_requested.is_set()
                self._set_state(
                    BridgeState.CLOSED if stopping else BridgeState.FAILED,
                    ("CLOSED" if cleaned else "CLEANUP_UNCONFIRMED")
                    if stopping
                    else ("INITIAL_SNAPSHOT_FAILED" if cleaned else "STARTUP_CLEANUP_UNCONFIRMED"),
                )
                stop.cancel()
                return
        if self._stop_requested.is_set() or stop in done:
            self._set_state(BridgeState.CLOSING, "CLOSING")
            cleaned = await self._cleanup_bindings()
            self._set_state(BridgeState.CLOSED, "CLOSED" if cleaned else "CLEANUP_UNCONFIRMED")
            return
        self._run_queue = asyncio.Queue(maxsize=self._run_queue_size)
        self._control_queue = asyncio.Queue(maxsize=self._control_queue_size)
        self._urgent_queue = asyncio.Queue(maxsize=self._control_queue_size)
        workers = [asyncio.create_task(self._worker("run"))]
        workers.extend(
            asyncio.create_task(self._worker("control")) for _ in range(self._control_workers)
        )
        workers.extend(asyncio.create_task(self._worker("urgent")) for _ in range(2))
        self._set_state(BridgeState.READY, "READY")
        await stop
        self._set_state(BridgeState.CLOSING, "CLOSING")
        self._reject_ingress("SHUTTING_DOWN")
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

    def _adopt_bootstrap_result(self, boot: asyncio.Task[BackendBindings]) -> bool:
        """Claim a completed result before any path can close its resources."""

        bindings = boot.result()
        if type(bindings) is not BackendBindings:
            return False
        self._bindings = bindings
        return True

    async def _discard_bootstrap(self, boot: asyncio.Task[BackendBindings]) -> bool:
        """Reclaim a late success within the bounded startup cleanup window."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout
        if not boot.done():
            boot.cancel()
            await asyncio.wait({boot}, timeout=self._shutdown_timeout)
        if not boot.done():
            # The bootstrap still owns anything it created. Its own finally
            # must clean partial resources; this bridge cannot confirm that.
            return False
        if boot.cancelled():
            return True
        if boot.exception() is not None:
            return not isinstance(boot.exception(), BackendCleanupError)
        if not self._adopt_bootstrap_result(boot):
            return False
        return await self._cleanup_bindings(timeout=max(0.0, deadline - loop.time()))

    async def _cleanup_bindings(self, *, timeout: float | None = None) -> bool:
        bindings = self._bindings
        self._bindings = None
        if bindings is None or bindings.close is None:
            return True
        if not callable(bindings.close):
            return False

        async def invoke_close() -> None:
            assert bindings.close is not None
            await bindings.close()

        cleanup = asyncio.create_task(invoke_close())
        cleanup.add_done_callback(self._consume_task)
        done, _ = await asyncio.wait(
            {cleanup}, timeout=self._shutdown_timeout if timeout is None else timeout
        )
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
                    self._emit_command(command, CommandStage.UNCONFIRMED, "BACKEND_UNCONFIRMED")
                elif self._records[command.command_id].stage is CommandStage.PROCESSING:
                    self._emit_command(command, CommandStage.BACKEND_RETURNED, operation.result())
            finally:
                self._operations.pop(command.command_id, None)
                queue.task_done()

    async def _execute(self, command: _Command) -> str:
        bindings = self._bindings
        assert bindings is not None
        if command.lane == "run":
            runner = bindings.runner_for_task(command.target_id)
            if not isinstance(runner, TaskRunner) or runner.task_id != command.target_id:
                return "RUNNER_UNAVAILABLE"
            try:
                update = await runner.advance()
                if type(update) is not RunUpdate or update.task_id != command.target_id:
                    return "RUNNER_UNCONFIRMED"
                if update.approval_id is not None:
                    self._pending_runs[update.approval_id] = (update.task_id, update.epoch)
                return f"RUN_{update.status.upper()}_QUERY_REQUIRED"
            finally:
                # Even a failed or unclassified run must not leave the UI on
                # an older projection; Store decides what is displayed.
                await self._publish_after(command.target_id)
        control = bindings.human_control
        if command.action == ControlAction.REVIEW:
            snapshot = await control.review(command.target_id)
            if snapshot is None:
                return "REVIEW_UNAVAILABLE"
            self.review_ready.emit(
                ApprovalReview(
                    command.command_id,
                    command.target_id,
                    snapshot.task_id,
                    snapshot.request.epoch,
                    _review_header(snapshot),
                    snapshot.arguments_bytes,
                )
            )
            return "REVIEW_READY"
        if command.action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            try:
                return await self._stop_task(bindings, control, command)
            finally:
                await self._publish_after(command.target_id)
        known = self._pending_runs.get(command.target_id)
        try:
            return await self._decide_approval(bindings, control, command)
        finally:
            # Approval failures can halt the Task (and Guard) while an
            # approvable waiting projection is still on screen.
            routed = known or self._pending_runs.get(command.target_id)
            if routed is not None:
                await self._publish_after(routed[0])

    async def _stop_task(
        self, bindings: BackendBindings, control: HumanControl, command: _Command
    ) -> str:
        discard = bindings.discard_queued
        try:
            if discard is not None:
                # Synchronous: queued commands are settled before wire, and
                # the stop latch below is set before its first await.
                discard(command.target_id)
        finally:
            # A failed discard still issues the stop (fail closed); its error
            # then leaves this command unconfirmed.
            if command.action == ControlAction.CANCEL:
                result = await control.cancel(command.target_id)
            else:
                result = await control.emergency_halt(command.target_id)
        if isinstance(result, (Denied, Pending, Released, Withheld, OutcomeUnknown)):
            return f"CONTROL_{result.kind.upper()}_QUERY_REQUIRED"
        return "CONTROL_UNCONFIRMED"

    async def _decide_approval(
        self, bindings: BackendBindings, control: HumanControl, command: _Command
    ) -> str:
        if command.action == ControlAction.APPROVE:
            result = await control.approve(command.target_id)
        else:
            result = await control.reject(command.target_id)
        pending = self._pending_runs.get(command.target_id)
        if pending is not None:
            task_id, epoch = pending
            runner = bindings.runner_for_task(task_id)
            update = await runner.resume_approval(task_id, epoch, command.target_id, result)
            if update.status != "pending":
                self._pending_runs.pop(command.target_id, None)
            if update.approval_id is not None:
                self._pending_runs[update.approval_id] = (task_id, epoch)
            if update.status == "guard_error":
                return "CONTROL_GUARD_ERROR_QUERY_REQUIRED"
        if isinstance(result, (Denied, Pending, Released, Withheld, OutcomeUnknown)):
            return f"CONTROL_{result.kind.upper()}_QUERY_REQUIRED"
        return "CONTROL_UNCONFIRMED"

    async def _publish_after(self, task_id: str) -> None:
        if not self._stop_requested.is_set():
            await self._publish_task(task_id)

    async def _publish_task(self, task_id: str) -> None:
        bindings = self._bindings
        if bindings is None or bindings.snapshot is None:
            return
        snapshot = await bindings.snapshot(task_id)
        if self._stop_requested.is_set():
            return
        if type(snapshot) is not TaskProjection or snapshot.task_id != task_id:
            raise ValueError("Invalid public Task projection")
        self.task_changed.emit(snapshot)

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()  # Suppress default raw exception logging; events use fixed codes.


def _review_header(snapshot: ApprovalSnapshot) -> str:
    """Trusted request metadata on one line.

    The UI renders every newline in review text as a visible escape, so the
    fields use a separator. Tool, kind and expiry tell apart two pending
    approvals with the same server and arguments.
    """
    req = snapshot.request
    destination = req.destination.boundary_id if req.destination else "없음"
    expires = snapshot.expires_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return " · ".join(
        (
            f"도구: {req.tool.tool_name} ({req.tool.kind.name})",
            f"MCP 서버: {req.tool.server_id} (경계 {req.server_boundary.boundary_id})",
            f"목적지 경계: {destination}",
            f"Task 등급: {snapshot.effective_label.name}",
            f"만료: {expires}",
        )
    )
