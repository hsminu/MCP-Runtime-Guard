"""A-local, pre-parse tools/list ingress over externally owned byte sources.

The trusted owner supplies cancellable read(n), write-all and drain operations;
this module never launches or closes a process. During one collection every
candidate JSON-RPC message must fit both the page and remaining listing budget
before UTF-8/JSON parsing. This conservative candidate rule includes concurrent
notifications: their response association cannot be trusted before parsing.
Only exact responses to registered tools/list integer IDs debit the total.
Request IDs increase across collections, so late old pages cannot charge a new
collection. Admission and SDK delivery are separated by backpressure, so the
SDK-facing stream re-checks each response at delivery (check_delivery).
Outside collection these limits do not change ordinary tool results.
All failures are sticky and contain fixed codes, never source/payload text.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import NoReturn


class IngressError(RuntimeError):
    """A sanitized receive failure; a failed connection is never retried."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class IngressGate:
    """One connection's listing scopes; used only on its owning event loop."""

    def __init__(self) -> None:
        self.active = False
        self.reason: str | None = None
        self.failed = asyncio.Event()
        self._page_bytes = 0
        self._remaining_bytes = 0
        self._pending: set[int] = set()
        self._last_id = -1

    @property
    def candidate_limit(self) -> int:
        return min(self._page_bytes, self._remaining_bytes)

    def begin(self, page_bytes: int, total_bytes: int) -> None:
        if self.reason is not None:
            raise IngressError(self.reason)
        if self.active:
            raise ValueError("tools/list collection already active")
        if any(type(n) is not int or n < 1 for n in (page_bytes, total_bytes)):
            raise ValueError("positive integer ingress limits required")
        self._page_bytes = page_bytes
        self._remaining_bytes = total_bytes
        self._pending.clear()
        self.active = True

    def end(self) -> None:
        self.active = False
        self._pending.clear()

    def expect(self, request_id: int) -> None:
        if self.reason is not None:
            raise IngressError(self.reason)
        if not self.active or type(request_id) is not int or request_id <= self._last_id:
            self.fail("TOOLS_LIST_REQUEST_ID_INVALID")
        self._last_id = request_id
        self._pending.add(request_id)

    def fail(self, reason: str) -> NoReturn:
        if self.reason is None:
            self.reason = reason
        self.failed.set()
        raise IngressError(self.reason) from None

    def check_size(self, size: int) -> None:
        if self.reason is not None:
            raise IngressError(self.reason)
        if not self.active:
            return
        if size > self._page_bytes:
            self.fail("TOOLS_LIST_PAGE_TOO_LARGE")
        if size > self._remaining_bytes:
            self.fail("TOOLS_LIST_TOTAL_TOO_LARGE")

    def accept(self, payload: bytes) -> bool:
        self.check_size(len(payload))
        if not self.active:
            return False
        # Parsing is solely for association AFTER the raw bytes fit. The SDK
        # remains responsible for validating the complete JSON-RPC envelope.
        try:
            value = json.loads(payload)
        except (ValueError, RecursionError):
            self.fail("TOOLS_LIST_MALFORMED")
        if type(value) is not dict or not ({"result", "error"} & value.keys()):
            return False
        if "method" in value or ("result" in value and "error" in value):
            self.fail("TOOLS_LIST_MALFORMED")
        response_id = value.get("id")
        if type(response_id) is int and response_id in self._pending:
            self._remaining_bytes -= len(payload)
            self._pending.remove(response_id)
            return True
        # mcp 1.29 normalizes numeric strings and pydantic coerces bool/float
        # IDs. Reject those aliases before they can bypass exact association.
        alias: object = response_id
        if type(response_id) is str:
            try:
                alias = int(response_id)
            except ValueError:
                return False
        if type(alias) in (int, bool, float) and alias in self._pending:
            self.fail("TOOLS_LIST_RESPONSE_ID_INVALID")
        return False

    def check_delivery(self, response_id: object) -> None:
        """Run as the SDK takes a response, with no await before its ID lookup.

        A frame admitted before its tools/list ID was expected (pre-sent by a
        peer that predicted the ID, then held behind backpressure) was never
        debited. If its ID is still pending at delivery, it must not complete
        the listing. Uses the SDK's own str->int normalization.
        """
        alias = response_id
        if type(alias) is str:
            try:
                alias = int(alias)
            except ValueError:
                return
        if type(alias) is int and alias in self._pending:
            self.fail("TOOLS_LIST_RESPONSE_UNADMITTED")


class PipeFrameReader:
    """LF-framed MCP bytes, with checks before decode or JSON validation.

    LF is framing and is excluded. A CR preceding LF remains JSON whitespace
    and counts. read(n) must return bytes of length <= n, with b'' at EOF.
    Cancellation ends local admission; source lifetime belongs to the owner.
    """

    def __init__(self, read_bytes: Callable[[int], Awaitable[bytes]], gate: IngressGate) -> None:
        self._read = read_bytes
        self._gate = gate
        self._buffer = bytearray()
        self._eof = False

    async def receive(self) -> bytes | None:
        if self._eof:
            return None
        try:
            while True:
                newline = self._buffer.find(b"\n")
                if newline >= 0:
                    self._gate.check_size(newline)
                    frame = bytes(self._buffer[:newline])
                    del self._buffer[: newline + 1]
                    self._gate.accept(frame)
                    return frame
                self._gate.check_size(len(self._buffer))
                limit = 4096
                if self._gate.active:
                    limit = min(limit, self._gate.candidate_limit + 1 - len(self._buffer))
                chunk = await self._read(limit)
                if type(chunk) is not bytes or len(chunk) > limit:
                    self._gate.fail("TOOLS_LIST_PIPE_READ_INVALID")
                if not chunk:
                    self._eof = True
                    if self._buffer or self._gate.active:
                        self._gate.fail("TOOLS_LIST_INGRESS_EOF")
                    return None
                self._buffer.extend(chunk)
        except (OSError, EOFError, RuntimeError):
            self._gate.fail("TOOLS_LIST_INGRESS_IO_FAILED")
        except asyncio.CancelledError:
            self._buffer.clear()
            self._eof = True
            raise
