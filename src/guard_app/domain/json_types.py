"""인자와 스키마를 원본과 분리해 깊게 고정하는 JSON 값 타입."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Self, cast

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class FrozenJsonObject(Mapping[str, "JsonValue"]):
    """객체는 읽기 전용 mapping, 배열은 tuple로 보관한다. 원문 repr은 숨긴다."""

    _data: Mapping[str, JsonValue]
    _allow_floats: ClassVar[bool] = True

    def __init__(self, value: object) -> None:
        if isinstance(value, FrozenJsonObject):
            value = value.to_dict()
        if type(value) is not dict:
            raise ValueError("JSON 객체가 필요하다")
        frozen = _freeze(value, allow_floats=self._allow_floats, active=set(), depth=0)
        if not isinstance(frozen, FrozenJsonObject):
            raise ValueError("JSON 객체가 필요하다")
        object.__setattr__(self, "_data", frozen._data)

    @classmethod
    def _from_data(cls, data: dict[str, JsonValue]) -> Self:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_data", MappingProxyType(data))
        return instance

    def __getitem__(self, key: str) -> JsonValue:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def to_dict(self) -> dict[str, object]:
        """어댑터·직렬화에 쓸 독립된 가변 사본을 반환한다."""
        return {key: _thaw(value) for key, value in self._data.items()}

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Pydantic 검증과 JSON 직렬화에 불변 타입을 연결한다."""
        return core_schema.no_info_plain_validator_function(
            cls,
            json_schema_input_schema=core_schema.dict_schema(
                keys_schema=core_schema.str_schema(strict=True),
                values_schema=core_schema.any_schema(),
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize),
        )


class Arguments(FrozenJsonObject):
    """8.1절의 MVP 인자 타입. float·NaN·비 JSON 값을 허용하지 않는다."""

    __slots__ = ()
    _allow_floats = False


type JsonValue = str | int | bool | float | None | FrozenJsonObject | tuple[JsonValue, ...]


def _freeze(value: object, *, allow_floats: bool, active: set[int], depth: int) -> JsonValue:
    if depth > 64:
        raise ValueError("JSON 중첩 깊이가 제한을 초과했다")
    if value is None or type(value) in (str, bool, int):
        if isinstance(value, str):
            _validate_string(value)
        return cast("str | int | bool | None", value)
    if type(value) is float:
        number = value
        if not allow_floats or not math.isfinite(number):
            raise ValueError("허용하지 않는 JSON 숫자다")
        return number
    if isinstance(value, FrozenJsonObject):
        value = value.to_dict()
    if type(value) not in (dict, list):
        raise ValueError("허용하지 않는 JSON 값 타입이다")
    identity = id(value)
    if identity in active:
        raise ValueError("순환 JSON 값은 허용하지 않는다")
    active.add(identity)
    try:
        if type(value) is dict:
            frozen: dict[str, JsonValue] = {}
            for key, child in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise ValueError("JSON 객체 키는 문자열이어야 한다")
                _validate_string(key)
                frozen[key] = _freeze(
                    child, allow_floats=allow_floats, active=active, depth=depth + 1
                )
            return FrozenJsonObject._from_data(frozen)
        return tuple(
            _freeze(child, allow_floats=allow_floats, active=active, depth=depth + 1)
            for child in cast(list[object], value)
        )
    finally:
        active.remove(identity)


def _thaw(value: JsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _serialize(value: FrozenJsonObject) -> dict[str, object]:
    return value.to_dict()


def _validate_string(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("UTF-8로 표현할 수 없는 문자열은 허용하지 않는다")
