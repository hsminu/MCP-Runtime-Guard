"""8장 승인 사본의 순수 구성. 해시 연산은 B의 security 경계에서 주입한다.

요청 원문과 monotonic deadline은 프로세스 메모리 전용이다. Store로는
ApprovalRecord의 해시·운영자·UTC 시간만 전달하며 이 타입을 저장하지 않는다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from guard_app.domain.enums import Decision, Label, TaskStatus, ToolKind
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, PolicyDecision, ResolvedRequest, TaskState


class SnapshotError(ValueError):
    """원문을 포함하지 않는 승인 사본 설정·맥락 오류."""


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalSnapshot:
    """승인 대기 중 재해석할 깊은 불변 사본. repr에 원문·ID를 넣지 않는다."""

    request: ResolvedRequest = field(repr=False)
    state: TaskState = field(repr=False)
    decision: PolicyDecision = field(repr=False)
    effective_label: Label
    approver_id: str
    expires_at_utc: datetime
    monotonic_deadline: float
    arguments_bytes: bytes = field(repr=False)
    payload: FrozenJsonObject = field(repr=False)
    hash: str

    @property
    def task_id(self) -> str:
        return self.request.task_id

    def expired(self, *, now_utc: datetime, now_monotonic: float) -> bool:
        """두 시계 중 하나만 만료되어도 사용 불가다. 만료 경계는 >=다."""
        _check_utc(now_utc)
        _check_monotonic(now_monotonic)
        return now_utc >= self.expires_at_utc or now_monotonic >= self.monotonic_deadline

    def __repr__(self) -> str:
        return "ApprovalSnapshot(<redacted>)"


def make_snapshot(
    req: ResolvedRequest,
    state: TaskState,
    decision: PolicyDecision,
    *,
    effective_label: Label,
    approver_id: str,
    expires_at_utc: datetime,
    monotonic_deadline: float,
    hash_json: Callable[[object], str],
    canonicalize: Callable[[object], bytes],
) -> ApprovalSnapshot:
    """ASK의 고정 맥락을 구성한다. 함수는 시계·DB·파일·네트워크를 읽지 않는다.

    status와 row_revision은 예약/승인 메타데이터 전이에 따라 바뀌므로 해시에서
    제외한다. last_read_label은 effective_label로 고정한다(6.1·8.1절).
    """
    req = ResolvedRequest.model_validate(req)
    state = TaskState.model_validate(state)
    decision = PolicyDecision.model_validate(decision)
    _check_context(req, state, decision, effective_label)
    _check_utc(expires_at_utc)
    _check_monotonic(monotonic_deadline)
    if (
        type(approver_id) is not str
        or not approver_id
        or any(0xD800 <= ord(char) <= 0xDFFF for char in approver_id)
    ):
        raise SnapshotError("승인자 식별자가 올바르지 않다")
    # Arguments 재검증은 model_construct 등으로 우회한 입력에도 float를 거부한다.
    arguments = Arguments(req.arguments)
    arguments_bytes = canonicalize(arguments)
    if type(arguments_bytes) is not bytes:
        raise SnapshotError("승인 직렬화 결과가 bytes가 아니다")
    arguments_hash = _checked_hash(hash_json(arguments))
    payload = FrozenJsonObject(
        {
            "task_id": req.task_id,
            "epoch": req.epoch,
            "call_seq": req.call_seq,
            "request_id": req.request_id,
            "server_id": req.tool.server_id,
            "tool_name": req.tool.tool_name,
            "tool_fingerprint": req.tool.fingerprint,
            "server_boundary_id": req.server_boundary.boundary_id,
            "destination_id": None if req.destination is None else req.destination.boundary_id,
            "arguments_digest": arguments_hash,
            "effective_label": int(effective_label),
            "security_version": state.security_version,
            "policy_version": state.policy_version,
            "approver_id": approver_id,
            "expires_at_utc": expires_at_utc.isoformat(timespec="microseconds"),
            # 같은 ID를 유지한 설정 변경도 사본 의미를 바꾼다.
            "profile_id": state.profile_id,
            "high_watermark": int(state.high_watermark),
            "unclassified": state.unclassified,
            "tool_kind": req.tool.kind.value,
            "resource_arg": req.tool.resource_arg,
            "destination_arg": req.tool.destination_arg,
            "resource_id": req.resource_id,
            "server_boundary": _boundary_payload(req.server_boundary),
            "destination": None if req.destination is None else _boundary_payload(req.destination),
        }
    )
    return ApprovalSnapshot(
        request=req,
        state=state,
        decision=decision,
        effective_label=effective_label,
        approver_id=approver_id,
        expires_at_utc=expires_at_utc,
        monotonic_deadline=float(monotonic_deadline),
        arguments_bytes=arguments_bytes,
        payload=payload,
        hash=_checked_hash(hash_json(payload)),
    )


def _check_context(
    req: ResolvedRequest, state: TaskState, decision: PolicyDecision, effective_label: Label
) -> None:
    if type(effective_label) is not Label:
        raise SnapshotError("승인 유효 등급이 올바르지 않다")
    if decision.decision is not Decision.ASK:
        raise SnapshotError("ASK 판정만 승인 사본을 만들 수 있다")
    if state.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL) or state.unclassified:
        raise SnapshotError("승인 가능한 Task 상태가 아니다")
    if req.task_id != state.task_id or req.epoch != state.epoch:
        raise SnapshotError("승인 요청과 Task 맥락이 다르다")
    if (
        decision.policy_version != state.policy_version
        or decision.security_version != state.security_version
    ):
        raise SnapshotError("승인 판정의 버전이 Task와 다르다")
    if req.tool.kind is ToolKind.OTHER or (
        req.tool.kind is ToolKind.SEND and req.destination is None
    ):
        raise SnapshotError("승인 가능한 도구·목적지 설정이 아니다")


def _boundary_payload(boundary: Boundary) -> dict[str, object]:
    return {
        "boundary_id": boundary.boundary_id,
        "tier": boundary.tier.value,
        "max_label": int(boundary.max_label),
    }


def _checked_hash(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SnapshotError("승인 해시가 SHA-256 형식이 아니다")
    return value


def _check_utc(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise SnapshotError("승인 시간은 UTC-aware여야 한다")


def _check_monotonic(value: float) -> None:
    if type(value) not in (int, float):
        raise SnapshotError("승인 monotonic 시각이 올바르지 않다")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise SnapshotError("승인 monotonic 시각이 올바르지 않다")
