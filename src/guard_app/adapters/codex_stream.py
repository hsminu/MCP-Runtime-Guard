"""A-local newline framing over injected Codex stdin/stdout byte operations.

Pinned Codex 0.162.1 reads stdin with ``BufRead.lines`` and appends LF to each
stdout JSON message (openai/codex commit 092d3acd6bec3e3a14bdc7e7a2810ab628ab759d,
``codex-rs/app-server-transport/src/transport/stdio.rs``, lines 68, 129-134).
This is Codex framing, separate from C's managed MCP ingress.

The owner supplies cancellable async byte operations: read(n) returns at most
n bytes and b"" at EOF; write(data) writes all bytes or fails; drain() confirms
backpressure completion. These are local seams, not a launcher return contract.
No process, file, source close, or source deletion is performed here.

The receive buffer never exceeds max_frame_bytes + 1 (the extra byte detects
LF or an oversized line), and JSON/UTF-8 parsing follows framing in
JsonRpcClient. LF is removed; CR in CRLF remains JSON whitespace and counts
toward the frame limit. Empty/whitespace lines and unterminated EOF fail closed.
Cancellation or failure closes admission without retrying a possibly partial
write. The owner must terminate underlying sources if they ignore cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast


class CodexStreamError(RuntimeError):
    """A fixed framing/I/O failure without a payload or source exception."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CodexStreamChannel:
    """One asyncio-loop-owned FrameChannel; close only invalidates local I/O."""

    def __init__(
        self,
        read_bytes: Callable[[int], Awaitable[bytes]],
        write_bytes: Callable[[bytes], Awaitable[None]],
        drain: Callable[[], Awaitable[None]],
        *,
        max_frame_bytes: int,
        read_chunk_bytes: int = 4096,
    ) -> None:
        if any(type(n) is not int or n < 1 for n in (max_frame_bytes, read_chunk_bytes)):
            raise ValueError("positive integer stream limits required")
        self._read_bytes = read_bytes
        self._write_bytes = write_bytes
        self._drain = drain
        self._max_frame_bytes = max_frame_bytes
        self._read_chunk_bytes = read_chunk_bytes
        self._buffer = bytearray()
        self._receive_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._closed_reason: str | None = None
        self._operations: set[asyncio.Future[object]] = set()

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def close(self) -> None:
        """Wake local waiters; underlying source/process ownership stays external."""
        self._stop("CLOSED")

    def _stop(self, reason: str) -> None:
        if self._closed_reason is None:
            self._closed_reason = reason
        self._buffer.clear()
        self._closed.set()
        for operation in tuple(self._operations):
            if not operation.done():
                operation.cancel()

    def _fail(self, reason: str) -> None:
        self._stop(reason)
        raise CodexStreamError(self._closed_reason or reason)

    def _require_open(self) -> None:
        if self._closed_reason is not None:
            raise CodexStreamError(self._closed_reason)

    def _operation_done(self, operation: asyncio.Future[object]) -> None:
        self._operations.discard(operation)
        if not operation.cancelled():
            # Even a source that ignores cancellation cannot log a late raw error.
            operation.exception()

    async def _io[T](self, awaitable: Awaitable[T]) -> T:
        operation = asyncio.ensure_future(awaitable)
        tracked = cast("asyncio.Future[object]", operation)
        self._operations.add(tracked)
        operation.add_done_callback(lambda _done: self._operation_done(tracked))
        closing = asyncio.create_task(self._closed.wait())
        try:
            await asyncio.wait((operation, closing), return_when=asyncio.FIRST_COMPLETED)
            self._require_open()
            if operation.cancelled() or operation.exception() is not None:
                self._fail("IO_FAILED")
            return operation.result()
        finally:
            closing.cancel()
            if not operation.done():
                operation.cancel()

    async def _read(self, limit: int) -> bytes:
        return await self._read_bytes(limit)

    async def _write(self, frame: bytes) -> None:
        await self._write_bytes(frame)

    async def _flush(self) -> None:
        await self._drain()

    async def receive(self) -> bytes | None:
        try:
            async with self._receive_lock:
                if self._closed_reason == "EOF":
                    return None
                self._require_open()
                while True:
                    newline = self._buffer.find(b"\n")
                    if newline >= 0:
                        frame = bytes(self._buffer[:newline])
                        del self._buffer[: newline + 1]
                        if not frame.strip():
                            self._fail("EMPTY_FRAME")
                        return frame
                    if len(self._buffer) > self._max_frame_bytes:
                        self._fail("FRAME_TOO_LARGE")
                    limit = min(
                        self._read_chunk_bytes, self._max_frame_bytes + 1 - len(self._buffer)
                    )
                    chunk = await self._io(self._read(limit))
                    if type(chunk) is not bytes or len(chunk) > limit:
                        self._fail("READ_CONTRACT")
                    if not chunk:
                        if self._buffer:
                            self._fail("PARTIAL_EOF")
                        self._stop("EOF")
                        return None
                    self._buffer.extend(chunk)
        except asyncio.CancelledError:
            self._stop("CANCELLED")
            raise

    async def send(self, frame: bytes) -> None:
        try:
            async with self._send_lock:
                self._require_open()
                if type(frame) is not bytes or not frame.strip() or b"\n" in frame:
                    self._fail("INVALID_FRAME")
                if len(frame) > self._max_frame_bytes:
                    self._fail("FRAME_TOO_LARGE")
                await self._io(self._write(frame + b"\n"))
                self._require_open()
                await self._io(self._flush())
        except asyncio.CancelledError:
            self._stop("CANCELLED")
            raise
