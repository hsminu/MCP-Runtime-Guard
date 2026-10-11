"""B2 정책 의미의 단위 검증. T04/T26/T27/T30/T31/T34의 판정 부분만 다룬다.

실제 dispatch·wire·수신 부작용 검증은 C의 독립 통합 테스트 대상이다.
"""

import pytest

from guard_app.core.matrix import judge_boundary
from guard_app.core.policy import PolicyConfigError, effective_label, evaluate
from guard_app.core.taint import merge
from guard_app.domain.enums import Decision, Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import AccessResult, Boundary, PolicyInput
from guard_app.domain.reasons import Reason


def _boundary(
    boundary_id: str = "server", tier: TrustTier = TrustTier.INTERNAL, cap: Label | None = None
) -> Boundary:
    if cap is None:
        cap = Label.CONFIDENTIAL if tier is TrustTier.INTERNAL else Label.INTERNAL
    return Boundary(boundary_id=boundary_id, tier=tier, max_label=cap)


def _input(
    *,
    label: Label = Label.PUBLIC,
    kind: ToolKind = ToolKind.READ,
    server: Boundary | None = None,
    destination: Boundary | None = None,
    access: AccessResult | None = None,
    state_updates: dict[str, object] | None = None,
    **updates: object,
) -> PolicyInput:
    state: dict[str, object] = {
        "task_id": "task-1",
        "epoch": 1,
        "profile_id": "default",
        "policy_version": "policy-1",
        "status": TaskStatus.RUNNING,
        "security_version": 7,
    }
    state.update(state_updates or {})
    values: dict[str, object] = {
        "request": {
            "request_id": "request-1",
            "task_id": "task-1",
            "epoch": 1,
            "call_seq": 4,
            "tool": {
                "server_id": "server-1",
                "tool_name": "registered-tool",
                "kind": kind,
                "input_schema": {"type": "object", "additionalProperties": False},
                "destination_arg": "destination_id" if kind is ToolKind.SEND else None,
                "resource_arg": None,
                "fingerprint": "a" * 64,
            },
            "arguments": {},
            "resource_id": None,
            "server_boundary": server or _boundary(),
            "destination": destination,
        },
        "state": state,
        "effective_label": label,
        "registration_status": RegStatus.ACTIVE,
        "registered_fingerprint": "a" * 64,
        "access": access or AccessResult(allowed=True, reason="approved", error=False),
        "policy_version": "policy-1",
        "phase": "submit",
    }
    values.update(updates)
    return PolicyInput.model_validate(values)


@pytest.mark.parametrize(
    ("tier", "cap", "label", "decision", "reason"),
    [
        (TrustTier.INTERNAL, Label.PUBLIC, Label.PUBLIC, Decision.ALLOW, Reason.OK),
        (
            TrustTier.INTERNAL,
            Label.PUBLIC,
            Label.INTERNAL,
            Decision.DENY,
            Reason.LABEL_EXCEEDS_BOUNDARY,
        ),
        (
            TrustTier.INTERNAL,
            Label.PUBLIC,
            Label.CONFIDENTIAL,
            Decision.DENY,
            Reason.LABEL_EXCEEDS_BOUNDARY,
        ),
        (TrustTier.INTERNAL, Label.INTERNAL, Label.PUBLIC, Decision.ALLOW, Reason.OK),
        (TrustTier.INTERNAL, Label.INTERNAL, Label.INTERNAL, Decision.ALLOW, Reason.OK),
        (
            TrustTier.INTERNAL,
            Label.INTERNAL,
            Label.CONFIDENTIAL,
            Decision.DENY,
            Reason.LABEL_EXCEEDS_BOUNDARY,
        ),
        (TrustTier.INTERNAL, Label.CONFIDENTIAL, Label.PUBLIC, Decision.ALLOW, Reason.OK),
        (TrustTier.INTERNAL, Label.CONFIDENTIAL, Label.INTERNAL, Decision.ALLOW, Reason.OK),
        (TrustTier.INTERNAL, Label.CONFIDENTIAL, Label.CONFIDENTIAL, Decision.ALLOW, Reason.OK),
        *[
            (tier, cap, label, decision, reason)
            for tier in (TrustTier.APPROVED_EXTERNAL, TrustTier.GENERAL_EXTERNAL)
            for cap, label, decision, reason in (
                (Label.PUBLIC, Label.PUBLIC, Decision.ALLOW, Reason.OK),
                (Label.PUBLIC, Label.INTERNAL, Decision.DENY, Reason.LABEL_EXCEEDS_BOUNDARY),
                (Label.PUBLIC, Label.CONFIDENTIAL, Decision.DENY, Reason.LABEL_EXCEEDS_BOUNDARY),
                (Label.INTERNAL, Label.PUBLIC, Decision.ALLOW, Reason.OK),
                (Label.INTERNAL, Label.INTERNAL, Decision.ASK, Reason.INTERNAL_TO_EXTERNAL),
                (
                    Label.INTERNAL,
                    Label.CONFIDENTIAL,
                    Decision.DENY,
                    Reason.LABEL_EXCEEDS_BOUNDARY,
                ),
            )
        ],
    ],
)
def test_boundary_matrix_and_cap_priority(
    tier: TrustTier, cap: Label, label: Label, decision: Decision, reason: Reason
) -> None:
    """T27/T30: 경계 cap이 먼저이며, 외부 두 tier는 같은 기본 행렬을 따른다."""
    verdict = judge_boundary(label, _boundary("receiver", tier, cap))
    assert verdict.boundary_id == "receiver"
    assert verdict.decision is decision
    assert verdict.reason is reason


@pytest.mark.parametrize("phase", ["submit", "approval_recheck"])
@pytest.mark.parametrize("status", list(TaskStatus))
def test_phase_accepts_only_its_expected_task_status(phase: str, status: TaskStatus) -> None:
    expected_status = TaskStatus.RUNNING if phase == "submit" else TaskStatus.WAITING_APPROVAL
    result = evaluate(_input(phase=phase, state_updates={"status": status}))
    if status is expected_status:
        assert result.decision is Decision.ALLOW
        assert result.reasons == (Reason.OK,)
    else:
        assert result.decision is Decision.DENY
        assert result.reasons == (Reason.TASK_NOT_RUNNING,)
        assert result.verdicts == ()


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"registration_status": RegStatus.PENDING}, Reason.REG_NOT_ACTIVE),
        ({"registration_status": RegStatus.BLOCKED}, Reason.REG_NOT_ACTIVE),
        ({"registered_fingerprint": "b" * 64}, Reason.TOOL_FINGERPRINT_CHANGED),
        ({"state_updates": {"unclassified": True}}, Reason.TASK_UNCLASSIFIED),
        ({"state_updates": {"status": TaskStatus.HALTED}}, Reason.TASK_NOT_RUNNING),
        ({"policy_version": "policy-2"}, Reason.POLICY_CHANGED),
        ({"kind": ToolKind.SEND}, Reason.UNKNOWN_DESTINATION),
        ({"kind": ToolKind.OTHER}, Reason.TOOL_UNSUPPORTED),
    ],
)
def test_precondition_failure_precedes_boundaries_and_agt(
    updates: dict[str, object], expected: Reason
) -> None:
    """T04/I6: 기본 ALLOW·ASK·DENY와 AGT 오류에 앞서 입력 전제를 검사한다."""
    values = {
        "label": Label.CONFIDENTIAL,
        "server": _boundary(tier=TrustTier.GENERAL_EXTERNAL),
        "access": AccessResult(allowed=False, reason="do not expose raw reason", error=True),
        **updates,
    }
    result = evaluate(_input(**values))
    assert result.decision is Decision.DENY
    assert result.reasons == (expected,)
    assert result.verdicts == ()
    assert result.policy_version == values.get("policy_version", "policy-1")
    assert result.security_version == 7


def test_all_precondition_reasons_are_retained_in_order() -> None:
    result = evaluate(
        _input(
            kind=ToolKind.SEND,
            registration_status=RegStatus.BLOCKED,
            registered_fingerprint="b" * 64,
            state_updates={"unclassified": True, "status": TaskStatus.HALTED},
            policy_version="policy-2",
        )
    )
    assert result.decision is Decision.DENY
    assert result.reasons == (
        Reason.REG_NOT_ACTIVE,
        Reason.TOOL_FINGERPRINT_CHANGED,
        Reason.TASK_UNCLASSIFIED,
        Reason.TASK_NOT_RUNNING,
        Reason.POLICY_CHANGED,
        Reason.UNKNOWN_DESTINATION,
    )
    assert result.verdicts == ()


@pytest.mark.parametrize(
    ("allowed", "error", "expected_reason"),
    [
        (True, False, Reason.OK),
        (False, False, Reason.AGT_DENY),
        (True, True, Reason.AGT_ERROR),
        (False, True, Reason.AGT_ERROR),
    ],
)
def test_agt_error_denies_even_when_allowed_is_true(
    allowed: bool, error: bool, expected_reason: Reason
) -> None:
    result = evaluate(
        _input(access=AccessResult(allowed=allowed, reason="untrusted raw text", error=error))
    )
    assert result.decision is (Decision.ALLOW if expected_reason is Reason.OK else Decision.DENY)
    assert result.reasons == (expected_reason,)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].reason is Reason.OK


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_external_server_denies_confidential_even_with_internal_destination(kind: ToolKind) -> None:
    """T26/T31: READ와 SEND 모두 MCP 서버 자체를 수신 경계로 판정한다."""
    result = evaluate(
        _input(
            kind=kind,
            label=Label.CONFIDENTIAL,
            server=_boundary("external-server", TrustTier.GENERAL_EXTERNAL),
            destination=_boundary("internal-destination"),
        )
    )
    assert result.decision is Decision.DENY
    assert result.reasons == (Reason.LABEL_EXCEEDS_BOUNDARY,)
    assert tuple(v.boundary_id for v in result.verdicts) == (
        ("external-server", "internal-destination")
        if kind is ToolKind.SEND
        else ("external-server",)
    )


def test_read_does_not_add_an_unrelated_destination_verdict() -> None:
    result = evaluate(
        _input(label=Label.CONFIDENTIAL, destination=_boundary(tier=TrustTier.GENERAL_EXTERNAL))
    )
    assert result.decision is Decision.ALLOW
    assert result.reasons == (Reason.OK,)
    assert tuple(v.boundary_id for v in result.verdicts) == ("server",)


@pytest.mark.parametrize(
    ("label", "tier", "decision", "reason"),
    [
        (Label.PUBLIC, TrustTier.GENERAL_EXTERNAL, Decision.ALLOW, Reason.OK),
        (Label.INTERNAL, TrustTier.GENERAL_EXTERNAL, Decision.ASK, Reason.INTERNAL_TO_EXTERNAL),
        (
            Label.CONFIDENTIAL,
            TrustTier.GENERAL_EXTERNAL,
            Decision.DENY,
            Reason.LABEL_EXCEEDS_BOUNDARY,
        ),
    ],
)
def test_equal_boundary_causes_are_not_deduplicated(
    label: Label, tier: TrustTier, decision: Decision, reason: Reason
) -> None:
    result = evaluate(
        _input(
            kind=ToolKind.SEND,
            label=label,
            server=_boundary("server", tier),
            destination=_boundary("destination", tier),
        )
    )
    assert result.decision is decision
    assert result.reasons == (reason, reason)
    assert tuple(v.boundary_id for v in result.verdicts) == ("server", "destination")
    assert result.policy_version == "policy-1"
    assert result.security_version == 7


def test_boundary_and_agt_denials_preserve_all_causes() -> None:
    result = evaluate(
        _input(
            kind=ToolKind.SEND,
            label=Label.CONFIDENTIAL,
            server=_boundary("server", TrustTier.GENERAL_EXTERNAL),
            destination=_boundary("destination", TrustTier.APPROVED_EXTERNAL),
            access=AccessResult(allowed=False, reason="raw agt reason", error=False),
        )
    )
    assert result.reasons == (
        Reason.LABEL_EXCEEDS_BOUNDARY,
        Reason.LABEL_EXCEEDS_BOUNDARY,
        Reason.AGT_DENY,
    )


def test_agt_allow_cannot_replace_human_approval() -> None:
    result = evaluate(
        _input(
            kind=ToolKind.SEND,
            label=Label.INTERNAL,
            destination=_boundary("destination", TrustTier.GENERAL_EXTERNAL),
        )
    )
    assert result.decision is Decision.ASK
    assert result.reasons == (Reason.INTERNAL_TO_EXTERNAL,)
    assert tuple(v.decision for v in result.verdicts) == (Decision.ALLOW, Decision.ASK)


@pytest.mark.parametrize("server_decision", list(Decision))
@pytest.mark.parametrize("destination_decision", list(Decision))
@pytest.mark.parametrize(
    ("agt_allowed", "agt_error"), [(True, False), (False, False), (True, True), (False, True)]
)
def test_server_destination_and_agt_composition(
    server_decision: Decision, destination_decision: Decision, agt_allowed: bool, agt_error: bool
) -> None:
    def boundary_for(decision: Decision, boundary_id: str) -> Boundary:
        if decision is Decision.ALLOW:
            return _boundary(boundary_id)
        if decision is Decision.ASK:
            return _boundary(boundary_id, TrustTier.GENERAL_EXTERNAL)
        return _boundary(boundary_id, TrustTier.INTERNAL, Label.PUBLIC)

    # 고정된 규범 표에서 기대값을 선택한다. 구현의 max 합성을 기대값에 사용하지 않는다.
    expected_table = {
        (Decision.ALLOW, Decision.ALLOW): Decision.ALLOW,
        (Decision.ALLOW, Decision.ASK): Decision.ASK,
        (Decision.ALLOW, Decision.DENY): Decision.DENY,
        (Decision.ASK, Decision.ALLOW): Decision.ASK,
        (Decision.ASK, Decision.ASK): Decision.ASK,
        (Decision.ASK, Decision.DENY): Decision.DENY,
        (Decision.DENY, Decision.ALLOW): Decision.DENY,
        (Decision.DENY, Decision.ASK): Decision.DENY,
        (Decision.DENY, Decision.DENY): Decision.DENY,
    }
    expected = expected_table[(server_decision, destination_decision)]
    agt_denies = agt_error or not agt_allowed
    if agt_denies:
        expected = Decision.DENY
    reason_for = {
        Decision.ALLOW: Reason.OK,
        Decision.ASK: Reason.INTERNAL_TO_EXTERNAL,
        Decision.DENY: Reason.LABEL_EXCEEDS_BOUNDARY,
    }
    expected_reasons = tuple(
        reason_for[decision]
        for decision in (server_decision, destination_decision)
        if decision is expected
    )
    if agt_denies:
        expected_reasons += (Reason.AGT_ERROR if agt_error else Reason.AGT_DENY,)
    result = evaluate(
        _input(
            kind=ToolKind.SEND,
            label=Label.INTERNAL,
            server=boundary_for(server_decision, "server"),
            destination=boundary_for(destination_decision, "destination"),
            access=AccessResult(allowed=agt_allowed, reason="raw reason", error=agt_error),
        )
    )
    assert result.decision is expected
    assert result.reasons == expected_reasons
    assert tuple(v.decision for v in result.verdicts) == (server_decision, destination_decision)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("accumulate", Label.CONFIDENTIAL), ("last_read", Label.INTERNAL), ("none", Label.PUBLIC)],
)
def test_effective_label_modes(mode: str, expected: Label) -> None:
    state = _input(
        state_updates={"high_watermark": Label.CONFIDENTIAL, "last_read_label": Label.INTERNAL}
    ).state
    assert effective_label(state, mode) is expected
    assert effective_label(state) is Label.CONFIDENTIAL


@pytest.mark.parametrize("mode", ["", "ACCUMULATE", "last-read", "unknown"])
def test_invalid_mode_fails_instead_of_falling_back(mode: str) -> None:
    with pytest.raises(PolicyConfigError):
        effective_label(_input().state, mode)


@pytest.mark.parametrize(
    ("mode", "decision"),
    [("accumulate", Decision.DENY), ("last_read", Decision.ALLOW), ("none", Decision.ALLOW)],
)
def test_comparison_modes_show_confidential_then_public_label_laundering(
    mode: str, decision: Decision
) -> None:
    """T34의 순수 정책 부분: 실제 replay 및 수신 계수는 포함하지 않는다."""
    initial = _input().state
    confidential = merge(initial, Label.CONFIDENTIAL, "secret/document", 1)
    state = merge(confidential, Label.PUBLIC, "public/document", 2)
    assert state.high_watermark is Label.CONFIDENTIAL
    assert state.last_read_label is Label.PUBLIC
    assert state.security_version == confidential.security_version
    inp = _input(
        kind=ToolKind.SEND,
        destination=_boundary("destination", TrustTier.GENERAL_EXTERNAL),
        state=state,
        label=effective_label(state, mode),
    )
    assert evaluate(inp).decision is decision


@pytest.mark.parametrize("mode", ["accumulate", "last_read", "none"])
def test_comparison_modes_keep_registration_and_agt_checks(mode: str) -> None:
    state = _input(state_updates={"high_watermark": Label.CONFIDENTIAL}).state
    label = effective_label(state, mode)
    blocked = evaluate(_input(state=state, label=label, registration_status=RegStatus.BLOCKED))
    assert blocked.decision is Decision.DENY
    assert blocked.reasons == (Reason.REG_NOT_ACTIVE,)
    agt = evaluate(
        _input(
            state=state,
            label=label,
            access=AccessResult(allowed=False, reason="denied", error=False),
        )
    )
    assert agt.decision is Decision.DENY
    assert agt.reasons == (Reason.AGT_DENY,)


@pytest.mark.parametrize("mode", ["accumulate", "last_read", "none"])
@pytest.mark.parametrize(
    ("updates", "state_updates", "expected"),
    [
        ({}, {"unclassified": True}, Reason.TASK_UNCLASSIFIED),
        ({}, {"status": TaskStatus.HALTED}, Reason.TASK_NOT_RUNNING),
        ({}, {"status": TaskStatus.WAITING_APPROVAL}, Reason.TASK_NOT_RUNNING),
        ({"kind": ToolKind.SEND}, {}, Reason.UNKNOWN_DESTINATION),
        ({"kind": ToolKind.OTHER}, {}, Reason.TOOL_UNSUPPORTED),
        ({"registered_fingerprint": "b" * 64}, {}, Reason.TOOL_FINGERPRINT_CHANGED),
        ({"policy_version": "policy-2"}, {}, Reason.POLICY_CHANGED),
    ],
)
def test_comparison_modes_preserve_all_preconditions(
    mode: str,
    updates: dict[str, object],
    state_updates: dict[str, object],
    expected: Reason,
) -> None:
    state_values = {
        "high_watermark": Label.CONFIDENTIAL,
        "last_read_label": Label.INTERNAL,
        **state_updates,
    }
    inp = _input(state_updates=state_values, **updates)
    result = evaluate(inp.model_copy(update={"effective_label": effective_label(inp.state, mode)}))
    assert result.decision is Decision.DENY
    assert result.reasons == (expected,)
    assert result.verdicts == ()


def test_policy_is_deterministic_and_does_not_mutate_input() -> None:
    inp = _input(kind=ToolKind.SEND, destination=_boundary("destination"))
    original_json = inp.model_dump_json()
    assert evaluate(inp) == evaluate(inp)
    assert inp.model_dump_json() == original_json


def test_config_error_is_a_value_error() -> None:
    assert issubclass(PolicyConfigError, ValueError)


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("error", [False, True])
def test_policy_output_never_copies_raw_agt_reason(allowed: bool, error: bool) -> None:
    """I9의 순수 반환값 부분만 검증하며 실제 로그 경로는 후속 검증 대상이다."""
    sensitive_reason = "synthetic-sensitive-agt-reason"
    result = evaluate(
        _input(access=AccessResult(allowed=allowed, reason=sensitive_reason, error=error))
    )
    assert sensitive_reason not in repr(result)
    assert sensitive_reason not in repr(result.model_dump())
    assert sensitive_reason not in result.model_dump_json()
