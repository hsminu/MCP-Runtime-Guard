"""Human controls consume bridge messages only; no store or transport access."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    ApprovalReview,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    ControlAction,
    LifecycleEvent,
    TaskProjection,
)

from .approval_detail import visible_review_text
from .presentation import APPROVAL_RESULTS, LIFECYCLE_MESSAGES, RUN_RESULTS
from .task_status import status_title
from .widgets import KeyboardActionButton, plain_label

_STOP_NAMES = {ControlAction.CANCEL: "작업 취소", ControlAction.EMERGENCY_HALT: "긴급 차단"}
_RUN = "run"
# Fallback when the bridge reports a lifecycle code without a fixed message.
_STATE_MESSAGES = {
    BridgeState.STARTING: LIFECYCLE_MESSAGES["STARTING"],
    BridgeState.READY: LIFECYCLE_MESSAGES["READY"],
    BridgeState.FAILED: "백엔드 오류로 멈췄어 · 더 실행할 수 없어.",
    BridgeState.CLOSING: LIFECYCLE_MESSAGES["CLOSING"],
    BridgeState.CLOSED: LIFECYCLE_MESSAGES["CLOSED"],
}
_NO_TASK_SUMMARY: dict[BridgeState | None, str] = {
    None: "백엔드 시작 중 · 아직 실행할 수 없어",
    BridgeState.STARTING: "백엔드 시작 중 · 아직 실행할 수 없어",
    BridgeState.READY: "백엔드 준비 완료 · 표시할 Task가 없어 실행할 수 없어",
    BridgeState.FAILED: "백엔드 시작 실패 · 실행할 수 없어",
    BridgeState.CLOSING: "백엔드 종료 중 · 실행할 수 없어",
    BridgeState.CLOSED: "백엔드 종료됨 · 실행할 수 없어",
}


class RuntimePanel(QWidget):
    def __init__(self, bridge: BackendBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.current: TaskProjection | None = None
        self._ready = False
        self._busy = False
        self._review_command: str | None = None
        self._reviewed: str | None = None
        self._commands: dict[str, ControlAction | str] = {}
        layout = QVBoxLayout(self)
        self.summary = plain_label(_NO_TASK_SUMMARY[None])
        self.message = plain_label("상태 확인 대기")
        layout.addWidget(self.summary)
        layout.addWidget(self.message)
        self.run_button = KeyboardActionButton("실행 / 다음 모델 단계")
        self.review_button = KeyboardActionButton("승인 사본 확인")
        self.approve_button = KeyboardActionButton("확인한 사본 승인")
        self.reject_button = KeyboardActionButton("거절")
        self.cancel_button = KeyboardActionButton("작업 취소")
        self.halt_button = KeyboardActionButton("긴급 차단")
        for button in self.buttons:
            layout.addWidget(button)
        for button in (self.cancel_button, self.halt_button):
            # Irreversible stops are never triggered by a default-button Enter.
            button.setAutoDefault(False)
            button.setDefault(False)
        self.detail = QPlainTextEdit()
        self.detail.setAccessibleName("승인 사본 상세")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.detail.setMinimumHeight(180)
        layout.addWidget(self.detail)
        self.run_button.clicked.connect(self._run)
        self.review_button.clicked.connect(lambda: self._control(ControlAction.REVIEW))
        self.approve_button.clicked.connect(lambda: self._control(ControlAction.APPROVE))
        self.reject_button.clicked.connect(lambda: self._control(ControlAction.REJECT))
        self.cancel_button.clicked.connect(lambda: self._control(ControlAction.CANCEL))
        self.halt_button.clicked.connect(lambda: self._control(ControlAction.EMERGENCY_HALT))
        bridge.lifecycle_changed.connect(self._lifecycle)
        bridge.command_changed.connect(self._command)
        bridge.task_changed.connect(self.set_snapshot)
        bridge.review_ready.connect(self._review)
        self._refresh()

    @property
    def buttons(self):
        return (
            self.run_button,
            self.review_button,
            self.approve_button,
            self.reject_button,
            self.cancel_button,
            self.halt_button,
        )

    def _refresh(self):
        active = self._ready and self.current is not None
        waiting = active and self.current.status is TaskStatus.WAITING_APPROVAL
        stoppable = bool(
            active and self.current.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        )
        enabled = {
            self.run_button: bool(
                active
                and not self._busy
                and self.current.status is TaskStatus.RUNNING
                and self.current.can_advance
            ),
            self.review_button: bool(waiting and not self._busy and self.current.approval_id),
            self.approve_button: bool(
                waiting
                and not self._busy
                and self._reviewed == self.current.approval_id
                and self._reviewed
            ),
            self.reject_button: bool(waiting and not self._busy and self.current.approval_id),
            self.cancel_button: stoppable,
            self.halt_button: stoppable,
        }
        # Disabling the focused button lets Qt move focus to the next enabled
        # button, which is 작업 취소 after run/review/approve. A repeated Enter
        # would then irreversibly stop the Task, so park focus on the read-only
        # detail first. Cancel and halt stay reachable by Tab and mouse.
        focused = QApplication.focusWidget()
        if focused in enabled and not enabled[focused]:
            self.detail.setFocus(Qt.FocusReason.OtherFocusReason)
        for button, value in enabled.items():
            button.setEnabled(value)

    def _clear_review(self):
        self._review_command = None
        self._reviewed = None
        self.detail.clear()

    def _run(self):
        if not self.run_button.isEnabled() or self.current is None:
            return
        self._busy = True
        self._clear_review()
        self.message.setText("실행 요청됨 · 백엔드 결과 대기")
        self._refresh()
        command_id = uuid4().hex
        self._commands[command_id] = _RUN
        self.bridge.request_run(self.current.task_id, command_id=command_id)

    def _control(self, action):
        if self.current is None:
            return
        target = (
            self.current.task_id
            if action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT)
            else self.current.approval_id
        )
        if target is None:
            return
        self._busy = True
        self._clear_review()
        self.message.setText("제어 요청됨 · 적용 여부 확인 중")
        self._refresh()
        command_id = uuid4().hex
        self._commands[command_id] = action
        if action is ControlAction.REVIEW:
            self._review_command = command_id
        self.bridge.request_control(action, target, command_id=command_id)

    def _lifecycle(self, event: LifecycleEvent):
        self._ready = event.state is BridgeState.READY
        if not self._ready:
            self._clear_review()
        text = LIFECYCLE_MESSAGES.get(event.code) or _STATE_MESSAGES.get(
            event.state, "백엔드 상태를 확인하지 못했어 · 아직 실행할 수 없어."
        )
        self.message.setText(f"{text}\n내부 코드: {event.code}")
        if self.current is None:
            # Without a Task snapshot the summary is the only status line, so it
            # must follow the backend state instead of staying at "starting".
            self.summary.setText(_NO_TASK_SUMMARY.get(event.state, _NO_TASK_SUMMARY[None]))
        self._refresh()

    def set_snapshot(self, snapshot: TaskProjection):
        self._clear_review()
        self.current = snapshot
        lines = [
            f"Task: {snapshot.task_id} · {status_title(snapshot.status)}",
            f"AGT: {snapshot.agt_mode} · 모델: {snapshot.model_mode}",
            f"호스트 dispatch: {snapshot.host_dispatch} · 수신 부작용: {snapshot.receiver_effect}",
        ]
        if snapshot.status is TaskStatus.RUNNING and not snapshot.can_advance:
            # RUNNING with a disabled run button must not look like a stuck UI.
            lines.append(
                "다음 사용자 지시를 기다리는 중이라 실행할 모델 단계가 없어. "
                "여기서 끝내려면 '작업 취소'를 눌러."
            )
        self.summary.setText("\n".join(lines))
        self._refresh()

    def _review(self, review: ApprovalReview):
        if (
            self.current is None
            or review.command_id != self._review_command
            or review.approval_id != self.current.approval_id
            or review.task_id != self.current.task_id
            or review.epoch != self.current.epoch
            or self.current.status is not TaskStatus.WAITING_APPROVAL
        ):
            return
        self.detail.setPlainText(
            visible_review_text(
                review.receiving_boundaries
                + "\n전체 인자:\n"
                + review.arguments_bytes.decode("utf-8")
            )
        )
        self._reviewed = review.approval_id
        self._refresh()

    def _command(self, event: CommandEvent):
        action = self._commands.get(event.command_id)
        if action is None:
            return
        if event.stage not in (
            CommandStage.REJECTED,
            CommandStage.UNCONFIRMED,
            CommandStage.BACKEND_RETURNED,
        ):
            return
        del self._commands[event.command_id]
        self._busy = bool(self._commands)
        if event.stage is CommandStage.BACKEND_RETURNED:
            text = self._result_text(action, event.code)
        elif event.stage is CommandStage.REJECTED:
            self._clear_review()
            text = "요청이 처리되지 않았어 · 백엔드 상태를 확인한 뒤 다시 시도해."
        else:
            self._clear_review()
            text = "적용 여부를 확인하지 못했어 · 위 Task 상태를 확인해."
        # Internal codes stay available for support, never as the message itself.
        self.message.setText(f"{text}\n내부 코드: {event.code}")
        self._refresh()

    def _result_text(self, action: ControlAction | str, code: str) -> str:
        name = _STOP_NAMES.get(action) if isinstance(action, ControlAction) else None
        if name is not None:
            # Cancel and halt always return Denied; success is the Task state
            # the backend published before this command result.
            stopped = self.current is not None and self.current.status in (
                TaskStatus.HALTED,
                TaskStatus.CLOSED,
            )
            if code.startswith("CONTROL_") and stopped:
                return (
                    f"{name} 완료 · Task를 정지해 이후 호출은 실행되지 않아. "
                    "이미 전송된 쓰기는 수신됐을 수 있어."
                )
            return f"{name} 적용을 확인하지 못했어 · 위 Task 상태를 확인해."
        results = RUN_RESULTS if action == _RUN else APPROVAL_RESULTS
        return results.get(code, "백엔드 응답을 해석하지 못했어 · 위 Task 상태를 확인해.")
