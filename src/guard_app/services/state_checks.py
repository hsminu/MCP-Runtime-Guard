"""Store 반환 Task의 공통 보존 검사. 새로운 DTO/저장 상태를 정의하지 않는다."""

from pydantic import ValidationError

from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason


def checked_task(value: TaskState, *, task_id: str, epoch: int) -> TaskState:
    try:
        state = TaskState.model_validate(value)
    except ValidationError:
        raise StoreError(Reason.STATE_COMMIT_FAILED) from None
    if state.task_id != task_id or state.epoch != epoch:
        raise StoreError(Reason.STATE_COMMIT_FAILED)
    return state


def preserve_task_context(
    state: TaskState, expected: TaskState | None, *, allow_last_read_change: bool = True
) -> None:
    """등급/미분류/버전/revision의 회귀와 기존 승격 이력 유실을 거부한다."""
    if expected is None:
        return
    changed = (
        state.high_watermark != expected.high_watermark
        or state.unclassified != expected.unclassified
    )
    if (
        state.task_id != expected.task_id
        or state.epoch != expected.epoch
        or state.profile_id != expected.profile_id
        or state.policy_version != expected.policy_version
        or state.high_watermark < expected.high_watermark
        or (expected.unclassified and not state.unclassified)
        or state.security_version < expected.security_version
        or state.row_revision < expected.row_revision
        or state.escalations[: len(expected.escalations)] != expected.escalations
        or (not allow_last_read_change and state.last_read_label != expected.last_read_label)
        or (changed and state.security_version <= expected.security_version)
    ):
        raise StoreError(Reason.STATE_COMMIT_FAILED)


def checked_status_change(value: TaskState, before: TaskState, target: TaskStatus) -> TaskState:
    """상태 전이 반환은 보안 맥락을 그대로 보존해야 한다.

    정상 전이 보안 버전 유지/+1의 기존 호환 범위를 유지한다. 최종 B6-02 합의는 별도다.
    실제 상태 변경의 row_revision은 CAS 대상보다 정확히 1 증가해야 한다.
    """
    state = checked_task(value, task_id=before.task_id, epoch=before.epoch)
    if (
        state.status is not target
        or state.row_revision != before.row_revision + int(before.status is not target)
        or state.security_version not in (before.security_version, before.security_version + 1)
        or state.profile_id != before.profile_id
        or state.policy_version != before.policy_version
        or state.high_watermark != before.high_watermark
        or state.last_read_label != before.last_read_label
        or state.unclassified != before.unclassified
        or state.escalations != before.escalations
    ):
        raise StoreError(Reason.STATE_COMMIT_FAILED)
    return state
