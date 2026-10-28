"""The Guard Transport: one ServerWorker per prevalidated server stream."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from functools import partial

from guard_app.adapters.mcp_transport.sdk_session import sdk_session_factory
from guard_app.adapters.mcp_transport.streams import HttpStreamFactory, StreamFactory
from guard_app.adapters.mcp_transport.tool_listing import ToolListing, ToolListLimits
from guard_app.adapters.mcp_transport.worker import (
    DEFAULT_PENDING_COMMANDS,
    MAX_RESULT_COPY_BYTES,
    ServerWorker,
    require_pending_limit,
    require_result_copy_budget,
)
from guard_app.domain.contracts import RawResult
from guard_app.domain.errors import DispatchError
from guard_app.domain.models import ResolvedRequest

_MAX_CONCURRENT_STARTS = 4


def _for_server(
    callback: Callable[[str], None] | None, server_id: str
) -> Callable[[], None] | None:
    return None if callback is None else partial(callback, server_id)


class McpTransport:
    """Guard Transport backed by one worker per prevalidated server stream.

    C's launcher and endpoint validators must supply the stream factories. A
    factory is opened only from its worker task and is never exposed to the
    Runner, UI, or model client. No transport-specific trust tier is inferred.
    The composition root should set ``max_result_copy_bytes`` high enough for
    its ResponseGate limit plus envelope overhead, within the 8 MiB ceiling.
    ``on_tools_changed`` receives only the server ID of each
    ``notifications/tools/list_changed`` (LIST-01). ``on_server_failed``
    receives only the server ID, once, when its session stops serving outside
    a requested close (#61).
    """

    def __init__(
        self,
        server_streams: Mapping[str, StreamFactory],
        *,
        call_timeout_s: float = 30.0,
        startup_timeout_s: float = 10.0,
        close_timeout_s: float = 60.0,
        max_result_copy_bytes: int = MAX_RESULT_COPY_BYTES,
        max_pending_commands: int = DEFAULT_PENDING_COMMANDS,
        on_dispatch: Callable[[str], None] | None = None,
        tool_list_limits: ToolListLimits | None = None,
        on_tools_changed: Callable[[str], None] | None = None,
        on_server_failed: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(server_streams, Mapping):
            raise TypeError("server stream mapping required")
        if on_tools_changed is not None and not callable(on_tools_changed):
            raise TypeError("tools changed callback must be callable")
        if on_server_failed is not None and not callable(on_server_failed):
            raise TypeError("server failed callback must be callable")
        if any(type(server_id) is not str or not server_id for server_id in server_streams):
            raise ValueError("nonempty server IDs required")
        require_result_copy_budget(max_result_copy_bytes)
        require_pending_limit(max_pending_commands)

        self._workers = {
            server_id: ServerWorker(
                sdk_session_factory(stream_factory, _for_server(on_tools_changed, server_id)),
                call_timeout_s=call_timeout_s,
                startup_timeout_s=startup_timeout_s,
                close_timeout_s=close_timeout_s,
                max_result_copy_bytes=max_result_copy_bytes,
                max_pending_commands=max_pending_commands,
                on_dispatch=on_dispatch,
                tool_list_limits=tool_list_limits,
                on_failed=_for_server(on_server_failed, server_id),
            )
            for server_id, stream_factory in server_streams.items()
        }
        # Worker construction has validated the call timeout. An HTTP read
        # bound below it lets a healthy slow JSON response end the session.
        if any(
            isinstance(stream_factory, HttpStreamFactory)
            and stream_factory.read_timeout_s < call_timeout_s
            for stream_factory in server_streams.values()
        ):
            raise ValueError("HTTP read timeout must not be shorter than the call timeout")
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("transport already started or closed")
        self._started = True
        starting: dict[asyncio.Task[None], int] = {}
        ready = False
        try:
            await self._start_in_windows(tuple(self._workers.values()), starting)
            ready = True
        finally:
            if not ready:
                for task in starting:
                    task.cancel()
                if starting:
                    await asyncio.gather(*starting, return_exceptions=True)
                await self.close()

    async def _start_in_windows(
        self, workers: tuple[ServerWorker, ...], starting: dict[asyncio.Task[None], int]
    ) -> None:
        """Start every worker, a bounded window at a time; `starting` holds the window."""
        next_index = 0
        while next_index < len(workers) or starting:
            if self._closed:
                raise DispatchError("WorkerClosed")
            # Keep both live startups and pending asyncio tasks bounded.
            # Each worker still owns its own session in its owner task.
            while next_index < len(workers) and len(starting) < _MAX_CONCURRENT_STARTS:
                task = asyncio.create_task(workers[next_index].start(), name="mcp-server-start")
                starting[task] = next_index
                next_index += 1
            completed, _ = await asyncio.wait(starting, return_when=asyncio.FIRST_COMPLETED)
            # If several startups settle together, select a failure in
            # registration order and do not launch the next window first.
            for task in sorted(completed, key=starting.__getitem__):
                del starting[task]
                try:
                    await task
                except RuntimeError:
                    if self._closed:
                        raise DispatchError("WorkerClosed") from None
                    raise
        if self._closed:
            raise DispatchError("WorkerClosed")

    async def call(self, req: ResolvedRequest) -> RawResult:
        # close() marks the transport closed before awaiting each worker.  A
        # later worker may still be READY while an earlier one drains, but no
        # new Guard request may enter any worker after shutdown starts.
        if self._closed:
            raise DispatchError("WorkerClosed")
        worker = self._workers.get(req.tool.server_id)
        if worker is None:
            raise DispatchError("UnknownServer")
        return await worker.call(req)

    def tool_listing(self, server_id: str) -> ToolListing | None:
        """One server's startup tools/list result for the composition root.

        None means collection is disabled or the server is unknown. Only the
        trusted composition root hands this to C's Registry/Scanner; it is
        never shown to the model or used as Guard trust input.
        """
        worker = self._workers.get(server_id)
        return None if worker is None else worker.tool_listing

    def discard_queued(self, task_id: str) -> int:
        """Synchronously keep a stopped Task's queued commands off every server.

        The composition root calls this right before awaiting a human cancel or
        emergency halt. Returns how many waiting commands were settled with
        ``DispatchError("DispatchStopped")``; active commands are unchanged.
        """
        if type(task_id) is not str:
            raise TypeError("task ID string required")
        return sum(worker.discard_queued(task_id) for worker in self._workers.values())

    async def close(self) -> None:
        self._closed = True
        # Quiesce every server before awaiting any one server's active call
        # or context exit. In particular, a queued SEND on a later server
        # cannot start while an earlier server is slow to close.
        workers = tuple(self._workers.values())
        for worker in workers:
            worker.request_close()
        outcomes = await asyncio.gather(
            *(worker.wait_closed() for worker in workers), return_exceptions=True
        )
        failure: DispatchError | None = None
        for outcome in outcomes:
            if isinstance(outcome, DispatchError) and failure is None:
                failure = outcome
            elif isinstance(outcome, BaseException) and failure is None:
                failure = DispatchError("WorkerCloseFailed")
        if failure is not None:
            raise failure
