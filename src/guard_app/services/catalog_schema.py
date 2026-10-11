"""등록 원본을 보존하면서 인자를 고정 Draft의 닫힌 사본으로 검증한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason

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


class CatalogConfigError(ValueError):
    """잘못된 신뢰 설정. 호출 인자·등록 metadata 원문을 공개하지 않는다."""


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ArgumentSchema:
    """MVP 고정 스키마. JSON Schema의 모든 형식을 지원하지 않는다."""

    _schema: FrozenJsonObject
    _validator: Draft202012Validator

    def __init__(self, registered_schema: FrozenJsonObject) -> None:
        original = registered_schema.to_dict()
        if original.get("type") != "object":
            raise CatalogConfigError("도구 인자의 루트 스키마는 object여야 한다")
        try:
            Draft202012Validator.check_schema(original)
        except (SchemaError, re.error, Unresolvable):
            raise CatalogConfigError("유효하지 않은 등록 인자 스키마다") from None
        working = registered_schema.to_dict()
        _prepare_schema(working)
        # oneOf 등은 사본을 더 엄격하게 바꿔도 전체 조건이 완화될 수 있다.
        # 원본 전체와 AND하여 등록본의 어떤 거부도 허용으로 바뀌지 않게 한다.
        working["allOf"] = [*cast("list[object]", working.get("allOf", [])), original]
        try:
            frozen = FrozenJsonObject(working)
        except ValueError:
            raise CatalogConfigError("검증 스키마가 지원하는 JSON 구조 범위를 초과했다") from None
        object.__setattr__(self, "_schema", frozen)
        object.__setattr__(
            self,
            "_validator",
            Draft202012Validator(
                working, format_checker=FormatChecker(formats=_FORMATS), registry=Registry()
            ),
        )

    @property
    def schema(self) -> FrozenJsonObject:
        """모델에 노출할 등록 원본과 분리된 불변 사본."""
        return self._schema

    def validate(self, arguments: Arguments) -> None:
        """스키마 예외 본문을 고정 reason_code로 번역한다."""
        try:
            self._validator.validate(arguments.to_dict())
        except ValidationError:
            raise ResolutionError(Reason.INVALID_ARGUMENTS) from None
        except (SchemaError, re.error, Unresolvable):
            raise CatalogConfigError("등록 스키마의 검증을 완료할 수 없다") from None

    def __repr__(self) -> str:
        return "ArgumentSchema(<redacted>)"


def _prepare_schema(value: object, *, in_all_of: bool = False, close_objects: bool = True) -> None:
    """schema 위치만 순회한다. const/default/examples 안의 자료는 건드리지 않는다."""
    if type(value) is bool:
        return
    if not isinstance(value, dict):
        raise CatalogConfigError("유효하지 않은 스키마 구조다")
    schema = cast("dict[str, object]", value)
    if any(key in schema for key in ("$ref", "$dynamicRef", "$recursiveRef")):
        raise CatalogConfigError("MVP는 참조 스키마를 지원하지 않는다")
    if schema.get("$schema", _DRAFT) not in (_DRAFT, f"{_DRAFT}#"):
        raise CatalogConfigError("MVP는 Draft 2020-12 스키마만 지원한다")
    if "format" in schema and schema["format"] not in _FORMATS:
        raise CatalogConfigError("지원하지 않는 스키마 format이다")

    kind = schema.get("type")
    declares_object = (
        kind == "object"
        or (isinstance(kind, list) and "object" in kind)
        or "properties" in schema
        or "patternProperties" in schema
    )
    if declares_object and in_all_of:
        raise CatalogConfigError("allOf에 분산된 객체 속성 선언은 지원하지 않는다")

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
                raise CatalogConfigError("유효하지 않은 스키마 속성 목록이다")
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
                raise CatalogConfigError("유효하지 않은 스키마 조합 목록이다")
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
        raise CatalogConfigError("유효하지 않은 스키마 정규식이다") from None
