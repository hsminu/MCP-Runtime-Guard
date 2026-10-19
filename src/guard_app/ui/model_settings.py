"""Codex owns model authentication and credential storage (A52, A89).

The panel asks Codex to start or cancel its own ChatGPT device-code login,
reads the account kind/plan, and logs out. An API key may also be entered
(contracts 2026-10-18, A89; replaces A65 here) under the Scanner key rules:
a masked field, only the last four characters shown, the field cleared right
after sending, and the key never logged, emitted as a signal or kept. It goes
only to an injected sender that hands it to Codex `account/login/start`;
Codex stores it in the Windows Credential Manager, the Guard stores nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from guard_app.services.bridge import CODEX_MODEL_ID, AccountView

from .widgets import KeyboardActionButton, plain_label

UNAVAILABLE = "Codex 실행 기능이 아직 준비 중이라 지금은 로그인하거나 실행할 수 없어."
_STATES = {
    "UNKNOWN": "로그인 상태를 아직 확인하지 않았어.",
    "SIGNED_OUT": "로그인되어 있지 않아.",
    "API_KEY": "API 키로 로그인되어 있어.",
    "CHATGPT": "ChatGPT 계정으로 로그인되어 있어.",
    "OTHER": "다른 방식으로 로그인되어 있어.",
    "PENDING_DEVICE": "아래 주소에서 코드를 입력해 로그인을 마쳐. 끝나면 상태가 바뀌어.",
    "LOGIN_NOT_COMPLETED": "로그인이 완료되지 않았어. 다시 시도해.",
    "FAILED": "Codex 계정 요청을 처리하지 못했어. 잠시 뒤 다시 확인해.",
    "UNAVAILABLE": UNAVAILABLE,
}
API_KEY_SENT = "API 키로 로그인을 요청했어. 결과를 기다리는 중이야."

# Hands one key to the bridge's Codex login command; never stores it.
type ApiKeySender = Callable[[str], object]


def _suffix(key: str) -> str:
    # A key of four characters or fewer would be shown whole; show nothing then.
    return key[-4:] if len(key) > 4 else ""


class CodexLoginInfo(QWidget):
    read_requested = Signal()
    device_login_requested = Signal()
    cancel_requested = Signal(str)
    logout_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = AccountView("UNAVAILABLE")
        self._api_key_sender: ApiKeySender | None = None
        self._sent_suffix = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(plain_label("Codex 로그인", name="cardTitle"))
        layout.addWidget(plain_label(f"사용 모델: {CODEX_MODEL_ID}", name="secondary"))
        layout.addWidget(
            plain_label(
                "ChatGPT 기기 코드와 API 키는 모두 Codex 안에서 로그인하는 방식이야. "
                "자격증명은 Codex가 Windows 자격증명 관리자에 직접 저장해.",
                name="secondary",
            )
        )
        self.status = plain_label(UNAVAILABLE, name="runtimeMessage")
        layout.addWidget(self.status)
        self.device = plain_label("", name="secondary")
        self.device.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device.setVisible(False)
        layout.addWidget(self.device)
        buttons = QHBoxLayout()
        self.read_button = KeyboardActionButton("상태 확인")
        self.login_button = KeyboardActionButton("ChatGPT로 로그인")
        self.cancel_button = KeyboardActionButton("로그인 취소")
        self.logout_button = KeyboardActionButton("로그아웃")
        self.read_button.clicked.connect(self.read_requested.emit)
        self.login_button.clicked.connect(self._login)
        self.cancel_button.clicked.connect(self._cancel)
        self.logout_button.clicked.connect(self.logout_requested.emit)
        for button in (self.read_button, self.login_button, self.cancel_button, self.logout_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        key_row = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setAccessibleName("Codex API 키 입력")
        self.api_key_input.setPlaceholderText("OpenAI API 키")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.api_key_input.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.api_key_input.setClearButtonEnabled(True)
        self.api_key_input.textChanged.connect(self._refresh_key)
        self.api_key_input.returnPressed.connect(self._send_api_key)
        self.api_key_button = KeyboardActionButton("API 키로 로그인")
        self.api_key_button.clicked.connect(self._send_api_key)
        key_row.addWidget(self.api_key_input, 1)
        key_row.addWidget(self.api_key_button)
        layout.addLayout(key_row)
        layout.addWidget(plain_label("Scanner는 아래에서 별도 API 키를 설정해.", name="secondary"))
        self.set_view(self._view)

    @property
    def view(self) -> AccountView:
        return self._view

    def set_api_key_sender(self, sender: ApiKeySender | None) -> None:
        self._api_key_sender = sender
        self._refresh_key()

    def set_view(self, view: object) -> None:
        if type(view) is not AccountView:
            view = AccountView("FAILED")
        self._view = view
        text = _STATES.get(view.state, _STATES["FAILED"])
        if view.state == "CHATGPT" and view.plan:
            text = f"ChatGPT 계정으로 로그인되어 있어 · 요금제 {view.plan}"
        if view.state == "API_KEY" and self._sent_suffix:
            text = f"API 키로 로그인되어 있어 · 끝 4자리 {self._sent_suffix}"
        elif view.state != "API_KEY":
            self._sent_suffix = ""
        self.status.setText(text)
        pending = view.state == "PENDING_DEVICE"
        self.device.setVisible(pending)
        self.device.setText(
            f"주소: {view.verification_url}\n코드: {view.user_code}" if pending else ""
        )
        available = view.state != "UNAVAILABLE"
        signed_in = view.state in ("API_KEY", "CHATGPT", "OTHER")
        self.read_button.setEnabled(available and not pending)
        self.login_button.setEnabled(available and not pending and not signed_in)
        self.cancel_button.setEnabled(pending)
        self.logout_button.setEnabled(available and signed_in)
        self._refresh_key()

    def _key_allowed(self) -> bool:
        state = self._view.state
        return self._api_key_sender is not None and state not in (
            "UNAVAILABLE",
            "PENDING_DEVICE",
            "API_KEY",
            "CHATGPT",
            "OTHER",
        )

    def _refresh_key(self, *_args: object) -> None:
        allowed = self._key_allowed()
        self.api_key_input.setEnabled(allowed)
        self.api_key_button.setEnabled(allowed and bool(self.api_key_input.text().strip()))
        if not allowed:
            self.api_key_input.clear()

    def _send_api_key(self) -> None:
        key = self.api_key_input.text().strip()
        # Clear first so no branch below leaves the key in the widget.
        self.api_key_input.clear()
        sender = self._api_key_sender
        if sender is None or not key or not self._key_allowed():
            self._refresh_key()
            return
        self._sent_suffix = _suffix(key)
        self.status.setText(API_KEY_SENT)
        self.login_button.setEnabled(False)
        self.api_key_button.setEnabled(False)
        sender(key)

    def hideEvent(self, event: QHideEvent) -> None:
        self.api_key_input.clear()
        super().hideEvent(event)

    def _login(self) -> None:
        self.login_button.setEnabled(False)
        self.device_login_requested.emit()

    def _cancel(self) -> None:
        if self._view.login_id:
            self.cancel_button.setEnabled(False)
            self.cancel_requested.emit(self._view.login_id)
