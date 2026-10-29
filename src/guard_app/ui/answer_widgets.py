"""Selectable plain widgets for already-visualized answer blocks (I5/I9, T18)."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .answer_blocks import Block, parse_blocks
from .widgets import KeyboardActionButton, plain_label


class CopyButton(KeyboardActionButton):
    def __init__(self, value: Callable[[], str], *, icon_only: bool = False) -> None:
        super().__init__("" if icon_only else "복사")
        self.setObjectName("ghostButton")
        self.set_visual(icon="copy")
        self.setAccessibleName("메시지 복사")
        self.setToolTip("메시지 복사")
        self._value = value
        self._rest = self.text()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setText(self._rest))
        self.clicked.connect(self._copy)

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._value())
            self.setText("복사함")
            self._timer.start(1500)


class CodeText(QPlainTextEdit):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.setObjectName("codeText")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPixelSize(13)
        self.setFont(font)
        self.setPlainText(text)
        self.ensurePolished()
        rows = min(20, max(1, len(text.split("\n"))))
        self.setFixedHeight(rows * self.fontMetrics().lineSpacing() + 30)

    def wheelEvent(self, event: QWheelEvent) -> None:
        horizontal = bool(event.angleDelta().x()) or bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        bar = self.horizontalScrollBar() if horizontal else self.verticalScrollBar()
        if bar.maximum() > bar.minimum():
            super().wheelEvent(event)
        else:
            event.ignore()


def _block_widget(block: Block) -> QWidget:
    if block.kind in ("code", "table"):
        frame = QFrame()
        frame.setObjectName("codeBlock")
        column = QVBoxLayout(frame)
        column.setContentsMargins(8, 8, 8, 8)
        body = CodeText(block.text)
        if block.kind == "code":
            head = QHBoxLayout()
            column.addLayout(head)
            head.addWidget(plain_label(block.language or "코드"), 1)
            head.addWidget(CopyButton(body.toPlainText))
        column.addWidget(body)
        return frame
    if block.kind == "rule":
        rule = QFrame()
        rule.setObjectName("answerRule")
        rule.setFixedHeight(1)
        return rule
    text = f"{block.marker} {block.text}" if block.kind == "list" else block.text
    label = plain_label(text, name="answerHeading" if block.kind == "heading" else "answerText")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if block.kind == "heading":
        label.setProperty("level", min(block.level, 3))
    if block.kind == "quote":
        label.setObjectName("answerQuote")
    if block.kind == "list":
        label.setContentsMargins(min(block.level, 16) * 16, 0, 0, 0)
    return label


class AnswerBody(QWidget):
    """Reuse the unchanged prefix when a streaming tail changes."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = ""
        self._blocks: tuple[Block, ...] = ()
        self._widgets: list[QWidget] = []
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(8)
        self.setText(text)

    def text(self) -> str:
        return self._text

    def clear(self) -> None:
        self.setText("")

    def set_streaming(self, streaming: bool) -> None:
        for button in self.findChildren(CopyButton):
            button.setToolTip("코드 복사" + (" · 작성 중인 내용임" if streaming else ""))

    def setText(self, text: str) -> None:
        self._text = text
        blocks = parse_blocks(text)
        same = 0
        for old, new in zip(self._blocks, blocks, strict=False):
            if old != new:
                break
            same += 1
        for widget in self._widgets[same:]:
            for edit in widget.findChildren(QPlainTextEdit):
                edit.clear()
            for label in widget.findChildren(QLabel):
                label.clear()
            if isinstance(widget, QLabel):
                widget.clear()
            self._column.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        del self._widgets[same:]
        for block in blocks[same:]:
            widget = _block_widget(block)
            self._widgets.append(widget)
            self._column.addWidget(widget)
        self._blocks = blocks
