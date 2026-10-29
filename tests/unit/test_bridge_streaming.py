"""Streamed model answers reach the UI only for the running command (A90, I7/I9)."""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    PARTIAL_FLUSH_S,
    BackendBindings,
    BackendBridge,
    BridgeState,
    TaskProjection,
)
from guard_app.services.runner import RunnerStateError, RunUpdate

from .test_bridge import FakeControl, attach, close_bridge, pump_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class StreamingRunner:
    """Calls the bridge's partial listener the way the session runtime does."""

    task_id = "task-1"
    epoch = 1

    def __init__(self, final: str | None) -> None:
        self.final = final
        self.listener = None
        self._input: str | None = None
        self.efforts: list[str | None] = []

    @property
    def can_accept_message(self) -> bool:
        return self._input is None

    @property
    def can_advance(self) -> bool:
        return False

    @property
    def runtime_status(self) -> str:
        return "idle"

    def add_user_message(self, content: str, effort: str | None = None) -> None:
        self._input = content
        self.efforts.append(effort)

    async def advance(self) -> RunUpdate:
        self._input = None
        emit = self.listener
        emit("task-1", 1, "Hel")
        emit("task-1", 1, "lo\n<b>")
        emit("task-1", 2, "stale-epoch")
        emit("other-task", 1, "other-task")
        emit("task-1", 1, "")
        await asyncio.sleep(PARTIAL_FLUSH_S * 4)
        emit("task-1", 1, "x")
        # Runs after the run has ended: must not be published.
        asyncio.get_running_loop().call_soon(emit, "task-1", 1, "late")
        if self.final is None:
            return RunUpdate("task-1", 1, "halted")
        return RunUpdate("task-1", 1, "complete", assistant_text=self.final)

    async def resume_approval(self, task_id, epoch, approval_id, outcome) -> RunUpdate:
        raise RunnerStateError()

    def close(self) -> None:
        pass


def start(app, final):
    runner = StreamingRunner(final)

    async def bootstrap():
        async def snapshot(task_id):
            return TaskProjection(
                task_id, 1, TaskStatus.RUNNING, can_accept_message=runner.can_accept_message
            )

        def register(listener):
            runner.listener = listener

        return BackendBindings(
            lambda task_id: runner,
            FakeControl(),
            snapshot=snapshot,
            initial_task_id="task-1",
            answer_partials=register,
        )

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    seen = []
    bridge.conversation_partial.connect(lambda event: seen.append(("partial", event)))
    bridge.conversation_ready.connect(lambda event: seen.append(("final", event)))
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY and runner.listener is not None)
    return bridge, receiver, seen, runner


def returned(receiver, command):
    return any(event.stage.value == "backend_returned" for event in receiver.stages(command))


def test_streamed_answer_is_coalesced_bound_to_the_command_and_precedes_the_final(app):
    bridge, receiver, seen, _ = start(app, "Hello final")
    try:
        command = bridge.request_message("task-1", 1, "question")
        pump_until(app, lambda: returned(receiver, command))
        app.processEvents()
        kinds = [kind for kind, _ in seen]
        assert kinds == ["final", "partial", "partial", "final"]
        user, first, last, final = (event for _, event in seen)
        assert user.role == "user"
        assert first.text == "Hello\n<b>"
        assert last.text == "Hello\n<b>x"
        assert final.text == "Hello final"
        for event in (first, last, final):
            assert (event.task_id, event.epoch, event.role) == ("task-1", 1, "assistant")
            assert event.command_id == command
        assert all("stale-epoch" not in e.text and "other" not in e.text for _, e in seen)
        assert all("late" not in e.text for _, e in seen)
        assert "Hello" not in repr(first)
        assert not bridge._streams.active()
    finally:
        close_bridge(app, bridge)


def test_run_without_a_final_answer_still_publishes_the_rest_of_its_stream(app):
    bridge, receiver, seen, _ = start(app, None)
    try:
        command = bridge.request_message("task-1", 1, "question")
        pump_until(app, lambda: returned(receiver, command))
        app.processEvents()
        assert [kind for kind, _ in seen] == ["final", "partial", "partial"]
        assert seen[-1][1].text == "Hello\n<b>x"
    finally:
        close_bridge(app, bridge)


def test_picked_reasoning_level_reaches_the_runner_and_unknown_levels_never_queue(app):
    bridge, receiver, _, runner = start(app, "ok")
    try:
        with pytest.raises(ValueError, match="Reasoning effort is invalid"):
            bridge.request_message("task-1", 1, "question", effort="ultra")
        command = bridge.request_message("task-1", 1, "question", effort="high")
        pump_until(app, lambda: returned(receiver, command))
        assert runner.efforts == ["high"]
    finally:
        close_bridge(app, bridge)
