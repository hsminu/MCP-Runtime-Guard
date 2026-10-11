"""기술설계서 4.2절의 신뢰 설정 기반 출처 분류.

순수 lexical 구조만 검사한다. 경로 해석·파일 접근·symlink 검사는 C의 안전 경로
구현을 사용해야 한다. 설정 오류의 SourceConfigError/ValidationError는 Catalog의
생성 경계에서 CatalogConfigError로 번역하며 입력 원문을 오류 문구에 넣지 않는다.
"""

from dataclasses import dataclass
from unicodedata import category

from pydantic import field_validator

from guard_app.domain.enums import Label
from guard_app.domain.models import Frozen, NonEmptyString, StrictLabel


class SourceConfigError(ValueError):
    """SourceClassifier 생성 입력의 잘못된 형식. 메시지는 상수만 사용한다."""


def _has_control(value: str) -> bool:
    return any(category(char) in {"Cc", "Cf", "Cs"} for char in value)


def valid_server_id(server_id: object) -> bool:
    """네임스페이스를 유일하게 나눌 서버 ID인지 검사한다. 정규화하지 않는다."""
    return (
        isinstance(server_id, str)
        and type(server_id) is str
        and bool(server_id)
        and "__" not in server_id
        and not server_id.endswith("_")
        and not _has_control(server_id)
    )


def _valid_tool_name(tool_name: object) -> bool:
    return (
        isinstance(tool_name, str)
        and type(tool_name) is str
        and bool(tool_name)
        and not _has_control(tool_name)
    )


def _valid_segment(segment: str) -> bool:
    return (
        bool(segment)
        and segment not in {".", ".."}
        and "/" not in segment
        and "\\" not in segment
        and not _has_control(segment)
    )


def canonical_resource_parts(resource_id: object) -> tuple[str, ...] | None:
    """빈 도구 단위 ID는 (), canonical 상대 POSIX ID는 세그먼트를 반환한다.

    absolute·dot·중복 구분자·backslash·제어 문자·surrogate·비문자열은 None이다.
    공백 제거, 점 축약, 유니코드 정규화, 실제 파일 접근은 하지 않는다.
    """
    if not isinstance(resource_id, str) or type(resource_id) is not str:
        return None
    if resource_id == "":
        return ()
    parts = tuple(resource_id.split("/"))
    return parts if all(_valid_segment(part) for part in parts) else None


class SourceRule(Frozen):
    """운영자가 설정한 서버·도구·세그먼트 접두어·등급의 불변 규칙."""

    server_id: NonEmptyString
    tool_name: NonEmptyString
    prefix: tuple[str, ...]
    label: StrictLabel

    @field_validator("server_id")
    @classmethod
    def validate_server(cls, server_id: str) -> str:
        if not valid_server_id(server_id):
            raise ValueError("출처 규칙의 서버 ID가 유효하지 않다")
        return server_id

    @field_validator("tool_name")
    @classmethod
    def validate_tool(cls, tool_name: str) -> str:
        if not _valid_tool_name(tool_name):
            raise ValueError("출처 규칙의 도구 이름이 유효하지 않다")
        return tool_name

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, prefix: tuple[str, ...]) -> tuple[str, ...]:
        if not all(_valid_segment(segment) for segment in prefix):
            raise ValueError("출처 규칙의 접두어 세그먼트가 유효하지 않다")
        return prefix


@dataclass(frozen=True, slots=True)
class SourceClassifier:
    """불변 설정 사본으로만 분류한다. 실제·예상 자원 ID 대조는 호출자가 한다."""

    rules: tuple[SourceRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.rules, tuple) or any(
            not isinstance(rule, SourceRule) for rule in self.rules
        ):
            raise SourceConfigError("출처 규칙은 검증된 SourceRule의 tuple이어야 한다")
        snapshot = tuple(SourceRule.model_validate(rule) for rule in self.rules)
        object.__setattr__(self, "rules", snapshot)

    def classify(self, server_id: str, tool_name: str, resource_id: str) -> Label | None:
        """일치 규칙의 최고 등급, 불명·미등록은 None. 출력의 label을 입력받지 않는다."""
        if not valid_server_id(server_id) or not _valid_tool_name(tool_name):
            return None
        parts = canonical_resource_parts(resource_id)
        if parts is None:
            return None
        matched = (
            rule.label
            for rule in self.rules
            if rule.server_id == server_id
            and rule.tool_name == tool_name
            and parts[: len(rule.prefix)] == rule.prefix
        )
        return max(matched, default=None)
