"""Plain-text message turns and in-place streamed answer rendering."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .widgets import plain_label

TRANSCRIPT_BODY_MARK = "│ "
USER_LABEL = "나"
ANSWER_LABEL = "Guard · 모델 답변 (검증되지 않은 내용)"
PARTIAL_ANSWER_LABEL = "Guard · 모델 답변 작성 중 (검증되지 않은 내용)"
STOPPED_ANSWER_LABEL = "Guard · 모델 답변 중단됨 (완료되지 않은 내용)"


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
