"""Human controls consume bridge messages only; no store or transport access."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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
    new_command_id,
)

from .approval_detail import visible_review_text
from .presentation import (
    APPROVAL_RESULTS,
    LIFECYCLE_MESSAGES,
    MISSING,
    RUN_RESULTS,
    task_label_text,
)
from .recovery import RecoveryPanel
from .task_status import FINAL_STATUSES, status_code, status_detail, status_title
from .widgets import Disclosure, KeyboardActionButton, plain_label

_STOP_NAMES = {ControlAction.CANCEL: "작업 취소", ControlAction.EMERGENCY_HALT: "긴급 차단"}
_RUN = "run"
# Fallback when the bridge reports a lifecycle code without a fixed message.
_STATE_MESSAGES = {
    BridgeState.STARTING: LIFECYCLE_MESSAGES["STARTING"],
    BridgeState.READY: LIFECYCLE_MESSAGES["READY"],
    BridgeState.FAILED: "백엔드 오류로 멈춤 · 더 실행할 수 없음.",
    BridgeState.CLOSING: LIFECYCLE_MESSAGES["CLOSING"],
    BridgeState.CLOSED: LIFECYCLE_MESSAGES["CLOSED"],
}
_NO_TASK_SUMMARY: dict[BridgeState | None, str] = {
    None: "백엔드 시작 중 · 아직 실행할 수 없음",
    BridgeState.STARTING: "백엔드 시작 중 · 아직 실행할 수 없음",
    BridgeState.READY: "백엔드 준비 완료 · 표시할 Task가 없어 실행할 수 없음",
    BridgeState.FAILED: "백엔드 시작 실패 · 실행할 수 없음",
    BridgeState.CLOSING: "백엔드 종료 중 · 실행할 수 없음",
    BridgeState.CLOSED: "백엔드 종료됨 · 실행할 수 없음",
}


def _count(value: int | None) -> str:
    # Codex Tasks have no Guard counters yet; never print Python's "None".
    return MISSING if value is None else str(value)


class RuntimePanel(QWidget):
    state_changed = Signal()
    message_rejected = Signal(str)
    selected_snapshot_changed = Signal(object)

    def __init__(self, bridge: BackendBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.current: TaskProjection | None = None
        self._ready = False
        self._review_command: str | None = None
        self._reviewed: str | None = None
        self._commands: dict[str, ControlAction | str] = {}
        self._command_scopes: dict[str, tuple[str, int]] = {}
        # Each session keeps its last status line and the internal code under it.
        self._messages: dict[tuple[str, int], tuple[str, str | None]] = {}
        self._code: str | None = None
        self._scope_enabled = False
        self._scope: tuple[str, int] | None = None
        self._snapshots: dict[tuple[str, int], TaskProjection] = {}
        layout = QVBoxLayout(self)
        self._sections = layout
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Flat sections divided by hairlines, not boxed cards (no card in a card).
        session = QFrame()
        session.setObjectName("runtimeCard")
        session_layout = QVBoxLayout(session)
        session_layout.setContentsMargins(0, 16, 0, 16)
        session_layout.setSpacing(12)
        session_layout.addWidget(plain_label("실행 컨트롤", name="cardTitle"))
        self.summary = plain_label(_NO_TASK_SUMMARY[None], name="secondary")
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.message = plain_label("상태 확인 대기", name="runtimeMessage")
        self.diagnostics = Disclosure("진단 정보")
        self.diagnostic_text = plain_label(name="secondary")
        self.diagnostic_text.setAccessibleName("세션 진단 정보")
        self.diagnostics.set_body(self.diagnostic_text)
        self.diagnostics.hide()

        # Demos and support need the internal code, so it stays one click away
        # under the message instead of being part of it (#52 C review).
        self.code_toggle = KeyboardActionButton("내부 코드 ▸")
        self.code_toggle.setObjectName("linkButton")
        self.code_toggle.setCheckable(True)
        self.code_toggle.setAutoDefault(False)
        self.code_toggle.setAccessibleName("내부 코드 펼치기")
        self.internal_code = plain_label(name="secondary")
        self.run_button = KeyboardActionButton("실행 / 다음 모델 단계")
        self.run_button.set_visual(icon="play", variant="primary")
        self._session_actions = QGridLayout()
        self._session_actions.setSpacing(12)
        session_layout.addLayout(self._session_actions)
        notice = QVBoxLayout()
        session_layout.addLayout(notice)
        notice.setSpacing(0)
        notice.addWidget(self.diagnostics)
        notice.addWidget(self.message)
        notice.addWidget(self.code_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        notice.addWidget(self.internal_code)
        layout.addWidget(session)

        approval = QFrame()
        approval.setObjectName("approvalCard")
        self.approval_section = approval
        approval_layout = QVBoxLayout(approval)
        approval_layout.setContentsMargins(0, 16, 0, 16)
        approval_layout.setSpacing(12)
        approval_layout.addWidget(plain_label("승인 검토", name="cardTitle"))
        approval_layout.addWidget(
            plain_label("요청의 수신처와 전체 인자를 확인한 뒤 승인하세요.", name="secondary")
        )
        self.review_button = KeyboardActionButton("승인 사본 확인")
        self.review_button.set_visual(icon="eye", variant="soft")
        self.approve_button = KeyboardActionButton("확인한 사본 승인")
        self.approve_button.set_visual(icon="check", variant="primary")
        self.reject_button = KeyboardActionButton("거절")
        self.reject_button.set_visual(icon="close")
        self.cancel_button = KeyboardActionButton("작업 취소")
        self.cancel_button.set_visual(icon="close")
        self.halt_button = KeyboardActionButton("긴급 차단")
        self.halt_button.set_visual(icon="stop", variant="danger")
        self._approval_actions = QGridLayout()
        self._approval_actions.setSpacing(8)
        approval_layout.addLayout(self._approval_actions)
        for button in (self.cancel_button, self.halt_button):
            # Irreversible stops are never triggered by a default-button Enter.
            button.setAutoDefault(False)
            button.setDefault(False)
        self.detail = QPlainTextEdit()
        self.detail.setAccessibleName("승인 사본 상세")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.detail.setPlaceholderText(
            "승인 요청이 도착하면 ‘승인 사본 확인’을 눌러 내용을 확인하세요."
        )
        approval_layout.addWidget(self.detail)
        self._fit_detail()
        layout.addWidget(approval)

        stop = QFrame()
        stop.setObjectName("stopCard")
        self._stop_section = stop
        self._stop_actions = QGridLayout(stop)
        self._stop_actions.setContentsMargins(0, 16, 0, 0)
        self._stop_actions.setSpacing(8)
        self._stop_label = plain_label("작업 제어", name="secondary")
        layout.addWidget(stop)
        self.recovery = RecoveryPanel(bridge)
        layout.addWidget(self.recovery)
        self._compact_actions: bool | None = None
        self._reflow_actions()
        # Match keyboard order to the visual groups. Reflow never changes focus.
        for previous, following in zip(self.buttons, self.buttons[1:], strict=False):
            QWidget.setTabOrder(previous, following)
        # The code toggle sits under the run button, before the approval section.
        QWidget.setTabOrder(self.run_button, self.code_toggle)
        QWidget.setTabOrder(self.code_toggle, self.review_button)
        self.code_toggle.toggled.connect(lambda _opened: self._fold_code())
        self._fold_code()
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
    def busy_scopes(self) -> frozenset[tuple[str, int]]:
        return frozenset(self._command_scopes.values())

    @property
    def _busy(self) -> bool:
        return (
            self.current is not None
            and (self.current.task_id, self.current.epoch) in self.busy_scopes
        )

    @property
    def _control_busy(self) -> bool:
        """A human control is in flight for this Task. A model turn that is
        itself waiting for this approval must not lock the approval buttons."""
        current = self.current
        if current is None:
            return False
        scope = (current.task_id, current.epoch)
        return any(
            self._command_scopes.get(command_id) == scope
            for command_id, action in self._commands.items()
            if isinstance(action, ControlAction)
        )

    @property
    def buttons(self) -> tuple[KeyboardActionButton, ...]:
        return (
            self.run_button,
            self.review_button,
            self.approve_button,
            self.reject_button,
            self.cancel_button,
            self.halt_button,
        )

    def pin_stop_section(self) -> QFrame:
        """Hand the stop controls to a host that pins them under its scroll
        area, so cancel and emergency halt are never scrolled out of view."""
        self._sections.removeWidget(self._stop_section)
        self._stop_actions.setContentsMargins(24, 12, 24, 12)
        return self._stop_section

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_compact_actions"):
            self._reflow_actions()

    def _reflow_actions(self) -> None:
        compact = self.width() < 600
        if compact == self._compact_actions:
            return
        self._compact_actions = compact
        for grid in (self._session_actions, self._approval_actions, self._stop_actions):
            while grid.count():
                grid.takeAt(0)
            for column in range(4):
                grid.setColumnStretch(column, 0)
        if compact:
            self._session_actions.addWidget(self.summary, 0, 0)
            self._session_actions.addWidget(self.run_button, 1, 0)
            for row, button in enumerate(
                (self.review_button, self.approve_button, self.reject_button)
            ):
                self._approval_actions.addWidget(button, row, 0)
            self._stop_actions.addWidget(self._stop_label, 0, 0)
            self._stop_actions.addWidget(self.cancel_button, 1, 0)
            self._stop_actions.addWidget(self.halt_button, 2, 0)
        else:
            self._session_actions.addWidget(self.summary, 0, 0)
            self._session_actions.addWidget(self.run_button, 0, 1)
            self._session_actions.setColumnStretch(0, 1)
            for column, button in enumerate(
                (self.review_button, self.approve_button, self.reject_button)
            ):
                self._approval_actions.addWidget(button, 0, column)
            self._approval_actions.setColumnStretch(3, 1)
            self._stop_actions.addWidget(self._stop_label, 0, 0)
            self._stop_actions.addWidget(self.cancel_button, 0, 1)
            self._stop_actions.addWidget(self.halt_button, 0, 2)
            self._stop_actions.setColumnStretch(0, 1)

    def _refresh(self) -> None:
        current = self.current if self._ready else None
        if self._scope_enabled and (
            current is None or self._scope != (current.task_id, current.epoch)
        ):
            current = None
        waiting = current is not None and current.status is TaskStatus.WAITING_APPROVAL
        stoppable = bool(
            current is not None
            and current.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        )
        enabled = {
            self.run_button: bool(
                current is not None
                and not self._busy
                and current.status is TaskStatus.RUNNING
                and current.can_advance
                and current.model_ready
            ),
            self.review_button: bool(
                current is not None and waiting and not self._control_busy and current.approval_id
            ),
            self.approve_button: bool(
                current is not None
                and waiting
                and not self._control_busy
                and self._reviewed == current.approval_id
                and self._reviewed
            ),
            self.reject_button: bool(
                current is not None and waiting and not self._control_busy and current.approval_id
            ),
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
        self._mark_approval(waiting)
        self.state_changed.emit()
        self.selected_snapshot_changed.emit(self.current)

    def _show_message(self, text: str, code: str | None = None) -> None:
        # An empty status line takes no room in the flat section.
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self._code = code
        self.internal_code.setText("" if code is None else f"내부 코드: {code}")
        self._fold_code()

    def _fold_code(self) -> None:
        """Show the code toggle only for a message with a code. Once opened,
        it stays open for the following messages."""
        shown = self._code is not None
        opened = shown and self.code_toggle.isChecked()
        if not shown and self.code_toggle.hasFocus():
            # Hiding the focused toggle would hand focus to the next button,
            # which can be 작업 취소. Park it on the read-only detail instead.
            self.detail.setFocus(Qt.FocusReason.OtherFocusReason)
        self.code_toggle.setVisible(shown)
        self.code_toggle.setText(f"내부 코드 {'▾' if opened else '▸'}")
        self.code_toggle.setAccessibleName(f"내부 코드 {'접기' if opened else '펼치기'}")
        self.internal_code.setVisible(opened)

    def set_scope(self, scope: tuple[str, int] | None) -> None:
        """Approval copies and controls are bound to the selected session."""
        self._scope_enabled = True
        if scope != self._scope:
            self._clear_review()
            if scope is None:
                self._show_message("새 세션 · 시작을 기다리는 중임.")
            else:
                self._show_message(*self._messages.get(scope, ("", None)))
        self._scope = scope
        self.current = self._snapshots.get(scope) if scope is not None else None
        if self.current is not None:
            self._render_snapshot(self.current)
        else:
            self.diagnostics.set_open(False)
            self.diagnostics.hide()
            self.diagnostic_text.clear()
            self.summary.setText("새 세션 · Codex 연결을 확인한 뒤 세션을 시작하세요.")
        self._refresh()

    def send_message(
        self, task_id: str, epoch: int, text: str, command_id: str, effort: str | None = None
    ) -> None:
        current = self.current
        if (
            current is None
            or (current.task_id, current.epoch) != (task_id, epoch)
            or not self._ready
            or self._busy
            or current.status is not TaskStatus.RUNNING
            or not current.can_accept_message
        ):
            self.message_rejected.emit(command_id)
            return
        self._clear_review()
        self._commands[command_id] = "message"
        self._command_scopes[command_id] = (task_id, epoch)
        self._show_message("메시지 요청됨 · 백엔드 결과 대기")
        self._messages[(task_id, epoch)] = (self.message.text(), None)
        self._refresh()
        self.bridge.request_message(task_id, epoch, text, command_id=command_id, effort=effort)

    def _clear_review(self) -> None:
        self._review_command = None
        self._reviewed = None
        self.detail.clear()
        self._fit_detail()

    def _mark_approval(self, waiting: bool) -> None:
        """Tint the approval section only while this Task waits on a person;
        the tint needs side room, so the section is inset only then."""
        section = self.approval_section
        if section.property("attention") == waiting:
            return
        section.setProperty("attention", waiting)
        layout = section.layout()
        if layout is not None:
            layout.setContentsMargins(12 if waiting else 0, 16, 12 if waiting else 0, 16)
        section.style().unpolish(section)
        section.style().polish(section)
        self._fit_detail()

    def _fit_detail(self) -> None:
        """With no approval waiting, the copy box is a two-line hint so the
        stop controls below stay in view. A waiting approval opens it to its
        reading height before the copy loads, so loading never moves the page;
        the copy scrolls inside. It is never hidden: focus parks here when a
        button is disabled."""
        self.detail.ensurePolished()
        waiting = self.approval_section.property("attention") is True
        lines = 12 if waiting or self.detail.toPlainText() else 2
        self.detail.setFixedHeight(lines * self.detail.fontMetrics().lineSpacing() + 24)

    def _run(self) -> None:
        if not self.run_button.isEnabled() or self.current is None:
            return
        self._clear_review()
        self._show_message("실행 요청됨 · 백엔드 결과 대기")
        command_id = new_command_id()
        self._commands[command_id] = _RUN
        self._command_scopes[command_id] = (self.current.task_id, self.current.epoch)
        self._messages[(self.current.task_id, self.current.epoch)] = (self.message.text(), None)
        self._refresh()
        self.bridge.request_run(self.current.task_id, command_id=command_id)

    def _control(self, action: ControlAction) -> None:
        if self.current is None:
            return
        if self._scope_enabled and self._scope != (self.current.task_id, self.current.epoch):
            return
        target = (
            self.current.task_id
            if action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT)
            else self.current.approval_id
        )
        if target is None:
            return
        self._clear_review()
        self._show_message("제어 요청됨 · 적용 여부 확인 중")
        command_id = new_command_id()
        self._commands[command_id] = action
        self._command_scopes[command_id] = (self.current.task_id, self.current.epoch)
        self._messages[(self.current.task_id, self.current.epoch)] = (self.message.text(), None)
        self._refresh()
        if action is ControlAction.REVIEW:
            self._review_command = command_id
        self.bridge.request_control(action, target, command_id=command_id, epoch=self.current.epoch)

    def _lifecycle(self, event: LifecycleEvent) -> None:
        self._ready = event.state is BridgeState.READY
        if not self._ready:
            self._clear_review()
        text = LIFECYCLE_MESSAGES.get(event.code) or _STATE_MESSAGES.get(
            event.state, "백엔드 상태를 확인하지 못함 · 아직 실행할 수 없음."
        )
        self._show_message(text, event.code)
        if self.current is None:
            # Without a Task snapshot the summary is the only status line, so it
            # must follow the backend state instead of staying at "starting".
            self.summary.setText(_NO_TASK_SUMMARY.get(event.state, _NO_TASK_SUMMARY[None]))
        self._refresh()

    def set_snapshot(self, snapshot: TaskProjection) -> None:
        self._snapshots[(snapshot.task_id, snapshot.epoch)] = snapshot
        if self._scope_enabled and self._scope != (snapshot.task_id, snapshot.epoch):
            return
        self._clear_review()
        self.current = snapshot
        self._render_snapshot(snapshot)
        self._refresh()

    def _render_snapshot(self, snapshot: TaskProjection) -> None:
        self.diagnostic_text.setText(
            "\n".join(
                (
                    f"Task ID: {snapshot.task_id}",
                    f"epoch: {snapshot.epoch}",
                    f"상태 코드: {status_code(snapshot.status)}",
                    f"호스트 dispatch: {_count(snapshot.host_dispatch)}",
                    f"수신 부작용: {_count(snapshot.receiver_effect)}",
                    f"모델 모드: {snapshot.model_mode}",
                )
            )
        )
        self.diagnostics.show()
        lines = [
            status_title(snapshot.status),
            task_label_text(snapshot.label, snapshot.unclassified),
            f"모델: {snapshot.model_id or snapshot.model_mode or MISSING}",
        ]
        if snapshot.status in FINAL_STATUSES:
            lines.append(status_detail(snapshot.status))
        elif not snapshot.model_ready:
            lines.append("모델 연결 미완료 · 현재 세션에서는 메시지를 보낼 수 없음.")
        elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
            lines.append("Runner가 멈춤 · 자동 재전송하지 않음. 새 세션에서 다시 시작하세요.")
        elif snapshot.status is TaskStatus.RUNNING and not snapshot.can_advance:
            # RUNNING with a disabled run button must not look like a stuck UI.
            lines.append(
                "다음 사용자 지시를 기다리는 중이라 실행할 모델 단계가 없음. "
                "여기서 끝내려면 '작업 취소'를 누르세요."
            )
        elif snapshot.status is TaskStatus.WAITING_APPROVAL:
            lines.append("승인 사본에서 수신처와 인자를 확인하세요.")
        elif snapshot.can_advance:
            lines.append("실행 버튼을 눌러 다음 모델 단계를 진행하세요.")
        self.summary.setText("\n".join(lines))

    def _review(self, review: ApprovalReview) -> None:
        if (
            self.current is None
            or review.command_id != self._review_command
            or review.approval_id != self.current.approval_id
            or review.task_id != self.current.task_id
            or review.epoch != self.current.epoch
            or self.current.status is not TaskStatus.WAITING_APPROVAL
            or (self._scope_enabled and self._scope != (self.current.task_id, self.current.epoch))
        ):
            return
        # One field per line, each escaped on its own. A newline inside a
        # value keeps its visible \n mark, so a line it starts can never pass
        # as a field or as the real 전체 인자 heading.
        lines = [visible_review_text(f"{label}: {value}") for label, value in review.fields]
        if not lines:
            lines = [visible_review_text(review.receiving_boundaries)]
        lines += ["", "전체 인자:", visible_review_text(review.arguments_bytes.decode("utf-8"))]
        self.detail.setPlainText("\n".join(lines))
        self._fit_detail()
        self._reviewed = review.approval_id
        self._refresh()

    def _command(self, event: CommandEvent) -> None:
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
        scope = self._command_scopes.pop(event.command_id, None)
        selected = not (
            scope is not None
            and (self.current is None or scope != (self.current.task_id, self.current.epoch))
        )
        if event.stage is CommandStage.BACKEND_RETURNED:
            snapshot = self._snapshots.get(scope) if scope is not None else None
            text = self._result_text(action, event.code, snapshot)
        elif event.stage is CommandStage.REJECTED:
            if selected:
                self._clear_review()
            text = "요청이 처리되지 않음 · 백엔드 상태를 확인한 뒤 다시 시도하세요."
        else:
            if selected:
                self._clear_review()
            text = "적용 여부를 확인하지 못함 · 위 Task 상태를 확인하세요."
        # Internal codes stay available for support, never as the message itself.
        message = (text, event.code)
        if scope is not None:
            self._messages[scope] = message
        if selected:
            self._show_message(*message)
        self._refresh()

    def _result_text(
        self, action: ControlAction | str, code: str, snapshot: TaskProjection | None = None
    ) -> str:
        current = snapshot or self.current
        if code == "MESSAGE_UNAVAILABLE":
            return "현재 Task가 메시지를 받을 수 없음. 백엔드 상태를 확인하세요."
        if code == "SESSION_STOP_RETURNED":
            if current is not None and current.status is TaskStatus.HALTED:
                return "작업 정지를 확인함. 새 세션에서 다시 시작하세요."
            return "정지 적용 여부를 확인하지 못함. Task 상태를 확인하세요."
        if code == "RUNNER_UNAVAILABLE":
            return RUN_RESULTS[code]
        name = _STOP_NAMES.get(action) if isinstance(action, ControlAction) else None
        if name is not None:
            # Cancel and halt always return Denied; success is the Task state
            # the backend published before this command result.
            stopped = current is not None and current.status in (
                TaskStatus.HALTED,
                TaskStatus.CLOSED,
            )
            if code.startswith("CONTROL_") and stopped:
                return (
                    f"{name} 완료 · Task를 정지해 이후 호출은 실행되지 않음. "
                    "이미 전송된 쓰기는 수신됐을 수 있음."
                )
            return f"{name} 적용을 확인하지 못함 · 위 Task 상태를 확인하세요."
        results = RUN_RESULTS if action in (_RUN, "message") else APPROVAL_RESULTS
        return results.get(code, "백엔드 응답을 해석하지 못함 · 위 Task 상태를 확인하세요.")
