"""Memory-only session views; backend Task identity is never inferred from text."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    CODEX_DEFAULT_EFFORT,
    CODEX_MODEL_ID,
    CODEX_MODELS,
    CODEX_REASONING_EFFORTS,
    ConversationEvent,
    SessionCreated,
    SessionOptions,
    TaskProjection,
    new_command_id,
    new_draft_key,
)

from .icons import line_icon
from .presentation import (
    CODEX_AUTH_FAILED_NOTICE,
    COMMAND_REFUSALS,
    EFFORT_NAMES,
    MODEL_NAMES,
    RUN_RESULTS,
    codex_failure_notice,
    session_blocker_notice,
    task_label_text,
)
from .style import TOKENS, ui_font
from .task_status import FINAL_STATUSES, status_code, status_title
from .untrusted_text import untrusted_display_text
from .widgets import IconMark, KeyboardActionButton, StatusPill, plain_label

TRANSCRIPT_BODY_MARK = "│ "
USER_LABEL = "나"
ANSWER_LABEL = "Guard · 모델 답변 (검증되지 않은 내용)"
PARTIAL_ANSWER_LABEL = "Guard · 모델 답변 작성 중 (검증되지 않은 내용)"
STOPPED_ANSWER_LABEL = "Guard · 모델 답변 중단됨 (완료되지 않은 내용)"
MEMORY_NOTICE = "대화·초안은 메모리에만 있음. Task 종료나 앱 종료 시 원문을 지움."
APPROVAL_BAND_TITLE = "승인이 필요한 도구 호출이 있음"
APPROVAL_BAND_DETAIL = "수신처와 전체 인자를 확인한 뒤 승인하거나 거절하세요."

# The conversation column width shared by header, transcript and composer.
_COLUMN_WIDTH = 720
_ROW_ROLE = Qt.ItemDataRole.UserRole + 1
# Header pill tones for Guard-confirmed statuses (words carry the meaning).
_PILL_TONES = {
    TaskStatus.RUNNING: "live",
    TaskStatus.WAITING_APPROVAL: "attn",
    TaskStatus.READY: "ok",
    TaskStatus.HALTED: "deny",
    TaskStatus.UNKNOWN_OUTCOME: "deny",
}
# Session list marks for Guard-confirmed statuses; shape differs per tone.
_STATUS_TONES = {
    TaskStatus.READY: "idle",
    TaskStatus.RUNNING: "running",
    TaskStatus.HALTED: "stopped",
    TaskStatus.UNKNOWN_OUTCOME: "stopped",
}


@dataclass(slots=True)
class _Turn:
    """One transcript entry. ``role`` comes from the writer, never the text."""

    role: str  # "user", "answer", or "plain" for setPlainText content
    label: str
    text: str
    widget: QWidget
    body: QLabel
    note: QLabel | None = None

    @property
    def plain(self) -> str:
        if self.role == "plain":
            return self.text
        # Speaker labels alone start at column 0. Every body line is marked, so
        # a model line such as "나" or "Guard · ..." cannot pose as a label.
        body = "\n".join(f"{TRANSCRIPT_BODY_MARK}{line}" for line in self.text.split("\n"))
        return f"{self.label}\n{body}\n"


def _message_label(text: str, name: str) -> QLabel:
    label = plain_label(text, name=name)
    # Selectable for copying; links are never interactive.
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class _Transcript(QScrollArea):
    """Messenger transcript built from PlainText labels (technical design 12.4).

    The user's message is a bubble on the right; a model answer is plain text
    under Guard's unverified-answer label, with no avatar. Each turn is its own
    widget whose role the writer sets, so model text cannot pose as the user or
    as a label. ``toPlainText`` keeps the marked text form for copying and checks."""

    changed = Signal()
    _BUBBLE_PADDING = 12
    # Orca caps a user bubble at 80% of the column; Paradev at 36rem.
    _BUBBLE_MAX = 576

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.turns: list[_Turn] = []
        self._side = 0
        self._follow = True
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("transcriptContent")
        self._column = QVBoxLayout(content)
        self._column.setSpacing(20)
        self._column.addStretch(1)
        self.setWidget(content)
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._track_bottom)
        bar.rangeChanged.connect(self._keep_bottom)

    def is_empty(self) -> bool:
        return not self.turns

    def toPlainText(self) -> str:  # noqa: N802 - mirrors the Qt text widgets
        return "\n".join(turn.plain for turn in self.turns)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - mirrors the Qt text widgets
        self._remove_from(0)
        if text:
            row = QWidget()
            column = QVBoxLayout(row)
            column.setContentsMargins(0, 0, 0, 0)
            body = _message_label(text, "answerText")
            column.addWidget(body)
            self._add(_Turn("plain", "", text, row, body))
        self.changed.emit()

    def clear(self) -> None:
        self._remove_from(0)
        self.changed.emit()

    def write_turn(self, label: str, text: str, index: int | None) -> int:
        """Append a turn, or replace the turn at ``index`` and all after it."""
        role = "user" if label == USER_LABEL else "answer"
        last = self.turns[-1] if index is not None and index == len(self.turns) - 1 else None
        if last is not None and last.role == role == "answer" and last.note is not None:
            # A streamed answer is updated in place instead of rebuilt.
            last.label, last.text = label, text
            self._style_note(last.note, label)
            last.body.setText(text)
        else:
            if index is not None:
                self._remove_from(index)
            self._add(self._user_turn(text) if role == "user" else self._answer_turn(label, text))
        self.changed.emit()
        return len(self.turns) - 1

    def set_side(self, side: int) -> None:
        self._side = side
        self._column.setContentsMargins(side, 20, side, 24)
        self._fit_bubbles()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_bubbles()

    def _add(self, turn: _Turn) -> None:
        self.turns.append(turn)
        self._column.insertWidget(self._column.count() - 1, turn.widget)
        if turn.role == "user":
            self._fit_bubble(turn)

    def _remove_from(self, index: int) -> None:
        for turn in self.turns[index:]:
            # Drop the Qt text copy now; the widget itself goes on the next loop.
            turn.body.clear()
            turn.widget.hide()
            self._column.removeWidget(turn.widget)
            turn.widget.deleteLater()
        del self.turns[index:]

    def _user_turn(self, text: str) -> _Turn:
        row = QWidget()
        row.setAccessibleName("내 메시지")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addStretch(1)
        bubble = QFrame()
        bubble.setObjectName("userBubble")
        inner = QVBoxLayout(bubble)
        pad = self._BUBBLE_PADDING
        inner.setContentsMargins(pad, 8, pad, 8)
        body = _message_label(text, "userText")
        inner.addWidget(body)
        line.addWidget(bubble)
        return _Turn("user", USER_LABEL, text, row, body)

    def _answer_turn(self, label: str, text: str) -> _Turn:
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        note = plain_label(name="answerLabel")
        self._style_note(note, label)
        column.addWidget(note)
        body = _message_label(text, "answerText")
        column.addWidget(body)
        return _Turn("answer", label, text, row, body, note)

    @staticmethod
    def _style_note(note: QLabel, label: str) -> None:
        note.setText(label)
        tone = {PARTIAL_ANSWER_LABEL: "live", STOPPED_ANSWER_LABEL: "stopped"}.get(label, "")
        if note.property("tone") != tone:
            note.setProperty("tone", tone)
            note.style().unpolish(note)
            note.style().polish(note)

    def _fit_bubbles(self) -> None:
        for turn in self.turns:
            if turn.role == "user":
                self._fit_bubble(turn)

    def _fit_bubble(self, turn: _Turn) -> None:
        """Hug the text up to the bubble cap, then wrap."""
        column = self.viewport().width() - 2 * self._side
        cap = min(self._BUBBLE_MAX, int(column * 0.8))
        limit = max(80, cap - 2 * self._BUBBLE_PADDING - 2)
        body = turn.body
        body.ensurePolished()
        metrics = body.fontMetrics()
        natural = max(metrics.horizontalAdvance(line) for line in turn.text.split("\n")) + 2
        body.setFixedWidth(min(natural, limit))

    def _track_bottom(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - 3

    def _keep_bottom(self, _minimum: int, maximum: int) -> None:
        # New text keeps a reader at the bottom there; a reader above stays put.
        if self._follow:
            self.verticalScrollBar().setValue(maximum)


@dataclass(frozen=True, slots=True)
class _SessionRow:
    title: str
    time: str
    status: str
    tone: str


class _SessionRowDelegate(QStyledItemDelegate):
    """Draw a session row as status mark, title, time and status line.

    The item's display text stays "title\\nHH:MM · status" for search and
    assistive technology. Marks differ in shape as well as color; the selected
    row is a bordered card, as in Orca's worktree list."""

    _HEIGHT = 44

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), self._HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(_ROW_ROLE)
        if not isinstance(row, _SessionRow):
            super().paint(painter, option, index)
            return
        state = option.state
        rect = QRectF(option.rect).adjusted(0.5, 1.5, -0.5, -1.5)
        selected = bool(state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            painter.setPen(QPen(QColor(TOKENS["selected_line"]), 1))
            painter.setBrush(QColor(TOKENS["selected"]))
            painter.drawRoundedRect(rect, 6, 6)
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TOKENS["hover"]))
            painter.drawRoundedRect(rect, 6, 6)
        if state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(TOKENS["line_hover"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
        self._paint_mark(painter, row.tone, QPointF(rect.left() + 13, rect.top() + 13))
        left = rect.left() + 26
        right = rect.right() - 8
        # Times are data: tabular mono, as in Paradev's workspace rows.
        time_font = ui_font(11, mono=True)
        time_width = QFontMetrics(time_font).horizontalAdvance(row.time)
        painter.setFont(time_font)
        painter.setPen(QColor(TOKENS["mute"]))
        painter.drawText(
            QRectF(right - time_width, rect.top() + 4, time_width, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            row.time,
        )
        title_font = ui_font(13, QFont.Weight.Medium)
        title_width = max(0, int(right - time_width - 8 - left))
        painter.setFont(title_font)
        painter.setPen(QColor(TOKENS["ink"]))
        painter.drawText(
            QRectF(left, rect.top() + 4, title_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(
                row.title, Qt.TextElideMode.ElideRight, title_width
            ),
        )
        status_font = ui_font(11)
        status_width = max(0, int(right - left))
        painter.setFont(status_font)
        # Only a pending approval colors its words; other states stay gray.
        painter.setPen(QColor(TOKENS["warn" if row.tone == "approval" else "mute"]))
        painter.drawText(
            QRectF(left, rect.top() + 22, status_width, 15),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(status_font).elidedText(
                row.status, Qt.TextElideMode.ElideRight, status_width
            ),
        )
        painter.restore()

    @staticmethod
    def _paint_mark(painter: QPainter, tone: str, center: QPointF) -> None:
        x, y = center.x(), center.y()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)
        if tone == "draft":
            pen = QPen(QColor(TOKENS["subtle"]), 1.3)
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.drawEllipse(center, 4, 4)
        elif tone == "running":
            # An open ember arc reads as work in progress (Paradev's spinner glyph).
            pen = QPen(QColor(TOKENS["accent"]), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(QRectF(x - 4.5, y - 4.5, 9, 9), 90 * 16, -270 * 16)
        elif tone == "idle":
            painter.setPen(QPen(QColor(TOKENS["ok"]), 1.6))
            painter.drawEllipse(center, 3.8, 3.8)
        elif tone in ("attention", "approval"):
            painter.setBrush(QColor(TOKENS["warn"]))
            painter.drawPolygon(
                [QPointF(x, y - 5), QPointF(x + 5, y), QPointF(x, y + 5), QPointF(x - 5, y)]
            )
        elif tone == "stopped":
            painter.setBrush(QColor(TOKENS["danger"]))
            painter.drawRoundedRect(QRectF(x - 4, y - 4, 8, 8), 1.5, 1.5)
        else:
            painter.setPen(QPen(QColor(TOKENS["subtle"]), 1.6))
            painter.drawLine(QPointF(x - 3, y), QPointF(x + 3, y))


@dataclass(slots=True)
class _PartialAnswer:
    """The streamed answer turn at the end of a transcript (A90)."""

    command_id: str
    start: int  # turn index in the transcript
    text: str


class ModelPicker(KeyboardActionButton):
    """Composer chip: the session's model and reasoning level, from fixed lists.

    The model is chosen before a Task starts and then belongs to that Task's
    thread; the reasoning level goes with each message from then on. Neither
    comes from model output.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("modelChip")
        self.set_visual(icon="chevron_down")
        self.setIconSize(QSize(12, 12))
        # Chevron after the text; the menu itself reads left to right.
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.model = CODEX_MODEL_ID
        self.effort = CODEX_DEFAULT_EFFORT
        self._fixed: str | None = None
        menu = QMenu(self)
        menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.model_actions = self._group(
            menu, "모델", {model: MODEL_NAMES.get(model, model) for model in CODEX_MODELS}
        )
        self.effort_actions = self._group(
            menu, "추론 수준", {effort: EFFORT_NAMES[effort] for effort in CODEX_REASONING_EFFORTS}
        )
        self.model_actions[self.model].setChecked(True)
        self.effort_actions[self.effort].setChecked(True)
        self.setMenu(menu)
        self._render()

    def _group(self, menu: QMenu, title: str, names: dict[str, str]) -> dict[str, QAction]:
        # A disabled heading: styled menus drop addSection's text.
        if menu.actions():
            menu.addSeparator()
        menu.addAction(title).setEnabled(False)
        group = QActionGroup(menu)
        actions = {}
        for value, name in names.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, v=value: self._pick(v))
            actions[value] = action
        return actions

    def _pick(self, value: str) -> None:
        if value in self.model_actions:
            self.model = value
        else:
            self.effort = value
        self._render()

    @property
    def message_effort(self) -> str | None:
        """The level to send with a message; None when the model has no levels."""
        return None if self._fixed is not None else self.effort

    def set_model_locked(self, locked: bool) -> None:
        """A started Codex Task keeps its model; only the reasoning level changes."""
        self._fixed = None
        for action in self.model_actions.values():
            action.setEnabled(not locked)
        self.setEnabled(True)
        self._render()

    def set_fixed(self, text: str) -> None:
        """A non-Codex (synthetic) model: shown, nothing to pick."""
        self._fixed = text
        self.setEnabled(False)
        self._render()

    def _render(self) -> None:
        if self._fixed is not None:
            self.setText(self._fixed)
            self.setAccessibleName(f"세션 {self._fixed}")
            self.setToolTip("")
            return
        model = MODEL_NAMES.get(self.model, self.model)
        effort = EFFORT_NAMES[self.effort]
        self.setText(f"{model} · 추론 {effort}")
        self.setAccessibleName(f"세션 모델 {model} · 추론 수준 {effort} · 눌러서 바꾸기")
        self.setToolTip(f"Codex {self.model} · effort {self.effort}")


@dataclass(slots=True)
class SessionView:
    key: str
    title: str
    widget: QWidget
    transcript: _Transcript
    draft: QPlainTextEdit
    notice: QLabel
    status: QLabel
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
    epoch: QLabel
    grade: QLabel
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
    new_session_requested = Signal(str, object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sessions: dict[str, SessionView] = {}
        self.current_key = ""
        self._next_session_number = 0
        self._active_key: str | None = None
        self._ready = False
        self._session_blocker = ""
        self._busy_scopes: frozenset[tuple[str, int]] = frozenset()
        self._seen_events: set[tuple[str, int, str, str]] = set()
        self._terminal_keys: set[str] = set()
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
        self.list.setItemDelegate(_SessionRowDelegate(self.list))
        self.list.itemClicked.connect(lambda item: self.select(item.data(Qt.ItemDataRole.UserRole)))
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
        titles.setSpacing(4)
        heading = plain_label(title, name="sessionTitle")
        titles.addWidget(heading)
        facts = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        facts.setSpacing(16)
        pill = StatusPill()
        facts.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        status = plain_label("아직 연결된 Task가 없음", name="sessionMeta")
        epoch = plain_label(name="sessionMeta")
        grade = plain_label(name="sessionMeta")
        for fact in (status, epoch, grade):
            facts.addWidget(fact)
        facts.addStretch(1)
        titles.addLayout(facts)
        header.addLayout(titles, 1)
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
        words.setSpacing(2)
        words.addWidget(plain_label(APPROVAL_BAND_TITLE, name="approvalBandTitle"))
        words.addWidget(plain_label(APPROVAL_BAND_DETAIL, name="approvalBandDetail"))
        band.addLayout(words, 1)
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
        composer.addLayout(controls)
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
            status,
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
            epoch,
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
            view.pill.setToolTip(status_code(snapshot.status))
        view.epoch.setVisible(snapshot is not None)
        view.grade.setVisible(snapshot is not None)
        if snapshot is None:
            text = "아직 연결된 Task가 없음"
            note = (
                view.command_notice
                or session_blocker_notice(self._session_blocker)
                or (
                    "Codex 연결과 시작 검사가 확인되면 세션을 시작할 수 있음. "
                    "로그인은 설정에서 안내를 확인하세요."
                )
            )
        else:
            text = f"Task {snapshot.task_id}"
            view.epoch.setText(f"epoch {snapshot.epoch}")
            view.grade.setText(task_label_text(snapshot.label, snapshot.unclassified))
            if failure_notice:
                note = failure_notice
            elif snapshot.status is TaskStatus.WAITING_APPROVAL:
                note = "승인 대기 중 · 결과 · 실행 활동에서 요청 사본을 확인하세요."
            elif snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                note = (
                    CODEX_AUTH_FAILED_NOTICE
                    if snapshot.connection_code == "CODEX_AUTH_FAILED"
                    else "Task가 종료되거나 잠김. 대화·초안 원문을 지웠고 다시 실행할 수 없음."
                )
            elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
                note = (
                    "모델 응답을 완료하지 못해 이 Runner가 멈춤. 자동 재전송하지 않음. "
                    "Store의 Task 상태는 위에 표시된 값임. 새 세션에서 다시 시작하세요."
                )
            elif not snapshot.model_ready:
                note = "모델 연결을 확인하지 못해 실행할 수 없음."
            elif busy or view.pending_command:
                note = "실행 요청 처리 중 · 백엔드 결과를 기다리는 중임."
            elif not self._ready:
                note = "백엔드 준비 상태를 확인하는 중임."
            elif view.command_notice:
                note = view.command_notice
            elif snapshot.can_advance:
                note = "진행할 모델 단계가 있음. 결과 · 실행 활동에서 실행을 누르세요."
            elif writable:
                note = "다음 메시지를 보낼 수 있음. 도구 호출은 Guard를 통해 검사됨."
            else:
                note = "사용자 입력을 받을 수 있는지 백엔드 확인을 기다리는 중임."
            if not snapshot.tools_available:
                note = "도구 없는 대화 · " + note
        view.status.setText(text)
        view.notice.setText(note)
        waiting = snapshot is not None and snapshot.status is TaskStatus.WAITING_APPROVAL
        view.approval.setVisible(waiting)
        # The band says it; a failure notice still shows above it.
        view.notice.setVisible(not waiting or bool(failure_notice))

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
        view.title = view.title.replace("새 세션", "세션", 1)
        view.heading.setText(view.title)
        view.pending_command = None
        view.command_notice = ""
        self.sessions[key] = view
        if was_selected:
            self.current_key = key
        self.set_snapshot(event.snapshot)
        if was_selected:
            self.select(key)

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
        for view in self.sessions.values():
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
            if view.snapshot is None:
                status, tone = "초안 · 미연결", "draft"
            elif view.snapshot.connection_code == "CODEX_CLEANUP_UNCONFIRMED":
                status, tone = "정리 완료 미확인", "attention"
            elif view.snapshot.status is TaskStatus.WAITING_APPROVAL:
                status, tone = "승인 필요 · 대기 중", "approval"
            elif view.snapshot.status not in FINAL_STATUSES and not view.snapshot.model_ready:
                status, tone = "연결 필요", "attention"
            else:
                status = status_title(view.snapshot.status)
                tone = _STATUS_TONES.get(view.snapshot.status, "closed")
            shown, _ = untrusted_display_text(view.title)
            time = f"{view.updated:%H:%M}"
            text = f"{shown}\n{time} · {status}"
            code = "" if view.snapshot is None else status_code(view.snapshot.status)
            identity = "" if view.snapshot is None else f"{view.snapshot.task_id} {code}"
            if needle and needle not in f"{text} {identity}".casefold():
                continue
            item = QListWidgetItem(text)
            item.setToolTip(code)
            item.setData(Qt.ItemDataRole.UserRole, view.key)
            item.setData(_ROW_ROLE, _SessionRow(shown, time, status, tone))
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
        for view in self.sessions.values():
            self._clear_content(view)
        self._seen_events.clear()
        self.set_ready(False)
