"""등록 원본을 보존하면서 인자를 고정 Draft의 닫힌 사본으로 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.domain.schema_jobs import (
    MAX_REGISTERED_SCHEMA_BYTES,
    PREPARE_DEADLINE_S,
    SchemaJobRequest,
    SchemaJobResponse,
    SchemaJobRunnerError,
    SchemaRunnerFailure,
    SchemaToolIssueCode,
    _jcs_size,
)
from guard_app.ports import SchemaJobRunner
from guard_app.security.ids import new_id
from guard_app.services.schema_jobs import (
    SchemaJobCodecError,
    SchemaJobFailure,
    SchemaJobResult,
    _preflight_model,
    check_job_response,
    encode_request,
)
from guard_app.services.schema_jobs import prepare as prepare_schema
from guard_app.services.schema_jobs import validate as validate_schema

SCHEMA_JOBS_VERSION = "1"


class SchemaPreparationCache:
    """앱 실행 하나의 지문·작업 버전별 준비 결과. 디스크에 저장하지 않는다.

    성공과 모든 실패를 같은 실행에서 다시 시도하지 않는다. FAILED·TOO_LARGE는
    지문이 바뀌면 새 키로 준비하며 TIMEOUT·UNAVAILABLE은 새 앱 실행의 새 캐시에서
    한 번 다시 시도한다. UNAVAILABLE이면 같은 캐시의 이후 미준비 도구도 호출 없이
    UNAVAILABLE로 기록한다. 동시에 준비해도 캐시가 없는 키만 순차로 처리한다.
    """

    __slots__ = ("_entries", "_lock", "_unavailable")

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], FrozenJsonObject | SchemaToolIssueCode] = {}
        self._lock = asyncio.Lock()
        self._unavailable = False

    def get(self, fingerprint: str) -> FrozenJsonObject | SchemaToolIssueCode | None:
        """현재 작업 의미 버전에 해당하는 불변 결과만 반환한다."""
        return self._entries.get((fingerprint, SCHEMA_JOBS_VERSION))

    def _put(self, fingerprint: str, value: FrozenJsonObject | SchemaToolIssueCode) -> None:
        self._entries[(fingerprint, SCHEMA_JOBS_VERSION)] = value

    def __repr__(self) -> str:
        return "SchemaPreparationCache(<redacted>)"


async def _run_prepare(
    runner: SchemaJobRunner, request: SchemaJobRequest, deadline: float
) -> SchemaJobResponse:
    response = await runner.run(request, deadline=deadline)
    return check_job_response(request, response)


async def prepare_tool_schemas(
    cache: SchemaPreparationCache, runner: SchemaJobRunner, tools: Iterable[ToolSpec]
) -> None:
    """캐시에 없는 등록 도구를 순차 준비한다. 화면 조회에서 호출하지 않는다."""
    async with cache._lock:
        for tool in tools:
            fingerprint = tool.fingerprint
            if cache.get(fingerprint) is not None:
                continue
            if cache._unavailable:
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE)
                continue
            try:
                if _jcs_size(tool.input_schema, MAX_REGISTERED_SCHEMA_BYTES) > (
                    MAX_REGISTERED_SCHEMA_BYTES
                ):
                    cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE)
                    continue
                request = SchemaJobRequest(
                    kind="PREPARE", job_id=new_id("schema"), schema_data=tool.input_schema
                )
            except (TypeError, ValueError, AttributeError, RecursionError):
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_FAILED)
                continue
            try:
                # 검증된 DTO의 codec 오류는 요청 envelope의 크기·요소·깊이 상한이다.
                _preflight_model(request)
            except SchemaJobCodecError:
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE)
                continue
            except (TypeError, ValueError, AttributeError, RecursionError):
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_FAILED)
                continue
            try:
                # 상한과 별도로 JCS에 표현할 수 없는 값은 요청 생성 실패다.
                encode_request(request)
            except SchemaJobCodecError:
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_FAILED)
                continue
            deadline = asyncio.get_running_loop().time() + PREPARE_DEADLINE_S
            (checked,) = await asyncio.gather(
                _run_prepare(runner, request, deadline), return_exceptions=True
            )
            if isinstance(checked, asyncio.CancelledError):
                raise checked
            if isinstance(checked, SchemaJobRunnerError):
                failure_mapping = {
                    SchemaRunnerFailure.TIMEOUT: SchemaToolIssueCode.SCHEMA_PREPARE_TIMEOUT,
                    SchemaRunnerFailure.TOO_LARGE: SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE,
                    SchemaRunnerFailure.UNAVAILABLE: SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE,
                }
                issue = (
                    failure_mapping.get(
                        checked.failure, SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE
                    )
                    if isinstance(checked.failure, SchemaRunnerFailure)
                    else SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE
                )
                cache._put(fingerprint, issue)
                if issue is SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE:
                    cache._unavailable = True
                continue
            if isinstance(checked, BaseException):
                # worker/응답 불명은 원문 없이 모두 실행 불가로 닫는다.
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE)
                cache._unavailable = True
                continue
            if checked.code == "OK" and checked.prepared is not None:
                cache._put(fingerprint, checked.prepared)
            else:
                cache._put(fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_FAILED)


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
        SchemaJobFailure.PATTERN_PROPERTIES: "patternProperties 스키마는 지원하지 않는다",
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
