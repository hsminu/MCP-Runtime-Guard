"""Read-only Store queries for the approval list and call log (B6-05, #86).

One query of each kind runs at a time and only the newest waiting request is
kept, so a burst of refreshes never piles up reads. A Store error, a timeout
or shutdown answers ``failed``: the screen shows it as unknown, never as an
empty result. The pages come from B unchanged and carry no raw text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from guard_app.domain.queries import ApprovalListQuery, CallListQuery
from guard_app.ports import StoreQueries
from guard_app.services.bridge_types import QueryReply, QueryRequest

QUERY_TIMEOUT_S = 5.0


class QueryLane:
    """Backend loop only."""

    def __init__(
        self,
        answer: Callable[[QueryReply], None],
        consume: Callable[[asyncio.Future[Any]], None],
        *,
        timeout_s: float = QUERY_TIMEOUT_S,
    ) -> None:
        self._answer = answer
        self._consume = consume
        self._timeout_s = timeout_s
        self._queries: StoreQueries | None = None
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._waiting: dict[str, tuple[int, QueryRequest]] = {}
        self._closed = False

    def open(self, queries: StoreQueries | None) -> None:
        self._queries = queries

    def submit(self, kind: str, token: int, request: QueryRequest) -> None:
        if self._closed:
            return
        if self._queries is None:
            self._answer(QueryReply(kind, token, None, failed=True))
        elif kind in self._running:
            self._waiting[kind] = (token, request)
        else:
            self._start(kind, token, request)

    def _start(self, kind: str, token: int, request: QueryRequest) -> None:
        task = asyncio.ensure_future(asyncio.wait_for(self._read(kind, request), self._timeout_s))
        self._running[kind] = task
        task.add_done_callback(lambda done: self._finished(kind, token, done))

    async def _read(self, kind: str, request: QueryRequest) -> Any:
        queries = self._queries
        if queries is None:
            raise LookupError()
        if kind == "approvals" and type(request) is ApprovalListQuery:
            return await queries.list_approvals(request)
        if kind == "calls" and type(request) is CallListQuery:
            return await queries.list_calls(request)
        if kind == "call" and type(request) is str:
            return await queries.get_call(request)
        raise LookupError()

    def _finished(self, kind: str, token: int, task: asyncio.Future[Any]) -> None:
        # The A82 pattern: the outcome is read from the finished task and only
        # "failed" crosses, without the exception text or type.
        self._running.pop(kind, None)
        self._consume(task)
        failed = task.cancelled() or task.exception() is not None
        if not self._closed:
            self._answer(QueryReply(kind, token, None if failed else task.result(), failed=failed))
            waiting = self._waiting.pop(kind, None)
            if waiting is not None:
                self._start(kind, *waiting)

    async def close(self, timeout_s: float) -> bool:
        """Refuse new queries and end running ones; True when they ended in time."""
        self._closed = True
        self._waiting.clear()
        running = set(self._running.values())
        for task in running:
            task.cancel()
        if not running:
            return True
        _, pending = await asyncio.wait(running, timeout=timeout_s)
        return not pending
