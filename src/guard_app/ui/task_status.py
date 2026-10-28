"""Read-only Task status presentation for backend-confirmed state."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from guard_app.domain.enums import TaskStatus

from .widgets import plain_label

# Titles are Korean words only; the enum name is secondary and shown through
# status_code() in tooltips, details and logs.
_STATUS_TEXT: dict[TaskStatus, tuple[str, str]] = {
    TaskStatus.READY: (
        "시작 전",
        "Task가 시작 전임. 실행 가능 여부는 서버 등록과 모델 환경을 백엔드에서 확인해야 함.",
    ),
    TaskStatus.RUNNING: (
        "진행 중",
        "Task가 진행 중임. 취소나 긴급 차단을 요청했더라도 적용 여부는 백엔드 결과로 확인해야 "
        "함. 이미 전송된 쓰기는 수신됐을 수 있음.",
    ),
    TaskStatus.WAITING_APPROVAL: (
        "승인 대기",
        "승인 처리를 기다리고 있음. 승인·거절·취소 결과는 백엔드가 확정한 뒤 표시함.",
    ),
    TaskStatus.HALTED: (
        "중지됨",
        "이 Task는 이어서 실행할 수 없음. 새 Task가 필요함.",
    ),
    TaskStatus.UNKNOWN_OUTCOME: (
        "결과 불명",
        "전송이 실행됐는지 확인되지 않음. 수신측을 확인해야 함. 이 Task는 다시 실행할 수 없음.",
    ),
    TaskStatus.CLOSED: (
        "종료",
        "이 Task는 종료됨. 실행할 수 없음.",
    ),
}
_UNKNOWN_TEXT = (
    "상태 미확인",
    "Task 상태를 확인할 수 없음. 실행과 제어는 백엔드 조회 후에 판단함.",
)


# Ended Tasks: a reconnect or resend never continues them, so their own status
# text must win over connection hints such as model_ready.
FINAL_STATUSES = frozenset({TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED})


def status_title(status: TaskStatus | None) -> str:
    text = _STATUS_TEXT.get(status) if status is not None else None
    return (text or _UNKNOWN_TEXT)[0]


def status_code(status: TaskStatus | None) -> str:
    """The confirmed enum name for tooltips and details; empty when unknown."""
    return status.name if isinstance(status, TaskStatus) else ""


def status_detail(status: TaskStatus | None) -> str:
    text = _STATUS_TEXT.get(status) if status is not None else None
    return (text or _UNKNOWN_TEXT)[1]


class TaskStatusCard(QFrame):
    """Present a confirmed TaskStatus without creating a control path.

    The owner supplies a status from the backend. Missing or invalid values are
    displayed as unconfirmed; this widget does not infer state from model text.
    """

    def __init__(self, status: TaskStatus | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The first section of the results page: flat, closed by a hairline.
        self.setObjectName("taskStatusSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(4)
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
        title, detail = _UNKNOWN_TEXT if valid_status is None else _STATUS_TEXT[valid_status]
        # This card is the session's detail, so the enum name is shown with it.
        if valid_status is not None:
            title = f"{title} ({status_code(valid_status)})"
        self.status_label.setText(title)
        self.detail_label.setText(detail)
        self.setAccessibleName(f"Task 상태: {title}. {detail}")
