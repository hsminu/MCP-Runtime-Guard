"""SCHEMA-01: 기존 검증 의미와 순수 준비/검증 연산의 동등성."""

import time

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services import schema_jobs
from guard_app.services.catalog_schema import ArgumentSchema, CatalogConfigError
from guard_app.services.schema_jobs import (
    PreparedSchema,
    SchemaJobFailure,
    SchemaJobResult,
    prepare,
    validate,
)


@pytest.mark.parametrize(
    ("schema", "accepted", "rejected"),
    [
        ({"type": "object", "properties": {"v": {"type": "integer"}}}, {"v": 1}, {"v": "1"}),
        (
            {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}, "properties": {"abc": {}}},
            {"abc": 1},
            {"ABC": 1},
        ),
        (
            {
                "type": "object",
                "properties": {"v": {"oneOf": [{"type": "integer"}, {"type": "string"}]}},
            },
            {"v": 1},
            {"v": True},
        ),
        (
            {"type": "object", "properties": {"v": {"format": "date"}}},
            {"v": "2026-01-01"},
            {"v": "not-a-date"},
        ),
        (
            {"type": "object", "properties": {"v": {"format": "email"}}},
            {"v": "a@example.com"},
            {"v": "invalid"},
        ),
        (
            {"type": "object", "properties": {"v": {"format": "ipv4"}}},
            {"v": "127.0.0.1"},
            {"v": "999.1.1.1"},
        ),
        (
            {"type": "object", "properties": {"v": {"format": "ipv6"}}},
            {"v": "::1"},
            {"v": "invalid"},
        ),
        ({"type": "object", "properties": {"v": {"format": "regex"}}}, {"v": "a+"}, {"v": "["}),
        (
            {"type": "object", "properties": {"v": {"format": "uuid"}}},
            {"v": "00000000-0000-0000-0000-000000000000"},
            {"v": "bad"},
        ),
    ],
)
def test_prepare_validate_match_argument_schema(schema, accepted, rejected):
    original = FrozenJsonObject(schema)
    result = prepare(original)
    assert isinstance(result, PreparedSchema)
    compiled = ArgumentSchema(original)
    assert result.schema == compiled.schema
    assert original.to_dict() == schema
    assert validate(result.schema, Arguments(accepted)) is SchemaJobResult.OK
    assert compiled.validate(Arguments(accepted)) is None
    assert validate(result.schema, Arguments(rejected)) is SchemaJobResult.INVALID_ARGUMENTS
    with pytest.raises(ResolutionError) as error:
        compiled.validate(Arguments(rejected))
    assert error.value.reason is Reason.INVALID_ARGUMENTS


@pytest.mark.parametrize(
    ("schema", "code", "message"),
    [
        ({"type": "array"}, SchemaJobFailure.ROOT, "도구 인자의 루트 스키마는 object여야 한다"),
        (
            {"type": "object", "$ref": "https://example.invalid"},
            SchemaJobFailure.REFERENCE,
            "MVP는 참조 스키마를 지원하지 않는다",
        ),
        (
            {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#"},
            SchemaJobFailure.DRAFT,
            "MVP는 Draft 2020-12 스키마만 지원한다",
        ),
        (
            {"type": "object", "properties": {"x": {"type": "invalid"}}},
            SchemaJobFailure.INVALID,
            "유효하지 않은 등록 인자 스키마다",
        ),
        (
            {"type": "object", "properties": {"x": {"format": "unknown"}}},
            SchemaJobFailure.FORMAT,
            "지원하지 않는 스키마 format이다",
        ),
    ],
)
def test_prepare_failures_preserve_catalog_messages(schema, code, message):
    original = FrozenJsonObject(schema)
    assert prepare(original) is code
    with pytest.raises(CatalogConfigError) as error:
        ArgumentSchema(original)
    assert str(error.value) == message
    assert "example.invalid" not in str(error.value)


@pytest.mark.parametrize(
    ("schema", "code", "message"),
    [
        ({"type": "object", "items": 1}, SchemaJobFailure.STRUCTURE, "유효하지 않은 스키마 구조다"),
        (
            {"type": "object", "properties": []},
            SchemaJobFailure.MAP,
            "유효하지 않은 스키마 속성 목록이다",
        ),
        (
            {"type": "object", "allOf": {}},
            SchemaJobFailure.LIST,
            "유효하지 않은 스키마 조합 목록이다",
        ),
        (
            {"type": "object", "pattern": "["},
            SchemaJobFailure.PATTERN,
            "유효하지 않은 스키마 정규식이다",
        ),
    ],
)
def test_defensive_prepare_failures_preserve_catalog_messages(monkeypatch, schema, code, message):
    monkeypatch.setattr(Draft202012Validator, "check_schema", lambda *_args, **_kwargs: None)
    original = FrozenJsonObject(schema)
    assert prepare(original) is code
    with pytest.raises(CatalogConfigError) as error:
        ArgumentSchema(original)
    assert str(error.value) == message


@pytest.mark.parametrize("error", [SchemaError("secret"), ValidationError("secret")])
def test_validation_error_type_and_original_exception_are_preserved(monkeypatch, error):
    compiled = ArgumentSchema(FrozenJsonObject({"type": "object"}))

    class FailingValidator:
        def __init__(self, *_args, **_kwargs):
            pass

        def validate(self, _arguments):
            raise error

    monkeypatch.setattr(schema_jobs, "_ArgumentValidator", FailingValidator)
    expected = ResolutionError if isinstance(error, ValidationError) else CatalogConfigError
    with pytest.raises(expected) as caught:
        compiled.validate(Arguments({}))
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    if isinstance(error, ValidationError):
        assert caught.value.reason is Reason.INVALID_ARGUMENTS
    else:
        assert str(caught.value) == "등록 스키마의 검증을 완료할 수 없다"


@pytest.mark.parametrize("number", [2**53, 10**100, -(10**100)])
def test_large_integer_schema_keeps_existing_catalog_behavior(number):
    original = {"type": "object", "default": number}
    result = prepare(FrozenJsonObject(original))
    assert isinstance(result, PreparedSchema)
    assert result.schema.to_dict()["allOf"][-1] == original
    compiled = ArgumentSchema(FrozenJsonObject(original))
    assert compiled.validate(Arguments({})) is None


def test_original_and_prevents_oneof_weakening():
    original = {
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
    registered = FrozenJsonObject(original)
    result = prepare(registered)
    assert isinstance(result, PreparedSchema)
    arguments = {"flag": True, "aux": 1}
    assert validate(result.schema, Arguments(arguments)) is SchemaJobResult.INVALID_ARGUMENTS
    assert result.schema.to_dict()["allOf"][-1] == original
    without_original = result.schema.to_dict()
    del without_original["allOf"][-1]
    assert Draft202012Validator(without_original).is_valid(arguments)
    assert not Draft202012Validator(original).is_valid(arguments)
    with pytest.raises(ResolutionError):
        ArgumentSchema(registered).validate(Arguments(arguments))
    assert registered.to_dict() == original


def test_server_patterns_never_run_on_arguments():
    """2026-10-30 C 점검(ReDoS, CWE-1333): 서버 정규식은 인자에 실행하지 않는다.

    고치기 전에는 `^(a+)+$`에 27자 인자가 2초였고 두 글자마다 4배로 늘었다.
    """
    schema = FrozenJsonObject(
        {"type": "object", "properties": {"q": {"type": "string", "pattern": "^(a+)+$"}}}
    )
    prepared = prepare(schema)
    assert isinstance(prepared, PreparedSchema)
    compiled = ArgumentSchema(schema)
    assert compiled.schema == prepared.schema  # 모델에 보이는 스키마에는 그대로 남는다
    slow = Arguments({"q": "a" * 64 + "!"})
    started = time.perf_counter()
    assert validate(prepared.schema, slow) is SchemaJobResult.OK
    assert compiled.validate(slow) is None
    assert time.perf_counter() - started < 1.0
    # pattern만 빠지고 나머지 검증은 그대로다.
    assert validate(prepared.schema, Arguments({"q": 1})) is SchemaJobResult.INVALID_ARGUMENTS


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "patternProperties": {"^x_": {"type": "integer"}}},
        {
            "type": "object",
            "properties": {"nested": {"type": "object", "patternProperties": {"^(a+)+$": {}}}},
        },
    ],
)
def test_pattern_properties_are_unsupported(schema):
    original = FrozenJsonObject(schema)
    assert prepare(original) is SchemaJobFailure.PATTERN_PROPERTIES
    with pytest.raises(CatalogConfigError) as error:
        ArgumentSchema(original)
    assert str(error.value) == "patternProperties 스키마는 지원하지 않는다"
