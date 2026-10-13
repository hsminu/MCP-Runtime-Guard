"""A26/A27/A33, I1/I3/I5/I7: Qt -> bridge -> Guard -> synthetic stdio smoke."""

import asyncio
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from examples.synthetic_app import bootstrap
from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    ControlAction,
    TaskProjection,
)
from guard_app.ui.runtime import RuntimePanel

from .test_bridge import FakeControl


def wait_until(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        # Release the GIL so cold SDK imports can progress on the backend worker.
        time.sleep(0.005)
    raise AssertionError("Qt/backend condition not reached")


@pytest.mark.parametrize("agt_mode", ["real", "mock"])
@pytest.mark.parametrize("mcp_transport", ["stdio", "http"])
def test_human_review_approve_and_shutdown_use_real_mcp(agt_mode, mcp_transport):
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap(agt_mode=agt_mode, mcp_transport=mcp_transport))
    panel = RuntimePanel(bridge)
    panel.show()
    try:
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        assert panel.current.agt_mode == agt_mode
        assert f"AGT: {agt_mode}" in panel.summary.text()
        panel.run_button.click()
        assert not panel.run_button.isEnabled()
        wait_until(app, panel.review_button.isEnabled)
        assert panel.current.status is TaskStatus.WAITING_APPROVAL
        assert panel.current.receiver_effect == 0
        assert not panel.approve_button.isEnabled()
        panel.review_button.click()
        wait_until(app, panel.approve_button.isEnabled)
        assert '"dest":"external"' in panel.detail.toPlainText()
        panel.approve_button.click()
        panel.approve_button.click()
        assert not panel.approve_button.isEnabled()
        wait_until(app, lambda: panel.current.receiver_effect == 1)
        assert panel.current.host_dispatch == 2  # READ + exactly one SEND
        assert panel.current.status is TaskStatus.RUNNING
        assert not panel.detail.toPlainText()
        wait_until(app, panel.run_button.isEnabled)
        panel.run_button.click()
        wait_until(app, lambda: not panel._busy)
        assert panel.current.receiver_effect == 1
        assert not panel.run_button.isEnabled()
    finally:
        bridge.close()
        wait_until(app, lambda: bridge.thread_done)
        panel.close()
    assert not bridge.backend_alive


def test_agt_denial_explains_result_and_disabled_run_button():
    # QA 2026-10-13: only RUN_DENIED_QUERY_REQUIRED was shown, with a RUNNING
    # Task and a disabled run button left unexplained.
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap(agt_mode="real", agt_policy="deny-send"))
    panel = RuntimePanel(bridge)
    try:
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        panel.run_button.click()
        wait_until(app, lambda: not panel._busy)
        message = panel.message.text()
        assert message.startswith("호출이 차단됐어")
        assert message.endswith("내부 코드: RUN_DENIED_QUERY_REQUIRED")
        assert panel.current.status is TaskStatus.RUNNING and panel.current.receiver_effect == 0
        assert not panel.run_button.isEnabled() and panel.cancel_button.isEnabled()
        assert "진행 중 · RUNNING" in panel.summary.text()
        assert "다음 사용자 지시를 기다리는 중" in panel.summary.text()
    finally:
        bridge.close()
        wait_until(app, lambda: bridge.thread_done)
        panel.close()


@pytest.mark.parametrize(
    "button,name", [("halt_button", "긴급 차단"), ("cancel_button", "작업 취소")]
)
def test_successful_stop_reads_as_success(button, name):
    # QA 2026-10-13: a successful emergency halt showed CONTROL_DENIED_... only.
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap(agt_mode="real"))
    panel = RuntimePanel(bridge)
    try:
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        getattr(panel, button).click()
        wait_until(app, lambda: not panel._busy)
        assert panel.current.status is TaskStatus.HALTED
        assert panel.message.text().startswith(f"{name} 완료")
        assert panel.message.text().endswith("내부 코드: CONTROL_DENIED_QUERY_REQUIRED")
        assert not any(b.isEnabled() for b in panel.buttons)
    finally:
        bridge.close()
        wait_until(app, lambda: bridge.thread_done)
        panel.close()


def test_stop_without_confirmed_halt_is_not_reported_as_success():
    app = QApplication.instance() or QApplication([])
    app.processEvents()

    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    panel = RuntimePanel(bridge)
    panel.set_snapshot(TaskProjection("task", 1, TaskStatus.RUNNING, can_advance=True))
    text = panel._result_text(ControlAction.EMERGENCY_HALT, "CONTROL_DENIED_QUERY_REQUIRED")
    assert text.startswith("긴급 차단 적용을 확인하지 못했어")
    assert "다음 사용자 지시" not in panel.summary.text()  # can_advance: no idle hint
    bridge.close()
    panel.close()


def test_immediate_rejection_does_not_leave_panel_busy():
    app = QApplication.instance() or QApplication([])
    app.processEvents()

    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    panel = RuntimePanel(bridge)
    panel._ready = True  # stale UI state racing backend readiness
    panel.set_snapshot(TaskProjection("task", 1, TaskStatus.RUNNING, can_advance=True))
    panel.run_button.click()
    assert not panel._busy
    assert not panel._commands
    assert "NOT_READY" in panel.message.text()
    bridge.close()
    panel.close()


@pytest.mark.parametrize("mode", ["error", "timeout", "close"])
def test_initial_snapshot_failure_or_stop_closes_resources(mode):
    app = QApplication.instance() or QApplication([])
    entered, cleaned = threading.Event(), threading.Event()

    async def snapshot(task_id):
        entered.set()
        if mode == "error":
            raise ValueError("RAW_MARKER")
        await asyncio.Event().wait()

    async def cleanup():
        cleaned.set()

    async def boot():
        return BackendBindings(
            lambda task_id: None,
            FakeControl(),
            close=cleanup,
            snapshot=snapshot,
            initial_task_id="task",
        )

    bridge = BackendBridge(boot, startup_timeout=0.1)
    bridge.start()
    wait_until(app, entered.is_set)
    if mode == "close":
        bridge.close()
    wait_until(app, lambda: bridge.thread_done)
    assert cleaned.is_set()
    assert bridge.state is (BridgeState.CLOSED if mode == "close" else BridgeState.FAILED)
