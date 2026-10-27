"""기술설계서 5.2·5.3절: 전제조건 우선, 가장 엄격한 판정 합성."""

from types import MappingProxyType

from guard_app.core.matrix import judge_boundary
from guard_app.domain.enums import Decision, Label, RegStatus, TaskStatus, ToolKind
from guard_app.domain.models import PolicyDecision, PolicyInput, TaskState
from guard_app.domain.reasons import Reason

_EXPECTED_STATUS = MappingProxyType(
    {"submit": TaskStatus.RUNNING, "approval_recheck": TaskStatus.WAITING_APPROVAL}
)


class PolicyConfigError(ValueError):
    """설정 로더가 처리할 taint_mode 오류. 설정 원문은 메시지에 넣지 않는다."""


def effective_label(state: TaskState, mode: str = "accumulate") -> Label:
    """모드 차이는 유효 등급에만 적용하며, 미분류·상태 검사를 대신하지 않는다."""
    if mode == "accumulate":
        return state.high_watermark
    if mode == "last_read":
        return state.last_read_label
    if mode == "none":
        return Label.PUBLIC
    raise PolicyConfigError("지원하지 않는 taint_mode")


def evaluate(inp: PolicyInput) -> PolicyDecision:
    """I2·I6: 시간·I/O 없이 판정하고 접근 규칙의 거부권으로만 결과를 강화한다."""
    preconditions = _preconditions(inp)
    if preconditions:
        return _deny(inp, preconditions)

    verdicts = [judge_boundary(inp.effective_label, inp.request.server_boundary)]
    if inp.request.tool.kind is ToolKind.SEND:
        destination = inp.request.destination
        if destination is None:
            return _deny(inp, (Reason.UNKNOWN_DESTINATION,))
        verdicts.append(judge_boundary(inp.effective_label, destination))

    access = Decision.DENY if inp.access.error or not inp.access.allowed else Decision.ALLOW
    final = max([verdict.decision for verdict in verdicts] + [access])
    reasons = tuple(verdict.reason for verdict in verdicts if verdict.decision is final)
    if access is Decision.DENY:
        reasons += (Reason.AGT_ERROR if inp.access.error else Reason.AGT_DENY,)
    return PolicyDecision(
        decision=final,
        reasons=reasons,
        verdicts=tuple(verdicts),
        policy_version=inp.policy_version,
        security_version=inp.state.security_version,
    )


def _preconditions(inp: PolicyInput) -> tuple[Reason, ...]:
    reasons: list[Reason] = []
    if inp.registration_status is not RegStatus.ACTIVE:
        reasons.append(Reason.REG_NOT_ACTIVE)
    if inp.request.tool.fingerprint != inp.registered_fingerprint:
        reasons.append(Reason.TOOL_FINGERPRINT_CHANGED)
    if inp.state.unclassified:
        reasons.append(Reason.TASK_UNCLASSIFIED)
    if inp.state.status is not _EXPECTED_STATUS[inp.phase]:
        reasons.append(Reason.TASK_NOT_RUNNING)
    if inp.state.policy_version != inp.policy_version:
        reasons.append(Reason.POLICY_CHANGED)
    if inp.request.tool.kind is ToolKind.SEND and inp.request.destination is None:
        reasons.append(Reason.UNKNOWN_DESTINATION)
    if inp.request.tool.kind is ToolKind.OTHER:
        reasons.append(Reason.TOOL_UNSUPPORTED)
    return tuple(reasons)


def _deny(inp: PolicyInput, reasons: tuple[Reason, ...]) -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.DENY,
        reasons=reasons,
        verdicts=(),
        policy_version=inp.policy_version,
        security_version=inp.state.security_version,
    )
