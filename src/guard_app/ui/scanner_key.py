"""Scanner LLM API key entry (contracts 2026-10-15).

The user prepares their own key. The product stores it only in Windows
Credential Manager through C's ``security/secrets.py`` (C2.10),
so this form takes the save step as an injected callback and
never stores, logs or emits the key itself. After any save attempt the input
is cleared; only the last four characters of a saved key stay on screen.
Nothing here sends the key anywhere.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QBoxLayout, QSizePolicy, QWidget

from .widgets import (
    Disclosure,
    KeyboardActionButton,
    ResponsiveRow,
    SecretLineEdit,
    SectionCard,
    StatusPill,
    fill_row_height,
    plain_label,
)

# Returns True only when the key was stored. The caller translates C's errors
# to False; the form never sees an error message or a stored value.
type KeySaver = Callable[[str], bool]
type KeyTail = Callable[[], str | None]

GUIDANCE = (
    "Scanner가 쓸 OpenAI API 키는 직접 준비하세요. 이 앱 전용 프로젝트 키를 새로 만들고 "
    "사용 한도를 걸어 두는 걸 권장함. 키는 이 PC의 Windows 자격증명 관리자에만 "
    "저장하고 우리 서버로 보내지 않음."
)
NOT_CONNECTED = "아직 저장 기능이 연결되지 않아 키를 저장할 수 없음."
SAVE_FAILED = "키를 저장하지 못함. 다시 입력하세요."
SAVE_UNCONFIRMED = "저장 결과를 확인하지 못함. 저장된 키를 확인할 수 없음."
NO_SAVED_KEY = "저장된 키 없음"


def _suffix(key: str) -> str:
    # A key of four characters or fewer would be shown whole; show nothing then.
    return key[-4:] if len(key) > 4 else ""


class ScannerKeyForm(SectionCard):
    """Masked single-key input with an injected, fail-closed save step."""

    key_saved = Signal()

    def __init__(
        self,
        saver: KeySaver | None = None,
        parent: QWidget | None = None,
        *,
        tail: KeyTail | None = None,
    ) -> None:
        super().__init__("Scanner API 키", parent)
        self._saver = saver
        self._saved_suffix = ""
        self._has_saved_key = False
        self._save_failed = False
        self._saving = False
        self.status_pill = StatusPill()
        self.set_status(self.status_pill)
        self.add_widget(
            plain_label("Scanner가 MCP 서버 검사에 쓸 OpenAI API 키임. Codex 인증과 별개임.")
        )
        self.key_input = SecretLineEdit()
        self.key_input.setMinimumWidth(280)
        self.key_input.setMaximumWidth(560)
        self.key_input.setAccessibleName("Scanner API 키 입력")
        self.key_input.setPlaceholderText("OpenAI API 키")
        self.key_input.setClearButtonEnabled(True)
        self.key_input.textChanged.connect(self._edit_changed)
        self.key_input.returnPressed.connect(self._save)
        self.save_button = KeyboardActionButton("저장")
        self.save_button.set_visual(icon="check", variant="primary")
        self.save_button.clicked.connect(self._save)
        self.key_row = ResponsiveRow(600)
        self.key_row.row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.key_row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.key_row.add_widget(self.key_input, 1)
        self.key_row.add_widget(self.save_button)
        fill_row_height(self.key_input, self.save_button)
        self.add_widget(self.key_row)
        self.status = plain_label("", name="secondary")
        self.add_widget(self.status)
        self.details = Disclosure()
        self.guidance = plain_label(GUIDANCE, name="secondary")
        self.details.set_body(self.guidance)
        self.add_widget(self.details)
        if tail is not None:
            suffix = tail()
            # Reject an invalid callback result instead of displaying a full key.
            if (
                type(suffix) is str
                and len(suffix) == 4
                and all(char.isascii() and (char.isalnum() or char in "_-") for char in suffix)
            ):
                self._saved_suffix = suffix
                self._has_saved_key = True
        self._refresh()

    @property
    def saved_suffix(self) -> str:
        """Last four characters of the last saved key, or an empty string."""

        return self._saved_suffix

    def _edit_changed(self, *_args: object) -> None:
        if self._saver is None and self.key_input.text():
            self.key_input.clear()
        if not self._saving:
            self.status.setText("입력됨 · 저장 전" if self.key_input.text() else "")
        self._refresh()

    def _refresh(self) -> None:
        connected = self._saver is not None
        self.key_input.setEnabled(connected and not self._saving)
        self.save_button.setEnabled(
            connected and not self._saving and bool(self.key_input.text().strip())
        )
        self.save_button.setText("새 키로 교체" if self._has_saved_key else "저장")
        if not connected:
            self.status.setText(NOT_CONNECTED)
            self.status_pill.set_status("저장 기능 미연결", "neutral")
        elif self._save_failed:
            self.status_pill.set_status("저장 실패", "deny")
        elif self._has_saved_key:
            shown = f" · 끝 4자리 {self._saved_suffix}" if self._saved_suffix else ""
            self.status_pill.set_status("저장됨" + shown, "ok")
        else:
            self.status_pill.set_status(NO_SAVED_KEY, "neutral")
        self.status.setVisible(bool(self.status.text()))

    def _save(self) -> None:
        key = self.key_input.text().strip()
        # Clear first so neither branch below leaves the key in the widget.
        self.key_input.clear()
        if self._saver is None or not key or self._saving:
            self._refresh()
            return
        self._saved_suffix = ""
        self._has_saved_key = False
        self._save_failed = False
        self._saving = True
        self.status.setText("저장 중…")
        self._refresh()
        self.status.repaint()
        completed = False
        try:
            saved = self._saver(key) is True
            completed = True
        finally:
            # Preserve the existing fail-closed display even if the saver raises;
            # exceptions still propagate to the caller, never rendered as text.
            self._saving = False
            if not completed:
                self._save_failed = True
                self.status.setText(SAVE_UNCONFIRMED)
                self._refresh()
        if saved:
            self._saved_suffix = _suffix(key)
            self._has_saved_key = True
            shown = f" · 끝 4자리 {self._saved_suffix}" if self._saved_suffix else ""
            self.status.setText("저장됨" + shown)
        else:
            self._save_failed = True
            self.status.setText(SAVE_FAILED)
        self._refresh()
        if saved:
            self.key_saved.emit()

    def hideEvent(self, event: QHideEvent) -> None:
        self.key_input.clear()
        super().hideEvent(event)
