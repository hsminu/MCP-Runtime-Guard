"""A API transport: I1/I5/I7/I9, no live network or credentials."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage

from guard_app.adapters import openai_model
from guard_app.adapters.model_client import (
    ModelCompletionError,
    ModelDeadlineError,
    ModelFormatError,
)
from guard_app.domain.contracts import ConversationMessage, ToolView
from guard_app.domain.json_types import FrozenJsonObject

_CREDENTIAL = "synthetic-credential-marker"
_HISTORY = (ConversationMessage(role="user", content="synthetic user input"),)


def _tool(name: str = "docs__read") -> ToolView:
    return ToolView(
        tool_ref=name,
        description="reviewed description",
        input_schema=FrozenJsonObject(
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            }
        ),
    )


def _message(text: str = "synthetic answer") -> dict:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _envelope(*items: dict, status: str = "completed", **extra) -> dict:
    return {"id": "resp_1", "status": status, "output": list(items), **extra}


def _function(name: str, **extra) -> dict:
    return {
        "type": "function_call",
        "id": "fc_1",
        "status": "completed",
        "name": name,
        "call_id": "call_1",
        "arguments": '{"path":"synthetic-item"}',
        **extra,
    }


@pytest.fixture
def connect(monkeypatch):
    @asynccontextmanager
    async def create(handler, *, timeout_s=1.0):
        requests = []

        async def record(request):
            requests.append(request)
            return await handler(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(record), follow_redirects=False)
        monkeypatch.setattr(openai_model, "_make_http_client", lambda timeout: http)
        client = openai_model.OpenAIModelClient(
            model="gpt-6.1-sol", api_key=_CREDENTIAL, timeout_s=timeout_s
        )
        try:
            yield client, requests
        finally:
            await client.aclose()

    return create


@pytest.mark.asyncio
async def test_text_uses_fixed_stateless_endpoint_and_clears_credentials(connect):
    async def handler(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == f"Bearer {_CREDENTIAL}"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["tools"] == []
        assert body["tool_choice"] == "none"
        assert body["parallel_tool_calls"] is False
        assert body["input"] == [{"role": "user", "content": "synthetic user input"}]
        assert _CREDENTIAL.encode() not in request.content
        return httpx.Response(200, json=_envelope(_message()))

    async with connect(handler) as (client, requests):
        turn = await client.step(_HISTORY, ())
        assert turn.text == "synthetic answer"
        assert turn.calls == ()
        assert len(requests) == 1
        assert _CREDENTIAL not in repr(client)
        assert "synthetic answer" not in repr(client._replay)
    assert client._api_key is None
    assert client._replay == {}
    assert client._http.is_closed
    await client.aclose()
    with pytest.raises(ModelCompletionError):
        await client.step(_HISTORY, ())


@pytest.mark.asyncio
async def test_function_results_and_encrypted_reasoning_replay_stable_names(connect):
    encrypted = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "synthetic-encrypted-reasoning",
    }
    first_names = {}

    async def handler(request):
        body = json.loads(request.content)
        names = {tool["description"]: tool["name"] for tool in body["tools"]}
        assert all(tool["type"] == "function" for tool in body["tools"])
        assert all(len(tool["name"]) <= 64 for tool in body["tools"])
        if not first_names:
            first_names.update(names)
            return httpx.Response(
                200,
                json=_envelope(
                    encrypted, _message("I will read it"), _function(names["read reviewed"])
                ),
            )
        assert names == first_names
        assert body["input"][1] == encrypted
        assert body["input"][3]["type"] == "function_call"
        assert body["input"][3]["name"] == names["read reviewed"]
        assert body["input"][4] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"text":"committed synthetic result","is_error":false}',
        }
        return httpx.Response(200, json=_envelope(_message("done")))

    read = _tool().model_copy(update={"description": "read reviewed"})
    send = _tool("other__read").model_copy(update={"description": "other reviewed"})
    async with connect(handler) as (client, requests):
        first = await client.step(_HISTORY, (read, send))
        assert first.text == "I will read it"
        assert len(first.calls) == 1
        assert first.calls[0].tool_ref == "docs__read"
        assert first.calls[0].arguments.to_dict() == {"path": "synthetic-item"}
        followup = (
            *_HISTORY,
            ConversationMessage(role="assistant", content=first.text, calls=first.calls),
            ConversationMessage(
                role="tool",
                model_call_id="call_1",
                content='{"text":"committed synthetic result","is_error":false}',
            ),
        )
        assert (await client.step(followup, (send, read))).text == "done"
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_refusal_is_plain_text_without_tool_execution(connect):
    message = _message()
    message["content"] = [{"type": "refusal", "refusal": "synthetic refusal"}]

    async def handler(request):
        return httpx.Response(200, json=_envelope(message))

    async with connect(handler) as (client, _):
        turn = await client.step(_HISTORY, ())
        assert turn.text == "synthetic refusal"
        assert not turn.calls


@pytest.mark.parametrize("phase", [None, "commentary", "final_answer"])
@pytest.mark.parametrize("caller", [None, {"type": "direct"}])
@pytest.mark.asyncio
async def test_installed_sdk_optional_metadata_is_preserved_for_replay(connect, phase, caller):
    outputs = []

    async def handler(request):
        body = json.loads(request.content)
        if not outputs:
            message = ResponseOutputMessage.model_validate({**_message(), "phase": phase})
            function = ResponseFunctionToolCall.model_validate(
                {
                    **_function(body["tools"][0]["name"]),
                    "status": None,
                    "caller": caller,
                    "namespace": None,
                }
            )
            outputs.extend([message.model_dump(mode="json"), function.model_dump(mode="json")])
            return httpx.Response(200, json=_envelope(*outputs))
        assert body["input"][1:3] == outputs
        return httpx.Response(200, json=_envelope(_message("done")))

    async with connect(handler) as (client, _):
        first = await client.step(_HISTORY, (_tool(),))
        history = (
            *_HISTORY,
            ConversationMessage(role="assistant", content=first.text, calls=first.calls),
            ConversationMessage(role="tool", model_call_id="call_1", content="committed result"),
        )
        assert (await client.step(history, (_tool(),))).text == "done"


@pytest.mark.parametrize(
    "case",
    [
        "unknown_tool",
        "unadvertised_tool",
        "extra_control",
        "bad_arguments",
        "extra_argument",
        "duplicate_call",
        "unknown_item",
        "unknown_content",
        "wrong_role",
        "missing_encrypted",
        "reasoning_only",
        "failed",
        "incomplete",
        "error",
        "refusal_with_call",
        "program_caller",
        "foreign_namespace",
    ],
)
@pytest.mark.asyncio
async def test_invalid_provider_output_never_becomes_a_guard_call(connect, case):
    async def handler(request):
        name = json.loads(request.content)["tools"][0]["name"]
        call = _function(name)
        payload = _envelope(call)
        if case == "unknown_tool":
            call["name"] = "approval__approve"
        elif case == "unadvertised_tool":
            call["name"] = openai_model._function_name("other__read")
        elif case == "extra_control":
            call["task_id"] = "forged-task"
        elif case == "bad_arguments":
            call["arguments"] = '{"path":"a","path":"b"}'
        elif case == "extra_argument":
            call["arguments"] = '{"path":"a","label":0}'
        elif case == "duplicate_call":
            payload["output"].append(call.copy())
        elif case == "unknown_item":
            payload = _envelope({"type": "web_search_call"})
        elif case == "unknown_content":
            message = _message()
            message["content"] = [{"type": "output_image", "image_url": "untrusted"}]
            payload = _envelope(message)
        elif case == "wrong_role":
            message = _message()
            message["role"] = "system"
            payload = _envelope(message)
        elif case == "missing_encrypted":
            payload = _envelope({"type": "reasoning", "id": "rs", "summary": []}, call)
        elif case == "reasoning_only":
            payload = _envelope({"type": "reasoning", "encrypted_content": "opaque"})
        elif case == "failed":
            payload["status"] = "failed"
        elif case == "incomplete":
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
        elif case == "error":
            payload["error"] = {"message": _CREDENTIAL}
        elif case == "program_caller":
            call["caller"] = {"type": "program", "caller_id": "untrusted"}
        elif case == "foreign_namespace":
            call["namespace"] = "untrusted"
        else:
            message = _message()
            message["content"] = [{"type": "refusal", "refusal": "no"}]
            payload["output"].append(message)
        return httpx.Response(200, json=payload)

    async with connect(handler) as (client, _):
        with pytest.raises(ModelFormatError) as captured:
            await client.step(_HISTORY, (_tool(),))
        assert _CREDENTIAL not in str(captured.value)
        assert client._replay == {}


@pytest.mark.asyncio
async def test_no_registered_tools_means_function_output_is_rejected(connect):
    async def handler(request):
        return httpx.Response(200, json=_envelope(_function("docs__read")))

    async with connect(handler) as (client, _):
        with pytest.raises(ModelFormatError):
            await client.step(_HISTORY, ())


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"completed","output":[],"output":[]}',
        b'{"status":"completed","output":[],"usage":NaN}',
        b"not-json",
    ],
)
@pytest.mark.asyncio
async def test_json_is_strict_before_normalization(connect, body):
    async def handler(request):
        return httpx.Response(200, content=body)

    async with connect(handler) as (client, _):
        with pytest.raises(ModelFormatError):
            await client.step(_HISTORY, ())


@pytest.mark.asyncio
async def test_response_body_is_bounded(connect, monkeypatch):
    monkeypatch.setattr(openai_model, "_MAX_RESPONSE_BYTES", 100)

    async def handler(request):
        return httpx.Response(200, json=_envelope(_message("x" * 200)))

    async with connect(handler) as (client, _):
        with pytest.raises(ModelFormatError):
            await client.step(_HISTORY, ())


@pytest.mark.parametrize("status", [302, 401, 429, 500])
@pytest.mark.asyncio
async def test_http_failure_is_not_retried_followed_or_disclosed(connect, caplog, status):
    caplog.set_level(logging.DEBUG)

    async def handler(request):
        return httpx.Response(
            status, text=_CREDENTIAL, headers={"location": "https://other.invalid"}
        )

    async with connect(handler) as (client, requests):
        with pytest.raises(ModelCompletionError) as captured:
            await client.step(_HISTORY, ())
        assert len(requests) == 1
        assert _CREDENTIAL not in str(captured.value)
        assert _CREDENTIAL not in caplog.text
        assert client._replay == {}


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
@pytest.mark.asyncio
async def test_network_exceptions_are_content_free(connect, failure):
    async def handler(request):
        raise failure(_CREDENTIAL, request=request)

    async with connect(handler) as (client, requests):
        expected = ModelDeadlineError if failure is httpx.ReadTimeout else ModelCompletionError
        with pytest.raises(expected) as captured:
            await client.step(_HISTORY, ())
        assert len(requests) == 1
        assert _CREDENTIAL not in str(captured.value)


@pytest.mark.parametrize("finish", ["timeout", "cancel", "close"])
@pytest.mark.asyncio
async def test_late_completion_after_timeout_cancel_or_close_cannot_restore_state(connect, finish):
    started = asyncio.Event()
    release = asyncio.Event()
    ended = asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        ended.set()
        return httpx.Response(200, json=_envelope(_message("late private answer")))

    async with connect(handler, timeout_s=0.02 if finish == "timeout" else 1) as (client, _):
        pending = asyncio.create_task(client.step(_HISTORY, ()))
        await started.wait()
        if finish == "timeout":
            with pytest.raises(ModelDeadlineError):
                await pending
        elif finish == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await asyncio.gather(client.aclose(), client.aclose())
        release.set()
        await ended.wait()
        if finish == "close":
            with pytest.raises(ModelCompletionError):
                await pending
            assert client._api_key is None
        await asyncio.sleep(0)
        assert client._replay == {}
        assert client._replay_bytes == 0


@pytest.mark.asyncio
async def test_changed_history_cannot_receive_another_turns_reasoning(connect):
    async def handler(request):
        return httpx.Response(200, json=_envelope(_message()))

    async with connect(handler) as (client, requests):
        first = await client.step(_HISTORY, ())
        changed = (
            *_HISTORY,
            ConversationMessage(role="assistant", content="replaced"),
            ConversationMessage(role="user", content="next"),
        )
        with pytest.raises(ModelFormatError):
            await client.step(changed, ())
        assert first.text == "synthetic answer"
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_http_client_ignores_environment_and_disables_redirects_and_retries(monkeypatch):
    options = {}
    transport_options = {}
    original_client = httpx.AsyncClient

    def transport(**kwargs):
        transport_options.update(kwargs)
        return httpx.MockTransport(lambda request: httpx.Response(500))

    def client(**kwargs):
        options.update(kwargs)
        return original_client(**kwargs)

    monkeypatch.setenv("OPENAI_BASE_URL", "https://other.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "https://other.invalid")
    monkeypatch.setattr(openai_model.httpx, "AsyncClient", client)
    monkeypatch.setattr(openai_model.httpx, "AsyncHTTPTransport", transport)
    model = openai_model.OpenAIModelClient(model="gpt-6.1-sol", api_key=_CREDENTIAL)
    try:
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        assert options["timeout"].read == 60
        assert transport_options == {"retries": 0, "trust_env": False}
    finally:
        await model.aclose()
