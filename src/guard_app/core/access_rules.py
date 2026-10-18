"""ACCESS-01 1단계·I2/I6: I/O 없는 정확 일치 접근 규칙과 정규화 표현."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeGuard, cast

from guard_app.domain.models import AccessResult


class AccessRulesError(ValueError):
    """잘못된 규칙을 정상 설정으로 정규화하지 않는 고정 오류."""


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AccessRules:
    """허용·거부 쌍의 독립된 불변 사본. 잘못된 집합은 전체가 무효다.

    목록은 정확한 list/tuple/set/frozenset, 쌍은 정확한 list/tuple로
    받는다. 이름은 비어 있지 않은 UTF-8 문자열이며 변환·정규화하지
    않는다. 중복은 합치고 두 목록의 교집합은 그대로 보존한다.
    무효 집합은 빈 규칙처럼 판정하지 않고 항상 error=True가 된다.
    파일 읽기·신뢰 출처 확인·policy_version 계산은 호출자 책임이다.
    """

    _allow: frozenset[tuple[str, str]]
    _deny: frozenset[tuple[str, str]]
    _valid: bool

    def __init__(self, *, allow: object = (), deny: object = ()) -> None:
        allowed = _freeze_pairs(allow)
        denied = _freeze_pairs(deny)
        valid = allowed is not None and denied is not None
        object.__setattr__(self, "_allow", allowed if valid else frozenset())
        object.__setattr__(self, "_deny", denied if valid else frozenset())
        object.__setattr__(self, "_valid", valid)

    @property
    def is_valid(self) -> bool:
        return self._valid

    def __repr__(self) -> str:
        return "AccessRules(<redacted>)"


def evaluate_access(rules: object, *, server_id: object, tool_name: object) -> AccessResult:
    """정확한 (server_id, tool_name)만 판정하며 기본값은 거부다.

    AccessResult.reason은 고정 문자열이며 Reason enum을 바꾸지 않는다.
    예상하지 못한 예외의 fail_closed 변환은 후속 Guard 연결 경계가 맡는다.
    """
    if type(rules) is not AccessRules or not rules.is_valid:
        return AccessResult(allowed=False, reason="ACCESS_RULES_INVALID", error=True)
    if not _is_name(server_id) or not _is_name(tool_name):
        return AccessResult(allowed=False, reason="ACCESS_REQUEST_INVALID", error=True)
    pair = (server_id, tool_name)
    if pair in rules._deny:
        return AccessResult(allowed=False, reason="ACCESS_RULE_DENY", error=False)
    if pair in rules._allow:
        return AccessResult(allowed=True, reason="ACCESS_RULE_ALLOW", error=False)
    return AccessResult(allowed=False, reason="ACCESS_RULE_MISSING", error=False)


def normalize_access_rules(rules: object) -> dict[str, list[list[str]]]:
    """policy_version 입력용으로 정렬된 JSON 표현의 독립 사본을 반환한다.

    형식은 {"allow": [[server_id, tool_name], ...], "deny": [...]}다.
    각 목록은 Python 문자열 순서로 정렬하며 입력 순서·중복을 제거한다.
    대소문자·Unicode 정규화·두 목록 간 중복은 바꾸지 않는다.
    잘못된 집합을 빈 정상 설정으로 해시하지 않는다.
    """
    if type(rules) is not AccessRules or not rules.is_valid:
        raise AccessRulesError("접근 규칙 형식이 유효하지 않다")
    return {
        "allow": [list(pair) for pair in sorted(rules._allow)],
        "deny": [list(pair) for pair in sorted(rules._deny)],
    }


def _freeze_pairs(value: object) -> frozenset[tuple[str, str]] | None:
    if type(value) not in (list, tuple, set, frozenset):
        return None
    pairs: set[tuple[str, str]] = set()
    for entry in cast(Iterable[object], value):
        if (
            type(entry) not in (list, tuple)
            or len(cast(list[object] | tuple[object, ...], entry)) != 2
        ):
            return None
        server_id, tool_name = cast(tuple[object, object], entry)
        if not _is_name(server_id) or not _is_name(tool_name):
            return None
        pairs.add((server_id, tool_name))
    return frozenset(pairs)


def _is_name(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and bool(value)
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )
