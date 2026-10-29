"""Plain-text draft input with IME-aware send keys and no attachments."""

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit

ATTACHMENT_NOTICE = (
    "파일 첨부는 지원하지 않음. 필요한 내용은 글로 붙여 넣거나 등록된 파일 서버 도구로 읽게 하세요."
)


class MessageDraft(QPlainTextEdit):
    send_requested = Signal()
    attachment_rejected = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._composing = False
        self.setPlaceholderText("메시지를 입력하세요 · Enter 보내기, Shift+Enter 줄바꿈")

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.isAutoRepeat():
                event.accept()
                return
            if self._composing:
                super().keyPressEvent(event)
                return
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.send_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        # Qt synthesizes text() from URLs; only explicit plain text is input.
        return source.hasFormat("text/plain") and bool(source.text())

    def insertFromMimeData(self, source: QMimeData) -> None:
        if self.isReadOnly():
            return
        if self.canInsertFromMimeData(source):
            self.insertPlainText(source.text())
        elif source.hasUrls():
            self.attachment_rejected.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and not self.canInsertFromMimeData(event.mimeData()):
            self.attachment_rejected.emit()
            event.ignore()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls() and not self.canInsertFromMimeData(event.mimeData()):
            event.ignore()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not self.canInsertFromMimeData(event.mimeData()):
            self.attachment_rejected.emit()
            event.ignore()
        else:
            super().dropEvent(event)
