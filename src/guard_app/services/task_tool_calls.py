"""A-local FIFO for one trusted Task's Codex tool calls (10.4, I6/I7/I8).

Create one queue per fresh Task/session and admit ``item/tool/call`` messages in
receive order. ``call_id`` is Codex's tool callId, never the JSON-RPC request id.
The injected handler must encompass the complete call, including any approval
wait, before returning a result. A future integration must use Guard.submit as
its only execution entry and supply the agreed Pending/STOP contracts. This
module neither dispatches tools nor changes Guard/Store state.

Instances belong to one asyncio loop. Payload size validation belongs to the
message parser; this queue bounds outstanding payload count and retained IDs.
No results or payloads are retained after completion. Hitting either limit
closes this queue instead of dropping a call and executing later calls.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class ToolCallQueueError(RuntimeError):
    """A fixed local failure code, without handler exceptions or call contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _Call[Payload, Result]:
    payload: Payload
    result: asyncio.Future[Result]


class TaskToolCallQueue[Payload, Result]:
    """Serialize one Task while allowing separate Task queues to run together.

    ``enqueue`` admits synchronously so admission order is receive order, even
    before callers await the returned futures. Duplicate callIds are rejected
    for the queue's entire lifetime; their original results are never replayed.
    Cancelling any returned future closes the Task queue and discards late
    results. Closing cannot establish whether an already started side effect
    occurred; the session owner still needs the agreed Guard stop procedure.
    """

    def __init__(
        self,
        task_id: str,
        handler: Callable[[str, Payload], Awaitable[Result]],
        *,
        max_pending: int = 64,
        max_calls: int = 4096,
    ) -> None:
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise ValueError("invalid trusted Task identifier")
        if any(type(value) is not int or value < 1 for value in (max_pending, max_calls)):
            raise ValueError("tool call limits must be positive integers")
        self._task_id = task_id
        self._handler = handler
        self._max_pending = max_pending
        self._max_calls = max_calls
        self._seen: set[str] = set()
        self._waiting: deque[_Call[Payload, Result]] = deque()
        self._active: _Call[Payload, Result] | None = None
        self._operation: asyncio.Task[Result] | None = None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending_count(self) -> int:
        """Outstanding calls, including the currently executing handler."""
        return len(self._waiting) + (self._active is not None)

    def enqueue(self, call_id: str, payload: Payload) -> asyncio.Future[Result]:
        """Reject duplicates; never execute an admitted call a second time."""
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ToolCallQueueError("TOOL_CALL_LOOP_MISMATCH")
        self._loop = loop
        if self._closed:
            raise ToolCallQueueError("TOOL_CALL_CLOSED")
        if self._has_cancellation():
            self._stop("TOOL_CALL_CANCELLED")
            raise ToolCallQueueError("TOOL_CALL_CANCELLED")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
            self._stop("TOOL_CALL_INVALID_ID")
            raise ToolCallQueueError("TOOL_CALL_INVALID_ID")
        if call_id in self._seen:
            raise ToolCallQueueError("TOOL_CALL_DUPLICATE")
        if self.pending_count >= self._max_pending or len(self._seen) >= self._max_calls:
            self._stop("TOOL_CALL_LIMIT")
            raise ToolCallQueueError("TOOL_CALL_LIMIT")
        self._seen.add(call_id)
        result: asyncio.Future[Result] = loop.create_future()
        result.add_done_callback(self._result_done)
        self._waiting.append(_Call(payload, result))
        self._start_next()
        return result

    def _result_done(self, result: asyncio.Future[Result]) -> None:
        if result.cancelled():
            self._stop("TOOL_CALL_CANCELLED")
        else:
            # A cancelled/abandoned waiter must not produce an unhandled error.
            result.exception()

    def _has_cancellation(self) -> bool:
        return (self._active is not None and self._active.result.cancelled()) or any(
            call.result.cancelled() for call in self._waiting
        )

    def _start_next(self) -> None:
        if self._closed or self._operation is not None or not self._waiting:
            return
        if self._has_cancellation():
            self._stop("TOOL_CALL_CANCELLED")
            return
        call = self._waiting.popleft()
        self._active = call
        self._operation = asyncio.create_task(self._execute(call.payload))
        self._operation.add_done_callback(self._completed)

    async def _execute(self, payload: Payload) -> Result:
        # A caller may cancel its future before this scheduled task first runs,
        # while the future's cancellation callback is still queued.
        if self._closed or self._has_cancellation():
            raise ToolCallQueueError("TOOL_CALL_CANCELLED")
        return await self._handler(self.task_id, payload)

    def _completed(self, operation: asyncio.Task[Result]) -> None:
        # Retrieve any handler error, including one arriving after close. Raw
        # exceptions never leave this local boundary or enter a result/log.
        failed = operation.cancelled() or operation.exception() is not None
        if self._closed:
            self._active = None
            self._operation = None
            return
        if self._has_cancellation():
            self._stop("TOOL_CALL_CANCELLED")
        elif failed:
            self._stop("TOOL_CALL_HANDLER_FAILED")
        elif self._active is not None:
            self._active.result.set_result(operation.result())
        self._active = None
        self._operation = None
        self._start_next()

    def _stop(self, code: str) -> None:
        if self._closed:
            return
        self._closed = True
        calls = list(self._waiting)
        self._waiting.clear()
        if self._active is not None:
            calls.append(self._active)
        for call in calls:
            if not call.result.done():
                call.result.set_exception(ToolCallQueueError(code))
        self._seen.clear()
        if self._operation is not None and not self._operation.done():
            self._operation.cancel()

    def close(self) -> None:
        """Stop admission and settle waiters immediately; invalidate late results."""
        self._stop("TOOL_CALL_CLOSED")

    async def aclose(self, *, cleanup_timeout_s: float = 1.0) -> None:
        """Bound cleanup even if a handler suppresses cancellation.

        An unconfirmed cleanup stays closed and keeps ownership of the handler
        until it finishes. The caller must not report that work as unsent.
        """
        if not math.isfinite(cleanup_timeout_s) or cleanup_timeout_s <= 0:
            raise ValueError("cleanup timeout must be positive and finite")
        self.close()
        operation = self._operation
        if operation is not None:
            _, pending = await asyncio.wait((operation,), timeout=cleanup_timeout_s)
            if pending:
                raise ToolCallQueueError("TOOL_CALL_CLEANUP_UNCONFIRMED")
