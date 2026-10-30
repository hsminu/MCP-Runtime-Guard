"""Plain-text message turns and in-place streamed answer rendering."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import (
    Qt,
    QTimer,
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

from .answer_widgets import AnswerBody, CopyButton
from .widgets import KeyboardActionButton, plain_label

TRANSCRIPT_BODY_MARK = "│ "
USER_LABEL = "나"
ANSWER_LABEL = "Guard · 모델 답변"
PARTIAL_ANSWER_LABEL = "Guard · 모델 답변 작성 중"
STOPPED_ANSWER_LABEL = "Guard · 모델 답변 중단됨 (완료되지 않은 내용)"
_clock = time.monotonic


@dataclass(slots=True)
class _Turn:
    """One transcript entry. ``role`` comes from the writer, never the text."""

    role: str  # "user", "answer", or "plain" for setPlainText content
    label: str
    text: str
    widget: QWidget
    body: QLabel | AnswerBody
    note: QLabel | None = None
    copy: CopyButton | None = None

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
        self._unseen = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("transcriptContent")
        self._column = QVBoxLayout(content)
        self._column.setSpacing(20)
        self._thinking_started: float | None = None
        self._thinking = plain_label("생각 중 · 0초", name="thinkingLabel")
        self._thinking.setAccessibleName("모델 응답 기다리는 중")
        self._thinking.hide()
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(1000)
        self._thinking_timer.timeout.connect(self._update_thinking_time)
        self._column.addWidget(self._thinking)
        self._column.addStretch(1)
        self.setWidget(content)
        self.jump = KeyboardActionButton("최신 메시지로 이동", self.viewport())
        self.jump.setObjectName("jumpLatest")
        self.jump.set_visual(icon="arrow_down")
        self.jump.clicked.connect(self.jump_latest)
        self.jump.hide()
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._track_bottom)
        bar.rangeChanged.connect(self._keep_bottom)

    def is_empty(self) -> bool:
        return not self.turns

    def set_thinking(self, active: bool) -> None:
        """Show an ephemeral waiting line outside the copied conversation."""
        if active:
            if self._thinking_started is not None:
                return
            self._thinking_started = _clock()
            self._update_thinking_time()
            self._thinking.show()
            self._thinking_timer.start()
        else:
            self._thinking_timer.stop()
            self._thinking_started = None
            self._thinking.hide()
            self._thinking.setText("생각 중 · 0초")

    def _update_thinking_time(self) -> None:
        if self._thinking_started is not None:
            elapsed = int(_clock() - self._thinking_started)
            self._thinking.setText(f"생각 중 · {elapsed}초")

    def toPlainText(self) -> str:  # noqa: N802 - mirrors the Qt text widgets
        return "\n".join(turn.plain for turn in self.turns)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - mirrors the Qt text widgets
        self.set_thinking(False)
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
        self.set_thinking(False)
        self._remove_from(0)
        self.changed.emit()

    def write_turn(self, label: str, text: str, index: int | None) -> int:
        """Append a turn, or replace the turn at ``index`` and all after it."""
        if not self._follow:
            self._unseen = True
            self._update_jump()
        role = "user" if label == USER_LABEL else "answer"
        last = self.turns[-1] if index is not None and index == len(self.turns) - 1 else None
        if last is not None and last.role == role == "answer" and last.note is not None:
            # A streamed answer is updated in place instead of rebuilt.
            last.label, last.text = label, text
            self._style_note(last.note, label)
            last.body.setText(text)
            if isinstance(last.body, AnswerBody):
                last.body.set_streaming(label == PARTIAL_ANSWER_LABEL)
            if last.copy is not None:
                self._copy_tooltip(last.copy, label)
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
        self._update_jump()

    def _add(self, turn: _Turn) -> None:
        self.turns.append(turn)
        self._column.insertWidget(self._column.indexOf(self._thinking), turn.widget)
        if turn.role == "user":
            self._fit_bubble(turn)

    def _remove_from(self, index: int) -> None:
        for turn in self.turns[index:]:
            # Drop the Qt text copy now; the widget itself goes on the next loop.
            turn.text = ""
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
        turn = _Turn("user", USER_LABEL, text, row, body)
        turn.copy = CopyButton(lambda: turn.text, icon_only=True)
        inner.addWidget(turn.copy, 0, Qt.AlignmentFlag.AlignRight)
        return turn

    def _answer_turn(self, label: str, text: str) -> _Turn:
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        note = plain_label(name="answerLabel")
        self._style_note(note, label)
        column.addWidget(note)
        body = AnswerBody(text)
        body.set_streaming(label == PARTIAL_ANSWER_LABEL)
        column.addWidget(body)
        turn = _Turn("answer", label, text, row, body, note)
        turn.copy = CopyButton(lambda: turn.text)
        self._copy_tooltip(turn.copy, label)
        column.addWidget(turn.copy, 0, Qt.AlignmentFlag.AlignLeft)
        return turn

    @staticmethod
    def _copy_tooltip(button: CopyButton, label: str) -> None:
        button.setToolTip(
            "메시지 복사" + (" · 작성 중인 내용임" if label == PARTIAL_ANSWER_LABEL else "")
        )

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
        if not isinstance(body, QLabel):
            return
        body.ensurePolished()
        metrics = body.fontMetrics()
        natural = max(metrics.horizontalAdvance(line) for line in turn.text.split("\n")) + 2
        body.setFixedWidth(min(natural, limit))

    def _track_bottom(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - 24
        if self._follow:
            self._unseen = False
        self._update_jump()

    def jump_latest(self) -> None:
        self._follow = True
        self._unseen = False
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self._update_jump()

    def _update_jump(self) -> None:
        self.jump.setText("새 메시지 · 최신으로 이동" if self._unseen else "최신 메시지로 이동")
        self.jump.adjustSize()
        self.jump.move(
            max(0, (self.viewport().width() - self.jump.width()) // 2),
            max(0, self.viewport().height() - self.jump.height() - 8),
        )
        self.jump.setVisible(not self._follow)
        self.jump.raise_()

    def _keep_bottom(self, _minimum: int, maximum: int) -> None:
        # New text keeps a reader at the bottom there; a reader above stays put.
        if self._follow:
            self.verticalScrollBar().setValue(maximum)
        self._update_jump()
