"""Display-only view for text the Guard does not trust.

Model answers, server or tool descriptions and Scanner ``threat_summary`` may
carry prompt-injection or deceptive characters. This view never interprets
them: no HTML, Markdown, links or external resources. Every character goes
through the same ``visible_review_text`` used for approval detail, so controls,
bidi runs and lookalikes are shown as codepoints. Real line breaks stay line
breaks so a multi-line answer remains readable; all other controls are
visualized. The caller decides the length cap and whether to show the
unverified notice (for ``threat_summary``: 300 characters and the notice,
contracts 2026-10-15). The view stores nothing besides its Qt document.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from .approval_detail import visible_review_text
from .widgets import plain_label

UNVERIFIED_NOTICE = "검증되지 않은 내용"


def untrusted_display_text(value: str, *, max_chars: int | None = None) -> tuple[str, int]:
    """Return the visualized text and how many original characters were left out.

    The cap counts original characters before visualization, so codepoint
    notation never pushes real content out of the shown part.
    """

    if type(value) is not str:
        raise TypeError("untrusted text has invalid type")
    if max_chars is not None and (type(max_chars) is not int or max_chars < 1):
        raise ValueError("untrusted text limit is invalid")
    omitted = 0
    if max_chars is not None and len(value) > max_chars:
        omitted = len(value) - max_chars
        value = value[:max_chars]
    shown = "\n".join(visible_review_text(line) for line in value.split("\n"))
    return shown, omitted


class UntrustedTextView(QWidget):
    """Read-only plain text with an optional unverified notice and length cap."""

    def __init__(
        self,
        accessible_name: str,
        *,
        max_chars: int | None = None,
        show_unverified_notice: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if max_chars is not None and (type(max_chars) is not int or max_chars < 1):
            raise ValueError("untrusted text limit is invalid")
        self._max_chars = max_chars
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.notice = plain_label(UNVERIFIED_NOTICE, name="unverifiedNotice")
        self.notice.setVisible(show_unverified_notice)
        layout.addWidget(self.notice)
        self.body = QPlainTextEdit()
        self.body.setAccessibleName(accessible_name)
        if show_unverified_notice:
            self.body.setAccessibleDescription(UNVERIFIED_NOTICE)
        self.body.setReadOnly(True)
        self.body.setUndoRedoEnabled(False)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.body, 1)
        self.omission = plain_label("", name="untrustedOmission")
        self.omission.setVisible(False)
        layout.addWidget(self.omission)

    def set_text(self, value: str) -> None:
        shown, omitted = untrusted_display_text(value, max_chars=self._max_chars)
        self.body.setPlainText(shown)
        self.omission.setText(f"뒤의 {omitted}자는 표시하지 않았어." if omitted else "")
        self.omission.setVisible(bool(omitted))

    def clear(self) -> None:
        # Python/Qt do not promise secure erasure; this only drops our document copy.
        self.body.setPlainText("")
        self.omission.setText("")
        self.omission.setVisible(False)
