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
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from guard_app.services.bridge import (
    CODEX_MODEL_ID,
    AccountView,
    CommandEvent,
    CommandStage,
)

from .answer_widgets import CopyButton
from .presentation import (
    ACCOUNT_REQUEST_REFUSED,
    CODEX_INSTALL_COMMAND,
    CODEX_LOGIN_NOTICES,
    CODEX_LOGIN_TEXT,
    COMMAND_REFUSALS,
    codex_install_notice,
)
from .widgets import (
    Disclosure,
    KeyboardActionButton,
    ResponsiveRow,
    SecretLineEdit,
    SectionCard,
    StatusPill,
    plain_label,
)

UNAVAILABLE = "Codex 실행 기능이 아직 준비 중이라 지금은 로그인하거나 실행할 수 없음."
_STATES = {
    "UNKNOWN": "로그인 상태를 아직 확인하지 않음.",
    "SIGNED_OUT": "로그인되어 있지 않음.",
    "API_KEY": CODEX_LOGIN_TEXT["api_key_saved"],
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
# FAILED notices that carry their own next step replace the generic FAILED line.
_OWN_NEXT_STEP = frozenset(
    {
        "BROWSER_OPEN_FAILED",
        "LOGIN_REQUEST_UNCONFIRMED",
        "LOGIN_CANCEL_UNCONFIRMED",
        "LOGOUT_REQUEST_UNCONFIRMED",
    }
)
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


_STATE_PILLS = {
    "UNKNOWN": ("확인 전", "neutral"),
    "SIGNED_OUT": ("로그인 안 됨", "neutral"),
    "API_KEY": ("API 키 저장됨", "neutral"),
    "CHATGPT": ("ChatGPT 로그인됨", "ok"),
    "OTHER": ("로그인됨(다른 방식)", "ok"),
    "PENDING_DEVICE": ("로그인 진행 중", "live"),
    "PENDING_BROWSER": ("로그인 진행 중", "live"),
    "LOGIN_NOT_COMPLETED": ("로그인 미완료", "attn"),
    "FAILED": ("요청 실패", "deny"),
    "UNAVAILABLE": ("사용 불가", "neutral"),
}


class CodexLoginInfo(SectionCard):
    read_requested = Signal()
    device_login_requested = Signal()
    browser_login_requested = Signal()
    cancel_requested = Signal(str)
    logout_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("LLM · Codex 인증", parent)
        self._view = AccountView("UNAVAILABLE")
        self._api_key_sender: ApiKeySender | None = None
        self._sent_suffix = ""
        self._key_pending = False
        self.status_pill = StatusPill()
        self.set_status(self.status_pill)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.description = plain_label("Codex 실행에 쓸 인증을 연결하세요. 기본은 API 키임.")
        layout.addWidget(self.description)
        self.key_row = ResponsiveRow(600)
        # Start stacked so the horizontal minimum cannot prevent a narrow resize.
        self.key_row.row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.key_row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.api_key_input = SecretLineEdit()
        self.api_key_input.setMinimumWidth(280)
        self.api_key_input.setMaximumWidth(560)
        self.api_key_input.setAccessibleName("Codex API 키 입력")
        self.api_key_input.setPlaceholderText("OpenAI API 키")
        self.api_key_input.setClearButtonEnabled(True)
        self.api_key_input.textChanged.connect(self._refresh_key)
        self.api_key_input.returnPressed.connect(self._send_api_key)
        self.api_key_button = KeyboardActionButton("API 키로 로그인")
        self.api_key_button.set_visual(icon="play", variant="primary")
        self.api_key_button.clicked.connect(self._send_api_key)
        self.key_row.add_widget(self.api_key_input, 1)
        self.key_row.add_widget(self.api_key_button)
        layout.addWidget(self.key_row)
        self.input_status = plain_label("", name="secondary")
        layout.addWidget(self.input_status)
        self.alternative = plain_label("ChatGPT로 로그인", name="secondary")
        layout.addWidget(self.alternative)
        self.login_methods = ResponsiveRow(600)
        self.login_methods.row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.login_methods.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.login_button = KeyboardActionButton("기기 코드로 로그인")
        self.browser_button = KeyboardActionButton("브라우저로 로그인")
        self.login_button.clicked.connect(self._login)
        self.browser_button.clicked.connect(self._browser_login)
        for button in (self.browser_button, self.login_button):
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.login_methods.row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.login_methods.add_widget(self.browser_button)
        self.login_methods.add_widget(self.login_button)
        layout.addWidget(self.login_methods)
        self.status = plain_label(UNAVAILABLE, name="runtimeMessage")
        layout.addWidget(self.status)
        self.install_row = QWidget()
        layout.addWidget(self.install_row)
        install_layout = QHBoxLayout(self.install_row)
        install_layout.setContentsMargins(0, 0, 0, 0)
        self.install_command = plain_label(CODEX_INSTALL_COMMAND, name="mono")
        self.install_command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.install_copy = CopyButton(lambda: CODEX_INSTALL_COMMAND)
        self.install_copy.setAccessibleName("설치 명령 복사")
        self.install_copy.setToolTip("설치 명령 복사")
        install_layout.addWidget(self.install_command, 1)
        install_layout.addWidget(self.install_copy)
        self.device = plain_label("", name="mono")
        self.device.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.device)
        self.action_row = ResponsiveRow(600)
        self.action_row.row.setDirection(QBoxLayout.Direction.TopToBottom)
        self.action_row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.read_button = KeyboardActionButton("상태 확인")
        self.cancel_button = KeyboardActionButton("로그인 취소")
        self.logout_button = KeyboardActionButton("로그아웃")
        self.read_button.clicked.connect(self.read_requested.emit)
        self.cancel_button.clicked.connect(self._cancel)
        self.logout_button.clicked.connect(self.logout_requested.emit)
        for button in (self.read_button, self.cancel_button, self.logout_button):
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.action_row.add_widget(button)
        self.action_row.row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.action_row)
        self.signed_in_hint = plain_label(
            "다른 방식으로 로그인하려면 먼저 로그아웃하세요.", name="secondary"
        )
        layout.addWidget(self.signed_in_hint)
        self.add_widget(body)
        self.details = Disclosure()
        details_body = QWidget()
        details_layout = QVBoxLayout(details_body)
        details_layout.setContentsMargins(0, 0, 0, 0)
        for text in (
            CODEX_LOGIN_TEXT["purpose"],
            CODEX_LOGIN_TEXT["device_help"],
            "Guard는 입력한 API 키를 따로 저장하지 않음.",
            f"사용 모델: {CODEX_MODEL_ID}",
        ):
            details_layout.addWidget(plain_label(text, name="secondary"))
        self.browser_hint = plain_label(CODEX_LOGIN_TEXT["browser_unavailable"], name="secondary")
        details_layout.addWidget(self.browser_hint)
        self.details.set_body(details_body)
        self.add_widget(self.details)
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
        self._key_pending = False
        self.status_pill.set_status(*_STATE_PILLS.get(view.state, _STATE_PILLS["FAILED"]))
        text = _STATES.get(view.state, _STATES["FAILED"])
        if view.state == "CHATGPT" and view.plan:
            text = f"ChatGPT 계정으로 로그인되어 있음 · 요금제 {view.plan}"
        if view.state == "API_KEY" and self._sent_suffix:
            text = CODEX_LOGIN_TEXT["api_key_saved_suffix"].format(suffix=self._sent_suffix)
        elif view.state != "API_KEY":
            self._sent_suffix = ""
        notice = _NOTICES.get(view.notice) if view.notice is not None else None
        if notice is not None and view.state == "FAILED" and view.notice in _OWN_NEXT_STEP:
            # The notice already says what happened and what to do; the generic
            # "try again later" line would contradict it.
            text = notice
        elif notice is not None:
            text = f"{text}\n{notice}"
        install_notice = (
            codex_install_notice(view.notice)
            if view.state == "UNAVAILABLE" and view.notice is not None
            else ""
        )
        if install_notice:
            text = install_notice
        self.install_row.setVisible(bool(install_notice))
        self.status.setText(text)
        self.status.setVisible(view.state not in ("UNKNOWN", "SIGNED_OUT") or notice is not None)
        pending = view.state in ("PENDING_DEVICE", "PENDING_BROWSER")
        device_pending = view.state == "PENDING_DEVICE"
        self.device.setVisible(device_pending)
        self.device.setText(
            f"주소: {view.verification_url}\n코드: {view.user_code}" if device_pending else ""
        )
        available = view.state != "UNAVAILABLE"
        signed_in = view.state in ("API_KEY", "CHATGPT", "OTHER")
        before_login = available and not pending and not signed_in
        self.description.setVisible(before_login)
        self.key_row.setVisible(before_login)
        self.alternative.setVisible(before_login)
        self.login_methods.setVisible(before_login)
        self.api_key_input.setVisible(before_login)
        self.api_key_button.setVisible(before_login)
        self.login_button.setVisible(before_login)
        self.browser_button.setVisible(before_login and view.browser_available)
        self.read_button.setVisible(available and not pending)
        self.cancel_button.setVisible(pending)
        self.logout_button.setVisible(available and (signed_in or view.notice in _RETRY_LOGOUT))
        self.action_row.setVisible(available)
        self.signed_in_hint.setVisible(signed_in)
        self.read_button.setEnabled(available and not pending)
        self.login_button.setEnabled(available and not pending and not signed_in)
        self.browser_button.setEnabled(self.login_button.isEnabled() and view.browser_available)
        self.browser_hint.setVisible(not view.browser_available)
        self.cancel_button.setEnabled(pending)
        self.logout_button.setEnabled(available and (signed_in or view.notice in _RETRY_LOGOUT))
        self._refresh_key()

    def _key_allowed(self) -> bool:
        state = self._view.state
        return (
            self._api_key_sender is not None
            and not self._key_pending
            and state
            not in (
                "UNAVAILABLE",
                "PENDING_DEVICE",
                "PENDING_BROWSER",
                "API_KEY",
                "CHATGPT",
                "OTHER",
            )
        )

    def _refresh_key(self, *_args: object) -> None:
        allowed = self._key_allowed()
        self.api_key_input.setEnabled(allowed)
        self.api_key_button.setEnabled(allowed and bool(self.api_key_input.text().strip()))
        if not allowed and self.api_key_input.text():
            self.api_key_input.clear()
        text = (
            API_KEY_SENT
            if self._key_pending
            else ("입력됨 · 아직 보내지 않음" if self.api_key_input.text() else "")
        )
        self.input_status.setText(text)
        self.input_status.setVisible(bool(text))

    def _send_api_key(self) -> None:
        key = self.api_key_input.text().strip()
        # Clear first so no branch below leaves the key in the widget.
        self.api_key_input.clear()
        sender = self._api_key_sender
        if sender is None or not key or not self._key_allowed():
            self._refresh_key()
            return
        self._sent_suffix = _suffix(key)
        self._key_pending = True
        self._refresh_key()
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
        self.status.show()
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
            self.status.show()
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
