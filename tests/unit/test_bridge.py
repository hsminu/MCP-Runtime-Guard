"""A24 bridge lifecycle and A25 queue foundation in offscreen Qt."""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from examples.legacy_runner import TaskRunner
from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import Denied, Outcome
from guard_app.domain.enums import Label, TaskStatus, ToolKind
from guard_app.domain.reasons import Reason
from guard_app.services import bridge as bridge_module
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
)
from guard_app.services.bridge_bindings import BindingsOwner
from guard_app.services.bridge_commands import review_fields, review_header
from guard_app.services.bridge_ingress import (
    ACCOUNT_ACTIONS,
    BridgeCommand,
    CommandIngress,
    CommandLedger,
    account_command,
)
from guard_app.services.runner import RunUpdate


@pytest.fixture(scope="module")
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def pump_until(app: QApplication, predicate: Callable[[], bool], timeout: float = 2) -> None:
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

    def has_stage(self, command_id, stage=CommandStage.BACKEND_RETURNED):
        return any(event.stage is stage for event in self.stages(command_id))

    def stages(self, command_id: str) -> list[CommandEvent]:
        return [event for event in self.commands if event.command_id == command_id]


class FakeRunner(TaskRunner):
    def __init__(
        self,
        task_id: str,
        gate: threading.Event | None = None,
        entered: threading.Event | None = None,
    ) -> None:
        self.task_id = task_id
        self.epoch = 1
        self._last_status = "idle"
        self._awaiting_user = True
        self._closed = self._stopped = False
        self._pending = None
        self._lock = asyncio.Lock()
        self._conversation = []
        self._gate = gate if gate is not None else threading.Event()
        self._entered = entered if entered is not None else threading.Event()
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
        self, gate: threading.Event | None = None, urgent_gate: threading.Event | None = None
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


def _owned_runners(runner, task_ids):
    # Queue tests register every owned Task explicitly.
    return {runner.task_id: runner, **{task_id: FakeRunner(task_id) for task_id in task_ids}}


def _start_runner_bridge(runner, control, *, runners=None, **limits):
    async def bootstrap():
        return BackendBindings(runners.get if runners is not None else lambda _: runner, control)

    bridge = BackendBridge(bootstrap, **limits)
    receiver = attach(bridge)
    bridge.start()
    return bridge, receiver


@contextmanager
def _running_runner_bridge(app, runner, control, *, release=(), **limits):
    bridge, receiver = _start_runner_bridge(runner, control, **limits)
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        yield bridge, receiver
    finally:
        for event in release:
            event.set()
        close_bridge(app, bridge)


def close_bridge(app: QApplication, bridge: BackendBridge) -> None:
    bridge.close()
    pump_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert bridge.state is BridgeState.CLOSED


def test_bridge_rejects_invalid_timeouts(app):
    async def bootstrap():
        raise AssertionError("must not run")

    failures = []
    for name in ("startup_timeout", "shutdown_timeout"):
        for value in (math.nan, math.inf, -math.inf, 0, -0.1, True, "1", 10**400):
            try:
                with pytest.raises(ValueError, match="Bridge limits are invalid"):
                    BackendBridge(bootstrap, **{name: value})
            except (AssertionError, pytest.fail.Exception) as error:
                failures.append((name, value, str(error)))
    assert failures == []


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
        pump_until(app, lambda: receiver.has_stage(command_id))
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
    runners = _owned_runners(runner, ["other-task"])
    with _running_runner_bridge(
        app, runner, control, runners=runners, run_queue_size=1, release=(gate,)
    ) as (bridge, receiver):
        first = bridge.request_run("task-1", command_id="first")
        pump_until(app, lambda: entered.is_set())
        second = bridge.request_run("task-1", command_id="second")
        pump_until(app, lambda: receiver.has_stage(second, CommandStage.ACCEPTED))
        third = bridge.request_run("task-1", command_id="third")
        pump_until(app, lambda: bool(receiver.stages(third)))
        assert receiver.stages(third)[-1].code == "QUEUE_FULL"
        emergency = bridge.request_control(ControlAction.EMERGENCY_HALT, "other-task")
        pump_until(app, lambda: receiver.has_stage(emergency))
        assert not gate.is_set()
        bridge.request_run("task-1", command_id=first)
        pump_until(app, lambda: len(receiver.stages(first)) >= 3)
        bridge.request_control(ControlAction.CANCEL, "other-task", command_id=first)
        pump_until(
            app, lambda: any(e.code == "COMMAND_ID_CONFLICT" for e in receiver.stages(first))
        )
        gate.set()
        pump_until(app, lambda: receiver.has_stage(second))
        assert runner.calls == 2
        assert [(name, target) for name, target, _ in control.calls] == [
            ("emergency_halt", "other-task")
        ]


def test_ingress_flood_is_bounded_and_emergency_enters_first(app) -> None:
    gate, entered = threading.Event(), threading.Event()
    gate.set()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()
    bridge, receiver = _start_runner_bridge(runner, control, run_queue_size=2, control_queue_size=1)
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
        run_ids = [bridge.request_run("task-1", command_id=f"flood-{index}") for index in range(24)]
        assert bridge._ingress.waiting("run") == 2
        assert all(not receiver.stages(command_id) for command_id in run_ids[:2])
        assert all(
            receiver.stages(command_id)[-1].code == "INGRESS_FULL" for command_id in run_ids[2:]
        )
        bridge.request_run("task-1", command_id=run_ids[0])
        assert not receiver.stages(run_ids[0])
        bridge.request_run("other-task", command_id=run_ids[0])
        assert receiver.stages(run_ids[0])[-1].code == "COMMAND_ID_CONFLICT"
        normal_controls = [
            bridge.request_control(
                ControlAction.APPROVE, f"approval-{index}", command_id=f"approval-{index}"
            )
            for index in range(3)
        ]
        assert bridge._ingress.waiting("control") == 1
        assert all(
            receiver.stages(command_id)[-1].code == "INGRESS_FULL"
            for command_id in normal_controls[1:]
        )
        urgent = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-2")
        assert bridge._ingress.waiting("urgent") == 1
        release.set()
        pump_until(app, lambda: receiver.has_stage(urgent))
        pump_until(app, lambda: runner.calls == 2)
        urgent_accept = next(
            event for event in receiver.stages(urgent) if event.stage is CommandStage.ACCEPTED
        )
        run_accept = next(
            event for event in receiver.stages(run_ids[0]) if event.stage is CommandStage.ACCEPTED
        )
        control_accept = next(
            event
            for event in receiver.stages(normal_controls[0])
            if event.stage is CommandStage.ACCEPTED
        )
        assert receiver.commands.index(urgent_accept) < receiver.commands.index(run_accept)
        assert receiver.commands.index(urgent_accept) < receiver.commands.index(control_accept)
        recovered = bridge.request_run("task-1")
        pump_until(app, lambda: receiver.has_stage(recovered))
        assert runner.calls == 3
        replay_blocked = threading.Event()

        def hold_for_replay() -> None:
            replay_blocked.set()
            replay_release.wait(timeout=2)

        loop.call_soon_threadsafe(hold_for_replay)
        assert replay_blocked.wait(timeout=2)
        waiting = [
            bridge.request_run("task-1", command_id=f"waiting-{index}") for index in range(2)
        ]
        prior_count = len(receiver.stages(run_ids[0]))
        bridge.request_run("task-1", command_id=run_ids[0])
        assert bridge._ingress.waiting("run") == 2
        assert bridge._ingress.waiting("replay") == 1
        assert len(receiver.stages(run_ids[0])) == prior_count
        replay_release.set()
        pump_until(app, lambda: len(receiver.stages(run_ids[0])) == prior_count + 1)
        assert receiver.stages(run_ids[0])[-1].stage is CommandStage.BACKEND_RETURNED
        pump_until(app, lambda: all(receiver.has_stage(command_id) for command_id in waiting))
        assert runner.calls == 5
    finally:
        release.set()
        replay_release.set()
        close_bridge(app, bridge)


def test_shutdown_rejects_commands_waiting_at_ingress(app) -> None:
    runner = FakeRunner("task-1")
    control = FakeControl()
    bridge, receiver = _start_runner_bridge(runner, control, run_queue_size=1, control_queue_size=1)
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
        assert bridge._ingress.waiting() == 0
        release.set()
        pump_until(app, lambda: bridge.thread_done)
        assert runner.calls == 0
        assert control.calls == []
    finally:
        release.set()
        close_bridge(app, bridge)


def test_shutdown_settles_command_already_removed_from_ingress(app, monkeypatch) -> None:
    runner = FakeRunner("task-1")
    control = FakeControl()
    bridge, receiver = _start_runner_bridge(runner, control)
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
        assert not bridge._ingress.waiting("run")
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
    with _running_runner_bridge(app, runner, control, run_queue_size=2) as (bridge, receiver):
        original_admit = bridge._admit

        def fail_one(command) -> None:
            if command.command_id == "broken":
                raise ValueError("private failure detail")
            original_admit(command)

        monkeypatch.setattr(bridge, "_admit", fail_one)
        broken = bridge.request_run("task-1", command_id="broken")
        healthy = bridge.request_run("task-1", command_id="healthy")
        pump_until(app, lambda: bool(receiver.stages(broken)) and receiver.has_stage(healthy))
        assert receiver.stages(broken)[-1].stage is CommandStage.UNCONFIRMED
        assert receiver.stages(broken)[-1].code == "ADMISSION_UNCONFIRMED"
        assert "private failure detail" not in repr(receiver.commands)
        assert not bridge._ingress.scheduled
        assert runner.calls == 1
        prior_count = len(receiver.stages(broken))
        bridge.request_run("task-1", command_id=broken)
        pump_until(app, lambda: len(receiver.stages(broken)) == prior_count + 1)
        assert receiver.stages(broken)[-1].code == "ADMISSION_UNCONFIRMED"
        assert runner.calls == 1


def test_replay_error_does_not_lose_original_processing_record(app, monkeypatch) -> None:
    gate, entered = threading.Event(), threading.Event()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()
    with _running_runner_bridge(app, runner, control, release=(gate,)) as (bridge, receiver):
        command_id = bridge.request_run("task-1")
        pump_until(app, lambda: entered.is_set())

        def fail_replay(_command, _previous) -> None:
            raise RuntimeError("private replay failure")

        monkeypatch.setattr(bridge, "_replay_or_conflict", fail_replay)
        bridge.request_run("task-1", command_id=command_id)
        pump_until(
            app,
            lambda: any(
                event.code == "REPLAY_UNCONFIRMED" for event in receiver.stages(command_id)
            ),
        )
        gate.set()
        pump_until(app, lambda: receiver.has_stage(command_id))
        assert runner.calls == 1
        assert "private replay failure" not in repr(receiver.commands)


def test_slow_approval_does_not_starve_other_task_emergency(app) -> None:
    gate = threading.Event()
    control = FakeControl(gate)
    runner = FakeRunner("unused")
    runners = _owned_runners(runner, ["other-task"])
    with _running_runner_bridge(
        app, runner, control, runners=runners, control_workers=2, release=(gate,)
    ) as (bridge, receiver):
        slow = bridge.request_control(ControlAction.APPROVE, "approval-1")
        slow_2 = bridge.request_control(ControlAction.APPROVE, "approval-2")
        pump_until(app, lambda: len([call for call in control.calls if call[0] == "approve"]) == 2)
        emergency = bridge.request_control(ControlAction.EMERGENCY_HALT, "other-task")
        pump_until(app, lambda: receiver.has_stage(emergency))
        assert not gate.is_set()
        assert receiver.stages(emergency)[-1].code == "CONTROL_DENIED_QUERY_REQUIRED"
        assert not receiver.has_stage(slow)
        assert not receiver.has_stage(slow_2)
        gate.set()
        pump_until(app, lambda: receiver.has_stage(slow))
        pump_until(app, lambda: receiver.has_stage(slow_2))
        assert control.calls[0][:2] == ("approve", "approval-1")
        assert control.calls[1][:2] == ("approve", "approval-2")
        assert control.calls[2][:2] == ("emergency_halt", "other-task")


def test_full_control_queue_rejects_explicitly(app) -> None:
    gate = threading.Event()
    control = FakeControl(gate)
    runner = FakeRunner("unused")
    runners = _owned_runners(runner, ["task-3"])
    with _running_runner_bridge(
        app,
        runner,
        control,
        runners=runners,
        control_queue_size=1,
        control_workers=1,
        release=(gate,),
    ) as (bridge, receiver):
        bridge.request_control(ControlAction.APPROVE, "approval-1")
        pump_until(app, lambda: control.entered.is_set())
        queued = bridge.request_control(ControlAction.REJECT, "approval-2")
        pump_until(app, lambda: receiver.has_stage(queued, CommandStage.ACCEPTED))
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
        pump_until(app, lambda: receiver.has_stage(emergency))
        assert not gate.is_set()
        assert ("emergency_halt", "task-3") in [(name, target) for name, target, _ in control.calls]


def test_full_urgent_queue_rejects_emergency_explicitly(app) -> None:
    urgent_gate = threading.Event()
    control = FakeControl(urgent_gate=urgent_gate)
    runner = FakeRunner("unused")
    runners = _owned_runners(runner, ["task-1", "task-2", "task-3", "task-4"])
    with _running_runner_bridge(
        app,
        runner,
        control,
        runners=runners,
        control_queue_size=1,
        control_workers=1,
        release=(urgent_gate,),
    ) as (bridge, receiver):
        first = bridge.request_control(ControlAction.CANCEL, "task-1")
        pump_until(app, lambda: receiver.has_stage(first, CommandStage.PROCESSING))
        bridge.request_control(ControlAction.CANCEL, "task-2")
        pump_until(app, lambda: len([call for call in control.calls if call[0] == "cancel"]) == 2)
        queued = bridge.request_control(ControlAction.CANCEL, "task-3")
        pump_until(app, lambda: receiver.has_stage(queued, CommandStage.ACCEPTED))
        full = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-4")
        pump_until(app, lambda: bool(receiver.stages(full)))
        assert receiver.stages(full)[-1].stage is CommandStage.REJECTED
        assert receiver.stages(full)[-1].code == "QUEUE_FULL"
        assert not any(call[:2] == ("emergency_halt", "task-4") for call in control.calls)


def test_completed_run_does_not_exhaust_control_command_ids(app) -> None:
    gate, entered = threading.Event(), threading.Event()
    gate.set()
    runner = FakeRunner("task-1", gate, entered)
    control = FakeControl()
    runners = _owned_runners(runner, ["task-2"])
    with _running_runner_bridge(app, runner, control, runners=runners, max_command_ids=1) as (
        bridge,
        receiver,
    ):
        run = bridge.request_run("task-1")
        pump_until(app, lambda: receiver.has_stage(run))
        urgent = bridge.request_control(ControlAction.EMERGENCY_HALT, "task-2")
        pump_until(app, lambda: receiver.has_stage(urgent))
        assert receiver.stages(urgent)[0].stage is CommandStage.ACCEPTED
        assert ("emergency_halt", "task-2") in [(name, target) for name, target, _ in control.calls]


def test_startup_failure_is_fixed_code_and_shutdown_refuses_work(app):
    def check(mode, patch):
        async def bootstrap():
            if mode == "human_control":
                return BackendBindings(lambda _: None, object())
            if mode == "binding":
                return BackendBindings(lambda _: None, FakeControl(), discard_queued="not callable")
            if mode == "thread":
                raise AssertionError("must not run")
            raise RuntimeError("secret startup details")

        def fail_start(self):
            raise RuntimeError("secret thread failure")

        if mode == "thread":
            patch.setattr(threading.Thread, "start", fail_start)
        bridge = BackendBridge(bootstrap)
        receiver = attach(bridge)
        bridge.start()
        if mode != "thread":
            try:
                pump_until(app, lambda: bridge.state is BridgeState.FAILED)
            finally:
                if bridge.state is not BridgeState.FAILED:
                    close_bridge(app, bridge)
        app.processEvents()
        assert bridge.state is BridgeState.FAILED
        if mode == "thread":
            assert bridge.thread_done
        code = "START_THREAD_FAILED" if mode == "thread" else "STARTUP_FAILED"
        assert receiver.states[-1] == LifecycleEvent(BridgeState.FAILED, code)
        assert "secret" not in repr(receiver.states)
        command = bridge.request_run("task-1")
        assert receiver.stages(command)[-1].code == "NOT_READY"
        close_bridge(app, bridge)
        late = bridge.request_control(ControlAction.CANCEL, "task-1")
        assert receiver.stages(late)[-1].code == "CLOSING"

    for mode in ("thread", "startup", "binding", "human_control"):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(mode, patch)


def test_startup_timeout_reclaims_late_bindings_once(app):
    def check(late_delay):
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
        assert bridge._bindings.current is None
        bridge.close()
        assert len(cleanup_threads) == 1

    for late_delay in (0.0, 0.03):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(late_delay)


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
    assert bridge._bindings.current is None
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
    assert receiver.states[-1] == LifecycleEvent(BridgeState.FAILED, "STARTUP_CLEANUP_UNCONFIRMED")
    assert "secret" not in repr(receiver.states)
    assert bridge._bindings.current is None
    bridge.close()
    assert cleanup_calls == 1
    assert receiver.states[-1] == LifecycleEvent(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")
    assert bridge.wait_done(0.0)
    assert bridge.cleanup_unconfirmed


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
    assert bridge._bindings.current is None
    # A110: main reads these after the Qt loop to decide whether to keep the lock.
    assert bridge.wait_done(0.0)
    assert not bridge.cleanup_unconfirmed
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
        return BackendBindings(lambda task_id: FakeRunner(task_id), FakeControl())

    bridge = BackendBridge(stubborn_bootstrap, startup_timeout=0.01, shutdown_timeout=0.05)
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
    runner = FakeRunner("unused")
    bridge, receiver = _start_runner_bridge(runner, control, shutdown_timeout=0.1)
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
            task_id=task_id, epoch=1, status=self.status, approval_id=self.approval_id
        )


class PendingRunner(TaskRunner):
    """Returns one pending approval, then applies or fails the resume."""

    def __init__(self, task_id: str, resume: Callable[[Outcome], RunUpdate]) -> None:
        self._conversation = []
        self.task_id = task_id
        self.epoch = 1
        self._last_status = "idle"
        self._awaiting_user = True
        self._closed = self._stopped = False
        self._pending = None
        self._lock = asyncio.Lock()
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


def test_approval_failure_always_republishes_store_projection(app):
    def check(resume_fails):
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

    for resume_fails in (False, True):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(resume_fails)


def test_stop_discards_queued_commands_before_control_starts(app):
    def check(action, discard_fails):
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

        runner = FakeRunner("task-1")
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

    for action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
        for discard_fails in (False, True):
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(
                    bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate()
                )
                check(action, discard_fails)


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

    read = review_header(snapshot("read_doc", ToolKind.READ, 5))
    send = review_header(snapshot("send_doc", ToolKind.SEND, 6))
    assert "\n" not in read and "\\n" not in read
    assert read == (
        "도구: read_doc (READ) · MCP 서버: docs (경계 docs) · 목적지 경계: 없음"
        " · Task 등급: INTERNAL · 만료: 2026-10-13 12:05:00 UTC"
    )
    assert "send_doc (SEND)" in send and "12:06:00 UTC" in send
    assert read != send
    # The review panel shows the same fields one per line.
    assert review_fields(snapshot("read_doc", ToolKind.READ, 5)) == (
        ("도구", "read_doc (READ)"),
        ("MCP 서버", "docs (경계 docs)"),
        ("목적지 경계", "없음"),
        ("Task 등급", "INTERNAL"),
        ("만료", "2026-10-13 12:05:00 UTC"),
    )


@pytest.fixture(autouse=True)
def isolate_process_start_gate(monkeypatch):
    # 의도적인 정리 미확인 테스트가 전역 게이트를 오염시켜 뒤 테스트를 깨뜨리지 않게 한다.
    monkeypatch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())


def test_unconfirmed_final_result_permanently_blocks_restart(app, monkeypatch):
    def check(failure, monkeypatch):
        """REC-01·REC-05, I1/I6: 정리 미확인 뒤 bootstrap 0; 대응 T/S ID 없음."""
        gate = bridge_module._ProcessStartGate()
        calls, reports = [], []
        original_finish = gate.finish

        def report(owner, code, thread):
            reports.append(code)
            original_finish(owner, code, thread)

        monkeypatch.setattr(gate, "finish", report)

        async def cleanup():
            if failure == "cleanup":
                raise bridge_module.BackendCleanupError()

        async def boot():
            if failure == "startup_cleanup":
                raise bridge_module.BackendCleanupError()
            return BackendBindings(lambda _: None, FakeControl(), close=cleanup)

        first = BackendBridge(boot, start_gate=gate)
        receiver = attach(first)
        if failure == "backend":

            async def broken_serve():
                raise RuntimeError("비공개 오류 표지")

            monkeypatch.setattr(first, "_serve", broken_serve)
        first.start()
        if failure in ("cleanup", "late_loop"):
            pump_until(app, lambda: first.state is BridgeState.READY)
            if failure == "late_loop":
                first._loop.call_soon_threadsafe(first._loop_error.set)
            first.close()
        pump_until(app, lambda: first.thread_done and not first.backend_alive)
        app.processEvents()
        assert (
            receiver.states[-1].code
            == {
                "cleanup": "CLEANUP_UNCONFIRMED",
                "startup_cleanup": "STARTUP_CLEANUP_UNCONFIRMED",
                "backend": "BACKEND_FAILURE",
                "late_loop": "CLEANUP_UNCONFIRMED",
            }[failure]
        )

        async def forbidden():
            calls.append(1)
            return BackendBindings(lambda _: None, FakeControl())

        for _ in range(2):
            second = BackendBridge(forbidden, start_gate=gate)
            denied = attach(second)
            second.start()
            assert denied.states == [LifecycleEvent(BridgeState.FAILED, "PROCESS_RESTART_REQUIRED")]
            assert second.thread_done and second._thread is None
            run_id = second.request_run("task")
            control_id = second.request_control(ControlAction.CANCEL, "task")
            assert denied.stages(run_id)[-1].code == "NOT_READY"
            assert denied.stages(control_id)[-1].code == "NOT_READY"
            second.close()
            assert denied.states[-1] == LifecycleEvent(BridgeState.CLOSED, "CLOSED")
            assert calls == []
        assert reports == [receiver.states[-1].code]
        first.close()
        assert len(reports) == 1

    for failure in ["cleanup", "startup_cleanup", "backend", "late_loop"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(failure, patch)


def test_confirmed_cleanup_allows_new_bridge_but_not_same_instance(app, monkeypatch):
    def check(ending, monkeypatch):
        """REC-01·REC-05, I1/I6: 확인 종료만 재시작 허용; 대응 T/S ID 없음."""
        gate = bridge_module._ProcessStartGate()
        calls = []

        async def snapshot(_):
            raise ValueError("비공개 조회 오류")

        async def boot():
            calls.append("first")
            if ending == "startup":
                raise ValueError("비공개 시작 오류")
            return BackendBindings(
                lambda _: None,
                FakeControl(),
                snapshot=snapshot if ending == "snapshot" else None,
                initial_task_id="task" if ending == "snapshot" else None,
            )

        first = BackendBridge(boot, start_gate=gate)
        events = attach(first)
        with monkeypatch.context() as patch:
            if ending in ("thread", "thread_create"):

                def fail(*args, **kwargs):
                    raise RuntimeError("비공개 스레드 오류")

                if ending == "thread":
                    patch.setattr(threading.Thread, "start", fail)
                else:
                    patch.setattr(bridge_module.threading, "Thread", fail)
            if ending == "new":
                first.close()
            else:
                first.start()
                if ending == "closed":
                    pump_until(app, lambda: first.state is BridgeState.READY)
                    first.close()
                try:
                    pump_until(app, lambda: first.thread_done and not first.backend_alive)
                finally:
                    if not first.thread_done:
                        close_bridge(app, first)
        app.processEvents()
        assert (
            events.states[-1].code
            == {
                "closed": "CLOSED",
                "startup": "STARTUP_FAILED",
                "snapshot": "INITIAL_SNAPSHOT_FAILED",
                "thread": "START_THREAD_FAILED",
                "thread_create": "START_THREAD_FAILED",
                "new": "CLOSED",
            }[ending]
        )
        before = len(calls)
        with pytest.raises(ValueError, match="Bridge has already started"):
            first.start()
        assert len(calls) == before

        async def next_boot():
            calls.append("second")
            return BackendBindings(lambda _: None, FakeControl())

        second = BackendBridge(next_boot, start_gate=gate)
        try:
            second.start()
            pump_until(app, lambda: second.state is BridgeState.READY)
            assert calls.count("second") == 1
        finally:
            close_bridge(app, second)
            first.close()

    for ending in ["closed", "startup", "snapshot", "thread", "thread_create", "new"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(ending, patch)


def test_active_bridge_blocks_another_start_until_thread_finishes(app):
    def check(phase):
        """REC-01·REC-05, I1/I6: 미종결 bridge와 동시 bootstrap 금지; 대응 T/S ID 없음."""
        gate = bridge_module._ProcessStartGate()
        release = threading.Event()
        calls = []

        async def wait():
            while not release.is_set():
                await asyncio.sleep(0.005)

        async def cleanup():
            if phase == "closing":
                await wait()

        async def boot():
            calls.append(1)
            if phase == "starting":
                await wait()
            return BackendBindings(lambda _: None, FakeControl(), close=cleanup)

        first = BackendBridge(boot, start_gate=gate)
        first.start()
        try:
            if phase != "starting":
                pump_until(app, lambda: first.state is BridgeState.READY)
            if phase == "closing":
                first.close()
            second = BackendBridge(boot, start_gate=gate)
            receiver = attach(second)
            second.start()
            assert receiver.states == [LifecycleEvent(BridgeState.FAILED, "BACKEND_ALREADY_ACTIVE")]
            assert second.thread_done and second._thread is None
            second.close()
            release.set()
            pump_until(app, lambda: len(calls) == 1)
        finally:
            release.set()
            close_bridge(app, first)
            pump_until(app, lambda: not first.backend_alive)
        third = BackendBridge(boot, start_gate=gate)
        try:
            third.start()
            pump_until(app, lambda: third.state is BridgeState.READY)
            assert calls == [1, 1]
        finally:
            close_bridge(app, third)

    for phase in ["starting", "ready", "closing"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(phase)


def test_start_gate_is_atomic_and_waits_for_reported_thread_exit():
    """REC-01·REC-05, I1/I6: 경쟁 시작 정확히 1개·최종 보고 후 생존 차단; 대응 T/S ID 없음."""
    gate = bridge_module._ProcessStartGate()
    barrier = threading.Barrier(12)
    results = []
    owners = [object() for _ in range(12)]

    def contend(owner):
        barrier.wait(timeout=2)
        results.append((owner, gate.try_start(owner)))

    threads = [threading.Thread(target=contend, args=(owner,)) for owner in owners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert len(results) == 12
    winners = [owner for owner, code in results if code is None]
    assert len(winners) == 1
    assert sum(code == "BACKEND_ALREADY_ACTIVE" for _, code in results) == 11
    release = threading.Event()
    lingering = threading.Thread(target=release.wait)
    lingering.start()
    try:
        gate.finish(winners[0], "CLOSED", lingering)
        assert gate.try_start(object()) == "BACKEND_ALREADY_ACTIVE"
    finally:
        release.set()
        lingering.join(timeout=2)
    assert gate.try_start(object()) is None


def test_distinct_tasks_run_while_same_task_commands_wait_and_shutdown_settles_all(app):
    """A-local concurrency; no Codex process/T46 claim."""
    first_gate, first_entered = threading.Event(), threading.Event()
    second_gate, second_entered = threading.Event(), threading.Event()
    one = FakeRunner("one", first_gate, first_entered)
    two = FakeRunner("two", second_gate, second_entered)
    runners = {"one": one, "two": two}

    async def bootstrap():
        return BackendBindings(runners.get, FakeControl())

    bridge = BackendBridge(bootstrap, run_queue_size=8)
    receiver = attach(bridge)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        first = bridge.request_run("one")
        pump_until(app, first_entered.is_set)
        waiting = [bridge.request_run("one") for _ in range(4)]
        second = bridge.request_run("two")
        pump_until(app, second_entered.is_set)
        assert one.calls == 1
        assert two.calls == 1
        assert not first_gate.is_set()
        assert not second_gate.is_set()
        pump_until(app, lambda: all(receiver.stages(command) for command in waiting))
        close_bridge(app, bridge)
        for command in [first, *waiting, second]:
            assert receiver.stages(command)[-1].stage in (
                CommandStage.UNCONFIRMED,
                CommandStage.REJECTED,
            )
        assert not bridge._ledger.unsettled()
        assert not bridge._lanes.running()
    finally:
        first_gate.set()
        second_gate.set()
        if not bridge.thread_done:
            close_bridge(app, bridge)


@pytest.mark.parametrize(
    ("lane", "action"), [("run", "advance"), ("control", "approve"), ("control", "cancel")]
)
def test_closed_ingress_refuses_new_and_replayed_commands(lane, action):
    # A137 M05, I6: no scheduler call or queued work after close.
    ingress, ledger, scheduled = CommandIngress(2, 2), CommandLedger(2), []
    command = BridgeCommand("old", lane, action, "task")
    assert ingress.offer(command, ledger, lambda: scheduled.append(True)) is None
    assert ingress.close() == [command]
    ledger.record(command.event(CommandStage.UNCONFIRMED, "ADMISSION_UNCONFIRMED"))
    for key in ("old", "new"):
        assert (
            ingress.offer(
                BridgeCommand(key, lane, action, "task"), ledger, lambda: scheduled.append(True)
            )
            == "CLOSING"
        )
    assert scheduled == [True]
    assert ingress.waiting() == 0 and not ingress.scheduled
    assert ingress.take() is None


def test_ledger_remembers_unconfirmed_admission_and_evicts_only_old_settled_ids():
    # A137 M06/M07, I6/I8: even admission failures have a replay record.
    ledger = CommandLedger(2)
    active = BridgeCommand("active", "run", "advance", "task")
    ledger.record(active.event(CommandStage.PROCESSING, "BACKEND_STARTED"))
    events = []
    for index in range(3):
        command = BridgeCommand(str(index), "run", "advance", "task")
        event = command.event(CommandStage.UNCONFIRMED, "ADMISSION_UNCONFIRMED")
        ledger.record(event)
        events.append(event)
        assert ledger.previous(command.command_id) == event
        assert ledger.settled() == tuple(events[-2:])
    assert ledger.previous("0") is None
    assert ledger.unsettled() == (active.event(CommandStage.PROCESSING, "BACKEND_STARTED"),)


@pytest.mark.parametrize("action", sorted(ACCOUNT_ACTIONS))
def test_only_api_key_account_command_carries_the_secret(action):
    # A137 M09, I9/S04: other actions must not retain an accidentally supplied key.
    command = account_command(action, "account", "PRIVATE_KEY")
    assert command.user_text == ("PRIVATE_KEY" if action == "account_login_api_key" else None)
    assert "PRIVATE_KEY" not in repr(command)


@pytest.mark.asyncio
async def test_shutdown_rejects_each_backend_lane_before_unconfirmed_fallback(app):
    # A137 M14, I6/T20 support: enqueue before workers can take the commands.
    async def bootstrap():
        return BackendBindings(lambda _: None, FakeControl())

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge._lanes.open()
    commands = [
        BridgeCommand(lane, lane, "advance", "task") for lane in ("run", "control", "urgent")
    ]
    for command in commands:
        bridge._lanes.queue(command.lane).put_nowait(command)
        bridge._ledger.record(command.event(CommandStage.ACCEPTED, "QUEUED"))
    try:
        await asyncio.wait_for(bridge._shut_down(), 2)
        for command in commands:
            assert receiver.stages(command.command_id) == [
                command.event(CommandStage.REJECTED, "SHUTTING_DOWN")
            ]
            await asyncio.wait_for(bridge._lanes.queue(command.lane).join(), 2)
        assert bridge._ledger.unsettled() == ()
    finally:
        bridge._lanes.cancel_workers()
        await asyncio.wait_for(asyncio.gather(*bridge._lanes.active(), return_exceptions=True), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [False, True], ids=["done", "after-cancel"])
@pytest.mark.parametrize("confirmed", [False, True], ids=["unconfirmed", "confirmed"])
async def test_late_bootstrap_requires_confirmed_cleanup(late, confirmed):
    # A137 M18, I6: a successful bootstrap, even one finishing after discard's
    # cancel, counts as cleaned up only when its close returns in the window.
    owner = BindingsOwner(0.05, BackendBridge._consume_task)
    started, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    if confirmed:
        release.set()

    async def cleanup():
        entered.set()
        await asyncio.wait_for(release.wait(), 2)

    async def bootstrap():
        if late:
            started.set()
            # Stands in for a bootstrap that finishes despite the cancel.
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.Event().wait(), 2)
        return BackendBindings(lambda _: None, FakeControl(), close=cleanup)

    boot = asyncio.create_task(bootstrap())
    await asyncio.wait_for(started.wait() if late else boot, 2)
    try:
        assert await asyncio.wait_for(owner.discard(boot), 2) is confirmed
        assert boot.done() and not boot.cancelled()
        assert entered.is_set()
        assert owner.current is None
    finally:
        release.set()
        await asyncio.wait_for(owner.close(), 2)
