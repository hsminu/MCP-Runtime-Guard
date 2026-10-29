"""SCHEMA-01 합의 전 제안: 검증 작업의 제한된 불변 데이터.

수치는 미측정 잠정값이며 SCHEMA-01 합의와 C 측정 뒤 확정한다.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self, cast

from pydantic import ConfigDict, Field, model_validator

from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Frozen

MAX_JOB_BYTES = 1024 * 1024
MAX_JOB_ELEMENTS = 100_000
MAX_JOB_DEPTH = 64
MAX_REGISTERED_SCHEMA_BYTES = 256 * 1024
INVALID_JOB_ID = "SCHEMA_JOB_INVALID"

JobId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]


class SchemaJobRequest(Frozen):
    """등록 준비 또는 준비본 인자 검증 요청. 원문은 repr에 넣지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["PREPARE", "VALIDATE"]
    job_id: JobId = Field(repr=False)
    schema_data: FrozenJsonObject = Field(alias="schema", repr=False)
    arguments: Arguments | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _preflight_registered_schema(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("kind") == "PREPARE":
            registered = value.get("schema", value.get("schema_data"))
            if isinstance(registered, FrozenJsonObject) or type(registered) is dict:
                try:
                    size = _jcs_size(registered, MAX_REGISTERED_SCHEMA_BYTES)
                except RecursionError:
                    raise ValueError("등록 스키마 구조가 유효하지 않다") from None
                if size > MAX_REGISTERED_SCHEMA_BYTES:
                    raise ValueError("등록 스키마 크기 제한을 초과했다")
        return value

    @model_validator(mode="after")
    def _check_kind_and_size(self) -> Self:
        if self.job_id == INVALID_JOB_ID:
            raise ValueError("예약된 작업 식별자다")
        if (self.kind == "PREPARE") != (self.arguments is None):
            raise ValueError("작업 종류와 인자가 일치하지 않는다")
        if self.kind == "PREPARE" and (
            _jcs_size(self.schema_data, MAX_REGISTERED_SCHEMA_BYTES) > MAX_REGISTERED_SCHEMA_BYTES
        ):
            raise ValueError("등록 스키마 크기 제한을 초과했다")
        return self


class SchemaJobResponse(Frozen):
    """원문 없는 결과와 성공한 PREPARE의 불변 스키마."""

    job_id: JobId = Field(repr=False)
    code: Literal["OK", "INVALID_ARGUMENTS", "SCHEMA_INVALID"]
    prepared: FrozenJsonObject | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        if self.code != "OK" and self.prepared is not None:
            raise ValueError("실패 응답에 스키마를 넣을 수 없다")
        if self.job_id == INVALID_JOB_ID and (
            self.code != "SCHEMA_INVALID" or self.prepared is not None
        ):
            raise ValueError("예약된 식별자는 고정 실패에만 쓴다")
        return self


def _integer_digits(value: int) -> int:
    """Python의 정수 문자열 자리수 제한과 무관하게 10진 자리수를 구한다."""
    number = abs(value)
    if number == 0:
        return 1
    if number.bit_length() > MAX_REGISTERED_SCHEMA_BYTES * 4:
        return MAX_REGISTERED_SCHEMA_BYTES + 1
    digits = ((number.bit_length() - 1) * 30103) // 100_000 + 1
    while number >= 10**digits:
        digits += 1
    while digits > 1 and number < 10 ** (digits - 1):
        digits -= 1
    return digits


def _jcs_number(value: float) -> str:
    """JCS의 binary64 숫자 표기 길이를 구하기 위한 ECMA-262 배치."""
    if not math.isfinite(value):
        raise ValueError("유한한 JSON 숫자가 필요하다")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    mantissa, _, exponent = repr(abs(value)).partition("e")
    integer, _, fraction = mantissa.partition(".")
    digits = integer + fraction
    leading = len(digits) - len(digits.lstrip("0"))
    position = len(integer) + (int(exponent) if exponent else 0) - leading
    digits = digits.lstrip("0").rstrip("0")
    if 0 < position <= 21:
        if position >= len(digits):
            return sign + digits + "0" * (position - len(digits))
        return sign + digits[:position] + "." + digits[position:]
    if -6 < position <= 0:
        return sign + "0." + "0" * -position + digits
    coefficient = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{coefficient}e{position - 1:+d}"


def _string_size(value: str, limit: int | None) -> int:
    # 길이만 계산해 크기가 큰 입력을 JSON 문자열·UTF-8 사본으로 만들지 않는다.
    if limit is not None and len(value) + 2 > limit:
        return limit + 1
    size = 2
    for char in value:
        code = ord(char)
        if char in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
            size += 2
        elif code < 32:
            size += 6
        elif code < 128:
            size += 1
        elif code < 2048:
            size += 2
        elif 0xD800 <= code <= 0xDFFF:
            raise ValueError("허용하지 않는 JSON 문자열이다")
        elif code < 65536:
            size += 3
        else:
            size += 4
        if limit is not None and size > limit:
            return limit + 1
    return size


def _jcs_size(value: object, limit: int | None = None) -> int:
    """키 순서와 무관한 JCS UTF-8 크기. limit을 넘으면 조기 반환한다."""
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        return _integer_digits(value) + int(value < 0)
    if type(value) is float:
        return len(_jcs_number(value))
    if type(value) is str:
        return _string_size(value, limit)
    if isinstance(value, FrozenJsonObject) or type(value) is dict:
        pairs = (
            value.items()
            if isinstance(value, FrozenJsonObject)
            else cast(dict[object, object], value).items()
        )
        size = 2
        for index, (key, child) in enumerate(pairs):
            if type(key) is not str:
                raise ValueError("JSON 객체 키가 유효하지 않다")
            size += int(index > 0) + _string_size(key, limit) + 1
            size += _jcs_size(child, None if limit is None else max(0, limit - size))
            if limit is not None and size > limit:
                return limit + 1
        return size
    if isinstance(value, tuple) or type(value) is list:
        size = 2
        for index, child in enumerate(value):
            size += int(index > 0)
            size += _jcs_size(child, None if limit is None else max(0, limit - size))
            if limit is not None and size > limit:
                return limit + 1
        return size
    raise ValueError("지원하지 않는 JSON 값이다")
