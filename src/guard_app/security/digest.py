"""8.1절의 정규화 JSON·SHA-256. 원문은 반환된 메모리 바이트에만 둔다."""

from __future__ import annotations

import hashlib
import json
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
