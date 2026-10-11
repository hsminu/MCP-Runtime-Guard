"""READ 분류 결과의 순수 병합. 저장·공개·SEND 처리는 호출자의 책임이다."""

from __future__ import annotations

from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.models import Escalation, TaskState


def merge(state: TaskState, observed: Label | None, source_id: str, seq: int) -> TaskState:
    """READ 관측으로 high-watermark·미분류 상태를 누적한다(I4/I6).

    ``None``은 반환된 자료의 분류 실패만 뜻하며 Task를 정지한다. 바이트가
    없는 알려진 전송 실패와 SEND 응답은 이 함수를 호출하지 않는다.
    ``row_revision``은 변경하지 않으며, 호출자는 결과를 원자적으로 저장한
    뒤에만 공개해야 한다. 같은 보안 상태의 반복 관측은 버전을 올리지 않는다.
    """
    _validate_observation(observed, source_id, seq)

    if observed is None:
        if state.unclassified and state.status is TaskStatus.HALTED:
            return state
        return state.model_copy(
            update={
                "unclassified": True,
                "status": TaskStatus.HALTED,
                "security_version": state.security_version + 1,
            }
        )

    update: dict[str, object] = {}
    if observed is not state.last_read_label:
        update["last_read_label"] = observed
    if observed > state.high_watermark:
        update.update(
            high_watermark=observed,
            security_version=state.security_version + 1,
            escalations=state.escalations
            + (
                Escalation(
                    at_seq=seq,
                    source_id=source_id,
                    from_label=state.high_watermark,
                    to_label=observed,
                ),
            ),
        )
    return state.model_copy(update=update) if update else state


def _validate_observation(observed: Label | None, source_id: str, seq: int) -> None:
    """동적 호출도 enum·식별자·순서의 도메인 기준을 우회하지 못하게 한다."""
    if observed is not None and not isinstance(observed, Label):
        raise TypeError("READ 관측 등급은 Label 또는 None이어야 한다")
    if not isinstance(source_id, str):
        raise TypeError("관측 출처는 문자열이어야 한다")
    if not source_id or any(0xD800 <= ord(char) <= 0xDFFF for char in source_id):
        raise ValueError("관측 출처는 비어 있지 않은 UTF-8 문자열이어야 한다")
    if type(seq) is not int:
        raise TypeError("관측 순서는 정수여야 한다")
    if seq < 0:
        raise ValueError("관측 순서는 음수일 수 없다")
