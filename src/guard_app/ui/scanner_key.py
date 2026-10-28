"""Scanner LLM API key entry (contracts 2026-10-15).

The user prepares their own key. The product stores it only in Windows
Credential Manager through C's ``security/secrets.py`` (C2.10, not yet
available), so this form takes the save step as an injected callback and
never stores, logs or emits the key itself. After any save attempt the input
is cleared; only the last four characters of a saved key stay on screen.
Nothing here sends the key anywhere.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from .widgets import KeyboardActionButton, plain_label

# Returns True only when the key was stored. The caller translates C's errors
# to False; the form never sees an error message or a stored value.
type KeySaver = Callable[[str], bool]

GUIDANCE = (
    "Scanner가 쓸 OpenAI API 키는 직접 준비하세요. 이 앱 전용 프로젝트 키를 새로 만들고 "
    "사용 한도를 걸어 두는 걸 권장함. 키는 이 PC의 Windows 자격증명 관리자에만 "
    "저장하고 우리 서버로 보내지 않음."
)
NOT_CONNECTED = "아직 저장 기능이 연결되지 않아 키를 저장할 수 없음."
SAVE_FAILED = "키를 저장하지 못함. 다시 입력하세요."
SAVE_UNCONFIRMED = "저장 결과를 확인하지 못함. 저장된 키를 확인할 수 없음."


def _suffix(key: str) -> str:
    # A key of four characters or fewer would be shown whole; show nothing then.
    return key[-4:] if len(key) > 4 else ""


class ScannerKeyForm(QWidget):
    """Masked single-key input with an injected, fail-closed save step."""

    key_saved = Signal()

    def __init__(self, saver: KeySaver | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._saver = saver
        self._saved_suffix = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(plain_label("Scanner API 키", name="sectionTitle"))
        self.guidance = plain_label(GUIDANCE)
        layout.addWidget(self.guidance)

        self.key_input = QLineEdit()
        self.key_input.setAccessibleName("Scanner API 키 입력")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.key_input.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.key_input.setClearButtonEnabled(True)
        self.key_input.textChanged.connect(self._refresh)
        self.key_input.returnPressed.connect(self._save)
        layout.addWidget(self.key_input)

        self.save_button = KeyboardActionButton("저장")
        self.save_button.set_visual(icon="check", variant="primary")
        self.save_button.clicked.connect(self._save)
        layout.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.status = plain_label("", name="scannerKeyStatus")
        layout.addWidget(self.status)
        self._refresh()

    @property
    def saved_suffix(self) -> str:
        """Last four characters of the last saved key, or an empty string."""

        return self._saved_suffix

    def _refresh(self, *_args: object) -> None:
        connected = self._saver is not None
        self.save_button.setEnabled(connected and bool(self.key_input.text().strip()))
        if not connected:
            self.status.setText(NOT_CONNECTED)

    def _save(self) -> None:
        key = self.key_input.text().strip()
        # Clear first so neither branch below leaves the key in the widget.
        self.key_input.clear()
        if self._saver is None or not key:
            self._refresh()
            return
        # A saver that raises leaves this, never the previous key's success.
        self._saved_suffix = ""
        self.status.setText(SAVE_UNCONFIRMED)
        if self._saver(key) is True:
            self._saved_suffix = _suffix(key)
            shown = f" · 끝 4자리 {self._saved_suffix}" if self._saved_suffix else ""
            self.status.setText("저장함" + shown)
            self.key_saved.emit()
        else:
            self.status.setText(SAVE_FAILED)
        self._refresh()

    def hideEvent(self, event: QHideEvent) -> None:
        self.key_input.clear()
        super().hideEvent(event)
