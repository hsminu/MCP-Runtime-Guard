"""C 내부 LLM·YARA 집계(I6·I9, T17·T19). B ScanReport·등록 판정은 별도다.

수집 완료 여부를 추정하지 않고 전달받은 정의 집합만 검사한다. 위험·진단 실패는
경고 자료이며 Guard 판정을 생성하지 않는다. 원문·SDK evidence는 결과에 복사하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, cast

import httpx

from guard_app.adapters.cisco import (
    CiscoMetadataAdapter,
    DefinitionAnalyzer,
    DefinitionInputError,
    PreparedToolDefinition,
    ToolDefinition,
    _check_limit,
    _json_text,
    prepare_tool_definition,
)
from guard_app.adapters.cisco_llm import CiscoFinding, ScannerLLMError, connect_luna_scanner
from guard_app.security.secrets import scanner_sdk_import_environment

DEFAULT_MAX_DEFINITION_BYTES = 65_536
type AnalyzerName = Literal["LLM", "YARA"]
type AnalyzerStatus = Literal["completed", "failed", "not_run"]
type Completion = Literal["completed", "partial", "failed", "not_scanned"]
_NAMES: tuple[AnalyzerName, ...] = ("LLM", "YARA")
_SEVERITIES = ("SAFE", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_ERROR_CODES = frozenset(
    {
        "SCAN_METADATA_INVALID",
        "SCAN_METADATA_TOO_LARGE",
        "SCAN_METADATA_LIMIT_INVALID",
        "SCAN_LLM_ENV_UNAVAILABLE",
        "SCAN_LLM_KEY_REFERENCE_INVALID",
        "SCAN_LLM_KEY_MISSING_OR_INVALID",
        "SCAN_LLM_PROFILE_UNSUPPORTED",
        "SCAN_LLM_ENDPOINT_UNSUPPORTED",
        "SCAN_LLM_LIMIT_INVALID",
        "SCAN_LLM_TIMEOUT",
        "SCAN_LLM_NOT_COMPLETED",
        "SCAN_LLM_RESPONSE_INVALID",
        "SCAN_LLM_AUTH_FAILED",
        "SCAN_LLM_RATE_LIMITED",
        "SCAN_LLM_API_FAILED",
        "SCAN_LLM_RESPONSE_INCOMPLETE",
        "SCAN_LLM_SDK_UNAVAILABLE",
        "SCAN_LLM_SDK_VERSION_UNSUPPORTED",
        "SCAN_LLM_SDK_SETUP_FAILED",
        "SCAN_YARA_SDK_UNAVAILABLE",
        "SCAN_YARA_SDK_VERSION_UNSUPPORTED",
        "SCAN_YARA_SDK_SETUP_FAILED",
        "SCAN_YARA_FAILED",
        "SCAN_YARA_TIMEOUT",
        "SCAN_LLM_FAILED",
        "SCAN_LLM_UNAVAILABLE",
        "SCAN_YARA_UNAVAILABLE",
        "SCAN_ANALYZER_RESULT_INVALID",
    }
)


class ScannerAggregationError(ValueError):
    """C 내부 고정 오류 코드. 예외 원문을 결과나 로그에 싣지 않는다."""


class PreparedAnalyzer(Protocol):
    async def analyze_prepared(
        self, prepared: PreparedToolDefinition
    ) -> Sequence[CiscoFinding]: ...


@dataclass(frozen=True, slots=True)
class FindingSummary:
    analyzer: AnalyzerName
    severity: str
    category: str = field(repr=False)
    code: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class AnalyzerResult:
    analyzer: AnalyzerName
    status: AnalyzerStatus
    findings: tuple[FindingSummary, ...] | None
    error_code: str | None
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ToolScanResult:
    index: int
    tool_name: str | None = field(repr=False)
    definition_bytes: int | None
    completion: Completion
    input_error_code: str | None
    analyzers: tuple[AnalyzerResult, ...]


@dataclass(frozen=True, slots=True)
class AnalyzerSummary:
    analyzer: AnalyzerName
    completed: int
    failed: int
    not_run: int
    known_findings_count: int
    setup_error_code: str | None


@dataclass(frozen=True, slots=True)
class ScanSummary:
    completion: Completion
    definitions_total: int
    completed: int
    partial: int
    failed: int
    not_scanned: int
    tools_with_warnings: int
    known_findings_count: int
    maximum_observed_severity: str | None


@dataclass(frozen=True, slots=True)
class MetadataScanResult:
    server_id: str = field(repr=False)
    scope: Literal["provided_definitions"]
    max_definition_bytes: int
    summary: ScanSummary
    analyzer_summary: tuple[AnalyzerSummary, ...]
    tools: tuple[ToolScanResult, ...]
    duration_seconds: float

    def to_json(self) -> str:
        """명시적 산출물. 자동 로그·UI 공개·영속화를 하지 않는다."""
        return _json_text(asdict(self))

    def to_dict(self) -> dict[str, object]:
        """호출자가 수정해도 내부 결과가 바뀌지 않는 JSON 사본."""
        return cast(dict[str, object], json.loads(self.to_json()))


def _error_code(error: ValueError | OSError, name: AnalyzerName) -> str:
    if isinstance(error, TimeoutError):
        return f"SCAN_{name}_TIMEOUT"
    if error.args and type(error.args[0]) is str and error.args[0] in _ERROR_CODES:
        return error.args[0]
    return f"SCAN_{name}_FAILED"


def _copy_findings(
    raw: Sequence[CiscoFinding], name: AnalyzerName
) -> tuple[tuple[FindingSummary, ...] | None, str | None]:
    if type(raw) not in (list, tuple):
        return None, "SCAN_ANALYZER_RESULT_INVALID"
    copied: list[FindingSummary] = []
    invalid = False
    for finding in raw:
        severity = getattr(finding, "severity", None)
        category = getattr(finding, "threat_category", None)
        source = getattr(finding, "analyzer", None)
        details = getattr(finding, "details", None)
        code = details.get("threat_type") if type(details) is dict else None
        if (
            type(severity) is not str
            or severity not in (*_SEVERITIES, "UNKNOWN")
            or type(category) is not str
            or not category.strip()
            or source != name
            or (code is not None and type(code) is not str)
        ):
            invalid = True
            continue
        try:
            category.encode("utf-8")
            if code is not None:
                code.encode("utf-8")
        except UnicodeError:
            invalid = True
            continue
        if category == "ANALYZER INFRASTRUCTURE":
            invalid = True
            continue
        copied.append(FindingSummary(name, severity, category, code))
        invalid |= severity == "UNKNOWN"
    if invalid:
        return tuple(copied) if copied else None, "SCAN_ANALYZER_RESULT_INVALID"
    return tuple(copied), None


def _completion(results: Sequence[AnalyzerResult]) -> Completion:
    statuses = [result.status for result in results]
    if all(status == "completed" for status in statuses):
        return "completed"
    if "completed" in statuses:
        return "partial"
    if all(status == "not_run" for status in statuses):
        return "not_scanned"
    return "failed"


class InternalMetadataScanner:
    """도구별 분석기는 병행하고 도구 사이는 순차 실행한다. 전역 큐는 별도다."""

    def __init__(
        self,
        llm: PreparedAnalyzer | None,
        yara: PreparedAnalyzer | None,
        *,
        max_definition_bytes: int = DEFAULT_MAX_DEFINITION_BYTES,
        setup_errors: Mapping[AnalyzerName, str] | None = None,
    ) -> None:
        _check_limit(max_definition_bytes)
        self._max_definition_bytes = max_definition_bytes
        self._providers = {"LLM": llm, "YARA": yara}
        self._setup_errors: dict[AnalyzerName, str] = {}
        for name in _NAMES:
            if self._providers[name] is None:
                code = (setup_errors or {}).get(name, f"SCAN_{name}_UNAVAILABLE")
                self._setup_errors[name] = code if code in _ERROR_CODES else f"SCAN_{name}_FAILED"

    async def scan(
        self, server_id: str, definitions: Sequence[ToolDefinition]
    ) -> MetadataScanResult:
        """await 전에 전체 입력을 고정한다. 서버 ID는 신뢰한 호출자가 제공한다."""
        if type(server_id) is not str or not server_id.strip():
            raise ScannerAggregationError("SCAN_SERVER_ID_INVALID")
        try:
            server_id.encode("utf-8")
        except UnicodeError:
            raise ScannerAggregationError("SCAN_SERVER_ID_INVALID") from None
        if not isinstance(definitions, Sequence) or isinstance(definitions, (str, bytes)):
            raise ScannerAggregationError("SCAN_METADATA_BATCH_INVALID")
        started = monotonic()
        snapshots: list[PreparedToolDefinition | DefinitionInputError] = []
        for definition in tuple(definitions):
            try:
                snapshots.append(
                    prepare_tool_definition(
                        definition, max_definition_bytes=self._max_definition_bytes
                    )
                )
            except DefinitionInputError as error:
                snapshots.append(error)
        tools: list[ToolScanResult] = []
        for index, snapshot in enumerate(snapshots):
            if isinstance(snapshot, DefinitionInputError):
                code = str(snapshot)
                if code not in {"SCAN_METADATA_INVALID", "SCAN_METADATA_TOO_LARGE"}:
                    code = "SCAN_METADATA_INVALID"
                tools.append(
                    ToolScanResult(
                        index,
                        None,
                        None,
                        "not_scanned",
                        code,
                        tuple(AnalyzerResult(name, "not_run", None, None, 0.0) for name in _NAMES),
                    )
                )
                continue
            tasks = [asyncio.create_task(self._analyze(name, snapshot)) for name in _NAMES]
            try:
                # 예기치 않은 프로그래밍 오류도 다른 분석기의 실행을 끊지 않는다.
                await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
                analyzers = tuple(task.result() for task in tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)  # 취소·예외 회수만 수행
            value = cast(dict[str, object], json.loads(snapshot.definition_json))
            tools.append(
                ToolScanResult(
                    index,
                    cast(str, value["name"]),
                    len(snapshot.definition_json.encode("utf-8")),
                    _completion(analyzers),
                    None,
                    analyzers,
                )
            )
        return self._aggregate(server_id, tuple(tools), monotonic() - started)

    async def _analyze(
        self, name: AnalyzerName, snapshot: PreparedToolDefinition
    ) -> AnalyzerResult:
        provider = self._providers[name]
        if provider is None:
            return AnalyzerResult(name, "failed", None, self._setup_errors[name], 0.0)
        started = monotonic()
        try:
            findings, error_code = _copy_findings(await provider.analyze_prepared(snapshot), name)
        except (ValueError, OSError) as error:
            findings, error_code = None, _error_code(error, name)
        return AnalyzerResult(
            name,
            "failed" if error_code else "completed",
            findings,
            error_code,
            monotonic() - started,
        )

    def _aggregate(
        self, server_id: str, tools: tuple[ToolScanResult, ...], duration: float
    ) -> MetadataScanResult:
        findings = [f for tool in tools for a in tool.analyzers for f in (a.findings or ())]
        counts = {
            status: sum(tool.completion == status for tool in tools)
            for status in ("completed", "partial", "failed", "not_scanned")
        }
        completion: Completion = "completed"
        if not tools and self._setup_errors:
            completion = "not_scanned"
        elif counts["completed"] != len(tools):
            if counts["completed"] or counts["partial"]:
                completion = "partial"
            else:
                completion = "failed" if counts["failed"] else "not_scanned"
        observed = [f.severity for f in findings if f.severity in _SEVERITIES]
        maximum = max(observed, key=_SEVERITIES.index) if observed else None
        if findings and maximum is None:
            maximum = "UNKNOWN"
        summary = ScanSummary(
            completion,
            len(tools),
            counts["completed"],
            counts["partial"],
            counts["failed"],
            counts["not_scanned"],
            sum(
                any(f.severity != "SAFE" for a in tool.analyzers for f in (a.findings or ()))
                for tool in tools
            ),
            len(findings),
            maximum,
        )
        analyzer_summary = []
        for name in _NAMES:
            results = [a for tool in tools for a in tool.analyzers if a.analyzer == name]
            analyzer_summary.append(
                AnalyzerSummary(
                    name,
                    sum(a.status == "completed" for a in results),
                    sum(a.status == "failed" for a in results),
                    sum(a.status == "not_run" for a in results),
                    sum(len(a.findings or ()) for a in results),
                    self._setup_errors.get(name),
                )
            )
        return MetadataScanResult(
            server_id,
            "provided_definitions",
            self._max_definition_bytes,
            summary,
            tuple(analyzer_summary),
            tools,
            duration,
        )


class _ThreadedYara:
    """동기 native matcher가 LLM 이벤트 루프를 막지 않게 한다. 강제 종료는 별도다."""

    def __init__(
        self,
        adapter: CiscoMetadataAdapter[CiscoFinding],
        native_error: type[Exception],
        native_timeout: type[Exception],
    ) -> None:
        self._adapter = adapter
        self._native_error = native_error
        self._native_timeout = native_timeout

    async def analyze_prepared(self, prepared: PreparedToolDefinition) -> list[CiscoFinding]:
        try:
            return await asyncio.to_thread(self._run, prepared)
        except self._native_timeout:
            raise ScannerAggregationError("SCAN_YARA_TIMEOUT") from None
        except self._native_error:
            raise ScannerAggregationError("SCAN_YARA_FAILED") from None

    def _run(self, prepared: PreparedToolDefinition) -> list[CiscoFinding]:
        return asyncio.run(self._adapter.analyze_prepared(prepared))


def _build_yara_provider(max_definition_bytes: int) -> PreparedAnalyzer:
    try:
        installed = version("cisco-ai-mcp-scanner")
    except PackageNotFoundError:
        raise ScannerAggregationError("SCAN_YARA_SDK_UNAVAILABLE") from None
    if installed != "4.8.6":
        raise ScannerAggregationError("SCAN_YARA_SDK_VERSION_UNSUPPORTED")
    try:
        with scanner_sdk_import_environment():
            factory = cast(
                Callable[[], DefinitionAnalyzer[CiscoFinding]],
                import_module("mcpscanner.core.analyzers.yara_analyzer").YaraAnalyzer,
            )
            native = import_module("yara")
        native_error = cast(type[Exception], native.Error)
        try:
            analyzer = factory()
        except native_error:
            raise ScannerAggregationError("SCAN_YARA_SDK_SETUP_FAILED") from None
        adapter = CiscoMetadataAdapter(
            analyzer, input_format="yara", max_definition_bytes=max_definition_bytes
        )
        return _ThreadedYara(
            adapter, cast(type[Exception], native.Error), cast(type[Exception], native.TimeoutError)
        )
    except (ImportError, OSError, ValueError, AttributeError):
        raise ScannerAggregationError("SCAN_YARA_SDK_SETUP_FAILED") from None


@asynccontextmanager
async def connect_metadata_scanner(
    env_file: Path | None = None,
    *,
    max_definition_bytes: int = DEFAULT_MAX_DEFINITION_BYTES,
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[InternalMetadataScanner]:
    """기존 Luna low와 native YARA를 연결한다. 한쪽 준비 실패도 별도로 기록한다."""
    _check_limit(max_definition_bytes)
    errors: dict[AnalyzerName, str] = {}
    async with AsyncExitStack() as stack:
        llm: PreparedAnalyzer | None = None
        yara: PreparedAnalyzer | None = None
        try:
            llm = await stack.enter_async_context(
                connect_luna_scanner(
                    env_file,
                    max_definition_bytes=max_definition_bytes,
                    environ=environ,
                    transport=transport,
                )
            )
        except ScannerLLMError as error:
            errors["LLM"] = _error_code(error, "LLM")
        try:
            yara = _build_yara_provider(max_definition_bytes)
        except ScannerAggregationError as error:
            errors["YARA"] = _error_code(error, "YARA")
        yield InternalMetadataScanner(
            llm, yara, max_definition_bytes=max_definition_bytes, setup_errors=errors
        )
