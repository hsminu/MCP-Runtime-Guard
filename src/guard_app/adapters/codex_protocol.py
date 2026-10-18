"""Pure Codex app-server 0.162.1 message builders and parsers (10.4, I7/I11).

Facts are from the pinned upstream commit 092d3acd6bec3e3a14bdc7e7a2810ab628ab759d
(app-server-protocol v2, request_processors/thread_processor.rs). No I/O here.

- `initialize` must declare `capabilities.experimentalApi`, otherwise the
  experimental `thread/start` fields `dynamicTools`/`environments` are refused.
- Dynamic tool and namespace names must match `^[a-zA-Z0-9_-]+$` (tool <= 128,
  namespace <= 64 characters), must not be `mcp` or start with `mcp__`, and the
  namespace must not be one of Codex's reserved namespaces. Contracts 2026-10-17
  allow any MCP tool name, so a tool outside these rules is left out of the
  model's list and reported instead of failing `thread/start` (A88/A92).
- `item/tool/call` params are `{threadId, turnId, callId, namespace?, tool,
  arguments}`; the answer is `{contentItems: [{type: "inputText", text}], success}`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from guard_app.domain.contracts import ReleasedResult, ToolView

CLIENT_NAME = "mcp_runtime_guard"
CLIENT_TITLE = "MCP Runtime Guard"
MAX_TURN_TEXT_CHARS = 1_048_576
TOOL_NAME_MAX = 128
NAMESPACE_MAX = 64
NAMESPACE_DESCRIPTION = "Tools reached only through MCP Runtime Guard."
_NAME = re.compile(r"[A-Za-z0-9_-]+")
RESERVED_NAMESPACES = frozenset(
    {
        "functions",
        "web",
        "browser",
        "python",
        "terminal",
        "file_search",
        "tool_search",
        "api_tool",
        "computer",
        "container",
        "image_gen",
        "multi_tool_use",
        "python_user_visible",
        "submodel_delegator",
    }
)
_CALL_FIELDS = frozenset({"threadId", "turnId", "callId", "namespace", "tool", "arguments"})


class CodexProtocolError(ValueError):
    """Fixed code only; never echoes model or server content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _codex_name(value: str, limit: int) -> bool:
    return (
        0 < len(value) <= limit
        and _NAME.fullmatch(value) is not None
        and value != "mcp"
        and not value.startswith("mcp__")
    )


def initialize_params(version: str) -> dict[str, object]:
    return {
        "clientInfo": {"name": CLIENT_NAME, "title": CLIENT_TITLE, "version": version},
        "capabilities": {"experimentalApi": True},
    }


@dataclass(frozen=True, slots=True)
class DynamicToolsPlan:
    """Namespaced tools Codex accepts, plus Guard tool_refs left out by name rules."""

    tools: tuple[dict[str, object], ...]
    excluded: tuple[str, ...]
    tool_refs: frozenset[str]


def build_dynamic_tools(views: tuple[ToolView, ...]) -> DynamicToolsPlan:
    """Group ToolView `server__tool` refs under one namespace per server."""

    grouped: dict[str, list[dict[str, object]]] = {}
    excluded: list[str] = []
    accepted: set[str] = set()
    for view in views:
        server_id, separator, tool_name = view.tool_ref.partition("__")
        if (
            not separator
            or not _codex_name(server_id, NAMESPACE_MAX)
            or server_id in RESERVED_NAMESPACES
            or not _codex_name(tool_name, TOOL_NAME_MAX)
            or view.tool_ref in accepted
        ):
            excluded.append(view.tool_ref)
            continue
        accepted.add(view.tool_ref)
        grouped.setdefault(server_id, []).append(
            {
                "type": "function",
                "name": tool_name,
                "description": view.description,
                "inputSchema": view.input_schema.to_dict(),
            }
        )
    tools: tuple[dict[str, object], ...] = tuple(
        {
            "type": "namespace",
            "name": server_id,
            "description": NAMESPACE_DESCRIPTION,
            "tools": entries,
        }
        for server_id, entries in grouped.items()
    )
    return DynamicToolsPlan(tools, tuple(excluded), frozenset(accepted))


def thread_start_params(
    *, model: str, base_instructions: str, plan: DynamicToolsPlan
) -> dict[str, object]:
    return {
        "model": model,
        "approvalsReviewer": "user",
        "ephemeral": True,
        "environments": [],
        "baseInstructions": base_instructions,
        "dynamicTools": list(plan.tools),
    }


def turn_start_params(thread_id: str, text: str) -> dict[str, object]:
    if type(text) is not str or not text or len(text) > MAX_TURN_TEXT_CHARS:
        raise CodexProtocolError("CODEX_INPUT_INVALID")
    return {"threadId": thread_id, "input": [{"type": "text", "text": text}]}


def turn_interrupt_params(thread_id: str, turn_id: str) -> dict[str, object]:
    return {"threadId": thread_id, "turnId": turn_id}


@dataclass(frozen=True, slots=True, repr=False)
class DynamicToolCall:
    thread_id: str
    turn_id: str
    call_id: str
    namespace: str | None
    tool: str
    arguments: dict[str, object]


def parse_dynamic_tool_call(params: object) -> DynamicToolCall:
    """Strict: unknown fields, missing fields or wrong types fail closed."""

    if type(params) is not dict or not set(params) <= _CALL_FIELDS:
        raise CodexProtocolError("CODEX_TOOL_CALL_INVALID")
    values = {key: params.get(key) for key in ("threadId", "turnId", "callId", "tool")}
    if any(type(value) is not str or not value for value in values.values()):
        raise CodexProtocolError("CODEX_TOOL_CALL_INVALID")
    namespace = params.get("namespace")
    if namespace is not None and (type(namespace) is not str or not namespace):
        raise CodexProtocolError("CODEX_TOOL_CALL_INVALID")
    arguments = params.get("arguments")
    if type(arguments) is not dict:
        raise CodexProtocolError("CODEX_TOOL_CALL_INVALID")
    return DynamicToolCall(
        thread_id=str(values["threadId"]),
        turn_id=str(values["turnId"]),
        call_id=str(values["callId"]),
        namespace=namespace,
        tool=str(values["tool"]),
        arguments=arguments,
    )


def tool_call_response(success: bool, texts: tuple[str, ...]) -> dict[str, object]:
    items = texts or ("",)
    return {
        "contentItems": [{"type": "inputText", "text": text} for text in items],
        "success": success is True,
    }


def released_texts(result: ReleasedResult) -> tuple[str, ...]:
    """The released text, then any structured content as one JSON string."""

    structured: Mapping[str, object] | None = (
        None if result.structured_content is None else result.structured_content.to_dict()
    )
    if structured is None:
        return (result.text,)
    encoded = json.dumps(structured, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return (result.text, encoded) if result.text else (encoded,)
