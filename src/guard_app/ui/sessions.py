"""Memory-only session views; backend Task identity is never inferred from text."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
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
    model: QComboBox
    header: QHBoxLayout
    controls: QHBoxLayout
    model_name: QLineEdit
    start: KeyboardActionButton
    snapshot: TaskProjection | None = None
    updated: datetime = field(default_factory=datetime.now)
    pending_command: str | None = None
    content_present: bool = False
    focus_draft: bool = False


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
        self._busy = False
        self._seen_events: set[tuple[str, str]] = set()
        self._terminal_keys: set[str] = set()
        self._multi_session = False
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
        model = QComboBox()
        model.addItem("모델 API", "api")
        model.addItem("CLI · 준비 중", "cli")
        model.setAccessibleName("새 세션 모델 연결 방식")
        controls.addWidget(model)
        model_name = QLineEdit()
        model_name.setPlaceholderText("모델 ID")
        model_name.setAccessibleName("새 세션의 API 모델 ID")
        model_name.setMaximumWidth(220)
        controls.addWidget(model_name)
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
            model_name,
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

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
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
        if not self._multi_session and self._active_key is not None and self._active_key != key:
            self._clear_content(self.sessions[self._active_key])
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
        active = snapshot is not None and (self._multi_session or view.key == self._active_key)
        writable = bool(
            active
            and self._ready
            and not self._busy
            and snapshot is not None
            and snapshot.status is TaskStatus.RUNNING
            and snapshot.can_accept_message
            and view.pending_command is None
        )
        view.send.setEnabled(writable and bool(view.draft.toPlainText().strip()))
        view.send.setVisible(snapshot is not None)
        view.start.setVisible(snapshot is None)
        view.start.setEnabled(self._ready and view.pending_command is None)
        view.model.setEnabled(snapshot is None and view.pending_command is None)
        view.model_name.setEnabled(snapshot is None and view.pending_command is None)
        view.results.setEnabled(active)
        if snapshot is not None:
            index = view.model.findData(snapshot.model_mode)
            if index < 0:
                view.model.addItem(f"현재 모델: {snapshot.model_mode}", snapshot.model_mode)
                index = view.model.count() - 1
            view.model.setCurrentIndex(index)
            view.model_name.setText(snapshot.model_id)
        view.draft.setReadOnly(
            bool(
                snapshot
                and snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
            )
        )
        if snapshot is None:
            text = "새 세션 · 모델 연결을 선택하고 시작해"
            note = (
                "API 모델 ID를 입력하고 세션을 시작해. "
                "CLI 실행과 보안 검사 기능은 아직 준비 중이야."
            )
        else:
            text = (
                f"{status_title(snapshot.status)} · Task {snapshot.task_id} "
                f"· epoch {snapshot.epoch}"
            )
            if not active:
                note = "이 Task는 현재 연결 대상이 아니야. 새 실행 요청은 보내지 않아."
            elif snapshot.status is TaskStatus.WAITING_APPROVAL:
                note = "승인 대기 중 · 결과 · 실행 활동에서 요청 사본을 확인해."
            elif snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                note = "Task가 종료되거나 잠겼어. 대화·초안 원문을 지웠고 다시 실행할 수 없어."
            elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
                note = (
                    "모델 응답을 완료하지 못해 이 Runner가 멈췄어. 자동 재전송하지 않아. "
                    "Store의 Task 상태는 위에 표시된 값이야. 새 세션에서 다시 시작해."
                )
            elif not snapshot.model_ready:
                note = {
                    "MODEL_ID_REQUIRED": "모델 ID가 없어. 새 세션에서 모델 ID를 입력해.",
                    "WINDOWS_CREDENTIALS_UNAVAILABLE": (
                        "Windows 자격증명 저장이 아직 연결되지 않아 API를 실행할 수 없어."
                    ),
                    "MODEL_KEY_MISSING": "저장된 모델 API 키가 없어. 설정에서 확인해.",
                    "CLI_DEPENDENCIES_UNAVAILABLE": (
                        "CLI 실행과 보안 검사 기능은 아직 준비 중이라 실행할 수 없어."
                    ),
                }.get(snapshot.connection_code, "모델 연결을 확인하지 못해 실행할 수 없어.")
            elif self._busy or view.pending_command:
                note = "실행 요청 처리 중 · 백엔드 결과를 기다리는 중이야."
            elif not self._ready:
                note = "백엔드 준비 상태를 확인하는 중이야."
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
        view.pending_command = command
        self._refresh_view(view)
        self.message_requested.emit(view.snapshot.task_id, view.snapshot.epoch, text, command)

    def _start(self, view: SessionView) -> None:
        if not view.start.isEnabled() or view.snapshot is not None or view is not self.current:
            return
        try:
            options = SessionOptions(str(view.model.currentData()), view.model_name.text().strip())
        except ValueError:
            view.notice.setText("모델 ID는 공백 없는 영문·숫자·기호 128자 이하로 입력해.")
            return
        command = uuid4().hex
        view.pending_command = command
        self._refresh_view(view)
        view.notice.setText("새 Task 생성 요청됨 · 백엔드 확인 중")
        self.new_session_requested.emit(view.key, options, command)

    def session_created(self, event: SessionCreated) -> None:
        view = self.sessions.get(event.draft_key)
        if view is None or view.pending_command != event.command_id or view.snapshot is not None:
            return
        self._multi_session = True
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
        self.sessions[key] = view
        if was_selected:
            self.current_key = key
        self.set_snapshot(event.snapshot)
        if was_selected:
            self.select(key)

    def conversation(self, event: ConversationEvent) -> None:
        key = f"task:{event.task_id}:{event.epoch}"
        view = self.sessions.get(key)
        marker = (event.command_id, event.role)
        if (
            not self._ready
            or view is None
            or (not self._multi_session and key != self._active_key)
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
                self._refresh_view(view)
                if code == "MESSAGE_UNAVAILABLE":
                    view.notice.setText("메시지를 접수하지 않았어. 현재 Task 상태를 다시 확인해.")
                elif code.startswith("SESSION_CREATION_"):
                    view.notice.setText("이 실행 구성에는 새 Task 생성 기능이 연결되지 않았어.")
                elif code in ("BACKEND_UNCONFIRMED", "NOT_READY", "STARTUP_FAILED"):
                    view.notice.setText("새 Task 생성을 확인하지 못했어. 백엔드 상태를 확인해.")

    def _refresh_list(self, *_args: object) -> None:
        needle = self.search.text().casefold().strip()
        self.list.clear()
        for view in sorted(self.sessions.values(), key=lambda item: item.updated, reverse=True):
            if view.snapshot is None:
                status = "초안 · 미연결"
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
