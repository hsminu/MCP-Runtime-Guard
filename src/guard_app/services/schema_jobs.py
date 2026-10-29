"""SCHEMA-01 합의 전 제안: 등록 스키마 준비와 인자 검증의 순수 연산.

정규식 등 실제 검증은 동기 연산이다. 제한된 프로세스 실행은 C 연결 이후에 맡는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from guard_app.domain.json_types import Arguments, FrozenJsonObject

_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_FORMATS = ("date", "email", "ipv4", "ipv6", "regex", "uuid")
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
_SCHEMA_SINGLE = (
    "items",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "additionalProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
)
_SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")


class SchemaJobFailure(StrEnum):
    """원문이 없는 등록 스키마 준비 실패 분류."""

    ROOT = "ROOT"
    INVALID = "INVALID"
    STRUCTURE = "STRUCTURE"
    REFERENCE = "REFERENCE"
    DRAFT = "DRAFT"
    FORMAT = "FORMAT"
    ALLOF_OBJECT = "ALLOF_OBJECT"
    MAP = "MAP"
    LIST = "LIST"
    PATTERN = "PATTERN"
    DEPTH = "DEPTH"


class SchemaJobResult(StrEnum):
    """준비된 스키마의 인자 검증 결과. 성공은 정책 허용을 뜻하지 않는다."""

    OK = "OK"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    SCHEMA_INVALID = "SCHEMA_INVALID"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSchema:
    """원본 AND와 폐쇄 규칙이 적용된 불변 사본. 지문은 포함하지 않는다."""

    schema: FrozenJsonObject

    def __repr__(self) -> str:
        return "PreparedSchema(<redacted>)"


class _PreparationError(Exception):
    """원문 없는 내부 준비 실패."""

    def __init__(self, code: SchemaJobFailure) -> None:
        self.code = code


def prepare(registered: FrozenJsonObject) -> PreparedSchema | SchemaJobFailure:
    """기존 등록 검사와 준비를 수행한다. 전달받은 불변 원본은 변경하지 않는다."""
    original = registered.to_dict()
    if original.get("type") != "object":
        return SchemaJobFailure.ROOT
    try:
        Draft202012Validator.check_schema(original)
    except (SchemaError, re.error, Unresolvable):
        return SchemaJobFailure.INVALID
    working = registered.to_dict()
    try:
        _prepare_schema(working)
    except _PreparationError as error:
        return error.code
    # 더 엄격하게 바뀐 oneOf 등이 원본 거부를 허용으로 바꾸지 않도록 전체와 AND한다.
    working["allOf"] = [*cast("list[object]", working.get("allOf", [])), original]
    try:
        frozen = FrozenJsonObject(working)
    except ValueError:
        return SchemaJobFailure.DEPTH
    return PreparedSchema(frozen)


def validate(prepared: FrozenJsonObject, arguments: Arguments) -> SchemaJobResult:
    """준비된 스키마 전체로 검증하고 원문 없는 분류만 반환한다."""
    validator = Draft202012Validator(
        prepared.to_dict(), format_checker=FormatChecker(formats=_FORMATS), registry=Registry()
    )
    try:
        validator.validate(arguments.to_dict())
    except ValidationError:
        return SchemaJobResult.INVALID_ARGUMENTS
    except (SchemaError, re.error, Unresolvable):
        return SchemaJobResult.SCHEMA_INVALID
    return SchemaJobResult.OK


def _prepare_schema(value: object, *, in_all_of: bool = False, close_objects: bool = True) -> None:
    """schema 위치만 순회한다. const/default/examples 안의 자료는 건드리지 않는다."""
    if type(value) is bool:
        return
    if not isinstance(value, dict):
        raise _PreparationError(SchemaJobFailure.STRUCTURE)
    schema = cast("dict[str, object]", value)
    if any(key in schema for key in ("$ref", "$dynamicRef", "$recursiveRef")):
        raise _PreparationError(SchemaJobFailure.REFERENCE)
    if schema.get("$schema", _DRAFT) not in (_DRAFT, f"{_DRAFT}#"):
        raise _PreparationError(SchemaJobFailure.DRAFT)
    if "format" in schema and schema["format"] not in _FORMATS:
        raise _PreparationError(SchemaJobFailure.FORMAT)

    kind = schema.get("type")
    declares_object = (
        kind == "object"
        or (isinstance(kind, list) and "object" in kind)
        or "properties" in schema
        or "patternProperties" in schema
    )
    if declares_object and in_all_of:
        raise _PreparationError(SchemaJobFailure.ALLOF_OBJECT)

    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        _check_pattern(pattern)
    patterns = schema.get("patternProperties")
    if isinstance(patterns, dict):
        for pattern in patterns:
            _check_pattern(pattern)

    for keyword in _SCHEMA_MAPS:
        if keyword in schema:
            children = schema[keyword]
            if not isinstance(children, dict):
                raise _PreparationError(SchemaJobFailure.MAP)
            for child in children.values():
                _prepare_schema(
                    child,
                    in_all_of=in_all_of and keyword == "dependentSchemas",
                    close_objects=close_objects,
                )
    for keyword in _SCHEMA_SINGLE:
        if keyword in schema:
            _prepare_schema(
                schema[keyword],
                in_all_of=in_all_of and keyword in ("not", "if", "then", "else"),
                close_objects=close_objects and keyword not in ("not", "if"),
            )
    for keyword in _SCHEMA_LISTS:
        if keyword in schema:
            children = schema[keyword]
            if not isinstance(children, list):
                raise _PreparationError(SchemaJobFailure.LIST)
            for child in children:
                _prepare_schema(
                    child,
                    in_all_of=keyword == "allOf" or (in_all_of and keyword != "prefixItems"),
                    close_objects=close_objects,
                )
    if declares_object and close_objects:
        schema["additionalProperties"] = False


def _check_pattern(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error:
        raise _PreparationError(SchemaJobFailure.PATTERN) from None
