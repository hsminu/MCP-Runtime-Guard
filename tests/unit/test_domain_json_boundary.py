"""I6: Python 검증과 JSON 검증에서 등급·판정의 숫자 의미가 같아야 한다."""

import json

import pytest
from pydantic import ValidationError

from guard_app.domain.models import Boundary, BoundaryVerdict, ProposedCall, TaskState


@pytest.mark.parametrize("value", [True, False, 1.0, 0.0, "1", None])
def test_json_boundary_label_rejects_noninteger_values(value: object) -> None:
    payload = {"boundary_id": "server", "tier": "internal", "max_label": value}

    with pytest.raises(ValidationError):
        Boundary.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["high_watermark", "last_read_label"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
def test_json_task_labels_reject_noninteger_values(field: str, value: object) -> None:
    payload = {
        "task_id": "task-1",
        "epoch": 1,
        "profile_id": "default",
        "policy_version": "policy-1",
        field: value,
    }

    with pytest.raises(ValidationError):
        TaskState.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
def test_json_decision_rejects_noninteger_values(value: object) -> None:
    payload = {"boundary_id": "server", "decision": value, "reason": "OK"}

    with pytest.raises(ValidationError):
        BoundaryVerdict.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("value", [0, 1, 2])
def test_json_exact_integer_enums_remain_supported(value: int) -> None:
    boundary = Boundary.model_validate_json(
        json.dumps({"boundary_id": "server", "tier": "internal", "max_label": value})
    )
    verdict = BoundaryVerdict.model_validate_json(
        json.dumps({"boundary_id": "server", "decision": value, "reason": "OK"})
    )

    assert boundary.max_label.value == value
    assert verdict.decision.value == value


@pytest.mark.parametrize("surrogate", [chr(0xD800), chr(0xDFFF)])
@pytest.mark.parametrize("as_key", [True, False])
def test_arguments_reject_strings_without_utf8_encoding(surrogate: str, as_key: bool) -> None:
    arguments = {surrogate: "value"} if as_key else {"value": surrogate}

    with pytest.raises(ValidationError):
        ProposedCall.model_validate(
            {"tool_ref": "docs__read", "arguments": arguments, "model_call_id": "model-1"}
        )
