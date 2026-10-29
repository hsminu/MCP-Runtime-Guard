"""Synthetic demo response validation only; no product/provider integration.

The provider-specific API remains a separate, explicitly selected integration.
This module accepts its normalized response as untrusted data and returns only
the B-owned ModelTurn/ProposedCall contracts. It never executes a tool.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable

from guard_app.domain.contracts import (
    Conversation,
    ConversationMessage,
    ModelTurn,
    ToolView,
)
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import ProposedCall


class ModelClient(Protocol):
    """A 구현. 새 Task의 대화·캐시·클라이언트 상태를 다른 Task와 분리한다(10.4절).

    tools는 ACTIVE 등록 ToolView만 받는다. 모델 제안은 ProposedCall로만 반환한다.
    conversation에는 ReleasedResult 또는 안전한 고정 상태 문구만 도구 결과로 둔다.
    """

    async def step(self, conversation: Conversation, tools: tuple[ToolView, ...]) -> ModelTurn: ...


class ModelFormatError(ValueError):
    """Fixed, content-free error for an invalid provider response."""

    def __init__(self) -> None:
        super().__init__("모델 응답 형식이 유효하지 않다")


class ModelDeadlineError(ModelFormatError):
    """Fixed, content-free error for a model completion deadline."""

    def __init__(self) -> None:
        ValueError.__init__(self, "모델 응답 대기 시간이 초과됐다")


class ModelCompletionError(ModelFormatError):
    """Fixed, content-free error for an unsuccessful provider completion."""

    def __init__(self) -> None:
        ValueError.__init__(self, "모델 요청을 완료할 수 없다")


type ModelCompletion = Callable[[Conversation, tuple[ToolView, ...]], Awaitable[object]]

DEFAULT_MODEL_COMPLETION_TIMEOUT_S = 60.0
MAX_MODEL_COMPLETION_TIMEOUT_S = 300.0


def _discard_late_completion(task: asyncio.Task[object]) -> None:
    """Retrieve a late failure without publishing its exception or response."""

    if not task.cancelled():
        task.exception()


# Standard exception families an injected provider completion can raise. The
# list is explicit (not a broad handler); anything else still propagates and
# the Runner stops without classifying it.
_PROVIDER_FAILURES = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
    RuntimeError,
)


# Bounds on one normalized provider response. The completion remains untrusted
# even when the provider transport has its own token or HTTP response limit.
MAX_MODEL_TEXT_CHARS = 65_536
MAX_MODEL_CALLS = 16
MAX_MODEL_ARGUMENT_CHARS = 65_536
MAX_MODEL_ARGUMENT_NODES = 4_096
MAX_MODEL_ARGUMENT_DEPTH = 64
MAX_MODEL_ARGUMENT_INTEGER_BITS = 4_096
MAX_MODEL_COMPLETION_CHARS = 262_144
_MAX_MODEL_JSON_STRUCTURE_TOKENS = 2 * MAX_MODEL_ARGUMENT_NODES

# These apply to the complete conversation sent on each model step, including
# committed tool results and prior call arguments. History is never truncated:
# dropping a result or decision could alter the meaning of the next proposal.
MAX_MODEL_CONVERSATION_MESSAGES = 128
MAX_MODEL_CONVERSATION_UTF8_BYTES = 1_048_576


def validate_model_conversation_budget(conversation: Conversation) -> None:
    """Reject an oversized normalized request before any provider egress.

    Count the UTF-8 bytes of compact JSON for every field the provider boundary
    receives. Count frozen call arguments without first thawing a large tree.
    The bound is local to one request; each turn includes all retained history.
    """

    if type(conversation) is not tuple or len(conversation) > MAX_MODEL_CONVERSATION_MESSAGES:
        raise ModelFormatError()
    remaining = [MAX_MODEL_CONVERSATION_UTF8_BYTES]

    def consume(size: int) -> None:
        if size > remaining[0]:
            raise ModelFormatError()
        remaining[0] -= size

    def walk(value: object) -> None:
        if type(value) is str:
            # UTF-8 uses at least one byte per code point. This check avoids
            # allocating an oversized encoded copy or escaped JSON string.
            if len(value) > remaining[0]:
                raise ModelFormatError()
            try:
                consume(len(json.dumps(value, ensure_ascii=False).encode("utf-8")))
            except UnicodeError:
                raise ModelFormatError() from None
        elif isinstance(value, (dict, FrozenJsonObject)):
            consume(2 + max(0, len(value) - 1))
            for key, child in value.items():
                walk(key)
                consume(1)  # colon
                walk(child)
        elif isinstance(value, (tuple, list)):
            consume(2 + max(0, len(value) - 1))
            for child in value:
                walk(child)
        elif type(value) is int:
            if value.bit_length() > MAX_MODEL_ARGUMENT_INTEGER_BITS:
                raise ModelFormatError()
            consume(len(str(value)))
        elif value is None or type(value) is bool:
            consume(4 if value is None or value is True else 5)
        else:
            raise ModelFormatError()

    consume(2 + max(0, len(conversation) - 1))
    for message in conversation:
        if not isinstance(message, ConversationMessage) or len(message.calls) > MAX_MODEL_CALLS:
            raise ModelFormatError()
        walk(
            {
                "role": message.role,
                "content": message.content,
                "calls": tuple(
                    {
                        "tool_ref": call.tool_ref,
                        "arguments": call.arguments,
                        "model_call_id": call.model_call_id,
                    }
                    for call in message.calls
                ),
                "model_call_id": message.model_call_id,
            }
        )


def _check_json_text_budget(value: str) -> int:
    """Bound JSON parsing and cheaply reject excessively wide structures."""

    if len(value) > MAX_MODEL_ARGUMENT_CHARS:
        raise ModelFormatError()
    in_string = False
    escaped = False
    structure_tokens = 0
    depth = 0
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[:,":
            # This is a conservative pre-parse bound, not a JSON parser. The
            # exact node count is checked on the decoded object below.
            structure_tokens += 1
            if structure_tokens > _MAX_MODEL_JSON_STRUCTURE_TOKENS:
                raise ModelFormatError()
            if char in "{[":
                depth += 1
                if depth > MAX_MODEL_ARGUMENT_DEPTH + 1:
                    raise ModelFormatError()
        elif char in "}]":
            depth -= 1
            if depth < 0:
                raise ModelFormatError()
    return len(value)


def _copy_object_budget(value: dict[str, object]) -> tuple[dict[str, object], int]:
    """Make a bounded argument copy before freezing or schema validation.

    The provider may retain its mutable response. Copying with a fixed work
    queue prevents it from growing a list after the budget check but before
    ``Arguments`` freezes the value.
    """

    nodes = 0
    chars = 0
    seen_containers: set[int] = set()
    copied_root: dict[str, object] | None = None
    pending: list[tuple[object, dict[str, object] | list[object] | None, str | int | None, int]] = [
        (value, None, None, 0)
    ]
    while pending:
        item, parent, slot, depth = pending.pop()
        nodes += 1
        if nodes > MAX_MODEL_ARGUMENT_NODES or depth > MAX_MODEL_ARGUMENT_DEPTH:
            raise ModelFormatError()
        copied: object
        if type(item) is dict:
            if id(item) in seen_containers:
                raise ModelFormatError()
            seen_containers.add(id(item))
            length = len(item)
            if length > MAX_MODEL_ARGUMENT_NODES - nodes - len(pending):
                raise ModelFormatError()
            copied = {}
            chars += 2 + max(0, length - 1)
            count = 0
            try:
                for key, child in item.items():
                    count += 1
                    if count > length or type(key) is not str:
                        raise ModelFormatError()
                    chars += len(key) + 3
                    if chars > MAX_MODEL_ARGUMENT_CHARS:
                        raise ModelFormatError()
                    pending.append((child, copied, key, depth + 1))
            except RuntimeError:
                raise ModelFormatError() from None
            if count != length or len(item) != length:
                raise ModelFormatError()
        elif type(item) is list:
            if id(item) in seen_containers:
                raise ModelFormatError()
            seen_containers.add(id(item))
            length = len(item)
            if length > MAX_MODEL_ARGUMENT_NODES - nodes - len(pending):
                raise ModelFormatError()
            copied_list: list[object] = [None] * length
            copied = copied_list
            chars += 2 + max(0, length - 1)
            try:
                for index in range(length):
                    pending.append((item[index], copied_list, index, depth + 1))
            except IndexError:
                raise ModelFormatError() from None
            if len(item) != length:
                raise ModelFormatError()
        elif type(item) is str:
            copied = item
            chars += len(item) + 2
        elif type(item) is int:
            bits = item.bit_length()
            if bits > MAX_MODEL_ARGUMENT_INTEGER_BITS:
                raise ModelFormatError()
            copied = item
            chars += max(1, bits * 30_103 // 100_000 + 1) + (item < 0)
        elif item is None or type(item) is bool:
            copied = item
            chars += 4 if item is None or item is True else 5
        else:
            raise ModelFormatError()
        if chars > MAX_MODEL_ARGUMENT_CHARS:
            raise ModelFormatError()
        if parent is None:
            copied_root = cast(dict[str, object], copied)
        elif type(parent) is dict:
            parent[cast(str, slot)] = copied
        else:
            cast(list[object], parent)[cast(int, slot)] = copied
    if copied_root is None:
        raise ModelFormatError()
    return copied_root, chars


def _check_completion_budget(
    text: str | None, calls: list[object]
) -> tuple[tuple[str, str, str | dict[str, object]], ...]:
    """Snapshot one bounded completion before parsing or retaining calls."""

    if (text is not None and len(text) > MAX_MODEL_TEXT_CHARS) or len(calls) > MAX_MODEL_CALLS:
        raise ModelFormatError()
    total_chars = len(text) if text is not None else 0
    prepared: list[tuple[str, str, str | dict[str, object]]] = []
    for raw in calls[: MAX_MODEL_CALLS + 1]:
        if (
            type(raw) is not dict
            or len(raw) != 3
            or "tool_ref" not in raw
            or "arguments" not in raw
            or "model_call_id" not in raw
        ):
            raise ModelFormatError()
        try:
            name = raw["tool_ref"]
            call_id = raw["model_call_id"]
            argument_value = raw["arguments"]
        except KeyError:
            raise ModelFormatError() from None
        if type(name) is not str or type(call_id) is not str:
            raise ModelFormatError()
        total_chars += len(name) + len(call_id)
        if total_chars > MAX_MODEL_COMPLETION_CHARS:
            raise ModelFormatError()
        prepared_argument: str | dict[str, object]
        if type(argument_value) is str:
            total_chars += _check_json_text_budget(argument_value)
            prepared_argument = argument_value
        elif type(argument_value) is dict:
            prepared_argument, argument_chars = _copy_object_budget(argument_value)
            total_chars += argument_chars
        else:
            raise ModelFormatError()
        if total_chars > MAX_MODEL_COMPLETION_CHARS:
            raise ModelFormatError()
        prepared.append((name, call_id, prepared_argument))
    if len(prepared) > MAX_MODEL_CALLS:
        raise ModelFormatError()
    return tuple(prepared)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ModelFormatError()
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise ModelFormatError()


def _arguments(value: object) -> Arguments:
    if type(value) is str:
        _check_json_text_budget(value)
        try:
            value = json.loads(
                value,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except (ValueError, RecursionError):
            raise ModelFormatError() from None
    if type(value) is not dict:
        raise ModelFormatError()
    value, _ = _copy_object_budget(value)
    try:
        return Arguments(value)
    except (ValueError, RecursionError):
        raise ModelFormatError() from None


def parse_model_turn(value: object, tools: tuple[ToolView, ...]) -> ModelTurn:
    """Validate an untrusted normalized model response against advertised tools.

    A provider adapter must normalize to ``{"text": str | None, "calls": list}``.
    Each call must have exactly ``tool_ref``, ``arguments`` and ``model_call_id``.
    Arguments may be a JSON object or an object encoded as JSON text; duplicate
    JSON keys and all unknown fields are rejected rather than stripped.
    """

    if type(tools) is not tuple or any(not isinstance(tool, ToolView) for tool in tools):
        raise ModelFormatError()
    names = tuple(tool.tool_ref for tool in tools)
    if len(names) != len(set(names)) or any("__" not in name for name in names):
        raise ModelFormatError()
    # Catalog.tool_views has already produced a closed, immutable schema. Its
    # output is validated directly: wrapping it in ArgumentSchema again would
    # treat Catalog's internal allOf closure as a new registered schema.
    schemas = {tool.tool_ref: tool.input_schema for tool in tools}
    if type(value) is not dict or len(value) != 2 or "text" not in value or "calls" not in value:
        raise ModelFormatError()
    try:
        text = value["text"]
        calls = value["calls"]
    except KeyError:
        raise ModelFormatError() from None
    if (text is not None and type(text) is not str) or type(calls) is not list:
        raise ModelFormatError()
    prepared_calls = _check_completion_budget(text, calls)
    if text is not None and any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ModelFormatError()

    proposals: list[ProposedCall] = []
    seen_call_ids: set[str] = set()
    for name, call_id, raw_arguments in prepared_calls:
        if name not in names:
            raise ModelFormatError()
        if call_id in seen_call_ids:
            raise ModelFormatError()
        seen_call_ids.add(call_id)
        try:
            arguments = _arguments(raw_arguments)
            schema = schemas[name].to_dict()
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(
                    formats=("date", "email", "ipv4", "ipv6", "regex", "uuid")
                ),
                registry=Registry(),
            ).validate(arguments.to_dict())
            proposals.append(
                ProposedCall(tool_ref=name, arguments=arguments, model_call_id=call_id)
            )
        except (
            ValidationError,
            JsonSchemaValidationError,
            SchemaError,
            Unresolvable,
            re.error,
            # The "regex" format compiles a model-chosen pattern. A huge
            # repetition count or deep nesting fails outside re.error.
            OverflowError,
            RecursionError,
        ):
            raise ModelFormatError() from None
    try:
        return ModelTurn(text=text, calls=tuple(proposals))
    except ValidationError:
        raise ModelFormatError() from None


class ProviderNeutralModelClient:
    """A ModelClient implementation with an injected, task-owned completion.

    The completion has no tool execution capability. Construct a new instance
    and backend for every Task; this class retains no global conversation or
    provider handle. Real provider transport and environment policy are A20.
    """

    def __init__(
        self,
        completion: ModelCompletion,
        *,
        timeout_s: float = DEFAULT_MODEL_COMPLETION_TIMEOUT_S,
    ) -> None:
        if not callable(completion):
            raise TypeError("모델 completion이 필요하다")
        if (
            type(timeout_s) not in (int, float)
            or timeout_s <= 0
            or timeout_s > MAX_MODEL_COMPLETION_TIMEOUT_S
            or not math.isfinite(timeout_s)
        ):
            raise ValueError("유효한 모델 응답 제한 시간이 필요하다")
        self._completion = completion
        self._timeout_s = float(timeout_s)

    async def step(self, conversation: Conversation, tools: tuple[ToolView, ...]) -> ModelTurn:
        if type(conversation) is not tuple or type(tools) is not tuple:
            raise ModelFormatError()
        if len(conversation) > MAX_MODEL_CONVERSATION_MESSAGES:
            raise ModelFormatError()
        # B contracts are frozen. Re-validation rejects adapter callers that
        # try to smuggle a mutable or malformed message into the backend.
        try:
            safe_conversation = tuple(ConversationMessage.model_validate(m) for m in conversation)
            safe_tools = tuple(ToolView.model_validate(tool) for tool in tools)
        except ValidationError:
            raise ModelFormatError() from None
        validate_model_conversation_budget(safe_conversation)

        async def complete() -> object:
            return await self._completion(safe_conversation, safe_tools)

        # asyncio.wait returns at the deadline even if a provider coroutine
        # suppresses cancellation. Never consume a completion after timeout.
        completion_task = asyncio.create_task(complete())
        try:
            done, _ = await asyncio.wait({completion_task}, timeout=self._timeout_s)
        except asyncio.CancelledError:
            completion_task.add_done_callback(_discard_late_completion)
            completion_task.cancel()
            raise
        if not done:
            completion_task.add_done_callback(_discard_late_completion)
            completion_task.cancel()
            raise ModelDeadlineError()
        if completion_task.cancelled():
            raise ModelCompletionError() from None
        try:
            raw = completion_task.result()
        except TimeoutError:
            raise ModelDeadlineError() from None
        except _PROVIDER_FAILURES:
            # Provider messages can echo credentials or request content. Only
            # the fixed completion error leaves this boundary.
            raise ModelCompletionError() from None
        return parse_model_turn(raw, safe_tools)
