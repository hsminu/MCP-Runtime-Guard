"""Memory-only session views; backend Task identity is never inferred from text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import (
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QKeySequence,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
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
    new_command_id,
    new_draft_key,
)

from .icons import line_icon
from .presentation import (
    COMMAND_REFUSALS,
    RUN_RESULTS,
    codex_failure_notice,
    task_label_text,
)
from .session_list import (
    _ROW_ROLE as _ROW_ROLE,
)
from .session_list import (
    _SessionRow as _SessionRow,
)
from .session_list import (
    _SessionRowDelegate as _SessionRowDelegate,
)
from .session_model import (
    ModelPicker as ModelPicker,
)
from .session_projection import _STATUS_TONES as _STATUS_TONES
from .session_projection import session_display, session_row_status
from .session_title import auto_title, clean_title
from .session_transcript import (
    ANSWER_LABEL as ANSWER_LABEL,
)
from .session_transcript import (
    PARTIAL_ANSWER_LABEL as PARTIAL_ANSWER_LABEL,
)
from .session_transcript import (
    STOPPED_ANSWER_LABEL as STOPPED_ANSWER_LABEL,
)
from .session_transcript import (
    TRANSCRIPT_BODY_MARK as TRANSCRIPT_BODY_MARK,
)
from .session_transcript import (
    USER_LABEL as USER_LABEL,
)
from .session_transcript import (
    _message_label as _message_label,
)
from .session_transcript import (
    _Transcript as _Transcript,
)
from .session_transcript import (
    _Turn as _Turn,
)
from .task_status import FINAL_STATUSES, status_code, status_title
from .untrusted_text import untrusted_display_text
from .widgets import (
    ConfirmDialog,
    ElidedLabel,
    IconMark,
    KeyboardActionButton,
    LabelPill,
    StatusPill,
    plain_label,
    plain_tooltip,
)

MEMORY_NOTICE = "대화·초안은 메모리에만 있음. Task 종료나 앱 종료 시 원문을 지움."
APPROVAL_BAND_TITLE = "승인이 필요한 도구 호출이 있음"
APPROVAL_BAND_DETAIL = "수신처와 전체 인자를 확인한 뒤 승인하거나 거절하세요."

# The conversation column width shared by header, transcript and composer.
_COLUMN_WIDTH = 720
# Header pill tones for Guard-confirmed statuses (words carry the meaning).
_PILL_TONES = {
    TaskStatus.RUNNING: "live",
    TaskStatus.WAITING_APPROVAL: "attn",
    TaskStatus.READY: "ok",
    TaskStatus.HALTED: "deny",
    TaskStatus.UNKNOWN_OUTCOME: "deny",
}


@dataclass(slots=True)
class _PartialAnswer:
    """The streamed answer turn at the end of a transcript (A90)."""

    command_id: str
    start: int  # turn index in the transcript
    text: str


@dataclass(slots=True)
class SessionView:
    key: str
    title: str
    widget: QWidget
    transcript: _Transcript
    draft: QPlainTextEdit
    notice: QLabel
    send: KeyboardActionButton
    results: KeyboardActionButton
    model: ModelPicker
    header: QHBoxLayout
    controls: QHBoxLayout
    start: KeyboardActionButton
    heading: QLabel
    head: QWidget
    bottom: QWidget
    greeting: QWidget
    column: QVBoxLayout
    pill: StatusPill
    grade: LabelPill
    facts: QBoxLayout
    approval: QFrame
    band: QBoxLayout
    snapshot: TaskProjection | None = None
    updated: datetime = field(default_factory=datetime.now)
    pending_command: str | None = None
    content_present: bool = False
    focus_draft: bool = False
    command_notice: str = ""
    partial: _PartialAnswer | None = None
    title_source: str = "auto"
    auto_title_set: bool = False
    pending_delete: str | None = None
    delete_notice: str = ""


class _SessionStack(QStackedWidget):
    def minimumSizeHint(self) -> QSize:
        # Hidden retained drafts must not widen the visible conversation.
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else QSize(0, 0)


class SessionWorkspace(QWidget):
    """One retained QWidget per session preserves drafts, caret and scroll."""

    # task_id, epoch, text, command_id, reasoning effort or None
    message_requested = Signal(str, int, str, str, object)
    results_requested = Signal()
    approval_review_requested = Signal()
    selection_changed = Signal(str)
    titles_changed = Signal()
    new_session_requested = Signal(str, object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sessions: dict[str, SessionView] = {}
        self.current_key = ""
        self._next_session_number = 0
        self._active_key: str | None = None
        self._ready = False
        self._session_blocker = ""
        self._startup_notice = ""
        self._busy_scopes: frozenset[tuple[str, int]] = frozenset()
        self._seen_events: set[tuple[str, int, str, str]] = set()
        self._terminal_keys: set[str] = set()
        self._removed_keys: set[str] = set()
        self._stopper: Callable[[str, int], str | None] | None = None
        self._stop_results: dict[str, str] | None = None
        self._dialogs: dict[str, QDialog] = {}
        self._centering = 0
        self._activity_open = False
        self.search = QLineEdit()
        self.search.setObjectName("sidebarSearch")
        self.search.setPlaceholderText("검색")
        self.search.setAccessibleName("세션 제목과 상태 검색")
        self.search.addAction(line_icon("search", "mute"), QLineEdit.ActionPosition.LeadingPosition)
        self.list = QListWidget()
        self.list.setObjectName("sessionList")
        self.list.setAccessibleName("세션 목록")
        self.list.setMinimumHeight(90)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        delegate = _SessionRowDelegate(self.list)
        delegate.menu_requested.connect(self.open_menu)
        self.list.setItemDelegate(delegate)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self._menu = QMenu(self.list)
        self._menu.setToolTipsVisible(True)
        for sequence, callback in (
            ("F2", self._rename_current_row),
            ("Delete", self._delete_current_row),
            ("Shift+F10", self._menu_current_row),
            ("Menu", self._menu_current_row),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self.list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        self.list.itemClicked.connect(
            lambda item: (
                self.select(item.data(Qt.ItemDataRole.UserRole))
                if not self._menu.isVisible()
                else None
            )
        )
        self.list.itemActivated.connect(
            lambda item: self.select(item.data(Qt.ItemDataRole.UserRole))
        )
        self.search.textChanged.connect(self._refresh_list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = _SessionStack()
        layout.addWidget(self.stack, 1)
        self.new_session()

    @property
    def current(self) -> SessionView:
        return self.sessions[self.current_key]

    def new_session(self) -> None:
        key = new_draft_key()
        self._next_session_number += 1
        number = self._next_session_number
        self._create(key, f"새 세션 {number}")
        self.select(key)

    def set_stopper(self, stopper: Callable[[str, int], str | None] | None) -> None:
        self._stopper = stopper

    def _rename_current_row(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.rename_session(item.data(Qt.ItemDataRole.UserRole))

    def _delete_current_row(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.delete_session(item.data(Qt.ItemDataRole.UserRole))

    def _menu_current_row(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.open_menu(
                item.data(Qt.ItemDataRole.UserRole),
                self.list.viewport().mapToGlobal(self.list.visualItemRect(item).center()),
            )

    def _context_menu(self, point: QPoint) -> None:
        item = self.list.itemAt(point)
        if item is not None:
            self.open_menu(
                item.data(Qt.ItemDataRole.UserRole), self.list.viewport().mapToGlobal(point)
            )

    def add_session_actions(self, menu: QMenu, key: str, *, current: bool = False) -> None:
        """The sidebar and compact shell share the same actions and availability."""
        prefix = "현재 세션 " if current else ""
        rename = menu.addAction(line_icon("edit", "ink"), prefix + "이름 변경…\tF2")
        remove = menu.addAction(line_icon("trash", "danger"), prefix + "삭제…\tDelete")
        rename.setEnabled(key in self.sessions)
        reason = self.delete_disabled_reason(key)
        remove.setEnabled(not reason)
        remove.setToolTip(reason)
        remove.setStatusTip(reason)
        rename.triggered.connect(lambda: self.rename_session(key))
        remove.triggered.connect(lambda: self.delete_session(key))
        menu.setToolTipsVisible(True)

    def open_menu(self, key: str, point: QPoint) -> None:
        if key not in self.sessions:
            return
        self._menu.clear()
        self.add_session_actions(self._menu, key)
        self._menu.popup(point)

    def _track_dialog(self, key: str, dialog: QDialog) -> None:
        self._dialogs[key] = dialog
        dialog.finished.connect(lambda: self._dialogs.pop(key, None))
        dialog.open()

    def rename_session(self, key: str) -> None:
        view = self.sessions.get(key)
        if view is None or key in self._dialogs:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("세션 이름 변경")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        edit = QLineEdit(view.title)
        edit.setAccessibleName("세션 이름")
        layout.addWidget(edit)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        buttons.addStretch()
        cancel = QPushButton("취소")
        save = QPushButton("저장")
        save.setProperty("variant", "primary")
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        save.setEnabled(bool(clean_title(edit.text())))
        edit.textChanged.connect(lambda text: save.setEnabled(bool(clean_title(text))))
        cancel.clicked.connect(dialog.reject)

        def store_title() -> None:
            title = clean_title(edit.text())
            if title and self.sessions.get(view.key) is view:
                view.title_source = "manual"
                self._set_title(view, title)
                dialog.accept()

        save.clicked.connect(store_title)
        dialog.finished.connect(lambda: edit.setText(""))
        self._track_dialog(key, dialog)
        edit.setFocus()
        edit.selectAll()

    def _set_title(self, view: SessionView, title: str) -> None:
        view.title = title
        view.heading.setText(self.display_title(view))
        view.heading.setToolTip(plain_tooltip(untrusted_display_text(title)[0]))
        self._refresh_list()
        self.titles_changed.emit()

    @staticmethod
    def display_title(view: SessionView) -> str:
        # The automatic truncation mark is UI punctuation, not message content.
        if view.title_source == "auto" and view.auto_title_set and view.title.endswith("…"):
            return untrusted_display_text(view.title[:-1])[0] + "…"
        return untrusted_display_text(view.title)[0]

    def delete_disabled_reason(self, key: str) -> str:
        view = self.sessions.get(key)
        if view is None:
            return "목록에 없는 세션임."
        if view.pending_delete is not None:
            return "중지 요청됨 · 확인 뒤 삭제"
        if view.snapshot is None:
            return (
                "세션 시작 결과를 기다리는 중이라 지금은 삭제할 수 없음."
                if view.pending_command is not None
                else ""
            )
        if view.snapshot.status not in FINAL_STATUSES | {
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
        }:
            return "실행 대기 중인 Task는 목록에서 지울 수 없음."
        return ""

    def delete_session(self, key: str) -> None:
        if self.delete_disabled_reason(key) or key in self._dialogs:
            return
        view = self.sessions[key]
        snapshot = view.snapshot
        if snapshot is None and not view.draft.toPlainText():
            self._remove_session(view)
            return
        stopping = snapshot is not None and snapshot.status in (
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
        )
        title, button = (
            ("작업 중지 후 삭제", "작업 중지 후 삭제") if stopping else ("세션 삭제", "삭제")
        )
        if snapshot is None:
            body = "작성 중인 초안이 지워짐. 이 세션을 삭제할까요?"
        elif stopping:
            state = (
                "승인 대기 중임" if snapshot.status is TaskStatus.WAITING_APPROVAL else "진행 중임"
            )
            body = (
                f"{state}\n삭제하려면 먼저 작업을 중지해야 함. 중지는 되돌릴 수 없음.\n"
                "이미 전송된 쓰기는 취소되지 않음.\n"
                "중지가 확인되면 목록에서 지움. 감사 기록과 호출 로그는 그대로 남음."
            )
        else:
            body = "목록에서 이 세션을 지움. 감사 기록과 호출 로그는 그대로 남음."
            if snapshot.status is TaskStatus.UNKNOWN_OUTCOME:
                body += "\n결과 불명 호출이 있음. 호출 로그에서 수신측 확인이 필요한지 확인하세요."
        dialog = ConfirmDialog(title, body, button, dangerous=True, parent=self)
        # Recheck the view after the nonblocking dialog: its state may have changed.
        dialog.accepted.connect(lambda: self._confirm_delete(view))
        self._track_dialog(key, dialog)

    def _confirm_delete(self, view: SessionView) -> None:
        if self.sessions.get(view.key) is not view or self.delete_disabled_reason(view.key):
            return
        snapshot = view.snapshot
        if snapshot is None or snapshot.status in FINAL_STATUSES:
            self._remove_session(view)
            return
        # A local bridge refusal can arrive synchronously before stop_task returns its ID.
        self._stop_results = {}
        command = None if self._stopper is None else self._stopper(snapshot.task_id, snapshot.epoch)
        results = self._stop_results
        self._stop_results = None
        if command is None:
            view.delete_notice = (
                "작업 중지를 요청하지 못해 삭제하지 않음. 결과 · 실행 활동에서 상태를 확인하세요."
            )
        else:
            view.delete_notice = ""
            view.pending_delete = command
        self._refresh_view(view)
        self._refresh_list()
        if command in results:
            self.settle_command(command, results[command])

    def _remove_session(self, view: SessionView) -> None:
        key = view.key
        self._removed_keys.add(key)
        self._clear_content(view)
        view.title = ""
        view.heading.clear()
        view.heading.setToolTip("")
        view.notice.clear()
        view.command_notice = view.delete_notice = ""
        view.pending_delete = None
        self.sessions.pop(key)
        self.stack.removeWidget(view.widget)
        view.widget.deleteLater()
        if self.current_key == key:
            self.current_key = ""
            if self.sessions:
                self.select(max(self.sessions.values(), key=lambda item: item.updated).key)
            else:
                self.new_session()
        else:
            self._refresh_list()
        self.titles_changed.emit()

    def _create(self, key: str, title: str) -> SessionView:
        page = QWidget()
        page.setObjectName("conversationPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        # A pane header across the page, as in Orca and Paradev: the session
        # title over Guard's status pill and Task facts, then the panel toggle.
        # Each fact is its own label, not one "A · B · C" string.
        head = QFrame()
        head.setObjectName("sessionHead")
        header = QHBoxLayout(head)
        header.setSpacing(12)
        titles = QVBoxLayout()
        header.addLayout(titles, 1)
        titles.setSpacing(4)
        heading = ElidedLabel(untrusted_display_text(title)[0])
        heading.setObjectName("sessionTitle")
        heading.setToolTip(plain_tooltip(untrusted_display_text(title)[0]))
        titles.addWidget(heading)
        facts = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        titles.addLayout(facts)
        facts.setSpacing(16)
        pill = StatusPill()
        facts.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        grade = LabelPill()
        facts.addWidget(grade, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        facts.addStretch(1)
        results = KeyboardActionButton("활동")
        results.setObjectName("ghostButton")
        results.set_visual(icon="panel")
        results.setAccessibleName("결과 · 실행 활동 패널")
        results.setToolTip("결과 · 실행 활동 패널 열기·닫기")
        results.setProperty("selected", self._activity_open)
        results.clicked.connect(self.results_requested.emit)
        header.addWidget(results, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(head)
        # An empty session states what happens next at the top of the column;
        # the composer stays at the bottom (stretch slot 3 holds the space).
        greeting = QWidget()
        greeting_layout = QVBoxLayout(greeting)
        greeting_layout.setSpacing(4)
        greeting_layout.addWidget(plain_label("아직 메시지가 없음", name="emptyTitle"))
        greeting_layout.addWidget(
            plain_label(
                "Codex에는 Guard 도구만 연결됨. 도구 호출과 승인 요청은 활동 패널에서 확인하세요.",
                name="emptyDetail",
            )
        )
        column.addWidget(greeting)
        transcript = _Transcript()
        transcript.setObjectName("conversationTranscript")
        transcript.setAccessibleName("세션 대화 · 검증되지 않은 모델 답변은 일반 텍스트로 표시")
        column.addWidget(transcript, 1)
        column.addStretch(0)
        # Bottom: Guard's notice for this session, the composer and the memory note.
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setSpacing(8)
        notice = plain_label("", name="sessionNotice")
        bottom_layout.addWidget(notice)
        # A pending approval is the one thing here that waits on a person, so
        # it gets a tinted band over the composer instead of the gray notice.
        # Shown only while Guard reports WAITING_APPROVAL, so it never becomes
        # background decoration that readers learn to skip.
        approval = QFrame()
        approval.setObjectName("approvalBand")
        approval.setAccessibleName("승인 요청 안내")
        band = QBoxLayout(QBoxLayout.Direction.LeftToRight, approval)
        band.setContentsMargins(12, 8, 8, 8)
        band.setSpacing(12)
        band.addWidget(
            IconMark("approvals", "warn", 20, 16, name="approvalMark"),
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        words = QVBoxLayout()
        band.addLayout(words, 1)
        words.setSpacing(2)
        words.addWidget(plain_label(APPROVAL_BAND_TITLE, name="approvalBandTitle"))
        words.addWidget(plain_label(APPROVAL_BAND_DETAIL, name="approvalBandDetail"))
        review = KeyboardActionButton("승인 사본 확인")
        review.setObjectName("approvalBandButton")
        review.set_visual(icon="eye")
        review.setToolTip("활동 패널에서 요청 사본을 엶")
        review.clicked.connect(self.approval_review_requested.emit)
        band.addWidget(review, 0, Qt.AlignmentFlag.AlignVCenter)
        approval.hide()
        bottom_layout.addWidget(approval)
        compose = QFrame()
        compose.setObjectName("composer")
        compose.setToolTip(MEMORY_NOTICE)
        composer = QVBoxLayout(compose)
        composer.setContentsMargins(12, 12, 8, 8)
        composer.setSpacing(8)
        draft = QPlainTextEdit()
        draft.setObjectName("messageDraft")
        draft.setAccessibleName("세션 메시지 초안")
        draft.setPlaceholderText("메시지를 입력하세요")
        composer.addWidget(draft)
        controls = QHBoxLayout()
        composer.addLayout(controls)
        controls.setSpacing(8)
        model = ModelPicker()
        controls.addWidget(model, 0, Qt.AlignmentFlag.AlignVCenter)
        controls.addStretch(1)
        start = KeyboardActionButton("세션 시작")
        start.setObjectName("composerButton")
        start.set_visual(icon="plus", variant="primary")
        start.setIconSize(QSize(14, 14))
        controls.addWidget(start)
        send = KeyboardActionButton()
        send.setObjectName("sendButton")
        send.set_visual(icon="arrow_up", variant="primary")
        send.setFixedSize(28, 28)
        send.setAccessibleName("보내기")
        send.setToolTip("보내기")
        controls.addWidget(send)
        bottom_layout.addWidget(compose)
        bottom_layout.addWidget(plain_label(MEMORY_NOTICE, name="sessionFootnote"))
        column.addWidget(bottom)
        view = SessionView(
            key,
            title,
            page,
            transcript,
            draft,
            notice,
            send,
            results,
            model,
            header,
            controls,
            start,
            heading,
            head,
            bottom,
            greeting,
            column,
            pill,
            grade,
            facts,
            approval,
            band,
        )
        self.sessions[key] = view
        self.stack.addWidget(page)
        draft.textChanged.connect(lambda: self._draft_changed(view))
        send.clicked.connect(lambda: self._send(view))
        start.clicked.connect(lambda: self._start(view))
        transcript.changed.connect(lambda: self._show_greeting(view))
        self._show_greeting(view)
        self._refresh_view(view)
        self._reflow()
        return view

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "sessions"):
            self._reflow()

    def minimumSizeHint(self) -> QSize:
        # Centering space beyond the base gutter is not needed width; counting
        # it would keep a shrinking scroll area from ever narrowing this page.
        hint = super().minimumSizeHint()
        return QSize(max(0, hint.width() - 2 * self._centering), hint.height())

    def _reflow(self) -> None:
        compact = self.width() < 500
        # Center one column; the transcript keeps its scroll bar at the edge.
        base = 12 if compact else 24
        side = max(base, (self.width() - _COLUMN_WIDTH) // 2)
        self._centering = side - base
        direction = (
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )
        edge = 12 if compact else 20
        for view in self.sessions.values():
            view.header.setDirection(direction)
            view.controls.setDirection(direction)
            view.band.setDirection(direction)
            view.facts.setDirection(direction)
            view.facts.setSpacing(4 if compact else 16)
            view.header.setContentsMargins(edge, 8, edge - 8, 8)
            view.transcript.set_side(side)
            for part, margins in (
                (view.greeting, (side, 24, side, 0)),
                (view.bottom, (side, 8, side, 12)),
            ):
                layout = part.layout()
                if layout is not None:
                    layout.setContentsMargins(*margins)
            self._fit_draft(view.draft)

    @staticmethod
    def _show_greeting(view: SessionView) -> None:
        empty = view.transcript.is_empty()
        view.greeting.setVisible(empty)
        view.transcript.setVisible(not empty)
        view.column.setStretch(3, 1 if empty else 0)

    @staticmethod
    def _fit_draft(draft: QPlainTextEdit) -> None:
        """Start at two lines and grow to six before the draft scrolls."""
        draft.ensurePolished()
        lines = int(draft.document().documentLayout().documentSize().height())
        rows = max(2, min(6, lines))
        margin = 2 * draft.document().documentMargin() + 2
        draft.setFixedHeight(int(rows * draft.fontMetrics().lineSpacing() + margin))

    def _draft_changed(self, view: SessionView) -> None:
        self._fit_draft(view.draft)
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

    def set_session_blocker(self, code: str) -> None:
        """Fixed backend code that already refuses new sessions, or ""."""

        self._session_blocker = code if type(code) is str else "SESSION_UNAVAILABLE"
        for view in self.sessions.values():
            self._refresh_view(view)

    def set_startup_notice(self, text: str) -> None:
        """Fixed startup progress text while the backend starts, or ""."""

        self._startup_notice = text if type(text) is str else ""
        for view in self.sessions.values():
            self._refresh_view(view)

    def set_busy_scopes(self, scopes: frozenset[tuple[str, int]]) -> None:
        self._busy_scopes = scopes
        for view in self.sessions.values():
            self._refresh_view(view)

    def set_snapshot(self, snapshot: TaskProjection) -> None:
        key = f"task:{snapshot.task_id}:{snapshot.epoch}"
        if key in self._removed_keys:
            return
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
        display = session_display(
            snapshot,
            ready=self._ready,
            busy_scopes=self._busy_scopes,
            pending_command=view.pending_command,
            command_notice=view.command_notice,
            session_blocker=self._session_blocker,
            startup_notice=self._startup_notice,
        )
        writable, active = display.writable, display.active
        view.send.setEnabled(writable and bool(view.draft.toPlainText().strip()))
        view.send.setVisible(snapshot is not None)
        view.start.setVisible(snapshot is None)
        view.start.setEnabled(
            self._ready and not self._session_blocker and view.pending_command is None
        )
        view.results.setEnabled(active)
        if snapshot is not None:
            if snapshot.model_mode == "codex":
                view.model.set_model_locked(True)
            else:
                view.model.set_fixed(f"모델: {snapshot.model_mode}")
        view.draft.setReadOnly(
            bool(
                snapshot
                and snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
            )
        )
        if snapshot is None:
            view.pill.set_status("초안 · 미연결", "neutral")
            view.pill.setToolTip("")
        else:
            view.pill.set_status(
                status_title(snapshot.status), _PILL_TONES.get(snapshot.status, "neutral")
            )
            view.pill.setToolTip(
                f"Task {snapshot.task_id} · epoch {snapshot.epoch} · {status_code(snapshot.status)}"
            )
        view.grade.setVisible(snapshot is not None)
        if snapshot is not None:
            view.grade.set_label(
                snapshot.label.name if snapshot.label is not None else None,
                text=task_label_text(snapshot.label, snapshot.unclassified),
            )
        view.notice.setText(view.delete_notice or display.note)
        waiting = display.waiting
        view.approval.setVisible(waiting)
        # The band says it; a failure notice still shows above it.
        view.notice.setVisible(
            not waiting or bool(display.failure_notice) or bool(view.delete_notice)
        )

    def _send(self, view: SessionView) -> None:
        if not view.send.isEnabled() or view.snapshot is None or view is not self.current:
            return
        text = view.draft.toPlainText()
        if len(text) > 32768:
            view.notice.setText("메시지는 32,768자 이하로 입력하세요.")
            return
        command = new_command_id()
        view.command_notice = ""
        view.pending_command = command
        self._refresh_view(view)
        self.message_requested.emit(
            view.snapshot.task_id, view.snapshot.epoch, text, command, view.model.message_effort
        )

    def _start(self, view: SessionView) -> None:
        if not view.start.isEnabled() or view.snapshot is not None or view is not self.current:
            return
        options = SessionOptions(model=view.model.model)
        command = new_command_id()
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
        if view.title_source != "manual":
            view.title = view.title.replace("새 세션", "세션", 1)
        view.heading.setText(self.display_title(view))
        view.heading.setToolTip(plain_tooltip(untrusted_display_text(view.title)[0]))
        view.pending_command = None
        view.command_notice = ""
        self.sessions[key] = view
        if was_selected:
            self.current_key = key
        self.set_snapshot(event.snapshot)
        if was_selected:
            self.select(key)
        self.titles_changed.emit()

    def _conversation_view(self, event: ConversationEvent) -> SessionView | None:
        view = self.sessions.get(f"task:{event.task_id}:{event.epoch}")
        if (
            not self._ready
            or view is None
            or view.snapshot is None
            or view.snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        ):
            return None
        return view

    def conversation(self, event: ConversationEvent) -> None:
        view = self._conversation_view(event)
        marker = (event.task_id, event.epoch, event.command_id, event.role)
        if view is None or marker in self._seen_events or event.role not in ("user", "assistant"):
            return
        self._seen_events.add(marker)
        if event.role == "user" and view.title_source == "auto" and not view.auto_title_set:
            view.auto_title_set = True
            self._set_title(view, auto_title(event.text, datetime.now()))
        partial = view.partial
        if partial is not None and (event.role == "user" or partial.command_id != event.command_id):
            self._stop_partial(view)
            partial = None
        label = USER_LABEL if event.role == "user" else ANSWER_LABEL
        # The final answer replaces its streamed block in place.
        self._write_block(view, label, event.text, None if partial is None else partial.start)
        view.partial = None
        if event.role == "user" and event.command_id == view.pending_command:
            view.draft.clear()
        view.content_present = True
        view.updated = datetime.now()
        self._refresh_list()

    def conversation_partial(self, event: ConversationEvent) -> None:
        """Replace the streamed answer of one run command with its text so far (A90)."""
        view = self._conversation_view(event)
        marker = (event.task_id, event.epoch, event.command_id, "assistant")
        if view is None or event.role != "assistant" or marker in self._seen_events:
            return
        partial = view.partial
        if partial is not None and partial.command_id != event.command_id:
            self._stop_partial(view)
            partial = None
        start = self._write_block(
            view, PARTIAL_ANSWER_LABEL, event.text, None if partial is None else partial.start
        )
        view.partial = _PartialAnswer(event.command_id, start, event.text)
        view.content_present = True
        view.updated = datetime.now()

    def _stop_partial(self, view: SessionView) -> None:
        """Keep an unfinished streamed answer, labelled as not completed."""
        partial = view.partial
        if partial is not None:
            self._write_block(view, STOPPED_ANSWER_LABEL, partial.text, partial.start)
            view.partial = None

    @staticmethod
    def _write_block(view: SessionView, label: str, value: str, start: int | None) -> int:
        """Append a speaker turn, or replace the one at ``start`` and after it."""
        text, _ = untrusted_display_text(value)
        return view.transcript.write_turn(label, text, start)

    def settle_command(self, command_id: str, code: str) -> None:
        if self._stop_results is not None:
            self._stop_results[command_id] = code
        for view in tuple(self.sessions.values()):
            if view.pending_delete == command_id:
                if view.snapshot is not None and view.snapshot.status in FINAL_STATUSES:
                    self._remove_session(view)
                    continue
                view.pending_delete = None
                view.delete_notice = (
                    "작업 중지를 확인하지 못해 삭제하지 않음. 상태를 확인한 뒤 다시 시도하세요."
                )
                self._refresh_view(view)
                self._refresh_list()
            if view.partial is not None and view.partial.command_id == command_id:
                self._stop_partial(view)
            if view.pending_command == command_id:
                view.pending_command = None
                # Always replace the pending notice, including unknown failures.
                view.command_notice = ""
                failure_notice = codex_failure_notice(code, creating=view.snapshot is None)
                if failure_notice:
                    view.command_notice = failure_notice
                elif code in COMMAND_REFUSALS:
                    view.command_notice = COMMAND_REFUSALS[code]
                elif code in ("RUNNER_UNAVAILABLE", "RUNNER_UNCONFIRMED"):
                    view.command_notice = RUN_RESULTS[code]
                elif code == "MESSAGE_UNAVAILABLE":
                    view.command_notice = (
                        "메시지를 접수하지 않음. 현재 Task 상태를 다시 확인하세요."
                    )
                elif code == "SESSION_CREATION_UNAVAILABLE":
                    view.command_notice = "이 실행 구성에는 새 Task 생성 기능이 연결되지 않음."
                elif code == "SESSION_CREATION_UNCONFIRMED":
                    view.command_notice = (
                        "새 Task 생성 결과를 확인하지 못함. 연결 상태를 확인하세요."
                    )
                elif code in ("BACKEND_UNCONFIRMED", "STARTUP_FAILED"):
                    view.command_notice = "요청 처리를 확인하지 못함. 연결 상태를 확인하세요."
                elif view.snapshot is None:
                    view.command_notice = "세션 시작 결과를 확인하지 못함. 연결 상태를 확인하세요."
                self._refresh_view(view)

    def set_activity_open(self, open_: bool) -> None:
        """Mark every header's 활동 button while the results dock is open."""
        self._activity_open = open_
        for view in self.sessions.values():
            view.results.setProperty("selected", open_)
            view.results.style().unpolish(view.results)
            view.results.style().polish(view.results)

    def _refresh_list(self, *_args: object) -> None:
        needle = self.search.text().casefold().strip()
        self.list.clear()
        for view in sorted(self.sessions.values(), key=lambda item: item.updated, reverse=True):
            status, tone = session_row_status(view.snapshot)
            if view.pending_delete is not None:
                status = "중지 요청됨 · 확인 뒤 삭제"
            shown = self.display_title(view)
            time = (
                f"{view.updated:%H:%M}"
                if view.updated.date() == datetime.now().date()
                else f"{view.updated.month}/{view.updated.day}"
            )
            text = f"{shown}\n{time} · {status}"
            code = "" if view.snapshot is None else status_code(view.snapshot.status)
            identity = "" if view.snapshot is None else f"{view.snapshot.task_id} {code}"
            if needle and needle not in f"{text} {identity}".casefold():
                continue
            item = QListWidgetItem(text)
            item.setToolTip(
                plain_tooltip(
                    f"{untrusted_display_text(view.title)[0]}\n{status}"
                    + (f" ({code})" if code else "")
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, view.key)
            item.setData(
                _ROW_ROLE,
                _SessionRow(
                    shown,
                    time,
                    status,
                    tone,
                    view.snapshot is not None
                    and view.snapshot.status is TaskStatus.WAITING_APPROVAL,
                ),
            )
            self.list.addItem(item)
            if view.key == self.current_key:
                self.list.setCurrentItem(item)

    @staticmethod
    def _clear_content(view: SessionView) -> None:
        view.transcript.clear()
        view.draft.clear()
        view.draft.document().clearUndoRedoStacks()
        view.pending_command = None
        view.partial = None
        view.content_present = False

    def clear_content(self) -> None:
        for dialog in tuple(self._dialogs.values()):
            dialog.reject()
        for view in self.sessions.values():
            self._clear_content(view)
        self._seen_events.clear()
        self.set_ready(False)
