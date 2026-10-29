"""Pure display decisions from backend-confirmed session state."""

from __future__ import annotations

from dataclasses import dataclass

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import TaskProjection

from .presentation import CODEX_AUTH_FAILED_NOTICE, codex_failure_notice, session_blocker_notice
from .task_status import FINAL_STATUSES, status_title

_STATUS_TONES = {
    TaskStatus.READY: "idle",
    TaskStatus.RUNNING: "running",
    TaskStatus.HALTED: "stopped",
    TaskStatus.UNKNOWN_OUTCOME: "stopped",
}


@dataclass(frozen=True, slots=True)
class SessionDisplay:
    active: bool
    writable: bool
    failure_notice: str
    text: str
    note: str
    waiting: bool
    pill: str
    tone: str


def session_display(
    snapshot: TaskProjection | None,
    *,
    ready: bool,
    busy_scopes: frozenset[tuple[str, int]],
    pending_command: str | None,
    command_notice: str,
    session_blocker: str,
    startup_notice: str,
    command_code: str = "",
) -> SessionDisplay:
    failure_notice = "" if snapshot is None else codex_failure_notice(snapshot.connection_code)
    active = snapshot is not None
    busy = bool(snapshot and (snapshot.task_id, snapshot.epoch) in busy_scopes)
    writable = bool(
        active
        and ready
        and not busy
        and snapshot is not None
        and snapshot.status is TaskStatus.RUNNING
        and snapshot.can_accept_message
        and not failure_notice
        and pending_command is None
    )
    connection_error = bool(
        failure_notice
        or (snapshot is not None and snapshot.connection_code == "CODEX_AUTH_FAILED")
        or codex_failure_notice(command_code, creating=snapshot is None)
        or command_code == "CODEX_AUTH_FAILED"
    )
    if snapshot is None:
        text = "아직 연결된 Task가 없음"
        note = (
            command_notice
            or session_blocker_notice(session_blocker)
            or startup_notice
            or (
                "Codex 연결과 시작 검사가 확인되면 세션을 시작할 수 있음. "
                "로그인은 설정에서 안내를 확인하세요."
            )
        )
    else:
        text = f"Task {snapshot.task_id}"
        if failure_notice:
            note = failure_notice
        elif snapshot.status is TaskStatus.WAITING_APPROVAL:
            note = "승인 대기 중 · 결과 · 실행 활동에서 요청 사본을 확인하세요."
        elif snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
            note = (
                CODEX_AUTH_FAILED_NOTICE
                if snapshot.connection_code == "CODEX_AUTH_FAILED"
                else "Task가 종료되거나 잠김. 대화·초안 원문을 지웠고 다시 실행할 수 없음."
            )
        elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
            note = (
                "모델 응답을 완료하지 못해 이 Runner가 멈춤. 자동 재전송하지 않음. "
                "Store의 Task 상태는 위에 표시된 값임. 새 세션에서 다시 시작하세요."
            )
        elif not snapshot.model_ready:
            note = "모델 연결을 확인하지 못해 실행할 수 없음."
        elif busy or pending_command:
            note = "실행 요청 처리 중 · 백엔드 결과를 기다리는 중임."
        elif not ready:
            note = "백엔드 준비 상태를 확인하는 중임."
        elif command_notice:
            note = command_notice
        elif snapshot.can_advance:
            note = "진행할 모델 단계가 있음. 결과 · 실행 활동에서 실행을 누르세요."
        elif writable:
            note = "다음 메시지를 보낼 수 있음. 도구 호출은 Guard를 통해 검사됨."
        else:
            note = "사용자 입력을 받을 수 있는지 백엔드 확인을 기다리는 중임."
        if not snapshot.tools_available:
            note = "도구 없는 대화 · " + note
    waiting = snapshot is not None and snapshot.status is TaskStatus.WAITING_APPROVAL
    if connection_error:
        pill, tone = "연결 오류", "deny"
    elif snapshot is None:
        pill, tone = ("시작 확인 중", "live") if pending_command else ("시작 전", "neutral")
    elif waiting:
        pill, tone = "승인 필요", "attn"
    elif snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
        pill, tone = (
            status_title(snapshot.status),
            "neutral" if snapshot.status is TaskStatus.CLOSED else "deny",
        )
    elif snapshot.runner_state in ("model_error", "guard_error", "stopped"):
        pill, tone = "멈춤", "deny"
    elif not snapshot.model_ready:
        pill, tone = "연결 필요", "attn"
    elif busy or pending_command:
        pill, tone = "응답 기다리는 중", "live"
    elif not ready:
        pill, tone = "준비 중", "neutral"
    elif snapshot.can_advance:
        pill, tone = "진행 대기", "attn"
    elif writable:
        pill, tone = "입력 가능", "neutral"
    else:
        pill, tone = "준비 중", "neutral"
    return SessionDisplay(active, writable, failure_notice, text, note, waiting, pill, tone)


def session_row_status(snapshot: TaskProjection | None) -> tuple[str, str]:
    if snapshot is None:
        status, tone = "초안 · 미연결", "draft"
    elif snapshot.connection_code == "CODEX_CLEANUP_UNCONFIRMED":
        status, tone = "정리 완료 미확인", "attention"
    elif snapshot.status is TaskStatus.WAITING_APPROVAL:
        status, tone = "승인 필요 · 대기 중", "approval"
    elif snapshot.status not in FINAL_STATUSES and not snapshot.model_ready:
        status, tone = "연결 필요", "attention"
    else:
        status = status_title(snapshot.status)
        tone = _STATUS_TONES.get(snapshot.status, "closed")
    return status, tone
