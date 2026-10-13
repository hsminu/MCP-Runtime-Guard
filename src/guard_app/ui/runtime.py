"""Human controls consume bridge messages only; no store or transport access."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

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
from .widgets import KeyboardActionButton, plain_label


class RuntimePanel(QWidget):
    def __init__(self, bridge: BackendBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.current: TaskProjection | None = None
        self._ready = False
        self._busy = False
        self._review_command: str | None = None
        self._reviewed: str | None = None
        self._commands: set[str] = set()
        layout = QVBoxLayout(self)
        self.summary = plain_label("백엔드 시작 중 · 아직 실행할 수 없어")
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
        self.detail = QPlainTextEdit()
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
        self.run_button.setEnabled(
            bool(
                active
                and not self._busy
                and self.current.status is TaskStatus.RUNNING
                and self.current.can_advance
            )
        )
        self.review_button.setEnabled(bool(waiting and not self._busy and self.current.approval_id))
        self.approve_button.setEnabled(
            bool(
                waiting
                and not self._busy
                and self._reviewed == self.current.approval_id
                and self._reviewed
            )
        )
        self.reject_button.setEnabled(bool(waiting and not self._busy and self.current.approval_id))
        for button in (self.cancel_button, self.halt_button):
            button.setEnabled(
                bool(
                    active
                    and self.current.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
                )
            )

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
        self._commands.add(command_id)
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
        self._commands.add(command_id)
        if action is ControlAction.REVIEW:
            self._review_command = command_id
        self.bridge.request_control(action, target, command_id=command_id)

    def _lifecycle(self, event: LifecycleEvent):
        self._ready = event.state is BridgeState.READY
        if not self._ready:
            self._clear_review()
            self.message.setText(f"백엔드: {event.code}")
        self._refresh()

    def set_snapshot(self, snapshot: TaskProjection):
        self._clear_review()
        self.current = snapshot
        self.summary.setText(
            f"Task: {snapshot.task_id} · {snapshot.status.value}\n"
            f"AGT: {snapshot.agt_mode} · 모델: {snapshot.model_mode}\n"
            f"호스트 dispatch: {snapshot.host_dispatch} · 수신 부작용: {snapshot.receiver_effect}"
        )
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
        if event.command_id not in self._commands:
            return
        if event.stage not in (
            CommandStage.REJECTED,
            CommandStage.UNCONFIRMED,
            CommandStage.BACKEND_RETURNED,
        ):
            return
        self._commands.discard(event.command_id)
        self._busy = bool(self._commands)
        if event.stage is CommandStage.BACKEND_RETURNED:
            self.message.setText(f"백엔드 응답 확인 · {event.code} · 위 Task 상태가 확정값이야")
        else:
            self._clear_review()
            self.message.setText(f"적용 미확인 · {event.code}")
        self._refresh()
