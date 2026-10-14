"""Cisco Scanner 4.8.6의 Luna low 요청 연결(I6·I9, T17·T19).

SDK 프롬프트·Finding 생성은 재사용하고 요청 단계만 인스턴스에서 교체한다.
전역 acompletion·설치 파일은 변경하지 않는다. B ScanReport와 연결하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import math
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol, cast

import httpx
from openai import APITimeoutError, AsyncOpenAI, AuthenticationError, OpenAIError, RateLimitError
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from guard_app.adapters.cisco import CiscoMetadataAdapter, DefinitionAnalyzer
from guard_app.security.redact import scanner_client_log_scope
from guard_app.security.secrets import (
    SecretLoadError,
    load_scanner_environment,
    scanner_sdk_import_environment,
)


class ScannerLLMError(ValueError):
    """원문 응답·예외·인증정보를 공개하지 않는 C 내부 오류 코드."""


class CiscoFinding(Protocol):
    """내부 SDK 결과의 조회 형태. 공통 DTO나 등록 판정을 대신하지 않는다."""

    severity: str
    analyzer: str
    threat_category: str


class _SDKRequestHook(Protocol):
    _make_llm_request: Callable[..., Awaitable[ChatCompletion]]


@dataclass(frozen=True, slots=True)
class ScannerLLMSettings:
    api_key: str = field(repr=False)
    model: str = "openai/gpt-6-luna"
    reasoning_effort: str = "low"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0
    max_completion_tokens: int = 4096

    def __post_init__(self) -> None:
        if (
            type(self.api_key) is not str
            or not self.api_key
            or self.api_key != self.api_key.strip()
            or "\n" in self.api_key
            or "\r" in self.api_key
        ):
            raise ScannerLLMError("SCAN_LLM_KEY_MISSING_OR_INVALID")
        if self.model != "openai/gpt-6-luna" or self.reasoning_effort != "low":
            raise ScannerLLMError("SCAN_LLM_PROFILE_UNSUPPORTED")
        if self.base_url.rstrip("/") != "https://api.openai.com/v1":
            raise ScannerLLMError("SCAN_LLM_ENDPOINT_UNSUPPORTED")
        if (
            type(self.timeout_seconds) not in (float, int)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or type(self.max_completion_tokens) is not int
            or self.max_completion_tokens <= 0
        ):
            raise ScannerLLMError("SCAN_LLM_LIMIT_INVALID")

    @classmethod
    def load(
        cls, env_file: Path | None = None, *, environ: Mapping[str, str] | None = None
    ) -> ScannerLLMSettings:
        try:
            values = load_scanner_environment(env_file, environ=environ)
        except SecretLoadError as error:
            raise ScannerLLMError(str(error)) from None
        try:
            timeout = float(values.get("MCP_SCANNER_LLM_TIMEOUT", "60"))
            tokens = int(values.get("MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS", "4096"))
        except ValueError:
            raise ScannerLLMError("SCAN_LLM_LIMIT_INVALID") from None
        return cls(
            api_key=values["MCP_SCANNER_LLM_API_KEY"],
            model=values.get("MCP_SCANNER_LLM_MODEL", "openai/gpt-6-luna"),
            reasoning_effort=values.get("MCP_SCANNER_LLM_REASONING_EFFORT", "low"),
            base_url=values.get("MCP_SCANNER_LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=timeout,
            max_completion_tokens=tokens,
        )


@dataclass(slots=True)
class _RequestState:
    valid_response: bool = False
    error_code: str | None = None


class _ConnectedLunaAnalyzer:
    def __init__(
        self,
        sdk_analyzer: DefinitionAnalyzer[CiscoFinding],
        client: AsyncOpenAI,
        settings: ScannerLLMSettings,
        known_threats: frozenset[str],
    ) -> None:
        self._sdk_analyzer = sdk_analyzer
        self._client = client
        self._settings = settings
        self._known_threats = known_threats
        self._state: ContextVar[_RequestState | None] = ContextVar(
            "scanner_luna_request_state", default=None
        )
        # 공개 요청 주입 설정이 없는 고정 SDK 4.8.6의 인스턴스 연결 지점.
        # 해당 버전의 호출 형태·오류 소비 동작을 설치 SDK 테스트로 확인한다.
        cast(_SDKRequestHook, sdk_analyzer)._make_llm_request = self._complete

    async def analyze(
        self, content: str, context: dict[str, object] | None = None
    ) -> list[CiscoFinding]:
        state = _RequestState()
        token = self._state.set(state)
        try:
            with scanner_client_log_scope():
                async with asyncio.timeout(self._settings.timeout_seconds):
                    findings = await self._sdk_analyzer.analyze(content, context)
        except TimeoutError:
            raise ScannerLLMError("SCAN_LLM_TIMEOUT") from None
        finally:
            self._state.reset(token)
        if state.error_code is not None:
            raise ScannerLLMError(state.error_code)
        if not state.valid_response:
            raise ScannerLLMError("SCAN_LLM_NOT_COMPLETED")
        if any(
            finding.analyzer != "LLM"
            or finding.threat_category == "ANALYZER INFRASTRUCTURE"
            or finding.severity not in {"HIGH", "MEDIUM", "LOW", "INFO", "SAFE"}
            for finding in findings
        ):
            raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")
        return findings

    async def _complete(self, messages: list[dict[str, str]], context: str = "") -> ChatCompletion:
        state = self._state.get()
        if state is None:
            raise ScannerLLMError("SCAN_LLM_NOT_COMPLETED")
        try:
            response = await self._client.chat.completions.create(
                model="gpt-6-luna",
                messages=cast(list[ChatCompletionMessageParam], messages),
                reasoning_effort="low",
                max_completion_tokens=self._settings.max_completion_tokens,
                timeout=self._settings.timeout_seconds,
            )
        except APITimeoutError:
            state.error_code = "SCAN_LLM_TIMEOUT"
        except AuthenticationError:
            state.error_code = "SCAN_LLM_AUTH_FAILED"
        except RateLimitError:
            state.error_code = "SCAN_LLM_RATE_LIMITED"
        except (OpenAIError, httpx.RequestError):
            state.error_code = "SCAN_LLM_API_FAILED"
        else:
            try:
                _validate_completion(response, self._known_threats)
            except ScannerLLMError as error:
                state.error_code = str(error)
            else:
                state.valid_response = True
                return response
        raise ScannerLLMError(state.error_code or "SCAN_LLM_API_FAILED") from None


def _validate_completion(response: ChatCompletion, known_threats: frozenset[str]) -> None:
    if len(response.choices) != 1:
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")
    choice = response.choices[0]
    if choice.finish_reason != "stop":
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INCOMPLETE")
    if choice.message.refusal or choice.message.tool_calls or not choice.message.content:
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")
    try:
        value = json.loads(
            choice.message.content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID") from None
    threat = value.get("threat_analysis") if isinstance(value, dict) else None
    if not isinstance(threat, dict):
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")
    malicious = threat.get("malicious_content_detected")
    primary = threat.get("primary_threats")
    if (
        type(malicious) is not bool
        or type(primary) is not list
        or any(type(name) is not str or name not in known_threats for name in primary)
        or len(primary) != len(set(primary))
        or bool(primary) != malicious
    ):
        raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")
        value[key] = child
    return value


def _reject_constant(value: str) -> object:
    raise ScannerLLMError("SCAN_LLM_RESPONSE_INVALID")


def _build_sdk_analyzer(
    settings: ScannerLLMSettings,
) -> tuple[DefinitionAnalyzer[CiscoFinding], frozenset[str]]:
    try:
        scanner_version = version("cisco-ai-mcp-scanner")
    except PackageNotFoundError:
        raise ScannerLLMError("SCAN_LLM_SDK_UNAVAILABLE") from None
    if scanner_version != "4.8.6":
        raise ScannerLLMError("SCAN_LLM_SDK_VERSION_UNSUPPORTED")
    # 가격표 조회는 import 시 발생하므로 키를 로드한 뒤, SDK import 전에 고정한다.
    try:
        with scanner_sdk_import_environment():
            config_factory = cast(Callable[..., object], import_module("mcpscanner.config").Config)
            analyzer_factory = cast(
                Callable[[object], DefinitionAnalyzer[CiscoFinding]],
                import_module("mcpscanner.core.analyzers.llm_analyzer").LLMAnalyzer,
            )
            mapping = cast(
                dict[str, object], import_module("mcpscanner.threats.threats").LLM_THREAT_MAPPING
            )
        config = config_factory(
            llm_provider_api_key=settings.api_key,
            llm_model=settings.model,
            llm_base_url=settings.base_url,
            llm_timeout=settings.timeout_seconds,
            llm_max_tokens=settings.max_completion_tokens,
            llm_max_retries=0,
        )
        analyzer = analyzer_factory(config)
    except (ImportError, OSError, ValueError, AttributeError):
        raise ScannerLLMError("SCAN_LLM_SDK_SETUP_FAILED") from None
    return analyzer, frozenset(mapping)


@asynccontextmanager
async def connect_luna_scanner(
    env_file: Path | None = None,
    *,
    max_definition_bytes: int,
    environ: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[CiscoMetadataAdapter[CiscoFinding]]:
    """선택한 Luna low를 연결하고 종료·취소 때 HTTP 클라이언트를 닫는다.

    environ은 프로세스 설정 위임·테스트용, transport는 실제 SDK의 HTTP 대역
    검증용이다. SDK 원시 Finding만 반환하며 B ScanReport를 만들지 않는다.

    SDK import(첫 연결 수 초~수십 초), TLS 인증서 로딩(매 연결 수백 ms), OpenAI
    리소스 지연 import(첫 연결 약 1초)는 동기 작업이다. 호출한 이벤트 루프를
    멈추지 않도록 작업 스레드에서 실행한다.
    """
    settings = ScannerLLMSettings.load(env_file, environ=environ)
    sdk_analyzer, known_threats = await asyncio.to_thread(_build_sdk_analyzer, settings)
    verify: ssl.SSLContext | bool = True
    if transport is None:
        # AsyncClient(trust_env=False)가 루프 안에서 만들 기본 문맥과 같다.
        verify = await asyncio.to_thread(httpx.create_ssl_context, trust_env=False)
    async with (
        httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            verify=verify,
            transport=transport,
        ) as http_client,
        AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=0,
            http_client=http_client,
        ) as client,
    ):
        # chat 속성은 첫 접근 때 openai.resources 전체를 import한다.
        await asyncio.to_thread(lambda: client.chat.completions)
        analyzer = _ConnectedLunaAnalyzer(sdk_analyzer, client, settings, known_threats)
        yield CiscoMetadataAdapter(
            analyzer, input_format="llm", max_definition_bytes=max_definition_bytes
        )
