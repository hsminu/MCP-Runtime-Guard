"""B1 모델의 I3/I6/I7/I9 경계를 단위 수준에서 검증한다.

실제 승인 소비, dispatch 차단, 로그 경로는 C의 독립 검증 대상이다.
"""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from guard_app.domain.enums import (
    Decision,
    Label,
    RegStatus,
    TaskStatus,
    ToolKind,
    TrustTier,
)
from guard_app.domain.models import (
    AccessResult,
    Arguments,
    Boundary,
    BoundaryVerdict,
    Escalation,
    FrozenJsonObject,
    PolicyDecision,
    PolicyInput,
    ProposedCall,
    ResolvedRequest,
    TaskState,
    ToolSpec,
)
from guard_app.domain.reasons import Reason


def make_tool(**updates: object) -> ToolSpec:
    values: dict[str, object] = {
        "server_id": "docs",
        "tool_name": "read_document",
        "kind": ToolKind.READ,
        "input_schema": {"type": "object", "properties": {}},
        "destination_arg": None,
        "resource_arg": "path",
        "fingerprint": "a" * 64,
    }
    values.update(updates)
    return ToolSpec.model_validate(values)


def make_boundary(**updates: object) -> Boundary:
    values: dict[str, object] = {
        "boundary_id": "docs",
        "tier": TrustTier.INTERNAL,
        "max_label": Label.CONFIDENTIAL,
    }
    values.update(updates)
    return Boundary.model_validate(values)


def make_request(**updates: object) -> ResolvedRequest:
    values: dict[str, object] = {
        "request_id": "request-1",
        "task_id": "task-1",
        "epoch": 1,
        "call_seq": 2,
        "tool": make_tool(),
        "arguments": {"path": "internal/plan.txt"},
        "resource_id": "internal/plan.txt",
        "server_boundary": make_boundary(),
        "destination": None,
    }
    values.update(updates)
    return ResolvedRequest.model_validate(values)


def make_state(**updates: object) -> TaskState:
    values: dict[str, object] = {
        "task_id": "task-1",
        "epoch": 1,
        "profile_id": "default",
        "policy_version": "policy-1",
    }
    values.update(updates)
    return TaskState.model_validate(values)


def make_policy_input(**updates: object) -> PolicyInput:
    values: dict[str, object] = {
        "request": make_request(),
        "state": make_state(status=TaskStatus.RUNNING),
        "effective_label": Label.PUBLIC,
        "registration_status": RegStatus.ACTIVE,
        "registered_fingerprint": "a" * 64,
        "access": AccessResult(allowed=True, reason="ok", error=False),
        "policy_version": "policy-1",
        "phase": "submit",
    }
    values.update(updates)
    return PolicyInput.model_validate(values)


def test_proposed_call_rejects_model_supplied_security_fields() -> None:
    """I7: 모델 입력은 Task·등급·승인자·경계를 주입할 수 없다."""
    for name, value in (
        ("task_id", "forged-task"),
        ("epoch", 999),
        ("call_seq", 999),
        ("label", Label.PUBLIC),
        ("approver_id", "forged-person"),
        ("server_boundary", make_boundary()),
        ("destination", make_boundary()),
    ):
        with pytest.raises(ValidationError):
            ProposedCall.model_validate(
                {
                    "tool_ref": "docs__read_document",
                    "arguments": {},
                    "model_call_id": "model-1",
                    name: value,
                }
            )


def test_nested_tool_cannot_inject_task_identifier() -> None:
    """I7: 중첩 모델도 미지 필드의 결정권을 허용하지 않는다."""
    tool = make_tool().model_dump()
    tool["task_id"] = "forged-task"
    with pytest.raises(ValidationError):
        make_request(tool=tool)


def test_argument_names_do_not_promote_to_trusted_fields() -> None:
    """인자 안의 task_id는 일반 JSON 데이터이며 신뢰 필드가 아니다."""
    request = make_request(arguments={"task_id": "forged-task", "label": 0})
    assert request.task_id == "task-1"
    assert request.arguments["task_id"] == "forged-task"
    assert request.server_boundary.max_label is Label.CONFIDENTIAL


@pytest.mark.parametrize("field", ["tool_ref", "model_call_id"])
def test_proposed_call_does_not_coerce_identity_fields(field: str) -> None:
    values = {"tool_ref": "docs__read_document", "arguments": {}, "model_call_id": "m-1"}
    values[field] = 12
    with pytest.raises(ValidationError):
        ProposedCall.model_validate(values)


@pytest.mark.parametrize("field", ["epoch", "call_seq"])
@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_request_counters_are_strict_integers(field: str, value: object) -> None:
    """I6/I7: bool이나 문자열을 실행 상태의 정수로 변환하지 않는다."""
    with pytest.raises(ValidationError):
        make_request(**{field: value})


def test_task_defaults_match_initial_security_state() -> None:
    state = make_state()
    assert state.high_watermark is Label.PUBLIC
    assert state.last_read_label is Label.PUBLIC
    assert state.unclassified is False
    assert state.status is TaskStatus.READY
    assert state.security_version == 0
    assert state.row_revision == 0
    assert state.escalations == ()


@pytest.mark.parametrize("field", ["epoch", "security_version", "row_revision"])
def test_task_boolean_is_not_an_integer_counter(field: str) -> None:
    with pytest.raises(ValidationError):
        make_state(**{field: True})


@pytest.mark.parametrize("field", ["allowed", "error"])
@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_access_result_does_not_coerce_boolean_fields(field: str, value: object) -> None:
    values = {"allowed": True, "reason": "ok", "error": False, field: value}
    with pytest.raises(ValidationError):
        AccessResult.model_validate(values)


@pytest.mark.parametrize("phase", [None, "approved", "dispatch", 0])
def test_policy_phase_rejects_unknown_values(phase: object) -> None:
    with pytest.raises(ValidationError):
        make_policy_input(phase=phase)


@pytest.mark.parametrize("phase", ["submit", "approval_recheck"])
def test_policy_phase_accepts_only_declared_phases(phase: str) -> None:
    assert make_policy_input(phase=phase).phase == phase


@pytest.mark.parametrize("tier", [TrustTier.APPROVED_EXTERNAL, TrustTier.GENERAL_EXTERNAL])
def test_external_boundary_cannot_receive_confidential(tier: TrustTier) -> None:
    """기술설계서 5.1: 외부 max_label의 기밀 허용을 설정 단계에서 거부한다."""
    with pytest.raises(ValidationError):
        make_boundary(tier=tier, max_label=Label.CONFIDENTIAL)
    assert make_boundary(tier=tier, max_label=Label.INTERNAL).max_label is Label.INTERNAL


@pytest.mark.parametrize("fingerprint", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_tool_fingerprint_requires_lowercase_sha256(fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        make_tool(fingerprint=fingerprint)


def test_security_models_are_frozen() -> None:
    """I3/I7: 생성 뒤 필드 대입으로 요청·판정·상태를 바꿀 수 없다."""
    verdict = BoundaryVerdict(boundary_id="docs", decision=Decision.ALLOW, reason=Reason.OK)
    models = [
        ProposedCall(tool_ref="docs__read_document", arguments={}, model_call_id="m-1"),
        make_tool(),
        make_boundary(),
        make_request(),
        make_state(),
        Escalation(
            at_seq=1,
            source_id="internal/plan.txt",
            from_label=Label.PUBLIC,
            to_label=Label.INTERNAL,
        ),
        AccessResult(allowed=True, reason="ok", error=False),
        make_policy_input(),
        verdict,
        PolicyDecision(
            decision=Decision.ALLOW,
            reasons=(Reason.OK,),
            verdicts=(verdict,),
            policy_version="policy-1",
            security_version=0,
        ),
    ]
    for model in models:
        name = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, name, getattr(model, name))


def test_arguments_detach_and_freeze_all_nested_containers() -> None:
    """I3: 호출자의 mutable 인자를 바꿔도 승인 대상 사본은 바뀌지 않는다."""
    original = {"items": [{"content": ["기밀문서", {"flag": True}]}]}
    call = ProposedCall(tool_ref="docs__read_document", arguments=original, model_call_id="m-1")
    assert isinstance(call.arguments, Arguments)
    assert isinstance(call.arguments, FrozenJsonObject)
    assert isinstance(call.arguments["items"], tuple)
    assert isinstance(call.arguments["items"][0], FrozenJsonObject)
    assert isinstance(call.arguments["items"][0]["content"][1], FrozenJsonObject)

    original["items"][0]["content"][0] = "변조"
    original["items"][0]["content"][1]["flag"] = False
    assert call.arguments["items"][0]["content"][0] == "기밀문서"
    assert call.arguments["items"][0]["content"][1]["flag"] is True

    with pytest.raises(TypeError):
        call.arguments["extra"] = "변조"
    with pytest.raises(TypeError):
        call.arguments["items"][0]["content"][0] = "변조"
    with pytest.raises(TypeError):
        call.arguments["items"][0]["content"][1]["flag"] = False


def test_to_dict_returns_a_fresh_mutable_deep_copy() -> None:
    call = ProposedCall(
        tool_ref="docs__read_document",
        arguments={"items": [{"value": "원본"}]},
        model_call_id="m-1",
    )
    first = call.arguments.to_dict()
    second = call.arguments.to_dict()
    assert first == second == {"items": [{"value": "원본"}]}
    assert first is not second
    assert first["items"] is not second["items"]
    assert first["items"][0] is not second["items"][0]
    first["items"][0]["value"] = "변조"
    assert second["items"][0]["value"] == "원본"
    assert call.arguments["items"][0]["value"] == "원본"


def test_schema_detaches_freezes_and_allows_finite_float() -> None:
    schema = {"properties": {"ratio": {"type": "number", "minimum": 0.25}}}
    tool = make_tool(input_schema=schema)
    assert isinstance(tool.input_schema, FrozenJsonObject)
    assert tool.input_schema["properties"]["ratio"]["minimum"] == 0.25
    schema["properties"]["ratio"]["minimum"] = -1
    assert tool.input_schema["properties"]["ratio"]["minimum"] == 0.25
    with pytest.raises(TypeError):
        tool.input_schema["properties"]["ratio"]["minimum"] = -1


@pytest.mark.parametrize("value", [0.0, 1.25, float("nan"), float("inf"), -float("inf")])
def test_arguments_reject_float_at_any_depth(value: float) -> None:
    """8.1: 승인 인자에 부동소수점을 허용하지 않는다."""
    with pytest.raises(ValidationError):
        ProposedCall(
            tool_ref="docs__read_document",
            arguments={"outer": [{"value": value}]},
            model_call_id="m-1",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_schema_rejects_nonfinite_float(value: float) -> None:
    with pytest.raises(ValidationError):
        make_tool(input_schema={"minimum": value})


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, b"secret", object()])
def test_arguments_reject_unsupported_value_types(value: object) -> None:
    with pytest.raises(ValidationError):
        ProposedCall(
            tool_ref="docs__read_document", arguments={"value": value}, model_call_id="m-1"
        )


@pytest.mark.parametrize("key", [1, True, None, ("key",)])
def test_arguments_reject_nonstring_keys(key: object) -> None:
    with pytest.raises(ValidationError):
        ProposedCall(
            tool_ref="docs__read_document",
            arguments={"nested": {key: "value"}},
            model_call_id="m-1",
        )


def test_arguments_reject_cycles_without_recursion_error() -> None:
    """I6: 순환 입력은 제어되지 않는 재귀 오류 대신 검증 실패가 된다."""
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValidationError):
        ProposedCall(tool_ref="docs__read_document", arguments=cyclic, model_call_id="m-1")


def test_shared_acyclic_json_nodes_are_accepted_and_detached() -> None:
    shared = {"content": ["공유"]}
    call = ProposedCall(
        tool_ref="docs__read_document",
        arguments={"first": shared, "second": shared},
        model_call_id="m-1",
    )
    shared["content"].append("변조")
    assert call.arguments.to_dict() == {
        "first": {"content": ["공유"]},
        "second": {"content": ["공유"]},
    }


def test_model_copy_revalidates_updates_and_leaves_original_unchanged() -> None:
    """I3: Pydantic 기본 copy의 미검증 update를 승인 사본에 허용하지 않는다."""
    call = ProposedCall(
        tool_ref="docs__read_document",
        arguments={"content": ["원본"]},
        model_call_id="m-1",
    )
    new_arguments = {"content": ["새 사본"]}
    copied = call.model_copy(update={"arguments": new_arguments})
    new_arguments["content"][0] = "변조"
    assert copied.arguments["content"] == ("새 사본",)
    assert call.arguments["content"] == ("원본",)
    assert copied is not call
    with pytest.raises(ValidationError):
        call.model_copy(update={"arguments": {"ratio": 0.5}})
    with pytest.raises(ValidationError):
        call.model_copy(update={"task_id": "forged-task"})
    with pytest.raises(ValidationError):
        make_state().model_copy(update={"epoch": True})
    assert call.arguments["content"] == ("원본",)


def test_model_copy_revalidates_external_boundary_limits() -> None:
    external = make_boundary(tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL)
    with pytest.raises(ValidationError):
        external.model_copy(update={"max_label": Label.CONFIDENTIAL})
    assert external.max_label is Label.INTERNAL


def test_repr_omits_arguments_and_schema() -> None:
    """I9: 기본 모델 표현에 전체 인자·스키마의 민감 본문을 넣지 않는다."""
    sensitive_value = "PRIVATE_ARGUMENT_MARKER_123"
    call = ProposedCall(
        tool_ref="docs__read_document", arguments={"content": sensitive_value}, model_call_id="m-1"
    )
    request = make_request(arguments={"content": sensitive_value})
    tool = make_tool(input_schema={"description": sensitive_value})
    for model in (call, request, tool):
        assert sensitive_value not in repr(model)
        assert sensitive_value not in str(model)
    assert "arguments=" not in repr(call)
    assert "arguments=" not in repr(request)
    assert "input_schema=" not in repr(tool)


def test_validation_text_hides_sensitive_input_values() -> None:
    """I9: 오류의 문자열 표현만 검사한다. errors()의 원문 무보관을 주장하지 않는다."""
    sensitive_value = "PRIVATE_ARGUMENT_MARKER_123"
    with pytest.raises(ValidationError) as captured:
        ProposedCall.model_validate(
            {
                "tool_ref": "docs__read_document",
                "arguments": {"content": sensitive_value},
                "model_call_id": "m-1",
                "task_id": sensitive_value,
            }
        )
    assert sensitive_value not in str(captured.value)
    assert sensitive_value not in repr(captured.value)


def test_json_roundtrip_preserves_unicode_and_enum_meaning() -> None:
    """8.1: NFC/NFD를 통일하지 않고 전송할 문자열 그대로 보존한다."""
    call = ProposedCall(
        tool_ref="docs__read_document",
        arguments={"text": "한글", "composed": "é", "decomposed": "e\u0301"},
        model_call_id="m-1",
    )
    serialized = call.model_dump_json()
    assert "한글" in serialized
    assert ProposedCall.model_validate_json(serialized) == call
    assert call.arguments["composed"] != call.arguments["decomposed"]

    request = make_request(arguments=call.arguments.to_dict())
    assert ResolvedRequest.model_validate_json(request.model_dump_json()) == request
    decision = PolicyDecision(
        decision=Decision.ASK,
        reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        verdicts=(
            BoundaryVerdict(
                boundary_id="external",
                decision=Decision.ASK,
                reason=Reason.INTERNAL_TO_EXTERNAL,
            ),
        ),
        policy_version="policy-1",
        security_version=3,
    )
    assert PolicyDecision.model_validate_json(decision.model_dump_json()) == decision


_json_text = st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=12)
_json_values = st.recursive(
    st.none() | st.booleans() | st.integers(min_value=-1_000_000, max_value=1_000_000) | _json_text,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(_json_text, children, max_size=5)
    ),
    max_leaves=24,
)


@settings(max_examples=50, deadline=None)
@given(st.dictionaries(_json_text, _json_values, max_size=6))
def test_recursive_arguments_roundtrip_and_copy_isolation(arguments: dict[str, object]) -> None:
    """I3: 허용 JSON 트리의 freeze·직렬화·사본이 원래 값을 보존한다."""
    expected = json.loads(json.dumps(arguments, ensure_ascii=False))
    call = ProposedCall(tool_ref="docs__read_document", arguments=arguments, model_call_id="m-1")
    assert call.arguments.to_dict() == expected
    assert call.model_dump(mode="json")["arguments"] == expected
    assert json.loads(call.model_dump_json())["arguments"] == expected
    assert ProposedCall.model_validate_json(call.model_dump_json()) == call
    detached = call.arguments.to_dict()
    detached.clear()
    assert call.arguments.to_dict() == expected
    arguments.clear()
    assert call.arguments.to_dict() == expected
