"""SCHEMA-01 합의 전 제안: 등록 스키마 준비와 인자 검증의 순수 연산.

정규식 등 실제 검증은 동기 연산이다. 제한된 프로세스 실행은 C 연결 이후에 맡는다.

등록 스키마의 정규식은 MCP 서버가 준 값이고 인자는 모델이 정한다. 같은 프로세스에서 시간
제한 없이 실행하면 백트래킹이 폭발하는 정규식(`^(a+)+$` 등)이 Guard를 멈춘다(ReDoS,
CWE-1333, 2026-10-30 C 점검: 27자 인자 2초, 두 글자마다 4배). 그래서 검증은 서버 정규식을
실행하지 않는다.

- `pattern`: 검증하지 않는다. 모델에 보이는 스키마에는 그대로 남고, 값 형식은 MCP 서버가
  검사한다. Guard 판정(목적지 정확 일치, 자원 해석)은 이 값에 기대지 않는다.
- `patternProperties`: 지원하지 않는다(`PATTERN_PROPERTIES`). 속성 이름 정규식은 추가 속성
  판정(`additionalProperties`·`unevaluatedProperties`)에서도 실행되기 때문이다.
- 스키마의 정규식 컴파일 검사는 그대로 둔다(컴파일은 인자를 받지 않는다).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import SchemaError, UnknownType, ValidationError
from pydantic import ValidationError as ModelValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.schema_jobs import (
    INVALID_JOB_ID,
    MAX_JOB_BYTES,
    MAX_JOB_DEPTH,
    MAX_JOB_ELEMENTS,
    SchemaJobRequest,
    SchemaJobResponse,
    _jcs_size,
)
from guard_app.security.digest import JcsCanonicalizationError, jcs_canonicalize

_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_CHECK_PREPARED_SCHEMA = Draft202012Validator.check_schema
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
    PATTERN_PROPERTIES = "PATTERN_PROPERTIES"
    DEPTH = "DEPTH"


def _skip_server_pattern(*_args: object) -> None:
    """서버가 준 `pattern`은 인자에 실행하지 않는다(모듈 설명, ReDoS)."""


# 준비 단계가 `patternProperties`를 거부하므로, 남은 정규식 실행 지점은 `pattern` 하나다.
_ArgumentValidator: type[Draft202012Validator] = validators.extend(  # type: ignore[no-untyped-call]
    Draft202012Validator, {"pattern": _skip_server_pattern}
)


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
    try:
        document = prepared.to_dict()
        _CHECK_PREPARED_SCHEMA(document)
        _check_prepared_supported(document)
        validator = _ArgumentValidator(
            document, format_checker=FormatChecker(formats=_FORMATS), registry=Registry()
        )
        validator.validate(arguments.to_dict())
    except ValidationError:
        return SchemaJobResult.INVALID_ARGUMENTS
    except (
        _PreparationError,
        UnknownType,
        SchemaError,
        Unresolvable,
        re.error,
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
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
    if "patternProperties" in schema:
        raise _PreparationError(SchemaJobFailure.PATTERN_PROPERTIES)

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


def _check_prepared_supported(value: object) -> None:
    """VALIDATE의 변조된 준비본도 기존 지원 범위의 schema 위치만 검사한다."""
    if type(value) is bool:
        return
    if type(value) is not dict:
        raise _PreparationError(SchemaJobFailure.STRUCTURE)
    schema = cast("dict[str, object]", value)
    if any(key in schema for key in ("$ref", "$dynamicRef", "$recursiveRef")):
        raise _PreparationError(SchemaJobFailure.REFERENCE)
    if schema.get("$schema", _DRAFT) not in (_DRAFT, f"{_DRAFT}#"):
        raise _PreparationError(SchemaJobFailure.DRAFT)
    if "format" in schema and schema["format"] not in _FORMATS:
        raise _PreparationError(SchemaJobFailure.FORMAT)
    if isinstance(schema.get("pattern"), str):
        _check_pattern(cast("str", schema["pattern"]))
    if "patternProperties" in schema:
        raise _PreparationError(SchemaJobFailure.PATTERN_PROPERTIES)
    for keyword in _SCHEMA_MAPS:
        if keyword in schema:
            children = cast("dict[str, object]", schema[keyword])
            for child in children.values():
                _check_prepared_supported(child)
    for keyword in _SCHEMA_SINGLE:
        if keyword in schema:
            _check_prepared_supported(schema[keyword])
    for keyword in _SCHEMA_LISTS:
        if keyword in schema:
            for child in cast("list[object]", schema[keyword]):
                _check_prepared_supported(child)


class SchemaJobCodecError(ValueError):
    """입력과 원래 예외를 담지 않는 스키마 작업 메시지 오류."""


def _bad_message() -> SchemaJobCodecError:
    return SchemaJobCodecError("스키마 작업 메시지가 유효하지 않다")


def _check_job_tree(value: object) -> None:
    """envelope 루트를 깊이 0으로 세고 객체 키가 아닌 값만 요소로 센다."""
    pending = [(value, 0)]
    count = 0
    while pending:
        current, depth = pending.pop()
        count += 1
        if count > MAX_JOB_ELEMENTS or depth > MAX_JOB_DEPTH:
            raise _bad_message()
        if type(current) is dict or isinstance(current, FrozenJsonObject):
            if count + len(pending) + len(current) > MAX_JOB_ELEMENTS:
                raise _bad_message()
            pending.extend((child, depth + 1) for child in current.values())
        elif type(current) is list or isinstance(current, tuple):
            if count + len(pending) + len(current) > MAX_JOB_ELEMENTS:
                raise _bad_message()
            pending.extend((child, depth + 1) for child in current)


def _check_json_nesting(document: str) -> None:
    """파서 호출 전에 문자열 안 괄호를 제외하고 중첩 컨테이너 한도를 검사한다."""
    depth = 0
    quoted = False
    escaped = False
    for char in document:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JOB_DEPTH + 1:
                raise _bad_message()
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise _bad_message()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _bad_message()
        result[key] = value
    return result


def _parse_integer(value: str) -> int | float:
    """큰 binary64 float의 JCS 정수 표기를 복원한다. 이후 정규 바이트로 확인한다."""
    number = int(value)
    if -(2**53 - 1) <= number <= 2**53 - 1:
        return number
    candidate = float(value)
    if not math.isfinite(candidate):
        raise _bad_message()
    return candidate


def _reject_constant(_value: str) -> object:
    raise _bad_message()


def _encode_model(value: SchemaJobRequest | SchemaJobResponse) -> bytes:
    document = value.model_dump(mode="json", exclude_none=True, by_alias=True)
    _check_job_tree(document)
    try:
        encoded = jcs_canonicalize(document)
    except (JcsCanonicalizationError, TypeError, ValueError, RecursionError):
        raise _bad_message() from None
    if len(encoded) > MAX_JOB_BYTES:
        raise _bad_message()
    return encoded


def _preflight_model(value: SchemaJobRequest | SchemaJobResponse) -> None:
    """Frozen 사본의 한도를 검사한 뒤에만 DTO 재검증과 JSON 사본 생성을 허용한다."""
    if type(value) is SchemaJobRequest:
        document: dict[str, object] = {
            "kind": value.kind,
            "job_id": value.job_id,
            "schema": value.schema_data,
        }
        if value.arguments is not None:
            document["arguments"] = value.arguments
    elif type(value) is SchemaJobResponse:
        document = {"job_id": value.job_id, "code": value.code}
        if value.prepared is not None:
            document["prepared"] = value.prepared
    else:
        raise _bad_message()
    _check_job_tree(document)
    if _jcs_size(document, MAX_JOB_BYTES) > MAX_JOB_BYTES:
        raise _bad_message()


def encode_request(request: SchemaJobRequest) -> bytes:
    """요청을 제한 검사 후 JCS 바이트로 직렬화한다."""
    try:
        _preflight_model(request)
        return _encode_model(SchemaJobRequest.model_validate(request))
    except (
        ModelValidationError,
        SchemaJobCodecError,
        TypeError,
        ValueError,
        AttributeError,
        RecursionError,
    ):
        raise _bad_message() from None


def encode_response(response: SchemaJobResponse) -> bytes:
    """응답에도 요청과 같은 제한을 적용한다."""
    try:
        _preflight_model(response)
        return _encode_model(SchemaJobResponse.model_validate(response))
    except (
        ModelValidationError,
        SchemaJobCodecError,
        TypeError,
        ValueError,
        AttributeError,
        RecursionError,
    ):
        raise _bad_message() from None


def _decode_document(data: bytes) -> object:
    if type(data) is not bytes or len(data) > MAX_JOB_BYTES:
        raise _bad_message()
    try:
        document = data.decode("utf-8", errors="strict")
        _check_json_nesting(document)
        value: object = json.loads(
            document,
            object_pairs_hook=_unique_pairs,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
        _check_job_tree(value)
        if jcs_canonicalize(value) != data:
            raise _bad_message()
        return value
    except (
        UnicodeError,
        json.JSONDecodeError,
        JcsCanonicalizationError,
        ValueError,
        RecursionError,
    ):
        raise _bad_message() from None


def decode_request(data: bytes) -> SchemaJobRequest:
    """부모와 worker 모두에서 원문 없는 고정 실패로 요청을 해독한다."""
    try:
        document = _decode_document(data)
        if type(document) is not dict or not set(document).issubset(
            {"kind", "job_id", "schema", "arguments"}
        ):
            raise _bad_message()
        if document.get("kind") == "PREPARE" and "arguments" in document:
            raise _bad_message()
        return SchemaJobRequest.model_validate(document)
    except (ModelValidationError, SchemaJobCodecError, TypeError, ValueError):
        raise _bad_message() from None


def decode_response(data: bytes) -> SchemaJobResponse:
    """부모가 받은 응답도 같은 바이트·요소·깊이 한도로 해독한다."""
    try:
        return SchemaJobResponse.model_validate(_decode_document(data))
    except (ModelValidationError, SchemaJobCodecError, TypeError, ValueError):
        raise _bad_message() from None


def check_job_response(request: SchemaJobRequest, response: SchemaJobResponse) -> SchemaJobResponse:
    """호출자는 결과를 쓰기 전에 요청 ID 및 PREPARE/VALIDATE 형태를 대조한다."""
    try:
        _preflight_model(request)
        _preflight_model(response)
        request = SchemaJobRequest.model_validate(request)
        response = SchemaJobResponse.model_validate(response)
        if request.job_id != response.job_id:
            raise _bad_message()
        if response.code == "OK" and (response.prepared is not None) != (request.kind == "PREPARE"):
            raise _bad_message()
        if request.kind == "PREPARE" and response.code == "INVALID_ARGUMENTS":
            raise _bad_message()
        return response
    except (
        ModelValidationError,
        SchemaJobCodecError,
        TypeError,
        ValueError,
        AttributeError,
        RecursionError,
    ):
        raise _bad_message() from None


def run_schema_job(request_bytes: bytes) -> bytes:
    """제한된 입력을 해독·실행·부호화하며 stdout/stderr에 쓰지 않는다."""
    try:
        request = decode_request(request_bytes)
    except SchemaJobCodecError:
        return encode_response(SchemaJobResponse(job_id=INVALID_JOB_ID, code="SCHEMA_INVALID"))
    try:
        if request.kind == "PREPARE":
            prepared = prepare(request.schema_data)
            if isinstance(prepared, PreparedSchema):
                response = SchemaJobResponse(
                    job_id=request.job_id, code="OK", prepared=prepared.schema
                )
            else:
                response = SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID")
        else:
            if request.arguments is None:
                return encode_response(
                    SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID")
                )
            result = validate(request.schema_data, request.arguments)
            response = SchemaJobResponse(job_id=request.job_id, code=result.value)
    except (
        _PreparationError,
        UnknownType,
        SchemaError,
        Unresolvable,
        re.error,
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ):
        response = SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID")
    try:
        return encode_response(response)
    except SchemaJobCodecError:
        return encode_response(SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID"))
