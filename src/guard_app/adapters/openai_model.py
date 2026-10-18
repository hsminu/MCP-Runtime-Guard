"""Task-owned Responses API transport; only Guard function proposals leave it.

Credentials are injected by app composition, never discovered here. Provider
reasoning/output replay lives only in this instance and is discarded on close.
No provider tool is executed by this adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass

import httpx

from guard_app.domain.contracts import Conversation, ConversationMessage, ModelTurn, ToolView

from .model_client import (
    ModelCompletionError,
    ModelFormatError,
    ProviderNeutralModelClient,
    parse_model_turn,
)

_ENDPOINT = "https://api.openai.com/v1/responses"
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_REQUEST_BYTES = 2_097_152


def _make_http_client(timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        follow_redirects=False,
        trust_env=False,
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ModelFormatError()
        result[name] = value
    return result


def _constant(_value: str) -> object:
    raise ModelFormatError()


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ModelFormatError() from None


def _function_name(tool_ref: str) -> str:
    # Stable across tools/list ordering changes, independent of model text.
    return "guard_" + hashlib.sha256(tool_ref.encode("utf-8")).hexdigest()[:56]


def _signature(message: ConversationMessage) -> bytes:
    return hashlib.sha256(_encoded(message.model_dump(mode="json"))).digest()


@dataclass(frozen=True, slots=True, repr=False)
class _Replay:
    signature: bytes
    output: tuple[dict[str, object], ...]


def _normalized_output(
    value: object, names: dict[str, str]
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    if type(value) is not dict or value.get("status") != "completed":
        raise ModelCompletionError()
    if value.get("error") is not None or value.get("incomplete_details") is not None:
        raise ModelCompletionError()
    output = value.get("output")
    if type(output) is not list or len(output) > 128:
        raise ModelFormatError()
    text: list[str] = []
    calls: list[dict[str, object]] = []
    replay: list[dict[str, object]] = []
    refusal = False
    for item in output:
        if type(item) is not dict:
            raise ModelFormatError()
        kind = item.get("type")
        if kind == "reasoning":
            if not set(item) <= {"type", "id", "summary", "content", "encrypted_content", "status"}:
                raise ModelFormatError()
            if (
                type(item.get("encrypted_content")) is not str
                or type(item.get("id")) is not str
                or type(item.get("summary")) is not list
                or item.get("status") not in (None, "completed")
            ):
                # store=False reasoning must be replayable without server state.
                raise ModelFormatError()
            for summary in item["summary"]:
                if (
                    type(summary) is not dict
                    or set(summary) != {"type", "text"}
                    or summary.get("type") != "summary_text"
                    or type(summary.get("text")) is not str
                ):
                    raise ModelFormatError()
            content = item.get("content")
            if content is not None:
                if type(content) is not list:
                    raise ModelFormatError()
                for part in content:
                    if (
                        type(part) is not dict
                        or set(part) != {"type", "text"}
                        or part.get("type") != "reasoning_text"
                        or type(part.get("text")) is not str
                    ):
                        raise ModelFormatError()
        elif kind == "function_call":
            if (
                not set(item)
                <= {"type", "id", "call_id", "name", "arguments", "status", "caller", "namespace"}
                or item.get("status") not in (None, "completed")
                or item.get("caller") not in (None, {"type": "direct"})
                or item.get("namespace") is not None
                or type(item.get("name")) is not str
                or item["name"] not in names
                or type(item.get("arguments")) is not str
                or type(item.get("call_id")) is not str
            ):
                raise ModelFormatError()
            calls.append(
                {
                    "tool_ref": names[item["name"]],
                    "arguments": item["arguments"],
                    "model_call_id": item["call_id"],
                }
            )
        elif kind == "message":
            if (
                not set(item) <= {"type", "id", "role", "status", "content", "phase"}
                or item.get("role") != "assistant"
                or item.get("status", "completed") != "completed"
                or item.get("phase") not in (None, "commentary", "final_answer")
                or type(item.get("content")) is not list
            ):
                raise ModelFormatError()
            for part in item["content"]:
                if type(part) is not dict:
                    raise ModelFormatError()
                if part.get("type") == "output_text":
                    if not set(part) <= {"type", "text", "annotations", "logprobs"}:
                        raise ModelFormatError()
                    content = part.get("text")
                elif part.get("type") == "refusal":
                    if not set(part) <= {"type", "refusal"}:
                        raise ModelFormatError()
                    content = part.get("refusal")
                    refusal = True
                else:
                    raise ModelFormatError()
                if type(content) is not str:
                    raise ModelFormatError()
                text.append(content)
        else:
            # Builtin/remote tools and unknown provider items are never accepted.
            raise ModelFormatError()
        replay.append(item)
    if (refusal and calls) or (not text and not calls):
        raise ModelFormatError()
    return {"text": "\n".join(text) if text else None, "calls": calls}, tuple(replay)


class OpenAIModelClient:
    """A fresh instance per Task; the endpoint cannot be supplied by the caller."""

    def __init__(self, *, model: str, api_key: str, timeout_s: float = 60.0) -> None:
        if (
            type(model) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model) is None
            or type(api_key) is not str
            or not 1 <= len(api_key) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in api_key)
            or type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 300
        ):
            raise ValueError("모델 연결 설정이 유효하지 않다")
        self._model = model
        self._api_key: str | None = api_key
        self._timeout = float(timeout_s)
        self._http = _make_http_client(self._timeout)
        self._lock = asyncio.Lock()
        self._closed = False
        self._generation = 0
        self._replay: dict[int, _Replay] = {}
        self._replay_bytes = 0
        self._close_task: asyncio.Task[None] | None = None

    def _input(self, conversation: Conversation) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        pending: set[str] = set()
        if any(index >= len(conversation) for index in self._replay):
            raise ModelFormatError()
        for index, message in enumerate(conversation):
            if message.role == "assistant":
                if message.model_call_id is not None or pending:
                    raise ModelFormatError()
                cached = self._replay.get(index)
                if cached is not None:
                    if cached.signature != _signature(message):
                        raise ModelFormatError()
                    result.extend(cached.output)
                else:
                    if message.content:
                        result.append({"role": "assistant", "content": message.content})
                    for call in message.calls:
                        result.append(
                            {
                                "type": "function_call",
                                "name": _function_name(call.tool_ref),
                                "call_id": call.model_call_id,
                                "arguments": _encoded(call.arguments.to_dict()).decode("utf-8"),
                            }
                        )
                pending = {call.model_call_id for call in message.calls}
                if len(pending) != len(message.calls):
                    raise ModelFormatError()
            elif message.role == "tool":
                if message.calls or message.model_call_id not in pending:
                    raise ModelFormatError()
                pending.remove(message.model_call_id)
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.model_call_id,
                        "output": message.content,
                    }
                )
            else:
                if message.calls or message.model_call_id is not None or pending:
                    raise ModelFormatError()
                result.append({"role": message.role, "content": message.content})
        if pending:
            raise ModelFormatError()
        return result

    async def _request(self, body: bytes, generation: int) -> object:
        credential = self._api_key
        if credential is None or self._closed:
            raise ModelCompletionError()
        try:
            async with self._http.stream(
                "POST",
                _ENDPOINT,
                content=body,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code != 200:
                    raise ModelCompletionError()
                received = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(received) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ModelFormatError()
                    received.extend(chunk)
                if self._closed or generation != self._generation:
                    raise ModelCompletionError()
                return json.loads(received, object_pairs_hook=_pairs, parse_constant=_constant)
        except httpx.TimeoutException:
            # The common boundary translates TimeoutError to its fixed deadline.
            raise TimeoutError() from None
        except httpx.HTTPError:
            raise ModelCompletionError() from None
        except (UnicodeError, RecursionError, json.JSONDecodeError):
            raise ModelFormatError() from None
        finally:
            self._http.cookies.clear()

    async def step(self, conversation: Conversation, tools: tuple[ToolView, ...]) -> ModelTurn:
        async with self._lock:
            if self._closed:
                raise ModelCompletionError()
            generation = self._generation
            received: list[tuple[tuple[dict[str, object], ...], int]] = []

            async def complete(
                safe_history: Conversation, safe_tools: tuple[ToolView, ...]
            ) -> object:
                # Validate names/schemas before advertising anything to the provider.
                parse_model_turn({"text": None, "calls": []}, safe_tools)
                names = {_function_name(tool.tool_ref): tool.tool_ref for tool in safe_tools}
                if len(names) != len(safe_tools):
                    raise ModelFormatError()
                payload = {
                    "model": self._model,
                    "input": self._input(safe_history),
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                    "parallel_tool_calls": False,
                    "max_output_tokens": 16_384,
                    "tool_choice": "auto" if safe_tools else "none",
                    "tools": [
                        {
                            "type": "function",
                            "name": _function_name(tool.tool_ref),
                            "description": tool.description,
                            "parameters": tool.input_schema.to_dict(),
                            "strict": False,
                        }
                        for tool in safe_tools
                    ],
                }
                body = _encoded(payload)
                if len(body) > _MAX_REQUEST_BYTES:
                    raise ModelFormatError()
                raw = await self._request(body, generation)
                normalized, replay = _normalized_output(raw, names)
                size = len(_encoded(replay))
                if self._replay_bytes + size > _MAX_RESPONSE_BYTES:
                    raise ModelFormatError()
                received.append((replay, size))
                return normalized

            turn = await ProviderNeutralModelClient(complete, timeout_s=self._timeout).step(
                conversation, tools
            )
            if self._closed or generation != self._generation or len(received) != 1:
                raise ModelCompletionError()
            replay, size = received[0]
            expected = ConversationMessage(
                role="assistant", content=turn.text or "", calls=turn.calls
            )
            self._replay[len(conversation)] = _Replay(_signature(expected), replay)
            self._replay_bytes += size
            return turn

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._generation += 1
            self._api_key = None
            self._replay.clear()
            self._replay_bytes = 0
            self._close_task = asyncio.create_task(self._http.aclose())
        try:
            await asyncio.shield(self._close_task)
        except httpx.HTTPError:
            raise ModelCompletionError() from None
