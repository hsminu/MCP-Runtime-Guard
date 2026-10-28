"""The pre-copy resource budget for an SDK tools/call result."""

from __future__ import annotations

import asyncio
from typing import cast

from mcp.types import CallToolResult
from pydantic import AnyUrl, BaseModel

_MAX_RESULT_CONTENT_BLOCKS = 256
_MAX_RESULT_VALUE_NODES = 4096


# The name is DispatchError.exception_type audit metadata, so it keeps no Error suffix.
# Renaming it would make new audit rows disagree with the ones already stored (#40).
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
    await _ResultWalk(max_copy_bytes).run(result)


type _Children = dict[str, object] | list[object] | tuple[object, ...]


class _ResultWalk:
    """One budgeted walk: what the eventual JSON copy may still take, and what is left to visit."""

    def __init__(self, max_copy_bytes: int) -> None:
        self._remaining_bytes = max_copy_bytes
        self._nodes = 0
        self._seen_containers: set[int] = set()
        self._pending: list[tuple[object, int]] = []

    async def run(self, result: CallToolResult) -> None:
        self._pending.append((result, 0))
        while self._pending:
            value, depth = self._pending.pop()
            self._nodes += 1
            if self._nodes > _MAX_RESULT_VALUE_NODES or depth > 64:
                raise ResultTooLarge()
            if self._nodes % 128 == 0:
                # Let shutdown/control and the call deadline run during a large
                # (but still bounded) structured result walk.
                await asyncio.sleep(0)
            # Container delimiters, aliases and scalar notation also take space
            # in the eventual JSON copy; this is a conservative fixed allowance.
            self._debit(8)
            if not self._debit_scalar(value):
                self._expand(value, depth)

    def _debit(self, amount: int) -> None:
        if amount > self._remaining_bytes:
            raise ResultTooLarge()
        self._remaining_bytes -= amount

    def _debit_string(self, value: str) -> None:
        # UTF-8 cannot use fewer bytes than the Python string has characters.
        # Reject a large field before allocating a second, encoded copy.
        if len(value) > self._remaining_bytes:
            raise ResultTooLarge()
        self._debit(len(value.encode("utf-8")))

    def _debit_scalar(self, value: object) -> bool:
        """Debit a scalar's copy; False when `value` is a container to walk."""
        if value is None or type(value) is bool:
            return True
        if type(value) is str:
            self._debit_string(value)
            return True
        if type(value) is bytes:
            if len(value) > self._remaining_bytes:
                raise ResultTooLarge()
            self._debit(4 * ((len(value) + 2) // 3))
            return True
        if type(value) is int:
            # Avoid converting an arbitrary-size integer to a new string.
            self._debit(max(1, (value.bit_length() + 2) // 3))
            return True
        if type(value) is float:
            self._debit(32)
            return True
        if isinstance(value, AnyUrl):
            # The SDK URL type exposes no cheap length operation. ResourceLink
            # content is unsupported by the current ResponseGate, so fail
            # closed without making an unbounded URI string copy.
            raise TypeError("unsupported MCP result URL")
        return False

    def _expand(self, value: object, depth: int) -> None:
        children = self._children(value, depth)
        identity = id(value)
        if identity in self._seen_containers:
            raise ResultTooLarge()
        self._seen_containers.add(identity)
        if len(children) > _MAX_RESULT_VALUE_NODES - self._nodes - len(self._pending):
            raise ResultTooLarge()
        if type(children) is dict:
            for key, child in children.items():
                if type(key) is not str:
                    raise ValueError("non-string MCP result key")
                self._debit_string(key)
                self._pending.append((child, depth + 1))
        else:
            self._pending.extend((child, depth + 1) for child in children)

    def _children(self, value: object, depth: int) -> _Children:
        if isinstance(value, BaseModel):
            children = vars(value)
            extra = value.model_extra
            if extra:
                self._pending.append((extra, depth + 1))
            return children
        if type(value) in (dict, list, tuple):
            return cast("_Children", value)
        raise ValueError("unsupported MCP result value")
