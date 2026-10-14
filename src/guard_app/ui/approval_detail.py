"""Read-only review of an explicitly authorized, in-memory approval value.

The future B approval API must authenticate the operator and return the same
snapshot used for approval. This widget only displays the supplied value; it
does not fetch, serialize, hash, approve, or retain that snapshot.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import shiboken6
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .presentation import INVISIBLE_LETTERS
from .widgets import KeyboardActionButton, plain_label, size_detail_for_viewport


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


def _is_hangul_syllable(char: str) -> bool:
    return 0xAC00 <= ord(char) <= 0xD7A3


def _is_conjoining_jamo(char: str) -> bool:
    number = ord(char)
    return 0x1100 <= number <= 0x11FF or 0xA960 <= number <= 0xA97F or 0xD7B0 <= number <= 0xD7FF


# Punctuation that renders like a URL/path separator, dot, colon, or hyphen
# and is unchanged by NFKC (NFKC-changing lookalikes are marked separately).
_CONFUSABLE_PUNCTUATION = frozenset(
    {
        0x02D0,  # MODIFIER LETTER TRIANGULAR COLON
        0x0589,  # ARMENIAN FULL STOP
        0x2010,  # HYPHEN
        0x2012,  # FIGURE DASH
        0x2013,  # EN DASH
        0x2027,  # HYPHENATION POINT
        0x2043,  # HYPHEN BULLET
        0x2044,  # FRACTION SLASH
        0x2212,  # MINUS SIGN
        0x2215,  # DIVISION SLASH
        0x2216,  # SET MINUS
        0x2236,  # RATIO
        0x29F5,  # REVERSE SOLIDUS OPERATOR
        0x29F8,  # BIG SOLIDUS
        0x3002,  # IDEOGRAPHIC FULL STOP
        0xA789,  # MODIFIER LETTER COLON
    }
)


def visible_review_text(value: str) -> str:
    """Show every original character without executing or hiding controls.

    Original backslashes are doubled, so an actual ``\\u200B`` cannot imitate
    the codepoint notation used for an invisible U+200B. Controls, marks,
    spaces with special width, right-to-left runs, and blank-looking letters
    (Hangul fillers, braille blank) use only codepoint notation. Lookalikes
    keep their glyph plus codepoint: non-ASCII letters in mixed-script tokens,
    conjoining Hangul jamo (which can compose a syllable that looks identical
    to a precomposed one), separator-like punctuation, and NFKC-changing
    characters. Precomposed Hangul syllables are never Latin lookalikes, so
    ordinary Korean mixed with ASCII stays unmarked. This display-only
    conversion never changes the input or approval bytes.
    """

    if type(value) is not str:
        raise TypeError("approval detail text has invalid type")
    suspicious: set[int] = set()
    # Keep hostname labels and URL/path segments together: a Cyrillic-only
    # label followed by Latin .com is still a mixed-script receiving target.
    for token in re.finditer(r"[\w./:@-]+", value, re.UNICODE):
        scripts = {
            unicodedata.name(char, "UNKNOWN").split()[0] for char in token.group() if char.isalpha()
        }
        if len(scripts) > 1:
            suspicious.update(range(token.start(), token.end()))
    pieces: list[str] = []
    for index, char in enumerate(value):
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
            or number in INVISIBLE_LETTERS
        ):
            pieces.append(_codepoint(char))
        elif _is_hangul_syllable(char):
            pieces.append(char)
        elif (
            index in suspicious
            or _is_conjoining_jamo(char)
            or number in _CONFUSABLE_PUNCTUATION
            or unicodedata.normalize("NFKC", char) != char
        ):
            pieces.append(f"{char}{_codepoint(char)}")
        else:
            pieces.append(char)
    return "".join(pieces)


class ApprovalDetailDialog(QDialog):
    """Plain-text destination and complete canonical arguments for one ASK.

    This is deliberately display-only. Approval state and first-click locking
    belong to the future A33 page and B control/event contract.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(240, 200)
        size_detail_for_viewport(self, 900, 650)
        self.setWindowTitle("승인 대상 상세")
        self._return_focus: QWidget | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 24, 24, 20)
        self._layout.setSpacing(10)
        self._layout.addWidget(plain_label("승인 대상 상세", name="pageTitle"))
        explanation = (
            "비가시 문자와 혼동 가능한 문자는 코드포인트로 표시해. "
            "표시만 바뀌며 승인 사본은 수정되지 않아."
        )
        self.description = plain_label(explanation)
        self._layout.addWidget(self.description)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setObjectName("approvalDetailScroll")
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        content = QWidget()
        fields = QVBoxLayout(content)
        fields.setContentsMargins(0, 0, 8, 0)
        fields.setSpacing(8)
        fields.addWidget(plain_label("전체 수신처"))
        self.destination_body = self._plain_body("승인 대상 전체 수신처")
        self.destination_body.setAccessibleDescription(explanation)
        fields.addWidget(self.destination_body, 1)

        fields.addWidget(plain_label("전체 인자 · 승인 사본의 UTF-8 바이트"))
        self.arguments_body = self._plain_body("승인 대상 전체 인자")
        self.arguments_body.setAccessibleDescription(explanation)
        fields.addWidget(self.arguments_body, 3)
        self._detail_scroll.setWidget(content)
        self._layout.addWidget(self._detail_scroll, 1)

        close_button = KeyboardActionButton("닫기")
        close_button.clicked.connect(self.close)
        self._layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.finished.connect(self._on_finished)

    @staticmethod
    def _plain_body(accessible_name: str) -> QPlainTextEdit:
        body = QPlainTextEdit()
        body.setAccessibleName(accessible_name)
        body.setReadOnly(True)
        body.setUndoRedoEnabled(False)
        body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        body.setMinimumHeight(128)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return body

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = self.height() <= 350
        self.description.setVisible(not compact)
        self._layout.setContentsMargins(
            12 if compact else 24,
            10 if compact else 24,
            12 if compact else 24,
            10 if compact else 20,
        )
        self._layout.setSpacing(6 if compact else 10)

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
        size_detail_for_viewport(self, 900, 650)
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
