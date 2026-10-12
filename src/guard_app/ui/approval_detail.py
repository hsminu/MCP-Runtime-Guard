"""Read-only review of an explicitly authorized, in-memory approval value.

The future B approval API must authenticate the operator and return the same
snapshot used for approval. This widget only displays the supplied value; it
does not fetch, serialize, hash, approve, or retain that snapshot.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import shiboken6
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout, QWidget

from .widgets import KeyboardActionButton, plain_label


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalDetailValue:
    """Trusted projection of one B-owned memory snapshot, never an audit row.

    ``arguments_bytes`` are the snapshot's already canonical UTF-8 bytes. The
    caller must not rebuild them from a mutable argument object for display.
    ``destination`` is the full resolved receiving target supplied by B.
    """

    destination: str
    arguments_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.destination) is not str or type(self.arguments_bytes) is not bytes:
            raise TypeError("approval detail fields have invalid types")

    def __repr__(self) -> str:
        return "ApprovalDetailValue(<redacted>)"


def _codepoint(char: str) -> str:
    number = ord(char)
    if number <= 0xFF:
        return f"\\x{number:02X}"
    if number <= 0xFFFF:
        return f"\\u{number:04X}"
    return f"\\U{number:08X}"


def visible_review_text(value: str) -> str:
    """Show every original character without executing or hiding controls.

    Original backslashes are doubled, so an actual ``\\u200B`` cannot imitate
    the codepoint notation used for an invisible U+200B. Printable non-ASCII
    characters keep their glyph plus their codepoint. Controls, marks, spaces
    with special width, and right-to-left runs use only codepoint notation.
    This display-only conversion never changes the input or approval bytes.
    """

    if type(value) is not str:
        raise TypeError("approval detail text has invalid type")
    pieces: list[str] = []
    for char in value:
        number = ord(char)
        if char == "\\":
            pieces.append("\\\\")
        elif char == "\n":
            pieces.append("\\n\n")
        elif char == "\t":
            pieces.append("\\t")
        elif number < 0x20 or number == 0x7F:
            pieces.append(_codepoint(char))
        elif number < 0x7F:
            pieces.append(char)
        elif (
            unicodedata.category(char)[0] in "CMZ"
            or unicodedata.bidirectional(char) in {"R", "AL", "AN"}
            or char.isspace()
        ):
            pieces.append(_codepoint(char))
        else:
            pieces.append(f"{char}{_codepoint(char)}")
    return "".join(pieces)


class ApprovalDetailDialog(QDialog):
    """Plain-text destination and complete canonical arguments for one ASK.

    This is deliberately display-only. Approval state and first-click locking
    belong to the future A33 page and B control/event contract.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(560, 420)
        self.resize(900, 650)
        self.setWindowTitle("승인 대상 상세")
        self._return_focus: QWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(plain_label("승인 대상 상세", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "비가시 문자와 혼동 가능한 문자는 코드포인트로 표시해. "
                "표시만 바뀌며 승인 사본은 수정되지 않아."
            )
        )

        layout.addWidget(plain_label("전체 수신처"))
        self.destination_body = self._plain_body("승인 대상 전체 수신처")
        layout.addWidget(self.destination_body, 1)

        layout.addWidget(plain_label("전체 인자 · 승인 사본의 UTF-8 바이트"))
        self.arguments_body = self._plain_body("승인 대상 전체 인자")
        layout.addWidget(self.arguments_body, 3)

        close_button = KeyboardActionButton("닫기")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.finished.connect(self._on_finished)

    @staticmethod
    def _plain_body(accessible_name: str) -> QPlainTextEdit:
        body = QPlainTextEdit()
        body.setAccessibleName(accessible_name)
        body.setReadOnly(True)
        body.setUndoRedoEnabled(False)
        body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return body

    def show_value(
        self, value: ApprovalDetailValue, *, return_focus: QWidget | None = None
    ) -> None:
        """Display the provided value after B has authorized its retrieval."""

        self._clear_sensitive_text()
        if type(value) is not ApprovalDetailValue:
            self.reject()
            raise TypeError("approval detail value has invalid type")
        try:
            destination = value.destination.encode("utf-8").decode("utf-8")
            arguments = value.arguments_bytes.decode("utf-8")
        except UnicodeError:
            self.reject()
            raise ValueError("approval detail is not valid UTF-8") from None

        self.destination_body.setPlainText(visible_review_text(destination))
        self.arguments_body.setPlainText(visible_review_text(arguments))
        self._return_focus = return_focus
        self.open()
        self.destination_body.setFocus()

    def _clear_sensitive_text(self) -> None:
        # The backend owns the immutable snapshot. Clear our Qt document copies
        # when the dialog closes; Python/Qt do not promise secure memory erasure.
        self.destination_body.setPlainText("")
        self.arguments_body.setPlainText("")

    def _on_finished(self, _result: int) -> None:
        self._clear_sensitive_text()
        target = self._return_focus
        self._return_focus = None
        if target is not None and shiboken6.isValid(target):
            QTimer.singleShot(0, target.setFocus)
