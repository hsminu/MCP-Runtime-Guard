"""A24 bridge lifecycle and A25 queue foundation in offscreen Qt."""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import Denied
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import HumanControl
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    ControlAction,
    LifecycleEvent,
)
from guard_app.services.runner import RunUpdate, TaskRunner


@pytest.fixture(scope="module")
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def pump_until(
    app: QApplication, predicate: Callable[[], bool], timeout: float = 2
) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


class Receiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.states: list[LifecycleEvent] = []
        self.commands: list[CommandEvent] = []
        self.signal_threads: list[QThread] = []

    @Slot(object)
    def on_lifecycle(self, event: LifecycleEvent) -> None:
        self.signal_threads.append(QThread.currentThread())
        self.states.append(event)

    @Slot(object)
    def on_command(self, event: CommandEvent) -> None:
        self.signal_threads.append(QThread.currentThread())
        self.commands.append(event)

    def stages(self, command_id: str) -> list[CommandEvent]:
        return [event for event in self.commands if event.command_id == command_id]


class FakeRunner(TaskRunner):
    def __init__(
        self, task_id: str, gate: threading.Event, entered: threading.Event
    ) -> None:
        self.task_id = task_id
        self._gate = gate
        self._entered = entered
        self.call_threads: list[int] = []
        self.calls = 0

    async def advance(self) -> RunUpdate:
        self.call_threads.append(threading.get_ident())
        self.calls += 1
        self._entered.set()
        while not self._gate.is_set():
            await asyncio.sleep(0.005)
        return RunUpdate(task_id=self.task_id, epoch=1, status="continue")


class FakeControl(HumanControl):
    def __init__(
        self,
        gate: threading.Event | None = None,
        urgent_gate: threading.Event | None = None,
    ) -> None:
        self.gate = gate
        self.urgent_gate = urgent_gate
        self.entered = threading.Event()
        self.calls: list[tuple[str, str, int]] = []

    async def approve(self, approval_id: str) -> Denied:
        self.calls.append(("approve", approval_id, threading.get_ident()))
        self.entered.set()
        if self.gate is not None:
            while not self.gate.is_set():
                await asyncio.sleep(0.005)
        return Denied(request_id=None, reasons=(Reason.APPROVAL_RACE,))

    async def reject(self, approval_id: str) -> Denied:
        self.calls.append(("reject", approval_id, threading.get_ident()))
        return Denied(request_id=None, reasons=(Reason.OK,))

    async def cancel(self, task_id: str) -> Denied:
        self.calls.append(("cancel", task_id, threading.get_ident()))
        if self.urgent_gate is not None:
            while not self.urgent_gate.is_set():
                await asyncio.sleep(0.005)
        return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))

    async def emergency_halt(self, task_id: str) -> Denied:
        self.calls.append(("emergency_halt", task_id, threading.get_ident()))
        if self.urgent_gate is not None:
            while not self.urgent_gate.is_set():
                await asyncio.sleep(0.005)
        return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))


def attach(bridge: BackendBridge) -> Receiver:
    receiver = Receiver()
    bridge.lifecycle_changed.connect(receiver.on_lifecycle)
    bridge.command_changed.connect(receiver.on_command)
    return receiver


def close_bridge(app: QApplication, bridge: BackendBridge) -> None:
    bridge.close()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert bridge.state is BridgeState.CLOSED


@pytest.mark.parametrize("timeout_name", ("startup_timeout", "shutdown_timeout"))
@pytest.mark.parametrize(
    "invalid_timeout",
    (math.nan, math.inf, -math.inf, 0, -0.1, True, "1", 10**400),
)
def test_bridge_rejects_invalid_timeouts(
    app: QApplication, timeout_name: str, invalid_timeout: object
) -> None:
    async def bootstrap() -> BackendBindings:
        raise AssertionError("must not run")

    with pytest.raises(ValueError, match="Bridge limits are invalid"):
        BackendBridge(bootstrap, **{timeout_name: invalid_timeout})


def test_qt_remains_responsive_and_receives_frozen_backend_events(app) -> None:
    ui_thread = QThread.currentThread()
    backend_threads: list[int] = []
    gate, entered = threading.Event(), threading.Event()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        backend_threads.append(threading.get_ident())
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    try:
        early = bridge.request_run("task-1")
        assert receiver.stages(early)[-1].code == "NOT_READY"
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        command_id = bridge.request_run("task-1")
        pump_until(app, lambda: entered.is_set())
        ticked: list[bool] = []
        QTimer.singleShot(0, lambda: ticked.append(True))
        pump_until(app, lambda: bool(ticked))
        assert not gate.is_set()
        gate.set()
        pump_until(
            app,
            lambda: any(
                event.stage is CommandStage.BACKEND_RETURNED
                for event in receiver.stages(command_id)
            ),
        )
        assert [event.stage for event in receiver.stages(command_id)] == [
            CommandStage.ACCEPTED,
            CommandStage.PROCESSING,
            CommandStage.BACKEND_RETURNED,
        ]
        assert receiver.stages(command_id)[-1].code == "RUN_CONTINUE_QUERY_REQUIRED"
        assert backend_threads == runner.call_threads
        assert backend_threads[0] != threading.get_ident()
        assert all(thread == ui_thread for thread in receiver.signal_threads)
        assert all(type(event) is CommandEvent for event in receiver.commands)
    finally:
        gate.set()
        close_bridge(app, bridge)


def test_run_queue_full_and_duplicate_command_cannot_reexecute(app) -> None:
    gate, entered = threading.Event(), threading.Event()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, run_queue_size=1)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        first = bridge.request_run("task-1", command_id="first")
        pump_until(app, lambda: entered.is_set())
        second = bridge.request_run("task-1", command_id="second")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.ACCEPTED for e in receiver.stages(second)
            ),
        )
        third = bridge.request_run("task-1", command_id="third")
        pump_until(app, lambda: bool(receiver.stages(third)))
        assert receiver.stages(third)[-1].code == "QUEUE_FULL"
        emergency = bridge.request_control(ControlAction.EMERGENCY_HALT, "other-task")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(emergency)
            ),
        )
        assert not gate.is_set()
        bridge.request_run("task-1", command_id=first)
        pump_until(app, lambda: len(receiver.stages(first)) >= 3)
        bridge.request_control(ControlAction.CANCEL, "other-task", command_id=first)
        pump_until(
            app,
            lambda: any(
                e.code == "COMMAND_ID_CONFLICT" for e in receiver.stages(first)
            ),
        )
        gate.set()
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(second)
            ),
        )
        assert runner.calls == 2
        assert [(name, target) for name, target, _ in control.calls] == [
            ("emergency_halt", "other-task")
        ]
    finally:
        gate.set()
        close_bridge(app, bridge)


def test_slow_approval_does_not_starve_other_task_emergency(app) -> None:
    gate = threading.Event()
    control = FakeControl(gate)
    runner = FakeRunner("unused", threading.Event(), threading.Event())

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, control_workers=2)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        slow = bridge.request_control(ControlAction.APPROVE, "approval-1")
        slow_2 = bridge.request_control(ControlAction.APPROVE, "approval-2")
        pump_until(
            app,
            lambda: len([call for call in control.calls if call[0] == "approve"]) == 2,
        )
        emergency = bridge.request_control(ControlAction.EMERGENCY_HALT, "other-task")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(emergency)
            ),
        )
        assert not gate.is_set()
        assert receiver.stages(emergency)[-1].code == "CONTROL_DENIED_QUERY_REQUIRED"
        assert not any(
            e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(slow)
        )
        assert not any(
            e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(slow_2)
        )
        gate.set()
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(slow)
            ),
        )
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(slow_2)
            ),
        )
        assert control.calls[0][:2] == ("approve", "approval-1")
        assert control.calls[1][:2] == ("approve", "approval-2")
        assert control.calls[2][:2] == ("emergency_halt", "other-task")
    finally:
        gate.set()
        close_bridge(app, bridge)


def test_full_control_queue_rejects_explicitly(app) -> None:
    gate = threading.Event()
    control = FakeControl(gate)
    runner = FakeRunner("unused", threading.Event(), threading.Event())

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, control_queue_size=1, control_workers=1)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        bridge.request_control(ControlAction.APPROVE, "approval-1")
        pump_until(app, lambda: control.entered.is_set())
        queued = bridge.request_control(ControlAction.REJECT, "approval-2")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.ACCEPTED for e in receiver.stages(queued)
            ),
        )
        full = bridge.request_control(ControlAction.APPROVE, "approval-3")
        pump_until(app, lambda: bool(receiver.stages(full)))
        assert receiver.stages(full)[-1] == CommandEvent(
            full,
            "control",
            ControlAction.APPROVE,
            "approval-3",
            CommandStage.REJECTED,
            "QUEUE_FULL",
        )
        emergency = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-3")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(emergency)
            ),
        )
        assert not gate.is_set()
        assert ("emergency_halt", "task-3") in [
            (name, target) for name, target, _ in control.calls
        ]
    finally:
        gate.set()
        close_bridge(app, bridge)


def test_full_urgent_queue_rejects_emergency_explicitly(app) -> None:
    urgent_gate = threading.Event()
    control = FakeControl(urgent_gate=urgent_gate)
    runner = FakeRunner("unused", threading.Event(), threading.Event())

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, control_queue_size=1, control_workers=1)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        first = bridge.request_control(ControlAction.CANCEL, "task-1")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.PROCESSING for e in receiver.stages(first)
            ),
        )
        bridge.request_control(ControlAction.CANCEL, "task-2")
        pump_until(
            app,
            lambda: len([call for call in control.calls if call[0] == "cancel"]) == 2,
        )
        queued = bridge.request_control(ControlAction.CANCEL, "task-3")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.ACCEPTED for e in receiver.stages(queued)
            ),
        )
        full = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-4")
        pump_until(app, lambda: bool(receiver.stages(full)))
        assert receiver.stages(full)[-1].stage is CommandStage.REJECTED
        assert receiver.stages(full)[-1].code == "QUEUE_FULL"
        assert not any(
            call[:2] == ("emergency_halt", "task-4") for call in control.calls
        )
    finally:
        urgent_gate.set()
        close_bridge(app, bridge)


def test_completed_run_does_not_exhaust_control_command_ids(app) -> None:
    gate, entered = threading.Event(), threading.Event()
    gate.set()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, max_command_ids=1)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        run = bridge.request_run("task-1")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(run)
            ),
        )
        urgent = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-2")
        pump_until(
            app,
            lambda: any(
                e.stage is CommandStage.BACKEND_RETURNED
                for e in receiver.stages(urgent)
            ),
        )
        assert receiver.stages(urgent)[0].stage is CommandStage.ACCEPTED
        assert ("emergency_halt", "task-2") in [
            (name, target) for name, target, _ in control.calls
        ]
    finally:
        close_bridge(app, bridge)


def test_thread_start_failure_settles_safe_state(app, monkeypatch) -> None:
    async def bootstrap() -> BackendBindings:
        raise AssertionError("must not run")

    def fail_start(self: threading.Thread) -> None:
        raise RuntimeError("secret thread failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    app.processEvents()
    assert bridge.state is BridgeState.FAILED
    assert bridge.thread_done
    assert receiver.states[-1] == LifecycleEvent(
        BridgeState.FAILED, "START_THREAD_FAILED"
    )
    assert "secret" not in repr(receiver.states)
    rejected = bridge.request_run("task-1")
    assert receiver.stages(rejected)[-1].code == "NOT_READY"
    close_bridge(app, bridge)


def test_startup_failure_is_fixed_code_and_shutdown_refuses_work(app) -> None:
    async def bootstrap() -> BackendBindings:
        raise RuntimeError("secret startup details")

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.FAILED)
    app.processEvents()
    assert receiver.states[-1] == LifecycleEvent(BridgeState.FAILED, "STARTUP_FAILED")
    assert "secret" not in repr(receiver.states)
    command_id = bridge.request_run("task-1")
    assert receiver.stages(command_id)[-1].code == "NOT_READY"
    close_bridge(app, bridge)
    late = bridge.request_control(ControlAction.CANCEL, "task-1")
    assert receiver.stages(late)[-1].code == "CLOSING"


def test_startup_timeout_reports_unfinished_cleanup_without_raw_error(app) -> None:
    async def stubborn_bootstrap() -> BackendBindings:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                continue
        return BackendBindings(
            lambda task_id: FakeRunner(task_id, threading.Event(), threading.Event()),
            FakeControl(),
        )

    bridge = BackendBridge(stubborn_bootstrap, startup_timeout=0.01)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert bridge.state is BridgeState.FAILED
    assert receiver.states[-1].code == "STARTUP_CLEANUP_UNCONFIRMED"
    assert "stubborn_bootstrap" not in repr(receiver.states)
    close_bridge(app, bridge)


def test_shutdown_marks_processing_control_unconfirmed(app) -> None:
    gate = threading.Event()
    control = FakeControl(gate)
    runner = FakeRunner("unused", threading.Event(), threading.Event())

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, shutdown_timeout=0.1)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        command_id = bridge.request_control(ControlAction.APPROVE, "approval-1")
        pump_until(app, lambda: control.entered.is_set())
        close_bridge(app, bridge)
        assert receiver.stages(command_id)[-1].stage is CommandStage.UNCONFIRMED
        assert not any(e.code.endswith("APPLIED") for e in receiver.stages(command_id))
    finally:
        gate.set()
        if not bridge.thread_done:
            close_bridge(app, bridge)
