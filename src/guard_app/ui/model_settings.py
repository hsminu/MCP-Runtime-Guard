"""Codex owns model authentication and credential storage (A52, A89).

The panel asks Codex to start or cancel its own ChatGPT device-code login,
reads the account kind/plan, and logs out. Logout is refused while a session
runs, and a logout whose saved credentials were not deleted says so (#21).
An API key may also be entered (contracts 2026-10-18, A89; replaces A65 here)
under the Scanner key rules:
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

from guard_app.services.bridge import (
    CODEX_MODEL_ID,
    AccountView,
    CommandEvent,
    CommandStage,
)

from .presentation import (
    ACCOUNT_REQUEST_REFUSED,
    CODEX_LOGIN_NOTICES,
    CODEX_LOGIN_TEXT,
    COMMAND_REFUSALS,
)
from .widgets import KeyboardActionButton, plain_label

UNAVAILABLE = "Codex 실행 기능이 아직 준비 중이라 지금은 로그인하거나 실행할 수 없음."
_STATES = {
    "UNKNOWN": "로그인 상태를 아직 확인하지 않음.",
    "SIGNED_OUT": "로그인되어 있지 않음.",
    "API_KEY": "API 키로 로그인되어 있음.",
    "CHATGPT": "ChatGPT 계정으로 로그인되어 있음.",
    "OTHER": "다른 방식으로 로그인되어 있음.",
    "PENDING_DEVICE": "아래 주소에서 코드를 입력해 로그인을 마치세요. 끝나면 상태가 바뀜.",
    "PENDING_BROWSER": CODEX_LOGIN_TEXT["browser_pending"],
    "LOGIN_NOT_COMPLETED": "로그인이 완료되지 않음. 다시 시도하세요.",
    "FAILED": "Codex 계정 요청을 처리하지 못함. 잠시 뒤 다시 확인하세요.",
    "UNAVAILABLE": UNAVAILABLE,
}
_NOTICES = {
    **CODEX_LOGIN_NOTICES,
    "LOGOUT_SESSIONS_RUNNING": (
        "실행 중인 세션이 있어 로그아웃하지 않음. 세션을 모두 끝낸 뒤 다시 시도하세요."
    ),
    "LOGOUT_EXIT_UNCONFIRMED": (
        "로그아웃했지만 Codex 종료를 확인하지 못해 저장된 로그인 정보를 지우지 않음. "
        "앱을 다시 시작하세요."
    ),
    "LOGOUT_CLEANUP_FAILED": (
        "로그아웃했지만 저장된 로그인 정보를 지우지 못함. 로그아웃을 다시 누르세요."
    ),
}
# Logout stays available after these, so the cleanup can be asked again.
_RETRY_LOGOUT = frozenset({"LOGOUT_CLEANUP_FAILED"})
# An accepted account command whose result is unknown; login starts use
# LOGIN_REQUEST_UNCONFIRMED. The state must be read again either way.
_UNCONFIRMED = {
    "account_read": "ACCOUNT_READ_UNCONFIRMED",
    "account_cancel_login": "LOGIN_CANCEL_UNCONFIRMED",
    "account_logout": "LOGOUT_REQUEST_UNCONFIRMED",
}
API_KEY_SENT = "API 키로 로그인을 요청함. 결과를 기다리는 중임."

# Hands one key to the bridge's Codex login command; never stores it.
type ApiKeySender = Callable[[str], object]


def _suffix(key: str) -> str:
    # A key of four characters or fewer would be shown whole; show nothing then.
    return key[-4:] if len(key) > 4 else ""


class CodexLoginInfo(QWidget):
    read_requested = Signal()
    device_login_requested = Signal()
    browser_login_requested = Signal()
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
                CODEX_LOGIN_TEXT["purpose"],
                name="secondary",
            )
        )
        layout.addWidget(plain_label(CODEX_LOGIN_TEXT["device_help"], name="secondary"))
        self.browser_hint = plain_label(CODEX_LOGIN_TEXT["browser_unavailable"], name="secondary")
        layout.addWidget(self.browser_hint)
        self.status = plain_label(UNAVAILABLE, name="runtimeMessage")
        layout.addWidget(self.status)
        self.device = plain_label("", name="secondary")
        self.device.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device.setVisible(False)
        layout.addWidget(self.device)
        buttons = QHBoxLayout()
        self.read_button = KeyboardActionButton("상태 확인")
        self.login_button = KeyboardActionButton(CODEX_LOGIN_TEXT["device_button"])
        self.browser_button = KeyboardActionButton(CODEX_LOGIN_TEXT["browser_button"])
        self.cancel_button = KeyboardActionButton("로그인 취소")
        self.logout_button = KeyboardActionButton("로그아웃")
        self.read_button.clicked.connect(self.read_requested.emit)
        self.login_button.clicked.connect(self._login)
        self.browser_button.clicked.connect(self._browser_login)
        self.cancel_button.clicked.connect(self._cancel)
        self.logout_button.clicked.connect(self.logout_requested.emit)
        for button in (
            self.read_button,
            self.browser_button,
            self.login_button,
            self.cancel_button,
            self.logout_button,
        ):
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
        layout.addWidget(
            plain_label("Scanner는 아래에서 별도 API 키를 설정하세요.", name="secondary")
        )
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
            text = f"ChatGPT 계정으로 로그인되어 있음 · 요금제 {view.plan}"
        if view.state == "API_KEY" and self._sent_suffix:
            text = f"API 키로 로그인되어 있음 · 끝 4자리 {self._sent_suffix}"
        elif view.state != "API_KEY":
            self._sent_suffix = ""
        notice = _NOTICES.get(view.notice) if view.notice is not None else None
        if notice is not None:
            text = f"{text}\n{notice}"
        self.status.setText(text)
        pending = view.state in ("PENDING_DEVICE", "PENDING_BROWSER")
        device_pending = view.state == "PENDING_DEVICE"
        self.device.setVisible(device_pending)
        self.device.setText(
            f"주소: {view.verification_url}\n코드: {view.user_code}" if device_pending else ""
        )
        available = view.state != "UNAVAILABLE"
        signed_in = view.state in ("API_KEY", "CHATGPT", "OTHER")
        self.read_button.setEnabled(available and not pending)
        self.login_button.setEnabled(available and not pending and not signed_in)
        self.browser_button.setEnabled(self.login_button.isEnabled() and view.browser_available)
        self.browser_hint.setVisible(not view.browser_available)
        self.cancel_button.setEnabled(pending)
        self.logout_button.setEnabled(available and (signed_in or view.notice in _RETRY_LOGOUT))
        self._refresh_key()

    def _key_allowed(self) -> bool:
        state = self._view.state
        return self._api_key_sender is not None and state not in (
            "UNAVAILABLE",
            "PENDING_DEVICE",
            "PENDING_BROWSER",
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
        self.browser_button.setEnabled(False)
        self.api_key_button.setEnabled(False)
        sender(key)

    def hideEvent(self, event: QHideEvent) -> None:
        self.api_key_input.clear()
        super().hideEvent(event)

    def _login(self) -> None:
        self._disable_login()
        self.device_login_requested.emit()

    def _browser_login(self) -> None:
        self._disable_login()
        self.browser_login_requested.emit()

    def _disable_login(self) -> None:
        self.status.setText(CODEX_LOGIN_TEXT["starting"])
        self.login_button.setEnabled(False)
        self.browser_button.setEnabled(False)
        self.api_key_button.setEnabled(False)

    def account_command_changed(self, event: object) -> None:
        if type(event) is not CommandEvent or not event.action.startswith("account_"):
            return
        if event.stage is CommandStage.REJECTED:
            # Nothing ran, so the last account state still holds. Restore the
            # buttons a start disabled and say the request was not taken.
            self.set_view(self._view)
            refusal = COMMAND_REFUSALS.get(event.code, ACCOUNT_REQUEST_REFUSED)
            self.status.setText(f"{self.status.text()}\n{refusal}")
        elif event.stage is CommandStage.UNCONFIRMED:
            self.set_view(
                AccountView(
                    "FAILED",
                    notice=_UNCONFIRMED.get(event.action, "LOGIN_REQUEST_UNCONFIRMED"),
                    browser_available=self._view.browser_available,
                )
            )

    def _cancel(self) -> None:
        if self._view.login_id:
            self.cancel_button.setEnabled(False)
            self.cancel_requested.emit(self._view.login_id)
