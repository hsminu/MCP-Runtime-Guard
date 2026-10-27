"""8.1절 승인 해시와 10.1절 JCS 도구 지문. 원문은 메모리 바이트에만 둔다."""

from __future__ import annotations

import hashlib
import json
import math
from typing import cast

from guard_app.domain.json_types import FrozenJsonObject


class CanonicalJsonError(ValueError):
    """원문을 노출하지 않는 승인 직렬화 실패."""


def canonical_json(value: object) -> bytes:
    """정렬된 UTF-8 JSON. float·비 JSON 타입·순환·surrogate를 거부한다.

    유니코드 NFC 변환을 하지 않는다. 고정 DTO의 불변 JSON 객체는 독립된
    dict/list 사본으로 직렬화하지만 일반 tuple·mapping은 받지 않는다.
    """
    copied = _copy_json(value, active=set(), depth=0)
    try:
        return json.dumps(
            copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise CanonicalJsonError("승인 JSON을 직렬화할 수 없다") from None


def digest(data: bytes) -> str:
    """정확한 bytes 사본의 SHA-256 소문자 64자리만 반환한다."""
    if type(data) is not bytes:
        raise CanonicalJsonError("해시 입력은 bytes여야 한다")
    return hashlib.sha256(data).hexdigest()


def json_digest(value: object) -> str:
    return digest(canonical_json(value))


class JcsCanonicalizationError(ValueError):
    """입력 원문을 노출하지 않는 JCS 직렬화 실패."""


def jcs_canonicalize(value: object) -> bytes:
    """RFC 8785의 UTF-8 바이트. 승인용 canonical_json과 별개다.

    정확한 dict/list/str/int/float/bool/None만 받으며 Python int는
    ±(2**53-1) 안이어야 한다. 유한 binary64 float는 그보다 커도 받는다.
    UTF-16 키 정렬, 배열 순서, 최소 문자열 이스케이프를 보존하고 NFC
    변환은 하지 않는다. 순환·surrogate·비유한 수·비 JSON 타입과
    인터프리터 재귀 한계 초과는 원문 없는 전용 오류로 거부한다.
    """
    chunks: list[str] = []
    try:
        _write_jcs(value, chunks=chunks, active=set())
    except RecursionError:
        raise JcsCanonicalizationError("JCS JSON 중첩 깊이가 제한을 초과했다") from None
    return "".join(chunks).encode("utf-8")


def tool_fingerprint(tool: object) -> str:
    """SDK가 재구성한 Tool 정의 dict 전체의 JCS SHA-256 소문자 지문.

    호출자는 tools/list의 SDK Tool에
    `tool.model_dump(mode="json", by_alias=True, exclude_unset=True)`를 적용한
    전체 dict를 전달한다. 원본 수신 JSON은 입력으로 쓰지 않는다.
    C Registry 등록과 A 연결 직후 대조는 같은 변환을 사용해야 한다.
    SDK 버전은 lock으로 고정하며, 버전 변경으로 변환 결과가 달라지면
    모든 서버가 재등록 대기가 된다(fail-closed). C4.12에서 예시 지문을 고정한다.
    파서의 중복 키·비표준 NaN 토큰 허용 설정도 호출자가 확인해야 한다.
    """
    if type(tool) is not dict:
        raise JcsCanonicalizationError("도구 지문 입력은 JSON 객체여야 한다")
    return digest(jcs_canonicalize(tool))


def _write_jcs(value: object, *, chunks: list[str], active: set[int]) -> None:
    if value is None:
        chunks.append("null")
    elif type(value) is bool:
        chunks.append("true" if value else "false")
    elif type(value) is str:
        chunks.append(_jcs_string(value))
    elif type(value) is int:
        if not -(2**53 - 1) <= value <= 2**53 - 1:
            raise JcsCanonicalizationError("JCS JSON 정수가 안전 범위를 벗어났다")
        chunks.append(str(value))
    elif type(value) is float:
        chunks.append(_jcs_float(value))
    elif type(value) in (dict, list):
        _write_jcs_container(value, chunks=chunks, active=active)
    else:
        raise JcsCanonicalizationError("허용하지 않는 JCS JSON 값 타입이다")


def _write_jcs_container(value: object, *, chunks: list[str], active: set[int]) -> None:
    identity = id(value)
    if identity in active:
        raise JcsCanonicalizationError("순환 JCS JSON 값은 허용하지 않는다")
    active.add(identity)
    try:
        if type(value) is list:
            chunks.append("[")
            for index, child in enumerate(cast(list[object], value)):
                if index:
                    chunks.append(",")
                _write_jcs(child, chunks=chunks, active=active)
            chunks.append("]")
        else:
            keyed: list[tuple[bytes, str, object]] = []
            for key, child in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise JcsCanonicalizationError("JCS JSON 객체 키는 문자열이어야 한다")
                quoted = _jcs_string(key)
                # Big-endian bytes compare unsigned UTF-16 code units without a BOM.
                keyed.append((key.encode("utf-16-be"), quoted, child))
            chunks.append("{")
            for index, (_, quoted, child) in enumerate(sorted(keyed, key=lambda item: item[0])):
                if index:
                    chunks.append(",")
                chunks.extend((quoted, ":"))
                _write_jcs(child, chunks=chunks, active=active)
            chunks.append("}")
    finally:
        active.remove(identity)


def _jcs_string(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise JcsCanonicalizationError("JCS JSON 문자열은 UTF-8이어야 한다")
    # For a scalar-only str, ensure_ascii=False has JSON.stringify's minimal escaping.
    return json.dumps(value, ensure_ascii=False)


def _jcs_float(value: float) -> str:
    if not math.isfinite(value):
        raise JcsCanonicalizationError("JCS JSON 숫자는 유한해야 한다")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    # Python's shortest round-trip digits use closest-value, ties-to-even selection.
    # Keep those digits; apply ECMA-262 Number::toString's decimal placement rules.
    mantissa, _, exponent = repr(abs(value)).partition("e")
    integer, _, fraction = mantissa.partition(".")
    digits = integer + fraction
    leading_zeros = len(digits) - len(digits.lstrip("0"))
    position = len(integer) + (int(exponent) if exponent else 0) - leading_zeros
    digits = digits.lstrip("0").rstrip("0")
    if 0 < position <= 21:
        if position >= len(digits):
            return sign + digits + "0" * (position - len(digits))
        return sign + digits[:position] + "." + digits[position:]
    if -6 < position <= 0:
        return sign + "0." + "0" * -position + digits
    coefficient = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{coefficient}e{position - 1:+d}"


def _copy_json(value: object, *, active: set[int], depth: int) -> object:
    if depth > 64:
        raise CanonicalJsonError("승인 JSON 중첩 깊이가 제한을 초과했다")
    if value is None or type(value) in (str, int, bool):
        if type(value) is str:
            _check_string(value)
        return value
    if isinstance(value, FrozenJsonObject):
        value = value.to_dict()
    if type(value) not in (dict, list):
        raise CanonicalJsonError("허용하지 않는 승인 JSON 값 타입이다")
    identity = id(value)
    if identity in active:
        raise CanonicalJsonError("순환 승인 JSON 값은 허용하지 않는다")
    active.add(identity)
    try:
        if type(value) is dict:
            copied: dict[str, object] = {}
            for key, child in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise CanonicalJsonError("승인 JSON 객체 키는 문자열이어야 한다")
                _check_string(key)
                copied[key] = _copy_json(child, active=active, depth=depth + 1)
            return copied
        return [
            _copy_json(child, active=active, depth=depth + 1) for child in cast(list[object], value)
        ]
    finally:
        active.remove(identity)


def _check_string(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise CanonicalJsonError("승인 JSON 문자열은 UTF-8이어야 한다")
