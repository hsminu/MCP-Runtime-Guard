"""등록 원본을 보존하면서 인자를 고정 Draft의 닫힌 사본으로 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services.schema_jobs import SchemaJobFailure, SchemaJobResult
from guard_app.services.schema_jobs import prepare as prepare_schema
from guard_app.services.schema_jobs import validate as validate_schema


class CatalogConfigError(ValueError):
    """잘못된 신뢰 설정. 호출 인자·등록 metadata 원문을 공개하지 않는다."""


_PREPARATION_MESSAGES = MappingProxyType(
    {
        SchemaJobFailure.ROOT: "도구 인자의 루트 스키마는 object여야 한다",
        SchemaJobFailure.INVALID: "유효하지 않은 등록 인자 스키마다",
        SchemaJobFailure.STRUCTURE: "유효하지 않은 스키마 구조다",
        SchemaJobFailure.REFERENCE: "MVP는 참조 스키마를 지원하지 않는다",
        SchemaJobFailure.DRAFT: "MVP는 Draft 2020-12 스키마만 지원한다",
        SchemaJobFailure.FORMAT: "지원하지 않는 스키마 format이다",
        SchemaJobFailure.ALLOF_OBJECT: "allOf에 분산된 객체 속성 선언은 지원하지 않는다",
        SchemaJobFailure.MAP: "유효하지 않은 스키마 속성 목록이다",
        SchemaJobFailure.LIST: "유효하지 않은 스키마 조합 목록이다",
        SchemaJobFailure.PATTERN: "유효하지 않은 스키마 정규식이다",
        SchemaJobFailure.DEPTH: "검증 스키마가 지원하는 JSON 구조 범위를 초과했다",
    }
)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ArgumentSchema:
    """MVP 고정 스키마. JSON Schema의 모든 형식을 지원하지 않는다."""

    _schema: FrozenJsonObject

    def __init__(self, registered_schema: FrozenJsonObject) -> None:
        result = prepare_schema(registered_schema)
        if isinstance(result, SchemaJobFailure):
            raise CatalogConfigError(_PREPARATION_MESSAGES[result]) from None
        object.__setattr__(self, "_schema", result.schema)

    @property
    def schema(self) -> FrozenJsonObject:
        """모델에 노출할 등록 원본과 분리된 불변 사본."""
        return self._schema

    def validate(self, arguments: Arguments) -> None:
        """스키마 예외 본문을 고정 reason_code로 번역한다."""
        result = validate_schema(self._schema, arguments)
        if result is SchemaJobResult.INVALID_ARGUMENTS:
            raise ResolutionError(Reason.INVALID_ARGUMENTS) from None
        if result is SchemaJobResult.SCHEMA_INVALID:
            raise CatalogConfigError("등록 스키마의 검증을 완료할 수 없다") from None

    def __repr__(self) -> str:
        return "ArgumentSchema(<redacted>)"
