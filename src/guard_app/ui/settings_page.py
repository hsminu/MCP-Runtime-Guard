"""Settings cards; display state only, with credentials passed to injected senders."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .model_settings import CodexLoginInfo
from .presentation import (
    SETTINGS_FILE_LOCATION,
    SETTINGS_FILE_NOT_READ,
    SETTINGS_FILE_PENDING,
    SETTINGS_FILE_STATUS,
)
from .scanner_key import KeySaver, KeyTail, ScannerKeyForm
from .widgets import Disclosure, KeyboardActionButton, SectionCard, StatusPill, plain_label

_FILE_PILLS = {
    "SETTINGS_LOADED": ("정책 확인됨", "ok"),
    "SETTINGS_REGISTRATION_SAVED": ("등록 저장됨", "ok"),
    "SETTINGS_FILE_MISSING": ("설정 파일 없음", "attn"),
    "SETTINGS_PROFILE_INVALID": ("정책 오류", "deny"),
    "REGISTRY_UNAVAILABLE": ("등록 기록 오류", "deny"),
}


class _SettingsStatusLabel(QLabel):
    """Keep the shell's existing setText API and fixed copy authoritative.

    The shell already translates codes to unique fixed texts. Project that same
    text into a pill here without changing its navigation or demo semantics.
    """

    def __init__(self, pill: StatusPill) -> None:
        super().__init__()
        self._pill = pill
        self.setObjectName("secondary")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setAccessibleName("설정 파일 상태")
        self.setText(SETTINGS_FILE_PENDING)

    def setText(self, text: str) -> None:
        super().setText(text)
        if text == SETTINGS_FILE_PENDING:
            status = ("확인 전", "neutral")
        elif text == SETTINGS_FILE_NOT_READ:
            status = ("시연 · 읽지 않음", "neutral")
        else:
            code = next((code for code, value in SETTINGS_FILE_STATUS.items() if value == text), "")
            status = _FILE_PILLS.get(code, ("상태 미확인", "neutral"))
        self._pill.set_status(*status)


class SettingsPage(QWidget):
    policy_requested = Signal()

    def __init__(
        self,
        saver: KeySaver | None = None,
        parent: QWidget | None = None,
        *,
        tail: KeyTail | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        self.content = QWidget()
        self.content.setMaximumWidth(760)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(plain_label("설정", name="pageTitle"))
        self.policy_card = SectionCard("Guard 정책 · 설정 파일")
        self.settings_pill = StatusPill()
        self.policy_card.set_status(self.settings_pill)
        self.settings_file_status = _SettingsStatusLabel(self.settings_pill)
        self.policy_card.add_widget(self.settings_file_status)
        self.policy_button = KeyboardActionButton("정책 · 경계 열기")
        self.policy_button.clicked.connect(self.policy_requested.emit)
        self.policy_card.content.addWidget(self.policy_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.policy_details = Disclosure()
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        location = plain_label(SETTINGS_FILE_LOCATION, name="mono")
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(location)
        details_layout.addWidget(
            plain_label("설정 파일을 바꾼 뒤에는 앱을 다시 시작하세요.", name="secondary")
        )
        self.policy_details.set_body(details)
        self.policy_card.add_widget(self.policy_details)
        self.codex_login_info = CodexLoginInfo()
        self.scanner_key_form = ScannerKeyForm(saver, tail=tail)
        for card in (self.policy_card, self.codex_login_info, self.scanner_key_form):
            layout.addWidget(card)
        row = QHBoxLayout()
        outer.addLayout(row)
        row.addWidget(self.content, 1)
        row.addStretch(0)
        outer.addStretch(1)
