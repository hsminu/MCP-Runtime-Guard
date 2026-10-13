"""B2 설계 5.4절의 정책 성질. I2/I6의 순수 판정 근거를 검증한다."""

from hypothesis import given, settings
from hypothesis import strategies as st

from guard_app.core.matrix import judge_boundary
from guard_app.core.policy import evaluate
from guard_app.domain.enums import Decision, Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import AccessResult, Boundary, PolicyInput
from guard_app.domain.reasons import Reason


@st.composite
def _boundaries(draw: st.DrawFn, boundary_id: str = "server") -> Boundary:
    tier = draw(st.sampled_from(list(TrustTier)))
    caps = list(Label) if tier is TrustTier.INTERNAL else [Label.PUBLIC, Label.INTERNAL]
    return Boundary(boundary_id=boundary_id, tier=tier, max_label=draw(st.sampled_from(caps)))


@st.composite
def _inputs(draw: st.DrawFn) -> PolicyInput:
    kind = draw(st.sampled_from([ToolKind.READ, ToolKind.SEND]))
    phase = draw(st.sampled_from(["submit", "approval_recheck"]))
    status = TaskStatus.RUNNING if phase == "submit" else TaskStatus.WAITING_APPROVAL
    return PolicyInput.model_validate(
        {
            "request": {
                "request_id": "request-1",
                "task_id": "task-1",
                "epoch": 1,
                "call_seq": draw(st.integers(min_value=0, max_value=1000)),
                "tool": {
                    "server_id": "registered-server",
                    "tool_name": "registered-tool",
                    "kind": kind,
                    "input_schema": {},
                    "destination_arg": "destination_id" if kind is ToolKind.SEND else None,
                    "resource_arg": None,
                    "fingerprint": "a" * 64,
                },
                "arguments": {},
                "resource_id": None,
                "server_boundary": draw(_boundaries("server")),
                "destination": draw(_boundaries("destination")) if kind is ToolKind.SEND else None,
            },
            "state": {
                "task_id": "task-1",
                "epoch": 1,
                "profile_id": "default",
                "policy_version": "policy-1",
                "status": status,
                "security_version": draw(st.integers(min_value=0, max_value=1000)),
            },
            "effective_label": draw(st.sampled_from(list(Label))),
            "registration_status": RegStatus.ACTIVE,
            "registered_fingerprint": "a" * 64,
            "access": AccessResult(
                allowed=draw(st.booleans()), reason="trusted translation", error=draw(st.booleans())
            ),
            "policy_version": "policy-1",
            "phase": phase,
        }
    )


@given(inp=_inputs(), labels=st.tuples(st.sampled_from(list(Label)), st.sampled_from(list(Label))))
@settings(max_examples=100)
def test_more_sensitive_label_never_softens_decision(
    inp: PolicyInput, labels: tuple[Label, Label]
) -> None:
    lower, higher = sorted(labels)
    low_result = evaluate(inp.model_copy(update={"effective_label": lower}))
    high_result = evaluate(inp.model_copy(update={"effective_label": higher}))
    assert low_result.decision <= high_result.decision


@given(
    inp=_inputs(),
    choose_destination=st.booleans(),
    cap_number=st.integers(min_value=0, max_value=2),
)
@settings(max_examples=100)
def test_lowering_either_boundary_cap_never_softens_decision(
    inp: PolicyInput, choose_destination: bool, cap_number: int
) -> None:
    target = (
        "destination" if choose_destination and inp.request.destination is not None else "server"
    )
    boundary = inp.request.destination if target == "destination" else inp.request.server_boundary
    assert boundary is not None
    tighter_cap = Label(min(cap_number, int(boundary.max_label)))
    tighter = boundary.model_copy(update={"max_label": tighter_cap})
    request_field = "destination" if target == "destination" else "server_boundary"
    tighter_request = inp.request.model_copy(update={request_field: tighter})
    assert (
        evaluate(inp.model_copy(update={"request": tighter_request})).decision
        >= evaluate(inp).decision
    )


@given(inp=_inputs())
@settings(max_examples=100)
def test_any_deny_component_absorbs_others(inp: PolicyInput) -> None:
    """I2: 독립 경계 결과나 AGT 중 하나라도 거부하면 최종 판정은 DENY다."""
    server_denies = (
        judge_boundary(inp.effective_label, inp.request.server_boundary).decision is Decision.DENY
    )
    destination_denies = (
        inp.request.tool.kind is ToolKind.SEND
        and inp.request.destination is not None
        and judge_boundary(inp.effective_label, inp.request.destination).decision is Decision.DENY
    )
    access_denies = inp.access.error or not inp.access.allowed
    if server_denies or destination_denies or access_denies:
        assert evaluate(inp).decision is Decision.DENY


@given(
    inp=_inputs(),
    violation=st.sampled_from(["registration", "fingerprint", "unclassified", "status", "policy"]),
)
@settings(max_examples=100)
def test_precondition_violation_always_wins(inp: PolicyInput, violation: str) -> None:
    """I6: 임의의 정상 정책 조합에 전제 실패를 추가하면 경계 평가 없이 DENY다."""
    changes: dict[str, object]
    if violation == "registration":
        changes = {"registration_status": RegStatus.PENDING}
        expected = Reason.REG_NOT_ACTIVE
    elif violation == "fingerprint":
        changes = {"registered_fingerprint": "b" * 64}
        expected = Reason.TOOL_FINGERPRINT_CHANGED
    elif violation == "unclassified":
        changes = {"state": inp.state.model_copy(update={"unclassified": True})}
        expected = Reason.TASK_UNCLASSIFIED
    elif violation == "status":
        changes = {"state": inp.state.model_copy(update={"status": TaskStatus.HALTED})}
        expected = Reason.TASK_NOT_RUNNING
    else:
        changes = {"policy_version": "policy-2"}
        expected = Reason.POLICY_CHANGED
    result = evaluate(inp.model_copy(update=changes))
    assert result.decision is Decision.DENY
    assert result.reasons == (expected,)
    assert result.verdicts == ()


@given(inp=_inputs())
@settings(max_examples=100)
def test_same_input_is_deterministic_and_preserves_versions(inp: PolicyInput) -> None:
    before = inp.model_dump_json()
    first = evaluate(inp)
    assert evaluate(inp) == first
    assert first.policy_version == inp.policy_version
    assert first.security_version == inp.state.security_version
    assert inp.model_dump_json() == before
