"""Memory-only session views; backend Task identity is never inferred from text."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    ConversationEvent,
    SessionCreated,
    SessionOptions,
    TaskProjection,
)

from .presentation import codex_failure_notice
from .task_status import status_title
from .untrusted_text import untrusted_display_text
from .widgets import KeyboardActionButton, plain_label


@dataclass(slots=True)
class SessionView:
    key: str
    title: str
    widget: QWidget
    transcript: QPlainTextEdit
    draft: QPlainTextEdit
    notice: QLabel
    status: QLabel
    send: KeyboardActionButton
    results: KeyboardActionButton
    model: QLabel
    header: QHBoxLayout
    controls: QHBoxLayout
    start: KeyboardActionButton
    snapshot: TaskProjection | None = None
    updated: datetime = field(default_factory=datetime.now)
    pending_command: str | None = None
    content_present: bool = False
    focus_draft: bool = False
    command_notice: str = ""


class _SessionStack(QStackedWidget):
    def minimumSizeHint(self) -> QSize:
        # Hidden retained drafts must not widen the visible conversation.
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else QSize(0, 0)


class SessionWorkspace(QWidget):
    """One retained QWidget per session preserves drafts, caret and scroll."""

    message_requested = Signal(str, int, str, str)
    results_requested = Signal()
    selection_changed = Signal(str)
    new_session_requested = Signal(str, object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sessions: dict[str, SessionView] = {}
        self.current_key = ""
        self._next_session_number = 0
        self._active_key: str | None = None
        self._ready = False
        self._busy_scopes: frozenset[tuple[str, int]] = frozenset()
        self._seen_events: set[tuple[str, int, str, str]] = set()
        self._terminal_keys: set[str] = set()
        self.search = QLineEdit()
        self.search.setPlaceholderText("세션 검색")
        self.search.setAccessibleName("세션 제목과 상태 검색")
        self.list = QListWidget()
        self.list.setObjectName("sessionList")
        self.list.setAccessibleName("세션 목록")
        self.list.setMinimumHeight(90)
        self.list.setWordWrap(True)
        self.list.itemClicked.connect(lambda item: self.select(item.data(Qt.ItemDataRole.UserRole)))
        self.list.itemActivated.connect(
            lambda item: self.select(item.data(Qt.ItemDataRole.UserRole))
        )
        self.search.textChanged.connect(self._refresh_list)
        layout = QVBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.stack = _SessionStack()
        layout.addWidget(self.stack, 1)
        self.new_session()

    @property
    def current(self) -> SessionView:
        return self.sessions[self.current_key]

    def new_session(self) -> None:
        key = f"draft:{uuid4().hex}"
        self._next_session_number += 1
        number = self._next_session_number
        self._create(key, f"새 세션 {number}")
        self.select(key)

    def _create(self, key: str, title: str) -> SessionView:
        page = QWidget()
        page.setObjectName("conversationPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        header = QHBoxLayout()
        heading = plain_label(title, name="sectionTitle")
        header.addWidget(heading, 1)
        results = KeyboardActionButton("결과 · 실행 활동")
        results.set_visual(icon="logs")
        results.clicked.connect(self.results_requested.emit)
        header.addWidget(results)
        column.addLayout(header)
        status = plain_label("아직 연결된 Task가 없어", name="secondary")
        column.addWidget(status)
        transcript = QPlainTextEdit()
        transcript.setObjectName("conversationTranscript")
        transcript.setReadOnly(True)
        transcript.setUndoRedoEnabled(False)
        transcript.setAccessibleName("세션 대화 · 검증되지 않은 모델 답변은 일반 텍스트로 표시")
        transcript.setPlaceholderText(
            "무엇을 도와줄까?\n\n대화와 공개된 답변이 여기에 표시돼.\n"
            "도구 실행과 승인은 결과 · 실행 활동에서 확인할 수 있어."
        )
        column.addWidget(transcript, 1)
        notice = plain_label(
            "새 Task 생성은 아직 연결되지 않았어. 초안을 작성하거나 연결된 세션을 선택해.",
            name="runtimeMessage",
        )
        column.addWidget(notice)
        compose = QFrame()
        compose.setObjectName("composer")
        composer = QVBoxLayout(compose)
        composer.setContentsMargins(14, 12, 14, 12)
        composer.setSpacing(8)
        draft = QPlainTextEdit()
        draft.setObjectName("messageDraft")
        draft.setAccessibleName("세션 메시지 초안")
        draft.setPlaceholderText("메시지를 입력해")
        draft.setMinimumHeight(66)
        draft.setMaximumHeight(130)
        composer.addWidget(draft)
        controls = QHBoxLayout()
        model = plain_label("Codex · gpt-6.1-sol", name="secondary")
        model.setAccessibleName("세션 모델 · Codex gpt-6.1-sol")
        controls.addWidget(model)
        controls.addStretch(1)
        start = KeyboardActionButton("세션 시작")
        start.set_visual(icon="plus", variant="primary")
        controls.addWidget(start)
        send = KeyboardActionButton("보내기")
        send.set_visual(icon="send", variant="primary")
        controls.addWidget(send)
        composer.addLayout(controls)
        column.addWidget(compose)
        column.addWidget(
            plain_label(
                "대화·초안은 메모리에만 있어. Task 종료나 앱 종료 시 원문을 지워.", name="secondary"
            )
        )
        view = SessionView(
            key,
            title,
            page,
            transcript,
            draft,
            notice,
            status,
            send,
            results,
            model,
            header,
            controls,
            start,
        )
        self.sessions[key] = view
        self.stack.addWidget(page)
        draft.textChanged.connect(lambda: self._draft_changed(view))
        send.clicked.connect(lambda: self._send(view))
        start.clicked.connect(lambda: self._start(view))
        self._refresh_view(view)
        self._reflow()
        return view

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "sessions"):
            self._reflow()

    def _reflow(self) -> None:
        compact = self.width() < 500
        margin = 12 if compact else 24
        self._layout.setContentsMargins(margin, 16, margin, 16)
        direction = (
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )
        for view in self.sessions.values():
            view.header.setDirection(direction)
            view.controls.setDirection(direction)

    def _draft_changed(self, view: SessionView) -> None:
        self._refresh_view(view)

    def select(self, key: str) -> None:
        if key not in self.sessions:
            return
        if self.current_key:
            self.current.focus_draft = self.current.draft.hasFocus()
        self.current_key = key
        view = self.current
        self.stack.setCurrentWidget(view.widget)
        self._refresh_list()
        self.selection_changed.emit(key)

    def return_to_conversation(self) -> None:
        if self.current.focus_draft:
            self.current.draft.setFocus(Qt.FocusReason.OtherFocusReason)

    def leaving_conversation(self) -> None:
        self.current.focus_draft = self.current.draft.hasFocus()

    def set_ready(self, ready: bool) -> None:
        self._ready = ready
        for view in self.sessions.values():
            self._refresh_view(view)

    def set_busy_scopes(self, scopes: frozenset[tuple[str, int]]) -> None:
        self._busy_scopes = scopes
        for view in self.sessions.values():
            self._refresh_view(view)

    def set_snapshot(self, snapshot: TaskProjection) -> None:
        key = f"task:{snapshot.task_id}:{snapshot.epoch}"
        if key in self._terminal_keys and snapshot.status in (
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
        ):
            return
        for other in self.sessions.values():
            if (
                other.snapshot is not None
                and other.snapshot.task_id == snapshot.task_id
                and other.snapshot.epoch > snapshot.epoch
            ):
                return
        first = self._active_key is None
        if key not in self.sessions:
            self._create(key, f"세션 · {snapshot.task_id}")
        self._active_key = key
        view = self.sessions[key]
        view.snapshot = snapshot
        view.updated = datetime.now()
        terminal = snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        if terminal:
            self._terminal_keys.add(key)
            self._clear_content(view)
        self._refresh_view(view)
        for other in self.sessions.values():
            if other is not view:
                self._refresh_view(other)
        self._refresh_list()
        if first:
            self.select(key)

    def _refresh_view(self, view: SessionView) -> None:
        snapshot = view.snapshot
        failure_notice = "" if snapshot is None else codex_failure_notice(snapshot.connection_code)
        active = snapshot is not None
        busy = bool(snapshot and (snapshot.task_id, snapshot.epoch) in self._busy_scopes)
        writable = bool(
            active
            and self._ready
            and not busy
            and snapshot is not None
            and snapshot.status is TaskStatus.RUNNING
            and snapshot.can_accept_message
            and not failure_notice
            and view.pending_command is None
        )
        view.send.setEnabled(writable and bool(view.draft.toPlainText().strip()))
        view.send.setVisible(snapshot is not None)
        view.start.setVisible(snapshot is None)
        view.start.setEnabled(self._ready and view.pending_command is None)
        view.results.setEnabled(active)
        if snapshot is not None:
            view.model.setText(
                "Codex · gpt-6.1-sol"
                if snapshot.model_mode == "codex"
                else f"모델: {snapshot.model_mode}"
            )
        view.draft.setReadOnly(
            bool(
                snapshot
                and snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
            )
        )
        if snapshot is None:
            text = "새 세션 · Codex gpt-6.1-sol"
            note = view.command_notice or (
                "Codex 연결과 시작 검사가 확인되면 세션을 시작할 수 있어. "
                "로그인은 설정에서 안내를 확인해."
            )
        else:
            text = (
                f"{status_title(snapshot.status)} · Task {snapshot.task_id} "
                f"· epoch {snapshot.epoch}"
            )
            if failure_notice:
                note = failure_notice
            elif snapshot.status is TaskStatus.WAITING_APPROVAL:
                note = "승인 대기 중 · 결과 · 실행 활동에서 요청 사본을 확인해."
            elif snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                note = (
                    "Codex 인증이 만료되거나 갱신되지 않아 이 Task를 정지했어. "
                    "새 세션에서 다시 시작해."
                    if snapshot.connection_code == "CODEX_AUTH_FAILED"
                    else "Task가 종료되거나 잠겼어. 대화·초안 원문을 지웠고 다시 실행할 수 없어."
                )
            elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
                note = (
                    "모델 응답을 완료하지 못해 이 Runner가 멈췄어. 자동 재전송하지 않아. "
                    "Store의 Task 상태는 위에 표시된 값이야. 새 세션에서 다시 시작해."
                )
            elif not snapshot.model_ready:
                note = "모델 연결을 확인하지 못해 실행할 수 없어."
            elif busy or view.pending_command:
                note = "실행 요청 처리 중 · 백엔드 결과를 기다리는 중이야."
            elif not self._ready:
                note = "백엔드 준비 상태를 확인하는 중이야."
            elif view.command_notice:
                note = view.command_notice
            elif snapshot.can_advance:
                note = "진행할 모델 단계가 있어. 결과 · 실행 활동에서 실행을 눌러."
            elif writable:
                note = "다음 메시지를 보낼 수 있어. 도구 호출은 Guard를 통해 검사돼."
            else:
                note = "사용자 입력을 받을 수 있는지 백엔드 확인을 기다리는 중이야."
            if not snapshot.tools_available:
                note = "도구 없는 대화 · " + note
        view.status.setText(text)
        view.notice.setText(note)

    def _send(self, view: SessionView) -> None:
        if not view.send.isEnabled() or view.snapshot is None or view is not self.current:
            return
        text = view.draft.toPlainText()
        if len(text) > 32768:
            view.notice.setText("메시지는 32,768자 이하로 입력해.")
            return
        command = uuid4().hex
        view.command_notice = ""
        view.pending_command = command
        self._refresh_view(view)
        self.message_requested.emit(view.snapshot.task_id, view.snapshot.epoch, text, command)

    def _start(self, view: SessionView) -> None:
        if not view.start.isEnabled() or view.snapshot is not None or view is not self.current:
            return
        options = SessionOptions()
        command = uuid4().hex
        view.command_notice = "새 Task 생성 요청됨 · 연결과 시작 검사 확인 중"
        view.pending_command = command
        self._refresh_view(view)
        self.new_session_requested.emit(view.key, options, command)

    def session_created(self, event: SessionCreated) -> None:
        view = self.sessions.get(event.draft_key)
        if view is None or view.pending_command != event.command_id or view.snapshot is not None:
            return
        self.sessions.pop(view.key)
        key = f"task:{event.snapshot.task_id}:{event.snapshot.epoch}"
        was_selected = self.current_key == view.key
        view.key = key
        view.title = view.title.replace("새 세션", "세션", 1)
        title_item = view.header.itemAt(0)
        heading = None if title_item is None else title_item.widget()
        if isinstance(heading, QLabel):
            heading.setText(view.title)
        view.pending_command = None
        view.command_notice = ""
        self.sessions[key] = view
        if was_selected:
            self.current_key = key
        self.set_snapshot(event.snapshot)
        if was_selected:
            self.select(key)

    def conversation(self, event: ConversationEvent) -> None:
        key = f"task:{event.task_id}:{event.epoch}"
        view = self.sessions.get(key)
        marker = (event.task_id, event.epoch, event.command_id, event.role)
        if (
            not self._ready
            or view is None
            or view.snapshot is None
            or view.snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
            or marker in self._seen_events
            or event.role not in ("user", "assistant")
        ):
            return
        self._seen_events.add(marker)
        text, _ = untrusted_display_text(event.text)
        label = "나" if event.role == "user" else "Guard · 모델 답변 (검증되지 않은 내용)"
        bar = view.transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 3
        previous = bar.value()
        view.transcript.appendPlainText(f"{label}\n{text}\n")
        if not at_bottom:
            bar.setValue(previous)
        if event.role == "user" and event.command_id == view.pending_command:
            view.draft.clear()
        view.content_present = True
        view.updated = datetime.now()
        self._refresh_list()

    def settle_command(self, command_id: str, code: str) -> None:
        for view in self.sessions.values():
            if view.pending_command == command_id:
                view.pending_command = None
                # Always replace the pending notice, including unknown failures.
                view.command_notice = ""
                failure_notice = codex_failure_notice(code, creating=view.snapshot is None)
                if failure_notice:
                    view.command_notice = failure_notice
                elif code == "MESSAGE_UNAVAILABLE":
                    view.command_notice = "메시지를 접수하지 않았어. 현재 Task 상태를 다시 확인해."
                elif code == "SESSION_CREATION_UNAVAILABLE":
                    view.command_notice = "이 실행 구성에는 새 Task 생성 기능이 연결되지 않았어."
                elif code == "SESSION_CREATION_UNCONFIRMED":
                    view.command_notice = "새 Task 생성 결과를 확인하지 못했어. 연결 상태를 확인해."
                elif code in ("BACKEND_UNCONFIRMED", "NOT_READY", "STARTUP_FAILED"):
                    view.command_notice = "요청 처리를 확인하지 못했어. 연결 상태를 확인해."
                elif view.snapshot is None:
                    view.command_notice = "세션 시작 결과를 확인하지 못했어. 연결 상태를 확인해."
                self._refresh_view(view)

    def _refresh_list(self, *_args: object) -> None:
        needle = self.search.text().casefold().strip()
        self.list.clear()
        for view in sorted(self.sessions.values(), key=lambda item: item.updated, reverse=True):
            if view.snapshot is None:
                status = "초안 · 미연결"
            elif view.snapshot.connection_code == "CODEX_CLEANUP_UNCONFIRMED":
                status = "정리 완료 미확인"
            elif view.snapshot.status is TaskStatus.WAITING_APPROVAL:
                status = "승인 필요 · 대기 중"
            elif not view.snapshot.model_ready:
                status = "연결 필요"
            else:
                status = status_title(view.snapshot.status)
            shown, _ = untrusted_display_text(view.title)
            text = f"{shown}\n{view.updated:%H:%M} · {status}"
            identity = "" if view.snapshot is None else view.snapshot.task_id
            if needle and needle not in f"{text} {identity}".casefold():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, view.key)
            self.list.addItem(item)
            if view.key == self.current_key:
                self.list.setCurrentItem(item)

    @staticmethod
    def _clear_content(view: SessionView) -> None:
        view.transcript.clear()
        view.draft.clear()
        view.draft.document().clearUndoRedoStacks()
        view.pending_command = None
        view.content_present = False

    def clear_content(self) -> None:
        for view in self.sessions.values():
            self._clear_content(view)
        self._seen_events.clear()
        self.set_ready(False)
