"""Read-only Task status presentation for backend-confirmed state."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from guard_app.domain.enums import TaskStatus

from .widgets import plain_label

_STATUS_TEXT: dict[TaskStatus, tuple[str, str]] = {
    TaskStatus.READY: (
        "시작 전 · READY",
        "Task가 시작 전이야. 실행 가능 여부는 서버 등록과 모델 환경을 백엔드에서 확인해야 해.",
    ),
    TaskStatus.RUNNING: (
        "진행 중 · RUNNING",
        "Task가 진행 중이야. 취소나 긴급 차단을 요청했더라도 적용 여부는 백엔드 결과로 확인해야 해. 이미 전송된 쓰기는 수신됐을 수 있어.",
    ),
    TaskStatus.WAITING_APPROVAL: (
        "승인 대기 · WAITING_APPROVAL",
        "승인 처리를 기다리고 있어. 승인·거절·취소 결과는 백엔드가 확정한 뒤 표시해.",
    ),
    TaskStatus.HALTED: (
        "중지됨 · HALTED",
        "이 Task는 이어서 실행할 수 없어. 새 Task가 필요해.",
    ),
    TaskStatus.UNKNOWN_OUTCOME: (
        "결과 불명 · UNKNOWN_OUTCOME",
        "전송이 실행됐는지 확인되지 않았어. 수신측을 확인해야 해. 이 Task는 다시 실행할 수 없어.",
    ),
    TaskStatus.CLOSED: (
        "종료 · CLOSED",
        "이 Task는 종료됐어. 실행할 수 없어.",
    ),
}
_UNKNOWN_TEXT = (
    "상태 미확인",
    "Task 상태를 확인할 수 없어. 실행과 제어는 백엔드 조회 후에 판단해.",
)


def status_title(status: TaskStatus | None) -> str:
    text = _STATUS_TEXT.get(status) if status is not None else None
    return (text or _UNKNOWN_TEXT)[0]


class TaskStatusCard(QFrame):
    """Present a confirmed TaskStatus without creating a control path.

    The owner supplies a status from the backend. Missing or invalid values are
    displayed as unconfirmed; this widget does not infer state from model text.
    """

    def __init__(
        self, status: TaskStatus | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("placeholderCard")
        self.setMinimumHeight(148)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        layout.addWidget(plain_label("Task 상태", name="secondary"))
        self.status_label = plain_label(name="cardTitle")
        self.detail_label = plain_label(name="secondary")
        layout.addWidget(self.status_label)
        layout.addWidget(self.detail_label)
        self._status: TaskStatus | None = None
        self.set_status(status)

    @property
    def status(self) -> TaskStatus | None:
        return self._status

    def set_status(self, status: TaskStatus | None) -> None:
        valid_status = status if isinstance(status, TaskStatus) else None
        self._status = valid_status
        title, detail = (
            _UNKNOWN_TEXT if valid_status is None else _STATUS_TEXT[valid_status]
        )
        self.status_label.setText(title)
        self.detail_label.setText(detail)
        self.setAccessibleName(f"Task 상태: {title}. {detail}")
