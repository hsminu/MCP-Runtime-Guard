"""A26/A27/A33, I1/I3/I5/I7: Qt -> bridge -> Guard -> synthetic stdio smoke."""

import asyncio
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from examples.synthetic_app import bootstrap
from guard_app.domain.enums import TaskStatus
from guard_app.services import bridge as bridge_module
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    ControlAction,
    TaskProjection,
)
from guard_app.ui.runtime import RuntimePanel

from .test_bridge import FakeControl


async def _unused():
    raise AssertionError("must not bootstrap")


def _close_panel(app, bridge, panel):
    bridge.close()
    wait_until(app, lambda: bridge.thread_done)
    panel.close()


def wait_until(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        # Release the GIL so cold SDK imports can progress on the backend worker.
        time.sleep(0.005)
    raise AssertionError("Qt/backend condition not reached")


def test_human_review_approve_and_shutdown_use_real_mcp():
    def check(mcp_transport):
        """T18 표시 연결 부분, I1/I3: 실제 승인 사본 표시·승인·SEND 수명 확인."""
        app = QApplication.instance() or QApplication([])
        bridge = BackendBridge(lambda: bootstrap(mcp_transport=mcp_transport))
        panel = RuntimePanel(bridge)
        panel.show()
        try:
            bridge.start()
            wait_until(app, panel.run_button.isEnabled)
            assert panel.current.model_mode == "mock"
            assert "모델: mock" in panel.summary.text()
            assert "AGT" not in panel.summary.text()
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
            _close_panel(app, bridge, panel)
        assert not bridge.backend_alive

    for mcp_transport in ["stdio", "http"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(mcp_transport)


def test_access_denial_explains_result_and_disabled_run_button():
    # QA 2026-10-13: only RUN_DENIED_QUERY_REQUIRED was shown, with a RUNNING
    # Task and a disabled run button left unexplained.
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap(access_policy="deny-send"))
    panel = RuntimePanel(bridge)
    try:
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        panel.run_button.click()
        wait_until(app, lambda: not panel._busy)
        message = panel.message.text()
        assert message.startswith("호출이 차단됨")
        assert "내부 코드" not in message
        assert panel.internal_code.text() == "내부 코드: RUN_DENIED_QUERY_REQUIRED"
        # R11: the shipped app never feeds the call log page, so do not send
        # the operator there for the reason.
        assert "호출 로그에서" not in message
        assert "아직 이 화면에 연결되지 않음" in message
        assert panel.current.status is TaskStatus.RUNNING and panel.current.receiver_effect == 0
        assert not panel.run_button.isEnabled() and panel.cancel_button.isEnabled()
        assert "진행 중 (RUNNING)" in panel.summary.text()
        assert "다음 사용자 지시를 기다리는 중" in panel.summary.text()
    finally:
        _close_panel(app, bridge, panel)


def test_successful_stop_reads_as_success():
    def check(button, name):
        app = QApplication.instance() or QApplication([])
        bridge = BackendBridge(lambda: bootstrap())
        panel = RuntimePanel(bridge)
        try:
            bridge.start()
            wait_until(app, panel.run_button.isEnabled)
            getattr(panel, button).click()
            wait_until(app, lambda: not panel._busy)
            assert panel.current.status is TaskStatus.HALTED
            assert panel.message.text().startswith(f"{name} 완료")
            assert panel.internal_code.text() == "내부 코드: CONTROL_DENIED_QUERY_REQUIRED"
            assert not any(b.isEnabled() for b in panel.buttons)
        finally:
            _close_panel(app, bridge, panel)

    for button, name in [("halt_button", "긴급 차단"), ("cancel_button", "작업 취소")]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(button, name)


def test_stop_without_confirmed_halt_is_not_reported_as_success():
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bridge = BackendBridge(_unused)
    panel = RuntimePanel(bridge)
    panel.set_snapshot(TaskProjection("task", 1, TaskStatus.RUNNING, can_advance=True))
    text = panel._result_text(ControlAction.EMERGENCY_HALT, "CONTROL_DENIED_QUERY_REQUIRED")
    assert text.startswith("긴급 차단 적용을 확인하지 못함")
    assert "다음 사용자 지시" not in panel.summary.text()  # can_advance: no idle hint
    bridge.close()
    panel.close()


def test_runner_status_codes_have_korean_operator_text():
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bridge = BackendBridge(_unused)
    panel = RuntimePanel(bridge)
    failures = []

    def check(action, code, start, fragment):
        text = panel._result_text(action, code)
        assert text.startswith(start) and fragment in text
        assert "도구 설정 오류" not in text
        assert "관리자에게" not in text
        if code == "RUN_WITHHELD_QUERY_REQUIRED":
            assert "계속 실행 중" not in text
            assert "정지됨" not in text
        assert code not in text  # the code stays a secondary line only
        panel._commands["command"] = action
        panel._command(
            CommandEvent("command", "run", str(action), "task", CommandStage.BACKEND_RETURNED, code)
        )
        assert panel.message.text() == text
        assert panel.internal_code.text() == f"내부 코드: {code}"

    try:
        for action, code, start, fragment in [
            ("run", "RUN_GUARD_ERROR_QUERY_REQUIRED", "Guard 내부 오류로", "이어서 실행할 수 없음"),
            ("run", "RUN_WITHHELD_QUERY_REQUIRED", "도구 결과를 공개하지 않음", "다시 조회된 표시"),
            ("run", "RUN_STOPPED_QUERY_REQUIRED", "실행이 정지됨", "새 Task"),
            (
                ControlAction.APPROVE,
                "CONTROL_GUARD_ERROR_QUERY_REQUIRED",
                "제어 처리 중 Guard 내부 오류",
                "다시 조회된 표시",
            ),
            (
                ControlAction.REJECT,
                "CONTROL_GUARD_ERROR_QUERY_REQUIRED",
                "제어 처리 중 Guard 내부 오류",
                "다시 조회된 표시",
            ),
        ]:
            try:
                check(action, code, start, fragment)
            except AssertionError as error:
                failures.append((action, code, str(error)))
        assert failures == []
    finally:
        bridge.close()
        panel.close()


def test_immediate_rejection_does_not_leave_panel_busy():
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bridge = BackendBridge(_unused)
    panel = RuntimePanel(bridge)
    panel._ready = True  # stale UI state racing backend readiness
    panel.set_snapshot(TaskProjection("task", 1, TaskStatus.RUNNING, can_advance=True))
    panel.run_button.click()
    assert not panel._busy
    assert not panel._commands
    assert "NOT_READY" not in panel.message.text()
    assert panel.internal_code.text() == "내부 코드: NOT_READY"
    bridge.close()
    panel.close()


@pytest.mark.parametrize("target", ["run", "review", "approve"])
def test_double_enter_on_safe_action_never_cancels_or_halts(target):
    # QA 2026-10-13 Q06: disabling the focused button moved focus to 작업 취소,
    # so a second Enter irreversibly stopped the Task.
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap())
    sent: list[ControlAction] = []
    request_control = bridge.request_control

    def spy(action, target_id, **kwargs):
        sent.append(action)
        return request_control(action, target_id, **kwargs)

    bridge.request_control = spy
    host = QWidget()
    panel = RuntimePanel(bridge, host)
    host.show()
    host.activateWindow()
    try:
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        if target in ("review", "approve"):
            panel.run_button.click()
            wait_until(app, panel.review_button.isEnabled)
        if target == "approve":
            panel.review_button.click()
            wait_until(app, panel.approve_button.isEnabled)
        button = getattr(panel, f"{target}_button")
        button.setFocus()
        app.processEvents()
        assert app.focusWidget() is button
        QTest.keyClick(button, Qt.Key.Key_Return)
        app.processEvents()
        focused = app.focusWidget()
        assert focused not in (panel.cancel_button, panel.halt_button)
        assert focused is panel.detail
        QTest.keyClick(focused, Qt.Key.Key_Return)
        QTest.keyClick(focused, Qt.Key.Key_Enter)
        wait_until(app, lambda: not panel._busy)
        app.processEvents()
        assert ControlAction.CANCEL not in sent
        assert ControlAction.EMERGENCY_HALT not in sent
        assert panel.current.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        # Destructive controls remain keyboard-reachable on purpose.
        assert panel.cancel_button.isEnabled()
        assert panel.cancel_button.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert not panel.cancel_button.autoDefault() and not panel.halt_button.autoDefault()
    finally:
        _close_panel(app, bridge, host)


def test_lifecycle_messages_are_current_korean_with_code_secondary():
    # QA 2026-10-13 R10: READY still read "백엔드: STARTING"; a failed start kept
    # the "시작 중" summary.
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: bootstrap())
    panel = RuntimePanel(bridge)
    try:
        assert panel.summary.text().startswith("백엔드 시작 중")
        bridge.start()
        wait_until(app, panel.run_button.isEnabled)
        assert panel.message.text() == "백엔드 준비 완료."
        assert panel.internal_code.text() == "내부 코드: READY"
        assert "STARTING" not in panel.message.text()
    finally:
        bridge.close()
        wait_until(app, lambda: bridge.thread_done)
    assert panel.message.text().startswith("백엔드 종료됨")
    assert panel.internal_code.text() == "내부 코드: CLOSED"
    panel.close()

    async def failing():
        raise RuntimeError("RAW_MARKER")

    bridge = BackendBridge(failing)
    panel = RuntimePanel(bridge)
    bridge.start()
    wait_until(app, lambda: bridge.thread_done)
    app.processEvents()
    assert panel.summary.text() == "백엔드 시작 실패 · 실행할 수 없음"
    assert panel.message.text().startswith("백엔드를 시작하지 못함")
    assert panel.internal_code.text() == "내부 코드: STARTUP_FAILED"
    assert "RAW_MARKER" not in panel.message.text()
    assert not any(button.isEnabled() for button in panel.buttons)
    bridge.close()
    panel.close()


def test_initial_snapshot_failure_or_stop_closes_resources():
    def check(mode):
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

    for mode in ["error", "timeout", "close"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(mode)


@pytest.fixture(autouse=True)
def isolate_process_start_gate(monkeypatch):
    # 의도적인 정리 미확인 테스트가 전역 게이트를 오염시켜 뒤 테스트를 깨뜨리지 않게 한다.
    monkeypatch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())


def test_restart_gate_failure_disables_every_runtime_action():
    def check(code):
        """REC-01·REC-05, I1/I6: 고정 재시작 안내와 실행·제어 비활성; 대응 T/S ID 없음."""
        from guard_app.services.bridge import LifecycleEvent
        from guard_app.ui.presentation import LIFECYCLE_MESSAGES

        app = QApplication.instance() or QApplication([])
        bridge = BackendBridge(lambda: bootstrap())
        panel = RuntimePanel(bridge)
        panel.set_snapshot(TaskProjection("task", 1, TaskStatus.WAITING_APPROVAL, "approval"))
        panel._ready = True
        panel._reviewed = "approval"
        panel._refresh()
        assert panel.approve_button.isEnabled()
        bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.FAILED, code))
        app.processEvents()
        assert panel.message.text() == LIFECYCLE_MESSAGES[code]
        assert panel.internal_code.text() == "내부 코드: " + code
        assert not any(button.isEnabled() for button in panel.buttons)
        assert panel._reviewed is None
        bridge.close()
        panel.close()

    for code in ["PROCESS_RESTART_REQUIRED", "BACKEND_ALREADY_ACTIVE"]:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
            check(code)


def test_internal_code_is_folded_under_the_message_and_follows_the_session():
    """#52 C 리뷰 UI 3: 안내 문구에는 코드가 없고, 코드는 접힌 상세에서 펼쳐 본다."""
    from guard_app.services.bridge import LifecycleEvent

    app = QApplication.instance() or QApplication([])
    app.processEvents()
    bridge = BackendBridge(_unused)
    host = QWidget()
    panel = RuntimePanel(bridge, host)
    host.show()
    host.activateWindow()
    app.processEvents()
    assert not panel.code_toggle.isVisible() and not panel.internal_code.isVisible()
    toggle = panel.code_toggle
    bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.READY, "READY"))
    app.processEvents()
    assert panel.message.text() == "백엔드 준비 완료."
    assert toggle.isVisible() and not panel.internal_code.isVisible()
    assert toggle.text() == "내부 코드 ▸" and toggle.accessibleName() == "내부 코드 펼치기"
    assert not toggle.autoDefault()
    toggle.click()
    assert panel.internal_code.isVisible()
    assert panel.internal_code.text() == "내부 코드: READY"
    assert toggle.text() == "내부 코드 ▾" and toggle.accessibleName() == "내부 코드 접기"

    # An opened code stays open for the next message and its code.
    panel.set_scope(("task", 1))
    panel._ready = True
    panel.set_snapshot(
        TaskProjection("task", 1, TaskStatus.RUNNING, can_advance=True, can_accept_message=True)
    )
    panel._commands["command"] = "run"
    panel._command_scopes["command"] = ("task", 1)
    panel._command(
        CommandEvent("command", "run", "run", "task", CommandStage.REJECTED, "NOT_READY")
    )
    assert "NOT_READY" not in panel.message.text()
    assert panel.internal_code.isVisible()
    assert panel.internal_code.text() == "내부 코드: NOT_READY"

    # "메시지 요청됨" has no code, so the toggle hides while 작업 취소 stays
    # enabled. Focus on the toggle goes to the read-only detail, never on to
    # the stop button (the unstarted bridge then rejects with NOT_READY).
    toggle.setFocus()
    app.processEvents()
    assert app.focusWidget() is toggle
    panel.send_message("task", 1, "hello", "message")
    app.processEvents()
    assert panel.cancel_button.isEnabled()
    assert app.focusWidget() is panel.detail

    # A session without a message shows no toggle. Switching back restores
    # that session's last message and code.
    panel.set_scope(("other", 1))
    assert panel.message.text() == ""
    assert not toggle.isVisible() and not panel.internal_code.isVisible()
    panel.set_scope(("task", 1))
    assert panel.message.text() == "요청이 처리되지 않음 · 백엔드 상태를 확인한 뒤 다시 시도하세요."
    assert toggle.isVisible() and panel.internal_code.text() == "내부 코드: NOT_READY"
    bridge.close()
    host.close()
