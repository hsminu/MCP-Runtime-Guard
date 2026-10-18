"""Codex owns model authentication and credential storage (A52, A89).

The panel only asks Codex to start or cancel its own ChatGPT device-code login,
reads the account kind/plan, and logs out. It never collects a model secret:
there is no key field here (A65), and nothing is stored by the Guard.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

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


class CodexLoginInfo(QWidget):
    read_requested = Signal()
    device_login_requested = Signal()
    cancel_requested = Signal(str)
    logout_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = AccountView("UNAVAILABLE")
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
        layout.addWidget(plain_label("Scanner는 아래에서 별도 API 키를 설정해.", name="secondary"))
        self.set_view(self._view)

    @property
    def view(self) -> AccountView:
        return self._view

    def set_view(self, view: object) -> None:
        if type(view) is not AccountView:
            view = AccountView("FAILED")
        self._view = view
        text = _STATES.get(view.state, _STATES["FAILED"])
        if view.state == "CHATGPT" and view.plan:
            text = f"ChatGPT 계정으로 로그인되어 있어 · 요금제 {view.plan}"
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

    def _login(self) -> None:
        self.login_button.setEnabled(False)
        self.device_login_requested.emit()

    def _cancel(self) -> None:
        if self._view.login_id:
            self.cancel_button.setEnabled(False)
            self.cancel_requested.emit(self._view.login_id)
