"""Cisco 개별 분석기의 도구 정의 입력 어댑터(I6·I9, T17).

정의 전체를 보존한다. SDK의 직접 인자 추출은 검사 범위를 보장하지 않으므로
LLM에는 전체 JSON을 description에 넣은 별도 봉투를 전달한다.
이 모듈의 SDK findings는 내부 결과다. ScanReport 번역·완료 집계·등록 판단은
별도 연결 작업이며, 빈 findings를 검사 완료나 활성화 허용으로 해석하지 않는다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast, runtime_checkable


@runtime_checkable
class DefinitionObject(Protocol):
    """B FrozenJsonObject.to_dict를 변경 없이 사용하는 구조적 인터페이스."""

    def to_dict(self) -> dict[str, object]: ...


type ToolDefinition = dict[str, object] | DefinitionObject


class DefinitionInputError(ValueError):
    """도구 정의 원문을 포함하지 않는 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class PreparedToolDefinition:
    """JSON 사본을 메모리에 보관한다. 기본 repr에는 원문을 노출하지 않는다."""

    definition_json: str = field(repr=False)

    def llm_content(self) -> str:
        """SDK 보호 구간에 전체 정의를 넣고 직접 인자 추출을 피한다."""
        return _json_text({"description": self.definition_json})


class DefinitionAnalyzer[FindingT](Protocol):
    """Cisco YaraAnalyzer/LLMAnalyzer의 공개 analyze 호출 형태."""

    async def analyze(
        self, content: str, context: dict[str, object] | None = None
    ) -> list[FindingT]: ...


class CiscoMetadataAdapter[FindingT]:
    """주입받은 분석기에 누락 없는 입력을 전달하는 C 내부 어댑터.

    크기 상한은 호출자가 명시한다. 공통 제한값이나 필수 분석기 정책은 여기서
    확정하지 않는다. SDK 예외는 성공 결과로 바꾸지 않고 호출 경계로 전달한다.
    """

    def __init__(
        self,
        analyzer: DefinitionAnalyzer[FindingT],
        *,
        input_format: Literal["yara", "llm"],
        max_definition_bytes: int,
    ) -> None:
        if input_format not in ("yara", "llm"):
            raise DefinitionInputError("SCAN_METADATA_FORMAT_INVALID")
        _check_limit(max_definition_bytes)
        self._analyzer = analyzer
        self._input_format = input_format
        self._max_definition_bytes = max_definition_bytes

    async def analyze_tool_definition(self, definition: ToolDefinition) -> list[FindingT]:
        prepared = prepare_tool_definition(
            definition, max_definition_bytes=self._max_definition_bytes
        )
        content = (
            prepared.llm_content() if self._input_format == "llm" else prepared.definition_json
        )
        # 원래 도구 이름은 JSON 안에 보존한다. SDK의 로그용 context에는
        # 공격자가 지정한 이름 대신 고정 식별자만 전달한다(I9).
        return await self._analyzer.analyze(
            content, context={"tool_name": "tool_definition", "content_type": "tool_definition"}
        )


def prepare_tool_definition(
    definition: ToolDefinition, *, max_definition_bytes: int
) -> PreparedToolDefinition:
    """도구 정의 전체를 직렬화한다. 생략·요약·문자열 정규화·절단을 하지 않는다.

    입력은 tools/list의 도구 객체 한 개다. 실행 인자·결과·연결 설정을 합치지
    않는다. 추가 정의 필드도 그대로 보존하며 알 수 없는 JSON 값은 실패한다.
    """
    _check_limit(max_definition_bytes)
    if isinstance(definition, DefinitionObject):
        value = definition.to_dict()
    elif type(definition) is dict:
        value = definition
    else:
        raise DefinitionInputError("SCAN_METADATA_INVALID")

    if type(value) is not dict:
        raise DefinitionInputError("SCAN_METADATA_INVALID")
    name = value.get("name")
    if type(name) is not str or not name.strip():
        raise DefinitionInputError("SCAN_METADATA_INVALID")
    if type(value.get("inputSchema")) is not dict:
        raise DefinitionInputError("SCAN_METADATA_INVALID")
    if value.get("description") is not None and type(value["description"]) is not str:
        raise DefinitionInputError("SCAN_METADATA_INVALID")
    if value.get("annotations") is not None and type(value["annotations"]) is not dict:
        raise DefinitionInputError("SCAN_METADATA_INVALID")

    try:
        _validate_json(value, active=set())
        text = _json_text(value)
        byte_count = len(text.encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise DefinitionInputError("SCAN_METADATA_INVALID") from None
    if byte_count > max_definition_bytes:
        raise DefinitionInputError("SCAN_METADATA_TOO_LARGE")
    return PreparedToolDefinition(text)


def _check_limit(limit: int) -> None:
    if type(limit) is not int or limit <= 0:
        raise DefinitionInputError("SCAN_METADATA_LIMIT_INVALID")


def _json_text(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _validate_json(value: object, *, active: set[int]) -> None:
    """키 변환 등으로 내용이 바뀌는 JSON 값을 거부한다. 원문은 공개하지 않는다."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list) or id(value) in active:
        raise DefinitionInputError("SCAN_METADATA_INVALID")
    active.add(id(value))
    try:
        if isinstance(value, dict):
            for key, child in value.items():
                if type(key) is not str:
                    raise DefinitionInputError("SCAN_METADATA_INVALID")
                _validate_json(child, active=active)
        else:
            for child in cast(list[object], value):
                _validate_json(child, active=active)
    finally:
        active.remove(id(value))
