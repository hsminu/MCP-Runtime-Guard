"""Ephemeral user turns remain bound to a running Task/epoch (I1/I7/I8/I9)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import ModelTurn
from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import BackendBindings, BackendBridge, BridgeState, TaskProjection

from .test_bridge import FakeControl, attach, close_bridge, pump_until
from .test_runner import FakeGuard, FakeModel, FakeStore, _runner, _state


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def start_bridge(app, status=TaskStatus.RUNNING, snapshot_epoch=1):
    model = FakeModel(ModelTurn(text="<b>model answer</b>", calls=()))
    state = _state()
    store = FakeStore(replace_state := state.model_copy(update={"status": status}))
    holder = {}

    async def bootstrap():
        runner = _runner(state, model, FakeGuard(), store)
        holder["runner"] = runner

        async def snapshot(task_id):
            return TaskProjection(
                task_id, snapshot_epoch, store.state.status, can_advance=runner.can_advance
            )

        async def close():
            runner.close()

        return BackendBindings(
            lambda task_id: runner,
            FakeControl(),
            close=close,
            snapshot=snapshot,
            initial_task_id=replace_state.task_id,
        )

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    events, projections = [], []
    bridge.conversation_ready.connect(events.append)
    bridge.task_changed.connect(projections.append)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY and bool(projections))
    return bridge, receiver, events, projections, model, holder, store


def finished(receiver, command):
    return any(
        event.stage.value in ("backend_returned", "rejected", "unconfirmed")
        for event in receiver.stages(command)
    )


def test_user_turn_and_model_answer_are_ephemeral_not_command_metadata(app):
    bridge, receiver, events, projections, model, holder, store = start_bridge(app)
    try:
        assert projections[-1].can_accept_message
        command = bridge.request_message("task-1", 1, "private-user-marker")
        pump_until(app, lambda: finished(receiver, command))
        assert [event.role for event in events] == ["user", "assistant"]
        assert events[0].text == "private-user-marker"
        assert events[1].text == "<b>model answer</b>"
        assert len(model.seen) == 1
        assert projections[-1].can_accept_message
        assert "private-user-marker" not in repr(receiver.commands)
        assert "private-user-marker" not in repr(bridge._ledger.settled())
        assert "private-user-marker" not in repr(events[0])
        store.state = store.state.model_copy(update={"status": TaskStatus.CLOSED})
        command = bridge.request_message("task-1", 1, "never-reopen")
        pump_until(app, lambda: finished(receiver, command))
        assert not holder["runner"]._conversation
        assert not holder["runner"].can_accept_message
        assert len(model.seen) == 1
    finally:
        close_bridge(app, bridge)


@pytest.mark.parametrize(
    ("status", "task_id", "epoch"),
    [
        (TaskStatus.WAITING_APPROVAL, "task-1", 1),
        (TaskStatus.UNKNOWN_OUTCOME, "task-1", 1),
        (TaskStatus.HALTED, "task-1", 1),
        (TaskStatus.CLOSED, "task-1", 1),
        (TaskStatus.RUNNING, "task-1", 2),
        (TaskStatus.RUNNING, "old-task", 1),
    ],
)
def test_user_turn_cannot_resume_approval_unknown_or_wrong_task_epoch(app, status, task_id, epoch):
    bridge, receiver, events, _, model, _, _ = start_bridge(app, status)
    try:
        command = bridge.request_message(task_id, epoch, "must-not-call-model")
        pump_until(app, lambda: finished(receiver, command))
        assert not model.seen
        assert not events
    finally:
        close_bridge(app, bridge)


def test_user_turn_rejected_while_runner_has_a_model_step_to_advance(app):
    bridge, receiver, events, _, model, holder, _ = start_bridge(app)
    try:
        # Backend-only setup models the state between tool dispatch and next model step.
        holder["runner"]._awaiting_user = False
        command = bridge.request_message("task-1", 1, "must-not-overwrite-turn")
        pump_until(app, lambda: finished(receiver, command))
        assert not model.seen
        assert not events
    finally:
        close_bridge(app, bridge)


def test_new_snapshot_epoch_cannot_accept_input_into_an_old_runner(app):
    bridge, receiver, events, projections, model, holder, _ = start_bridge(app, snapshot_epoch=2)
    try:
        assert holder["runner"].epoch == 1
        assert projections[-1].epoch == 2
        assert not projections[-1].can_accept_message
        command = bridge.request_message("task-1", 2, "must-not-enter-old-history")
        pump_until(app, lambda: finished(receiver, command))
        assert not events
        assert not model.seen
        assert len(holder["runner"]._conversation) == 1  # system message only
    finally:
        close_bridge(app, bridge)


@pytest.mark.parametrize(
    ("blocker", "create", "expected"),
    [
        (None, False, "SESSION_CREATION_UNAVAILABLE"),
        (lambda: "CODEX_CONFIG_UNAVAILABLE", True, "CODEX_CONFIG_UNAVAILABLE"),
        (lambda: None, True, ""),
    ],
)
def test_bridge_publishes_session_capability_before_ready(app, blocker, create, expected):
    async def create_session(_options):
        raise AssertionError("not requested")

    async def bootstrap():
        return BackendBindings(
            lambda task_id: None,
            FakeControl(),
            create_session=create_session if create else None,
            session_blocker=blocker,
        )

    bridge = BackendBridge(bootstrap)
    seen: list[str] = []
    states: list[BridgeState] = []
    bridge.session_capability_changed.connect(lambda code: seen.append(code))
    bridge.lifecycle_changed.connect(lambda event: states.append(event.state))
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(seen))
        assert seen == [expected]
    finally:
        close_bridge(app, bridge)


def test_backend_task_event_republishes_the_task_without_a_command(app):
    """Approval waits and asynchronous session stops reach the UI (I8): the
    bridge re-reads the authoritative snapshot, never a value from the event."""
    import asyncio

    status = {"value": TaskStatus.RUNNING}
    registered, loops, blockers = [], [], []

    async def snapshot(task_id):
        return TaskProjection(task_id, 1, status["value"])

    async def create_session(_options):
        raise AssertionError("not requested")

    def blocker():
        blockers.append(True)

    async def bootstrap():
        loops.append(asyncio.get_running_loop())
        return BackendBindings(
            lambda task_id: None,
            FakeControl(),
            snapshot=snapshot,
            create_session=create_session,
            session_blocker=blocker,
            task_events=registered.append,
        )

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    projections, capabilities = [], []
    bridge.task_changed.connect(projections.append)
    bridge.session_capability_changed.connect(capabilities.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(registered))
        assert len(registered) == 1
        assert projections == []
        checks = len(blockers)
        status["value"] = TaskStatus.HALTED
        loops[0].call_soon_threadsafe(registered[0], "task-1")
        pump_until(app, lambda: bool(projections))
        assert [(p.task_id, p.status) for p in projections] == [("task-1", TaskStatus.HALTED)]
        assert len(blockers) > checks  # session capability re-read as well
        for invalid in ("", None):
            loops[0].call_soon_threadsafe(registered[0], invalid)
        status["value"] = TaskStatus.WAITING_APPROVAL
        loops[0].call_soon_threadsafe(registered[0], "task-1")
        pump_until(app, lambda: len(projections) == 2)
        assert projections[-1].status is TaskStatus.WAITING_APPROVAL
        assert receiver.commands == []  # no command was invented for it
    finally:
        close_bridge(app, bridge)


def _guarded_codex_bridge(app, stops, resolved):
    from guard_app.services.runner import CodexTaskRunner

    control = FakeControl()

    class Session:
        async def send(self, text, effort=None):
            return "unused"

        async def interrupt(self):
            pass

        async def aclose(self):
            pass

    async def failure(_code):
        pass

    runner = CodexTaskRunner("task-1", 1, Session(), failure)

    async def snapshot(task_id):
        return TaskProjection(task_id, 1, TaskStatus.RUNNING)

    async def stop_session(task_id, epoch, action):
        stops.append((task_id, epoch, str(action)))

    async def bootstrap():
        return BackendBindings(
            lambda task_id: runner,
            control,
            snapshot=snapshot,
            initial_task_id="task-1",
            stop_session=stop_session,
        )

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY)
    return bridge, receiver, control


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_human_approval_goes_to_guard_only_never_to_the_model_call(app, action):
    """NOTIFY-01: the waiting model call hears Guard's final result, not this return."""
    from guard_app.services.bridge import ControlAction

    stops, resolved = [], []
    bridge, receiver, control = _guarded_codex_bridge(app, stops, resolved)
    try:
        command = bridge.request_control(ControlAction(action), "approval-7")
        pump_until(app, lambda: finished(receiver, command))
        assert [(call[0], call[1]) for call in control.calls] == [(action, "approval-7")]
        assert resolved == []
        assert stops == []
    finally:
        close_bridge(app, bridge)


@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
def test_task_stop_with_guard_also_stops_the_model_session(app, action):
    from guard_app.services.bridge import ControlAction

    stops, resolved = [], []
    bridge, receiver, control = _guarded_codex_bridge(app, stops, resolved)
    try:
        command = bridge.request_control(ControlAction(action), "task-1", epoch=1)
        pump_until(app, lambda: finished(receiver, command))
        assert [call[0] for call in control.calls] == [action]
        assert stops == [("task-1", 1, action)]
    finally:
        close_bridge(app, bridge)
