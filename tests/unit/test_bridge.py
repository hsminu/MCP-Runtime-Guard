"""A24 bridge lifecycle and A25 queue foundation in offscreen Qt."""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import Denied, Outcome
from guard_app.domain.enums import Label, TaskStatus, ToolKind
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import HumanControl
from guard_app.services.bridge import (
    ApprovalReview,
    BackendBindings,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    ControlAction,
    LifecycleEvent,
    TaskProjection,
    _review_header,
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


def test_ingress_flood_is_bounded_and_emergency_enters_first(app) -> None:
    gate, entered = threading.Event(), threading.Event()
    gate.set()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, run_queue_size=2, control_queue_size=1)
    receiver = attach(bridge)
    bridge.start()
    release = threading.Event()
    replay_release = threading.Event()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        loop = bridge._loop
        assert loop is not None
        blocked = threading.Event()

        def hold_backend() -> None:
            blocked.set()
            release.wait(timeout=2)

        loop.call_soon_threadsafe(hold_backend)
        assert blocked.wait(timeout=2)
        run_ids = [
            bridge.request_run("task-1", command_id=f"flood-{index}")
            for index in range(24)
        ]
        assert len(bridge._ingress["run"]) == 2
        assert all(not receiver.stages(command_id) for command_id in run_ids[:2])
        assert all(
            receiver.stages(command_id)[-1].code == "INGRESS_FULL"
            for command_id in run_ids[2:]
        )
        bridge.request_run("task-1", command_id=run_ids[0])
        assert not receiver.stages(run_ids[0])
        bridge.request_run("other-task", command_id=run_ids[0])
        assert receiver.stages(run_ids[0])[-1].code == "COMMAND_ID_CONFLICT"
        normal_controls = [
            bridge.request_control(
                ControlAction.APPROVE,
                f"approval-{index}",
                command_id=f"approval-{index}",
            )
            for index in range(3)
        ]
        assert len(bridge._ingress["control"]) == 1
        assert all(
            receiver.stages(command_id)[-1].code == "INGRESS_FULL"
            for command_id in normal_controls[1:]
        )
        urgent = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-2")
        assert len(bridge._ingress["urgent"]) == 1
        release.set()
        pump_until(
            app,
            lambda: any(
                event.stage is CommandStage.BACKEND_RETURNED
                for event in receiver.stages(urgent)
            ),
        )
        pump_until(app, lambda: runner.calls == 2)
        urgent_accept = next(
            event
            for event in receiver.stages(urgent)
            if event.stage is CommandStage.ACCEPTED
        )
        run_accept = next(
            event
            for event in receiver.stages(run_ids[0])
            if event.stage is CommandStage.ACCEPTED
        )
        control_accept = next(
            event
            for event in receiver.stages(normal_controls[0])
            if event.stage is CommandStage.ACCEPTED
        )
        assert receiver.commands.index(urgent_accept) < receiver.commands.index(
            run_accept
        )
        assert receiver.commands.index(urgent_accept) < receiver.commands.index(
            control_accept
        )

        recovered = bridge.request_run("task-1")
        pump_until(
            app,
            lambda: any(
                event.stage is CommandStage.BACKEND_RETURNED
                for event in receiver.stages(recovered)
            ),
        )
        assert runner.calls == 3

        replay_blocked = threading.Event()

        def hold_for_replay() -> None:
            replay_blocked.set()
            replay_release.wait(timeout=2)

        loop.call_soon_threadsafe(hold_for_replay)
        assert replay_blocked.wait(timeout=2)
        waiting = [
            bridge.request_run("task-1", command_id=f"waiting-{index}")
            for index in range(2)
        ]
        prior_count = len(receiver.stages(run_ids[0]))
        bridge.request_run("task-1", command_id=run_ids[0])
        assert len(bridge._ingress["run"]) == 2
        assert len(bridge._ingress["replay"]) == 1
        assert len(receiver.stages(run_ids[0])) == prior_count
        replay_release.set()
        pump_until(app, lambda: len(receiver.stages(run_ids[0])) == prior_count + 1)
        assert receiver.stages(run_ids[0])[-1].stage is CommandStage.BACKEND_RETURNED
        pump_until(
            app,
            lambda: all(
                any(
                    event.stage is CommandStage.BACKEND_RETURNED
                    for event in receiver.stages(command_id)
                )
                for command_id in waiting
            ),
        )
        assert runner.calls == 5
    finally:
        release.set()
        replay_release.set()
        close_bridge(app, bridge)


def test_shutdown_rejects_commands_waiting_at_ingress(app) -> None:
    runner = FakeRunner("task-1", threading.Event(), threading.Event())
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, run_queue_size=1, control_queue_size=1)
    receiver = attach(bridge)
    bridge.start()
    release = threading.Event()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        loop = bridge._loop
        assert loop is not None
        blocked = threading.Event()

        def hold_backend() -> None:
            blocked.set()
            release.wait(timeout=2)

        loop.call_soon_threadsafe(hold_backend)
        assert blocked.wait(timeout=2)
        run = bridge.request_run("task-1")
        urgent = bridge.request_control(ControlAction.CANCEL, "task-1")
        bridge.close()
        assert receiver.stages(run)[-1].code == "SHUTTING_DOWN"
        assert receiver.stages(urgent)[-1].code == "SHUTTING_DOWN"
        assert all(not queue for queue in bridge._ingress.values())
        release.set()
        pump_until(app, lambda: bridge.thread_done)
        assert runner.calls == 0
        assert control.calls == []
    finally:
        release.set()
        close_bridge(app, bridge)


def test_shutdown_settles_command_already_removed_from_ingress(
    app, monkeypatch
) -> None:
    runner = FakeRunner("task-1", threading.Event(), threading.Event())
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    release = threading.Event()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        original_admit = bridge._admit
        removed = threading.Event()

        def held_admit(command) -> None:
            removed.set()
            release.wait(timeout=2)
            original_admit(command)

        monkeypatch.setattr(bridge, "_admit", held_admit)
        command_id = bridge.request_run("task-1")
        assert removed.wait(timeout=2)
        assert not bridge._ingress["run"]
        bridge.close()
        release.set()
        pump_until(app, lambda: bool(receiver.stages(command_id)))
        assert receiver.stages(command_id)[-1].stage is CommandStage.REJECTED
        assert receiver.stages(command_id)[-1].code == "CLOSING"
        pump_until(app, lambda: bridge.thread_done)
        assert runner.calls == 0
    finally:
        release.set()
        close_bridge(app, bridge)


def test_admission_error_settles_command_and_continues_drain(app, monkeypatch) -> None:
    gate = threading.Event()
    gate.set()
    runner = FakeRunner("task-1", gate, threading.Event())
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap, run_queue_size=2)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        original_admit = bridge._admit

        def fail_one(command) -> None:
            if command.command_id == "broken":
                raise ValueError("private failure detail")
            original_admit(command)

        monkeypatch.setattr(bridge, "_admit", fail_one)
        broken = bridge.request_run("task-1", command_id="broken")
        healthy = bridge.request_run("task-1", command_id="healthy")
        pump_until(
            app,
            lambda: (
                bool(receiver.stages(broken))
                and any(
                    event.stage is CommandStage.BACKEND_RETURNED
                    for event in receiver.stages(healthy)
                )
            ),
        )
        assert receiver.stages(broken)[-1].stage is CommandStage.UNCONFIRMED
        assert receiver.stages(broken)[-1].code == "ADMISSION_UNCONFIRMED"
        assert "private failure detail" not in repr(receiver.commands)
        assert not bridge._ingress_scheduled
        assert runner.calls == 1
        prior_count = len(receiver.stages(broken))
        bridge.request_run("task-1", command_id=broken)
        pump_until(app, lambda: len(receiver.stages(broken)) == prior_count + 1)
        assert receiver.stages(broken)[-1].code == "ADMISSION_UNCONFIRMED"
        assert runner.calls == 1
    finally:
        close_bridge(app, bridge)


def test_replay_error_does_not_lose_original_processing_record(
    app, monkeypatch
) -> None:
    gate, entered = threading.Event(), threading.Event()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: runner, control)

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        command_id = bridge.request_run("task-1")
        pump_until(app, lambda: entered.is_set())

        def fail_replay(_command, _previous) -> None:
            raise RuntimeError("private replay failure")

        monkeypatch.setattr(bridge, "_replay_or_conflict", fail_replay)
        bridge.request_run("task-1", command_id=command_id)
        pump_until(
            app,
            lambda: any(
                event.code == "REPLAY_UNCONFIRMED"
                for event in receiver.stages(command_id)
            ),
        )
        gate.set()
        pump_until(
            app,
            lambda: any(
                event.stage is CommandStage.BACKEND_RETURNED
                for event in receiver.stages(command_id)
            ),
        )
        assert runner.calls == 1
        assert "private replay failure" not in repr(receiver.commands)
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


@pytest.mark.parametrize("late_delay", (0.0, 0.03))
def test_startup_timeout_reclaims_late_bindings_once(app, late_delay: float) -> None:
    returned = threading.Event()
    cleanup_threads: list[int] = []

    async def close_resources() -> None:
        cleanup_threads.append(threading.get_ident())

    async def bootstrap() -> BackendBindings:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Covers both immediate cancellation response and a bootstrap
            # that briefly ignores cancellation before handing back ownership.
            if late_delay:
                await asyncio.sleep(late_delay)
            returned.set()
            return BackendBindings(lambda task_id: None, FakeControl(), close_resources)

    bridge = BackendBridge(bootstrap, startup_timeout=0.01, shutdown_timeout=0.2)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert returned.is_set()
    assert receiver.states[-1] == LifecycleEvent(BridgeState.FAILED, "STARTUP_FAILED")
    assert len(cleanup_threads) == 1
    assert cleanup_threads[0] != threading.get_ident()
    assert bridge._bindings is None
    bridge.close()
    assert len(cleanup_threads) == 1


def test_close_during_startup_reclaims_cancelled_bootstrap_result(app) -> None:
    started = threading.Event()
    cleaned = threading.Event()
    cleanup_calls = 0

    async def close_resources() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        cleaned.set()

    async def bootstrap() -> BackendBindings:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return BackendBindings(lambda task_id: None, FakeControl(), close_resources)

    bridge = BackendBridge(bootstrap, shutdown_timeout=0.2)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, started.is_set)
    bridge.close()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert cleaned.is_set()
    assert cleanup_calls == 1
    assert receiver.states[-1] == LifecycleEvent(BridgeState.CLOSED, "CLOSED")
    assert BridgeState.READY not in [event.state for event in receiver.states]
    assert bridge._bindings is None
    bridge.close()
    assert cleanup_calls == 1


def test_late_bootstrap_close_error_is_fixed_unconfirmed_code(app) -> None:
    cleanup_calls = 0

    async def close_resources() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise RuntimeError("secret cleanup details")

    async def bootstrap() -> BackendBindings:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return BackendBindings(lambda task_id: None, FakeControl(), close_resources)

    bridge = BackendBridge(bootstrap, startup_timeout=0.01, shutdown_timeout=0.2)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert cleanup_calls == 1
    assert receiver.states[-1] == LifecycleEvent(
        BridgeState.FAILED, "STARTUP_CLEANUP_UNCONFIRMED"
    )
    assert "secret" not in repr(receiver.states)
    assert bridge._bindings is None
    bridge.close()
    assert cleanup_calls == 1
    assert receiver.states[-1] == LifecycleEvent(
        BridgeState.CLOSED, "CLEANUP_UNCONFIRMED"
    )


def test_completed_bootstrap_bindings_have_single_close_owner(app) -> None:
    cleanup_calls = 0

    async def close_resources() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: None, FakeControl(), close_resources)

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY)
    assert cleanup_calls == 0
    close_bridge(app, bridge)
    assert cleanup_calls == 1
    assert receiver.states[-1] == LifecycleEvent(BridgeState.CLOSED, "CLOSED")
    assert bridge._bindings is None
    bridge.close()
    assert cleanup_calls == 1


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

    bridge = BackendBridge(
        stubborn_bootstrap, startup_timeout=0.01, shutdown_timeout=0.05
    )
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert bridge.state is BridgeState.FAILED
    assert receiver.states[-1].code == "STARTUP_CLEANUP_UNCONFIRMED"
    assert "stubborn_bootstrap" not in repr(receiver.states)
    close_bridge(app, bridge)
    assert receiver.states[-1].code == "CLEANUP_UNCONFIRMED"


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



class ProjectionReceiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.projections: list[TaskProjection] = []
        self.reviews: list[ApprovalReview] = []

    @Slot(object)
    def on_task(self, projection: TaskProjection) -> None:
        self.projections.append(projection)

    @Slot(object)
    def on_review(self, review: ApprovalReview) -> None:
        self.reviews.append(review)


class StoreTruth:
    """Stand-in for the Store-backed snapshot binding."""

    def __init__(self, status: TaskStatus, approval_id: str | None = None) -> None:
        self.status = status
        self.approval_id = approval_id
        self.reads = 0

    async def snapshot(self, task_id: str) -> TaskProjection:
        self.reads += 1
        return TaskProjection(
            task_id=task_id,
            epoch=1,
            status=self.status,
            approval_id=self.approval_id,
        )


class PendingRunner(TaskRunner):
    """Returns one pending approval, then applies or fails the resume."""

    def __init__(self, task_id: str, resume: Callable[[Outcome], RunUpdate]) -> None:
        self.task_id = task_id
        self._resume = resume
        self.resumed: list[Outcome] = []

    async def advance(self) -> RunUpdate:
        return RunUpdate(task_id=self.task_id, epoch=1, status="pending", approval_id="approval-1")

    async def resume_approval(
        self, task_id: str, epoch: int, approval_id: str, outcome: Outcome
    ) -> RunUpdate:
        self.resumed.append(outcome)
        return self._resume(outcome)


def _start_with(app, bindings: Callable[[], BackendBindings]):
    async def bootstrap() -> BackendBindings:
        return bindings()

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    projections = ProjectionReceiver()
    bridge.task_changed.connect(projections.on_task)
    bridge.review_ready.connect(projections.on_review)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY)
    return bridge, receiver, projections


def _settled(receiver: Receiver, command_id: str) -> bool:
    return any(
        event.stage in (CommandStage.BACKEND_RETURNED, CommandStage.UNCONFIRMED)
        for event in receiver.stages(command_id)
    )


@pytest.mark.parametrize("resume_fails", (False, True))
def test_approval_failure_always_republishes_store_projection(app, resume_fails: bool) -> None:
    """Q09: the UI must not keep an approvable projection after a failed approval."""
    truth = StoreTruth(TaskStatus.WAITING_APPROVAL, "approval-1")

    def resume(outcome: Outcome) -> RunUpdate:
        # The approval attempt halted the Task (and Guard) in the Store.
        truth.status, truth.approval_id = TaskStatus.HALTED, None
        if resume_fails:
            raise RuntimeError("synthetic-private-marker")
        return RunUpdate(task_id="task-1", epoch=1, status="guard_error")

    class InternalErrorControl(FakeControl):
        async def approve(self, approval_id: str) -> Denied:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

    runner = PendingRunner("task-1", resume)
    bridge, receiver, projections = _start_with(
        app,
        lambda: BackendBindings(
            lambda task_id: runner, InternalErrorControl(), snapshot=truth.snapshot
        ),
    )
    try:
        run_id = bridge.request_run("task-1")
        pump_until(app, lambda: _settled(receiver, run_id))
        assert projections.projections[-1].status is TaskStatus.WAITING_APPROVAL
        approve_id = bridge.request_control(ControlAction.APPROVE, "approval-1")
        pump_until(app, lambda: _settled(receiver, approve_id))
        app.processEvents()
        assert projections.projections[-1].status is TaskStatus.HALTED
        assert projections.projections[-1].approval_id is None
        final = receiver.stages(approve_id)[-1]
        if resume_fails:
            assert final.stage is CommandStage.UNCONFIRMED
            assert final.code == "BACKEND_UNCONFIRMED"
        else:
            assert final.code == "CONTROL_GUARD_ERROR_QUERY_REQUIRED"
        assert "synthetic-private-marker" not in repr(receiver.commands)
        assert len(runner.resumed) == 1
    finally:
        close_bridge(app, bridge)


@pytest.mark.parametrize("action", (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT))
@pytest.mark.parametrize("discard_fails", (False, True))
def test_stop_discards_queued_commands_before_control_starts(
    app, action: ControlAction, discard_fails: bool
) -> None:
    """Q01/T20: queued pre-wire commands are settled before the stop latch."""
    order: list[tuple[str, str]] = []
    truth = StoreTruth(TaskStatus.RUNNING)

    def discard(task_id: str) -> int:
        order.append(("discard", task_id))
        if discard_fails:
            raise RuntimeError("synthetic-private-marker")
        return 2

    class OrderedControl(FakeControl):
        async def cancel(self, task_id: str) -> Denied:
            order.append(("cancel", task_id))
            truth.status = TaskStatus.HALTED
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))

        async def emergency_halt(self, task_id: str) -> Denied:
            order.append(("emergency_halt", task_id))
            truth.status = TaskStatus.HALTED
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))

    runner = FakeRunner("task-1", threading.Event(), threading.Event())
    bridge, receiver, projections = _start_with(
        app,
        lambda: BackendBindings(
            lambda task_id: runner,
            OrderedControl(),
            snapshot=truth.snapshot,
            discard_queued=discard,
        ),
    )
    try:
        command_id = bridge.request_control(action, "task-1")
        pump_until(app, lambda: _settled(receiver, command_id))
        app.processEvents()
        assert order == [("discard", "task-1"), (action.value, "task-1")]
        assert projections.projections[-1].status is TaskStatus.HALTED
        final = receiver.stages(command_id)[-1]
        if discard_fails:
            # The stop still ran; the unconfirmed discard is not hidden.
            assert final.stage is CommandStage.UNCONFIRMED
        else:
            assert final.code == "CONTROL_DENIED_QUERY_REQUIRED"
        assert "synthetic-private-marker" not in repr(receiver.commands)
    finally:
        close_bridge(app, bridge)


def test_non_callable_discard_binding_fails_startup(app) -> None:
    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda task_id: None, FakeControl(), discard_queued="not callable")

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.FAILED)
    app.processEvents()
    assert receiver.states[-1] == LifecycleEvent(BridgeState.FAILED, "STARTUP_FAILED")
    close_bridge(app, bridge)


def test_review_header_names_tool_kind_and_expiry_on_one_line() -> None:
    """R09: two pending approvals with equal server/arguments stay distinct."""

    def snapshot(tool_name: str, kind: ToolKind, minute: int) -> SimpleNamespace:
        boundary = SimpleNamespace(boundary_id="docs")
        return SimpleNamespace(
            request=SimpleNamespace(
                tool=SimpleNamespace(server_id="docs", tool_name=tool_name, kind=kind),
                server_boundary=boundary,
                destination=None,
            ),
            effective_label=Label.INTERNAL,
            expires_at_utc=datetime(2026, 10, 13, 12, minute, tzinfo=UTC),
        )

    read = _review_header(snapshot("read_doc", ToolKind.READ, 5))
    send = _review_header(snapshot("send_doc", ToolKind.SEND, 6))
    assert "\n" not in read and "\\n" not in read
    assert read == (
        "도구: read_doc (READ) · MCP 서버: docs (경계 docs) · 목적지 경계: 없음"
        " · Task 등급: INTERNAL · 만료: 2026-10-13 12:05:00 UTC"
    )
    assert "send_doc (SEND)" in send and "12:06:00 UTC" in send
    assert read != send
