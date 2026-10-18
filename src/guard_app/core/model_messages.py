"""B5.11·I5/I9: 모델 반환용 고정 문구. 결과 본문과 제어 명령은 다루지 않는다."""

from enum import StrEnum
from types import MappingProxyType

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.reasons import Reason

_DENIED_TEXT = "이 요청은 정책에 의해 차단되었습니다"
_STOPPED_TEXT = "작업이 정지되었습니다"


class ModelMessageError(ValueError):
    """입력이나 원래 예외를 포함하지 않는 모델 문구 변환 오류."""


class ModelMessageKind(StrEnum):
    """신뢰된 결과·최종 알림의 문구 분류. domain 계약을 대체하지 않는다."""

    RELEASED = "released"
    DENIED = "denied"
    PENDING = "pending"
    WITHHELD = "withheld"
    OUTCOME_UNKNOWN = "outcome_unknown"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_INVALIDATED = "approval_invalidated"
    NAMESPACE_MISSING = "namespace_missing"
    STOPPED = "stopped"


# 문서의 정책 피드백과 호출 형식 수정에 필요한 코드만 공개한다.
MODEL_VISIBLE_REASONS = frozenset(
    {
        Reason.INTERNAL_TO_EXTERNAL,
        Reason.CONFIDENTIAL_TO_EXTERNAL,
        Reason.TOOL_UNSUPPORTED,
        Reason.INVALID_ARGUMENTS,
    }
)

# 새 Reason을 자동으로 공개하거나 정상 처리하지 않는다. 누락은 검사·변환 실패다.
MODEL_REASON_MESSAGES = MappingProxyType(
    {
        Reason.OK: _DENIED_TEXT,
        Reason.INTERNAL_TO_EXTERNAL: "이 요청은 정책에 의해 차단되었습니다: INTERNAL_TO_EXTERNAL",
        Reason.CONFIDENTIAL_TO_EXTERNAL: (
            "이 요청은 정책에 의해 차단되었습니다: CONFIDENTIAL_TO_EXTERNAL"
        ),
        Reason.LABEL_EXCEEDS_BOUNDARY: _DENIED_TEXT,
        Reason.UNKNOWN_DESTINATION: _DENIED_TEXT,
        Reason.UNKNOWN_SERVER: _DENIED_TEXT,
        Reason.TOOL_UNSUPPORTED: "이 요청은 정책에 의해 차단되었습니다: TOOL_UNSUPPORTED",
        Reason.INVALID_ARGUMENTS: "이 요청은 정책에 의해 차단되었습니다: INVALID_ARGUMENTS",
        Reason.REG_NOT_ACTIVE: _DENIED_TEXT,
        Reason.TOOL_FINGERPRINT_CHANGED: _DENIED_TEXT,
        Reason.TASK_UNCLASSIFIED: _DENIED_TEXT,
        Reason.TASK_NOT_RUNNING: _DENIED_TEXT,
        Reason.POLICY_CHANGED: _DENIED_TEXT,
        Reason.STATE_MISSING: _DENIED_TEXT,
        Reason.AGT_DENY: _DENIED_TEXT,
        Reason.AGT_ERROR: _DENIED_TEXT,
        Reason.INTERNAL_ERROR: _DENIED_TEXT,
        Reason.UNSUPPORTED_CONTENT: _DENIED_TEXT,
        Reason.STATE_COMMIT_FAILED: _DENIED_TEXT,
        Reason.RESULT_UNCLASSIFIED: _DENIED_TEXT,
        Reason.POST_DISPATCH_ERROR: _DENIED_TEXT,
        Reason.APPROVAL_CONTEXT_LOST: _DENIED_TEXT,
        Reason.APPROVAL_STALE: _DENIED_TEXT,
        Reason.APPROVAL_RACE: _DENIED_TEXT,
    }
)

_KIND_MESSAGES = MappingProxyType(
    {
        ModelMessageKind.RELEASED: (True, ""),
        ModelMessageKind.WITHHELD: (False, _STOPPED_TEXT),
        ModelMessageKind.OUTCOME_UNKNOWN: (False, _STOPPED_TEXT),
        ModelMessageKind.APPROVAL_REJECTED: (False, "승인 요청이 거절되었습니다"),
        ModelMessageKind.APPROVAL_EXPIRED: (False, "승인 요청이 만료되었습니다"),
        ModelMessageKind.APPROVAL_CANCELLED: (False, "요청이 취소되었습니다"),
        ModelMessageKind.APPROVAL_INVALIDATED: (False, "승인 요청이 무효화되었습니다"),
        ModelMessageKind.NAMESPACE_MISSING: (False, _DENIED_TEXT),
        ModelMessageKind.STOPPED: (False, _STOPPED_TEXT),
    }
)

# 공개 사유가 여럿이면 입력 순서·중복에 관계없이 하나의 고정 문구를 고른다.
_PUBLIC_REASON_PRIORITY = (
    Reason.CONFIDENTIAL_TO_EXTERNAL,
    Reason.INTERNAL_TO_EXTERNAL,
    Reason.INVALID_ARGUMENTS,
    Reason.TOOL_UNSUPPORTED,
)


def fixed_model_message(kind: ModelMessageKind, reason: Reason | None = None) -> tuple[bool, str]:
    """신뢰된 분류·사유를 (success, 고정 문구)로 바꾸는 순수 함수.

    Released는 (True, "") 표시만 돌려주며 본문을 읽거나 공개하지 않는다.
    Pending은 최종 응답이 아니므로 ModelMessageError로 거부한다.
    승인 종결·namespace 누락·STOPPED는 후속 연결용 분류다. STOP-01의
    새 사유·DTO를 정의하지 않으며 정지 사유 코드는 모델에 보여주지 않는다.
    """
    if type(kind) is not ModelMessageKind:
        raise ModelMessageError("모델 반환 결과 분류가 유효하지 않다")
    if reason is not None:
        _reason_message(reason)
    if kind is ModelMessageKind.RELEASED and reason is not None:
        raise ModelMessageError("공개 결과의 고정 문구에는 사유를 받지 않는다")
    if kind is ModelMessageKind.PENDING:
        raise ModelMessageError("승인 대기에는 최종 모델 응답을 만들 수 없다")
    if kind is ModelMessageKind.DENIED:
        return False, _DENIED_TEXT if reason is None else _reason_message(reason)
    return _KIND_MESSAGES[kind]


def outcome_model_message(outcome: object) -> tuple[bool, str]:
    """기존 Outcome을 본문·식별자·버전 조회 없이 고정 문구로 바꾼다.

    Denied의 복수 사유에 비공개 사유가 섞이면 전체를 일반 거부 문구로
    가린다. 거절·만료·취소를 기존 APPROVAL_STALE에서 추측하지 않는다.
    종결 알림에서 확정된 분류를 fixed_model_message에 따로 전달해야 한다.
    """
    if type(outcome) is Released:
        return fixed_model_message(ModelMessageKind.RELEASED)
    if type(outcome) is Withheld:
        return fixed_model_message(ModelMessageKind.WITHHELD)
    if type(outcome) is OutcomeUnknown:
        return fixed_model_message(ModelMessageKind.OUTCOME_UNKNOWN)
    if type(outcome) is Pending:
        return fixed_model_message(ModelMessageKind.PENDING)
    if type(outcome) is not Denied:
        raise ModelMessageError("모델 반환 Outcome 형식이 유효하지 않다")
    reasons = outcome.reasons
    if type(reasons) is not tuple or not reasons:
        raise ModelMessageError("모델 반환 사유 목록이 유효하지 않다")
    for reason in reasons:
        _reason_message(reason)
    if any(reason not in MODEL_VISIBLE_REASONS for reason in reasons):
        return False, _DENIED_TEXT
    for reason in _PUBLIC_REASON_PRIORITY:
        if reason in reasons:
            return fixed_model_message(ModelMessageKind.DENIED, reason)
    raise ModelMessageError("모델 반환 사유 우선순위가 유효하지 않다")


def _reason_message(reason: Reason) -> str:
    if type(reason) is not Reason:
        raise ModelMessageError("모델 반환 사유 형식이 유효하지 않다")
    if reason not in MODEL_REASON_MESSAGES:
        raise ModelMessageError("모델 반환 사유 문구가 정의되지 않았다")
    return MODEL_REASON_MESSAGES[reason]
