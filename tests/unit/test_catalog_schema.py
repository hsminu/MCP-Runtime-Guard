"""B3/I6: 등록 스키마 사본의 검증·폐쇄·오류 원문 격리."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services.catalog_schema import ArgumentSchema, CatalogConfigError


def test_closed_schema_copy_preserves_registration_and_closes_nested_object() -> None:
    registered = FrozenJsonObject(
        {
            "type": "object",
            "properties": {
                "nested": {"type": "object", "properties": {"count": {"type": "integer"}}}
            },
        }
    )
    original = registered.to_dict()
    compiled = ArgumentSchema(registered)
    compiled.validate(Arguments({"nested": {"count": 2}}))
    for arguments in ({"unexpected": 1}, {"nested": {"unexpected": 1}}):
        with pytest.raises(ResolutionError) as caught:
            compiled.validate(Arguments(arguments))
        assert caught.value.reason is Reason.INVALID_ARGUMENTS
    assert registered.to_dict() == original
    assert compiled.schema["additionalProperties"] is False


@pytest.mark.parametrize("value", [True, "1", None])
def test_schema_does_not_coerce_integer_arguments(value: object) -> None:
    compiled = ArgumentSchema(
        FrozenJsonObject({"type": "object", "properties": {"count": {"type": "integer"}}})
    )
    with pytest.raises(ResolutionError) as caught:
        compiled.validate(Arguments({"count": value}))
    assert caught.value.reason is Reason.INVALID_ARGUMENTS


def test_validation_error_never_contains_argument_or_schema_secret() -> None:
    marker = "synthetic-private-schema-marker"
    compiled = ArgumentSchema(
        FrozenJsonObject({"type": "object", "properties": {"value": {"const": "allowed"}}})
    )
    with pytest.raises(ResolutionError) as caught:
        compiled.validate(Arguments({"value": marker}))
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "array"},
        {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#"},
        {"type": "object", "properties": {"value": {"$ref": "https://invalid.example/schema"}}},
        {"type": "object", "properties": {"value": {"$ref": "file:///private/schema.json"}}},
        {"type": "object", "properties": {"value": {"$ref": "#/$defs/value"}}},
        {"type": "object", "properties": {"value": {"$dynamicRef": "#value"}}},
        {"type": "object", "properties": {"value": {"format": "unknown-format"}}},
        {"type": "object", "properties": {"value": {"type": "unknown-type"}}},
        {"type": "object", "properties": {"value": {"pattern": "["}}},
        {"type": "object", "allOf": [{"properties": {"value": {"type": "string"}}}]},
        {"type": "object", "additionalProperties": "not a schema"},
        {
            "type": "object",
            "properties": {"value": {"type": "object", "additionalProperties": [1]}},
        },
        {"type": "object", "additionalProperties": {"$ref": "https://invalid.example/schema"}},
        {"type": "object", "additionalProperties": {"type": "string", "pattern": "("}},
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "allOf": [{"anyOf": [{"properties": {"x": {"minimum": 1}}}]}],
        },
    ],
)
def test_unsupported_or_invalid_schema_is_a_config_failure(schema: dict[str, object]) -> None:
    with pytest.raises(CatalogConfigError):
        ArgumentSchema(FrozenJsonObject(schema))


def test_annotation_json_is_not_treated_as_a_schema() -> None:
    data = {"$ref": "https://ordinary-json.example", "type": "object", "extra": True}
    registered = FrozenJsonObject(
        {
            "type": "object",
            "default": data,
            "examples": [data],
            "properties": {"value": {"const": data}},
        }
    )
    compiled = ArgumentSchema(registered)
    assert compiled.schema.to_dict()["default"] == data
    assert compiled.schema.to_dict()["examples"] == [data]
    assert compiled.schema.to_dict()["properties"]["value"]["const"] == data
    compiled.validate(Arguments({"value": data}))


@pytest.mark.parametrize(("value", "valid"), [("2026-10-11", True), ("not-a-date", False)])
def test_supported_format_is_checked(value: str, valid: bool) -> None:
    compiled = ArgumentSchema(
        FrozenJsonObject(
            {"type": "object", "properties": {"date": {"type": "string", "format": "date"}}}
        )
    )
    if valid:
        compiled.validate(Arguments({"date": value}))
    else:
        with pytest.raises(ResolutionError):
            compiled.validate(Arguments({"date": value}))


def test_schema_export_is_immutable_and_repr_redacted() -> None:
    marker = "synthetic-private-schema-marker"
    compiled = ArgumentSchema(
        FrozenJsonObject({"type": "object", "properties": {"value": {"const": marker}}})
    )
    snapshot = compiled.schema.to_dict()
    snapshot["properties"]["value"]["const"] = "mutated"
    compiled.validate(Arguments({"value": marker}))
    assert marker not in repr(compiled)


@pytest.mark.parametrize(
    "condition",
    [
        {
            "if": {"properties": {"flag": {"const": True}}, "required": ["flag"]},
            "then": {"required": ["note"]},
        },
        {"not": {"properties": {"flag": {"const": True}}, "required": ["flag"]}},
    ],
)
def test_closing_schema_never_weakens_if_or_not_predicate(condition: dict[str, object]) -> None:
    compiled = ArgumentSchema(
        FrozenJsonObject(
            {
                "type": "object",
                "properties": {
                    "flag": {"type": "boolean"},
                    "aux": {"type": "integer"},
                    "note": {"type": "string"},
                },
                **condition,
            }
        )
    )
    with pytest.raises(ResolutionError):
        compiled.validate(Arguments({"flag": True, "aux": 1}))
    compiled.validate(Arguments({"flag": False, "aux": 1}))


def test_closing_schema_never_turns_ambiguous_one_of_into_allow() -> None:
    compiled = ArgumentSchema(
        FrozenJsonObject(
            {
                "type": "object",
                "properties": {"flag": {"type": "boolean"}, "aux": {"type": "integer"}},
                "oneOf": [
                    {"type": "object", "properties": {"flag": {"type": "boolean"}}},
                    {
                        "type": "object",
                        "properties": {"flag": {"type": "boolean"}, "aux": {"type": "integer"}},
                    },
                ],
            }
        )
    )
    with pytest.raises(ResolutionError):
        compiled.validate(Arguments({"flag": True, "aux": 1}))


@pytest.mark.parametrize(
    "arguments",
    [{"flag": True, "aux": 1, "note": "okay"}, {"flag": False, "aux": 1, "other": "okay"}],
)
def test_if_else_still_accepts_valid_original_branches(arguments: dict[str, object]) -> None:
    schema = {
        "type": "object",
        "properties": {
            "flag": {"type": "boolean"},
            "aux": {"type": "integer"},
            "note": {"type": "string"},
            "other": {"type": "string"},
        },
        "if": {"properties": {"flag": {"const": True}}, "required": ["flag"]},
        "then": {"required": ["note"]},
        "else": {"required": ["other"]},
    }
    compiled = ArgumentSchema(FrozenJsonObject(schema))
    compiled.validate(Arguments(arguments))
    assert compiled.schema.to_dict()["if"] == schema["if"]


@pytest.mark.parametrize(("count", "valid"), [(1, True), (5, True), (0, False), (6, False)])
def test_scalar_all_of_constraints_are_supported(count: int, valid: bool) -> None:
    compiled = ArgumentSchema(
        FrozenJsonObject(
            {
                "type": "object",
                "properties": {
                    "count": {"allOf": [{"type": "integer"}, {"minimum": 1}, {"maximum": 5}]}
                },
            }
        )
    )
    if valid:
        compiled.validate(Arguments({"count": count}))
    else:
        with pytest.raises(ResolutionError):
            compiled.validate(Arguments({"count": count}))


# 캐시(.hypothesis)가 없는 첫 실행은 st.text의 유니코드 표 생성(약 1초)이 입력 생성
# 시간으로 잡혀 too_slow로 실패한다. 표가 생긴 뒤에는 통과하므로 이 검사만 끈다.
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    condition=st.sampled_from(
        [
            {
                "if": {"properties": {"flag": {"const": True}}, "required": ["flag"]},
                "then": {"required": ["note"]},
            },
            {"not": {"properties": {"flag": {"const": True}}, "required": ["flag"]}},
            {"oneOf": [{"required": ["flag"]}, {"required": ["aux"]}]},
        ]
    ),
    arguments=st.dictionaries(
        keys=st.sampled_from(["flag", "aux", "note", "unknown"]),
        values=st.one_of(st.booleans(), st.integers(), st.text(max_size=15), st.none()),
    ),
)
def test_compiled_acceptance_always_satisfies_original_constraints(
    condition: dict[str, object], arguments: dict[str, object]
) -> None:
    """I6: 자동 폐쇄·조합 처리가 등록 원본의 거부를 허용으로 바꾸지 않는다."""
    original = {
        "type": "object",
        "properties": {
            "flag": {"type": "boolean"},
            "aux": {"type": "integer"},
            "note": {"type": "string"},
        },
        **condition,
    }
    compiled = ArgumentSchema(FrozenJsonObject(original))
    try:
        compiled.validate(Arguments(arguments))
    except ResolutionError:
        return
    assert Draft202012Validator(original).is_valid(arguments)
