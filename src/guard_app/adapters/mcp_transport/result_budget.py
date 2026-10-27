"""The pre-copy resource budget for an SDK tools/call result."""

from __future__ import annotations

import asyncio
from typing import cast

from mcp.types import CallToolResult
from pydantic import AnyUrl, BaseModel

_MAX_RESULT_CONTENT_BLOCKS = 256
_MAX_RESULT_VALUE_NODES = 4096


# The name is DispatchError.exception_type audit metadata, so it keeps no Error suffix.
class ResultTooLarge(ValueError):  # noqa: N818
    """An SDK result exceeds the local pre-copy resource budget."""


async def check_result_budget(result: object, *, max_copy_bytes: int) -> None:
    """Walk an SDK result without first making its JSON or frozen copy.

    The MCP SDK has already parsed and materialized the wire response. C's
    stream ingress still needs an independent wire-size limit. This check
    bounds the next synchronous conversion, including nested structured
    content and otherwise ignored metadata.
    """

    if not isinstance(result, CallToolResult):
        raise TypeError("invalid MCP result")
    if len(result.content) > _MAX_RESULT_CONTENT_BLOCKS:
        raise ResultTooLarge()

    remaining_bytes = max_copy_bytes
    nodes = 0
    seen_containers: set[int] = set()
    pending: list[tuple[object, int]] = [(result, 0)]

    def debit(amount: int) -> None:
        nonlocal remaining_bytes
        if amount > remaining_bytes:
            raise ResultTooLarge()
        remaining_bytes -= amount

    def debit_string(value: str) -> None:
        # UTF-8 cannot use fewer bytes than the Python string has characters.
        # Reject a large field before allocating a second, encoded copy.
        if len(value) > remaining_bytes:
            raise ResultTooLarge()
        debit(len(value.encode("utf-8")))

    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_RESULT_VALUE_NODES or depth > 64:
            raise ResultTooLarge()
        if nodes % 128 == 0:
            # Let shutdown/control and the call deadline run during a large
            # (but still bounded) structured result walk.
            await asyncio.sleep(0)
        # Container delimiters, aliases and scalar notation also take space
        # in the eventual JSON copy; this is a conservative fixed allowance.
        debit(8)
        if value is None or type(value) is bool:
            continue
        if type(value) is str:
            debit_string(value)
            continue
        if type(value) is bytes:
            if len(value) > remaining_bytes:
                raise ResultTooLarge()
            debit(4 * ((len(value) + 2) // 3))
            continue
        if type(value) is int:
            # Avoid converting an arbitrary-size integer to a new string.
            debit(max(1, (value.bit_length() + 2) // 3))
            continue
        if type(value) is float:
            debit(32)
            continue
        if isinstance(value, AnyUrl):
            # The SDK URL type exposes no cheap length operation. ResourceLink
            # content is unsupported by the current ResponseGate, so fail
            # closed without making an unbounded URI string copy.
            raise TypeError("unsupported MCP result URL")
        children: dict[str, object] | list[object] | tuple[object, ...]
        if isinstance(value, BaseModel):
            children = vars(value)
            extra = value.model_extra
            if extra:
                pending.append((extra, depth + 1))
        elif type(value) in (dict, list, tuple):
            children = cast("dict[str, object] | list[object] | tuple[object, ...]", value)
        else:
            raise ValueError("unsupported MCP result value")

        identity = id(value)
        if identity in seen_containers:
            raise ResultTooLarge()
        seen_containers.add(identity)
        if len(children) > _MAX_RESULT_VALUE_NODES - nodes - len(pending):
            raise ResultTooLarge()
        if type(children) is dict:
            for key, child in children.items():
                if type(key) is not str:
                    raise ValueError("non-string MCP result key")
                debit_string(key)
                pending.append((child, depth + 1))
        else:
            pending.extend((child, depth + 1) for child in children)
