"""A36 read-only Task status display checks (I7/I8, T14/T20)."""

from __future__ import annotations

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel

from guard_app.domain.enums import TaskStatus
from guard_app.ui.task_status import TaskStatusCard


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.mark.parametrize(
    ("status", "title", "guidance"),
    [
        (TaskStatus.READY, "READY", "서버 등록과 모델 환경"),
        (TaskStatus.RUNNING, "RUNNING", "백엔드 결과"),
        (TaskStatus.WAITING_APPROVAL, "WAITING_APPROVAL", "백엔드가 확정"),
        (TaskStatus.HALTED, "HALTED", "새 Task"),
        (TaskStatus.UNKNOWN_OUTCOME, "UNKNOWN_OUTCOME", "수신측"),
        (TaskStatus.CLOSED, "CLOSED", "종료"),
    ],
)
def test_each_backend_status_has_distinct_guidance(
    app: QApplication, status: TaskStatus, title: str, guidance: str
) -> None:
    card = TaskStatusCard(status)

    assert card.status is status
    assert title in card.status_label.text()
    assert guidance in card.detail_label.text()
    assert card.findChildren(QAbstractButton) == []
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in card.findChildren(QLabel))


def test_unknown_outcome_after_running_never_looks_complete_or_runnable(
    app: QApplication,
) -> None:
    card = TaskStatusCard(TaskStatus.RUNNING)
    card.set_status(TaskStatus.UNKNOWN_OUTCOME)

    rendered = card.accessibleName()
    assert card.status is TaskStatus.UNKNOWN_OUTCOME
    assert "결과 불명" in rendered
    assert "실행됐는지 확인되지" in rendered
    assert "수신측" in rendered
    assert "다시 실행할 수 없어" in rendered
    assert "성공" not in rendered
    assert "재시도" not in rendered
    assert card.findChildren(QAbstractButton) == []


def test_missing_or_untrusted_status_clears_old_backend_status(
    app: QApplication,
) -> None:
    card = TaskStatusCard(TaskStatus.READY)

    card.set_status(None)
    assert card.status is None
    assert "상태 미확인" in card.status_label.text()
    assert "실행과 제어는 백엔드 조회 후" in card.detail_label.text()

    card.set_status(cast(TaskStatus, "running"))
    assert card.status is None
    assert "상태 미확인" in card.accessibleName()
    assert "READY" not in card.accessibleName()
    assert "RUNNING" not in card.accessibleName()
