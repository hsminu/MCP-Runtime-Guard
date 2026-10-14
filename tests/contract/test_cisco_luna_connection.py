"""I6·I9 / T17·T19: 실제 Cisco·OpenAI SDK와 HTTP 대역의 연결 인수.

외부 API는 호출하지 않는다. B ScanReport·Registry·관리 도구 실행은 없다.
"""

import asyncio
import json
import logging

import httpx
import pytest

from guard_app.adapters import cisco_llm
from guard_app.adapters.cisco_llm import ScannerLLMError, connect_luna_scanner

_KEY = "SENSITIVE_SYNTHETIC_KEY_MARKER"
_ENV = {"OPENAI_API_KEY": _KEY}
_DEFINITION = {
    "name": "tool_001",
    "description": "Return a fixed public status.",
    "inputSchema": {"type": "object", "description": "SENSITIVE_METADATA_MARKER"},
    "annotations": {"title": "ANNOTATIONS_MARKER"},
}


def response_body(content=None, *, finish_reason="stop", **message_fields):
    if content is None:
        content = json.dumps(
            {"threat_analysis": {"malicious_content_detected": False, "primary_threats": []}}
        )
    return {
        "id": "chatcmpl-synthetic",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, **message_fields},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def test_selected_profile_is_applied_to_actual_http_body_and_global_sdk_is_unchanged(caplog):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {_KEY}"
        return httpx.Response(200, json=response_body())

    async def run():
        from mcpscanner.core.analyzers import llm_analyzer

        original = llm_analyzer.acompletion
        async with connect_luna_scanner(
            environ={**_ENV, "MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS": "2048"},
            max_definition_bytes=10_000,
            transport=httpx.MockTransport(handler),
        ) as scanner:
            assert await scanner.analyze_tool_definition(_DEFINITION) == []
        assert llm_analyzer.acompletion is original

    caplog.set_level(logging.DEBUG)
    asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-6-luna"
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["max_completion_tokens"] == 2048
    assert "max_tokens" not in captured[0]
    assert "temperature" not in captured[0]
    assert "tools" not in captured[0]
    prompt = captured[0]["messages"][1]["content"]
    assert "SENSITIVE_METADATA_MARKER" in prompt
    assert "ANNOTATIONS_MARKER" in prompt
    assert _KEY not in caplog.text
    assert "SENSITIVE_METADATA_MARKER" not in caplog.text


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "SCAN_LLM_AUTH_FAILED"), (429, "SCAN_LLM_RATE_LIMITED"), (500, "SCAN_LLM_API_FAILED")],
)
def test_http_errors_have_one_request_and_do_not_become_empty_findings(status, code, caplog):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "SENSITIVE_ERROR_MARKER"}})

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            with pytest.raises(ScannerLLMError, match=f"^{code}$") as error:
                await scanner.analyze_tool_definition(_DEFINITION)
            assert "SENSITIVE_ERROR_MARKER" not in repr(error.value)

    caplog.set_level(logging.DEBUG)
    asyncio.run(run())
    assert len(calls) == 1
    assert "SENSITIVE_ERROR_MARKER" not in caplog.text
    assert _KEY not in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        "not JSON SENSITIVE_RESPONSE_MARKER",
        "{}",
        '{"threat_analysis":{"malicious_content_detected":"false","primary_threats":[]}}',
        '{"threat_analysis":{"malicious_content_detected":true,"primary_threats":[]}}',
        '{"threat_analysis":{"malicious_content_detected":false,'
        '"primary_threats":["PROMPT INJECTION"]}}',
        '{"threat_analysis":{"malicious_content_detected":true,"primary_threats":["UNRECOGNIZED"]}}',
        '{"threat_analysis":{},"threat_analysis":{"malicious_content_detected":false,"primary_threats":[]}}',
        '{"extra":NaN,"threat_analysis":{"malicious_content_detected":false,"primary_threats":[]}}',
    ],
)
def test_malformed_model_response_is_a_fixed_error(content):
    async def handler(request):
        return httpx.Response(200, json=response_body(content))

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            with pytest.raises(ScannerLLMError, match="^SCAN_LLM_RESPONSE_INVALID$"):
                await scanner.analyze_tool_definition(_DEFINITION)

    asyncio.run(run())


def test_truncated_response_is_not_completed_even_if_json_is_valid():
    async def handler(request):
        return httpx.Response(200, json=response_body(finish_reason="length"))

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            with pytest.raises(ScannerLLMError, match="^SCAN_LLM_RESPONSE_INCOMPLETE$"):
                await scanner.analyze_tool_definition(_DEFINITION)

    asyncio.run(run())


def test_timeout_is_a_fixed_error():
    async def handler(request):
        raise httpx.ReadTimeout("SENSITIVE_TIMEOUT_MARKER", request=request)

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            with pytest.raises(ScannerLLMError, match="^SCAN_LLM_TIMEOUT$"):
                await scanner.analyze_tool_definition(_DEFINITION)

    asyncio.run(run())


def test_concurrent_calls_do_not_share_failure_state():
    arrived = 0

    async def handler(request):
        nonlocal arrived
        arrived += 1
        this_call = arrived
        await asyncio.sleep(0)
        return httpx.Response(200, json=response_body("{}" if this_call == 1 else None))

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            results = await asyncio.gather(
                scanner.analyze_tool_definition(_DEFINITION),
                scanner.analyze_tool_definition(_DEFINITION),
                return_exceptions=True,
            )
        assert isinstance(results[0], ScannerLLMError)
        assert str(results[0]) == "SCAN_LLM_RESPONSE_INVALID"
        assert results[1] == []

    asyncio.run(run())


def test_sdk_version_mismatch_fails_before_any_http(monkeypatch):
    monkeypatch.setattr(cisco_llm, "version", lambda name: "4.8.7")

    async def run():
        with pytest.raises(ScannerLLMError, match="^SCAN_LLM_SDK_VERSION_UNSUPPORTED$"):
            async with connect_luna_scanner(environ=_ENV, max_definition_bytes=10_000):
                pytest.fail("unsupported SDK must not be connected")

    asyncio.run(run())


@pytest.mark.parametrize("status", [200, 401])
def test_http_transport_is_closed_after_success_or_failure(status):
    class TrackingTransport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True
            await super().aclose()

    async def handler(request):
        return httpx.Response(status, json=response_body())

    transport = TrackingTransport(handler)

    async def run():
        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=transport
        ) as scanner:
            if status == 200:
                assert await scanner.analyze_tool_definition(_DEFINITION) == []
            else:
                with pytest.raises(ScannerLLMError, match="^SCAN_LLM_AUTH_FAILED$"):
                    await scanner.analyze_tool_definition(_DEFINITION)
        assert transport.closed

    asyncio.run(run())


def test_whole_scan_deadline_cancels_request():
    entered = cancelled = False

    async def handler(request):
        nonlocal entered, cancelled
        entered = True
        try:
            await asyncio.sleep(10)
        finally:
            cancelled = True
        return httpx.Response(200, json=response_body())

    async def run():
        # 기한이 요청 시작 전에 끝나면 진행 중 요청의 취소를 확인할 수 없다.
        # 프로세스 첫 요청은 OpenAI SDK 지연 import(Windows 약 1초)를 기한 안에서 치르고,
        # Windows asyncio는 시계 해상도(15.6ms)보다 짧은 기한을 바로 만료로 처리한다.
        # 준비 요청을 먼저 보내고 두 값보다 충분히 긴 기한을 쓴다.
        def ready(request):
            return httpx.Response(200, json=response_body())

        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(ready)
        ) as warm:
            await warm.analyze_tool_definition(_DEFINITION)
        async with connect_luna_scanner(
            environ={**_ENV, "MCP_SCANNER_LLM_TIMEOUT": "0.5"},
            max_definition_bytes=10_000,
            transport=httpx.MockTransport(handler),
        ) as scanner:
            with pytest.raises(ScannerLLMError, match="^SCAN_LLM_TIMEOUT$"):
                await scanner.analyze_tool_definition(_DEFINITION)
        assert entered
        assert cancelled

    asyncio.run(run())


def test_external_cancellation_is_preserved():
    async def run():
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await asyncio.sleep(10)
            return httpx.Response(200, json=response_body())

        async with connect_luna_scanner(
            environ=_ENV, max_definition_bytes=10_000, transport=httpx.MockTransport(handler)
        ) as scanner:
            task = asyncio.create_task(scanner.analyze_tool_definition(_DEFINITION))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
