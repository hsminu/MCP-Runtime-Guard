"""Provider-neutral model boundary for B's ModelClient port.

The provider-specific API remains a separate, explicitly selected integration.
This module accepts its normalized response as untrusted data and returns only
the B-owned ModelTurn/ProposedCall contracts. It never executes a tool.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

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
from guard_app.domain.json_types import Arguments
from guard_app.domain.models import ProposedCall


class ModelFormatError(ValueError):
    """Fixed, content-free error for an invalid provider response."""

    def __init__(self) -> None:
        super().__init__("모델 응답 형식이 유효하지 않다")


type ModelCompletion = Callable[[Conversation, tuple[ToolView, ...]], Awaitable[object]]


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

    if type(tools) is not tuple or any(
        not isinstance(tool, ToolView) for tool in tools
    ):
        raise ModelFormatError()
    names = tuple(tool.tool_ref for tool in tools)
    if len(names) != len(set(names)) or any("__" not in name for name in names):
        raise ModelFormatError()
    # Catalog.tool_views has already produced a closed, immutable schema. Its
    # output is validated directly: wrapping it in ArgumentSchema again would
    # treat Catalog's internal allOf closure as a new registered schema.
    schemas = {tool.tool_ref: tool.input_schema for tool in tools}
    if type(value) is not dict or set(value) != {"text", "calls"}:
        raise ModelFormatError()
    text = value["text"]
    calls = value["calls"]
    if (text is not None and type(text) is not str) or type(calls) is not list:
        raise ModelFormatError()
    if text is not None and any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ModelFormatError()

    proposals: list[ProposedCall] = []
    seen_call_ids: set[str] = set()
    for raw in calls:
        if type(raw) is not dict or set(raw) != {
            "tool_ref",
            "arguments",
            "model_call_id",
        }:
            raise ModelFormatError()
        name = raw["tool_ref"]
        call_id = raw["model_call_id"]
        if type(name) is not str or name not in names or type(call_id) is not str:
            raise ModelFormatError()
        if call_id in seen_call_ids:
            raise ModelFormatError()
        seen_call_ids.add(call_id)
        try:
            arguments = _arguments(raw["arguments"])
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

    def __init__(self, completion: ModelCompletion) -> None:
        if not callable(completion):
            raise TypeError("모델 completion이 필요하다")
        self._completion = completion

    async def step(
        self, conversation: Conversation, tools: tuple[ToolView, ...]
    ) -> ModelTurn:
        if type(conversation) is not tuple or type(tools) is not tuple:
            raise ModelFormatError()
        # B contracts are frozen. Re-validation rejects adapter callers that
        # try to smuggle a mutable or malformed message into the backend.
        try:
            safe_conversation = tuple(
                ConversationMessage.model_validate(m) for m in conversation
            )
            safe_tools = tuple(ToolView.model_validate(tool) for tool in tools)
        except ValidationError:
            raise ModelFormatError() from None
        raw = await self._completion(safe_conversation, safe_tools)
        return parse_model_turn(raw, safe_tools)
