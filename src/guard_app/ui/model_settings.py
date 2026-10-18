"""Codex owns model authentication and credential storage."""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .widgets import plain_label


class CodexLoginInfo(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(plain_label("Codex 로그인", name="cardTitle"))
        layout.addWidget(plain_label("사용 모델: gpt-6.1-sol", name="secondary"))
        layout.addWidget(
            plain_label(
                "ChatGPT 기기 코드와 API 키는 모두 Codex 안에서 로그인하는 방식이야. "
                "자격증명은 Codex가 Windows 자격증명 관리자에 직접 저장해.",
                name="secondary",
            )
        )
        layout.addWidget(
            plain_label(
                "Codex 연결과 시작 검사가 아직 준비 중이라 지금은 로그인하거나 실행할 수 없어.",
                name="runtimeMessage",
            )
        )
        layout.addWidget(plain_label("Scanner는 아래에서 별도 API 키를 설정해.", name="secondary"))
