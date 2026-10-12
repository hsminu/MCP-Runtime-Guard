"""MCP session ownership and the Guard's queued Transport implementation.

The stream factories are supplied by the trusted composition root after C's
launcher/endpoint validation. This module deliberately does not accept paths,
URLs, environment dictionaries, or create a second tools/call entry point.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol

import anyio
from mcp import ClientSession
from mcp.types import CallToolResult

from guard_app.domain.contracts import RawResult
from guard_app.domain.errors import DispatchError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ResolvedRequest


class _ToolSession(Protocol):
    async def initialize(self) -> object: ...

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> CallToolResult: ...


type StreamFactory = Callable[[], AbstractAsyncContextManager[tuple[Any, ...]]]
type SessionFactory = Callable[[], AbstractAsyncContextManager[_ToolSession]]


class _State(Enum):
    NEW = auto()
    STARTING = auto()
    READY = auto()
    CLOSING = auto()
    CLOSED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class _Command:
    req: ResolvedRequest
    future: asyncio.Future[RawResult]


_STOP = object()


def _exception_name(exc: Exception) -> str:
    name = type(exc).__name__
    return (
        name
        if name.isascii() and name.isidentifier() and len(name) <= 64
        else "Exception"
    )


async def _discard_logging(_params: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


async def _discard_message(_message: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


def _sdk_session_factory(stream_factory: StreamFactory) -> SessionFactory:
    """Wrap only streams supplied by a verified composition root.

    No stdio/HTTP stream factory is shipped here: C's connection validation,
    isolated output sinks, and SDK logger containment must precede wiring.
    """

    @asynccontextmanager
    async def open_session():
        async with stream_factory() as streams:
            if len(streams) < 2:
                raise ValueError("MCP stream pair required")
            async with ClientSession(
                streams[0],
                streams[1],
                logging_callback=_discard_logging,
                message_handler=_discard_message,
            ) as session:
                yield session

    return open_session


class ServerWorker:
    """One long-lived task owns one server's stream and ClientSession.

    Only the Guard-facing `call` queues requests. Neither the session nor its
    streams are returned to callers. A cancelled caller abandons its future;
    an already running SDK call continues to a bounded outcome and is never
    re-enqueued. The Guard is responsible for Task status after cancellation.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        call_timeout_s: float,
        startup_timeout_s: float,
        close_timeout_s: float,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session factory required")
        for timeout in (call_timeout_s, startup_timeout_s, close_timeout_s):
            if (
                type(timeout) not in (int, float)
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError("positive finite worker timeouts required")
        self._session_factory = session_factory
        self._call_timeout_s = float(call_timeout_s)
        self._startup_timeout_s = float(startup_timeout_s)
        self._close_timeout_s = float(close_timeout_s)
        self._queue: asyncio.Queue[_Command | object] = asyncio.Queue()
        self._state = _State.NEW
        self._owner: asyncio.Task[None] | None = None
        self._active: _Command | None = None
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._unhealthy = False

    async def start(self) -> None:
        if self._state is not _State.NEW:
            raise RuntimeError("server worker already started")
        self._state = _State.STARTING
        self._owner = asyncio.create_task(self._run(), name="mcp-server-worker")
        self._owner.add_done_callback(self._consume_task_exception)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._startup_timeout_s)
        except TimeoutError:
            self._state = _State.CLOSING
            self._owner.cancel()
            raise DispatchError("SessionStartTimeout") from None
        if self._state is not _State.READY:
            raise DispatchError("SessionStartFailed")

    async def call(self, req: ResolvedRequest) -> RawResult:
        if self._state is not _State.READY:
            name = (
                "WorkerNotReady"
                if self._state in (_State.NEW, _State.STARTING)
                else "WorkerClosed"
            )
            raise DispatchError(name)
        future: asyncio.Future[RawResult] = asyncio.get_running_loop().create_future()
        future.add_done_callback(self._consume_command_failure)
        self._queue.put_nowait(_Command(req=req, future=future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # A queued call is skipped. An active call still runs at most once;
            # cancelling its waiter cannot claim the remote side did nothing.
            future.cancel()
            raise

    async def close(self) -> None:
        if self._state is _State.NEW:
            self._state = _State.CLOSED
            self._ready.set()
            self._stopped.set()
            return
        if self._state in (_State.STARTING, _State.READY):
            starting = self._state is _State.STARTING
            self._state = _State.CLOSING
            self._reject_queued("WorkerClosed")
            self._queue.put_nowait(_STOP)
            if starting and self._owner is not None:
                self._owner.cancel()
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=self._close_timeout_s)
        except TimeoutError:
            # Even a cancellation-resistant SDK call cannot keep the Guard's
            # command future pending forever after shutdown has failed.
            if self._active is not None and not self._active.future.done():
                self._active.future.set_exception(DispatchError("WorkerCloseTimeout"))
            if self._owner is not None:
                self._owner.cancel()
            raise DispatchError("WorkerCloseTimeout") from None

    async def _run(self) -> None:
        try:
            async with self._session_factory() as session:
                await session.initialize()
                if self._state is _State.STARTING:
                    self._state = _State.READY
                self._ready.set()
                while True:
                    item = await self._queue.get()
                    if item is _STOP:
                        break
                    assert isinstance(item, _Command)
                    if item.future.done():
                        continue
                    self._active = item
                    try:
                        await self._execute(session, item)
                    finally:
                        if not item.future.done():
                            item.future.set_exception(
                                DispatchError("WorkerInterrupted")
                            )
                        self._active = None
        finally:
            self._state = (
                _State.FAILED
                if self._unhealthy or self._state in (_State.STARTING, _State.READY)
                else _State.CLOSED
            )
            self._ready.set()
            self._reject_queued("WorkerClosed")
            self._stopped.set()

    async def _execute(self, session: _ToolSession, cmd: _Command) -> None:
        """The one broad exception boundary for per-command SDK failures."""
        watchdog = asyncio.get_running_loop().call_later(
            self._call_timeout_s + 0.05, self._force_settle_timeout, cmd
        )
        try:
            try:
                async with asyncio.timeout(self._call_timeout_s):
                    result = await session.call_tool(
                        cmd.req.tool.tool_name, cmd.req.arguments.to_dict()
                    )
                # This is a private, untrusted JSON copy for B's ResponseGate.
                payload = FrozenJsonObject(
                    result.model_dump(mode="json", by_alias=True, exclude_none=True)
                )
                raw = RawResult(payload=payload)
            except TimeoutError:
                # SDK timeout does not prove that no partial bytes arrived.
                # The Guard must contain READ and mark SEND outcome unknown.
                if not cmd.future.done():
                    cmd.future.set_exception(DispatchError("TimeoutError"))
                return
            except Exception as exc:  # noqa: BLE001 - designated ServerWorker._execute boundary
                if isinstance(
                    exc,
                    (
                        anyio.ClosedResourceError,
                        anyio.BrokenResourceError,
                        anyio.EndOfStream,
                        ConnectionError,
                        ExceptionGroup,
                    ),
                ):
                    self._unhealthy = True
                    self._state = _State.CLOSING
                    self._reject_queued("WorkerClosed")
                    self._queue.put_nowait(_STOP)
                if not cmd.future.done():
                    cmd.future.set_exception(DispatchError(_exception_name(exc)))
                return
            if not cmd.future.done():
                cmd.future.set_result(raw)
        finally:
            watchdog.cancel()

    def _force_settle_timeout(self, cmd: _Command) -> None:
        if self._active is not cmd:
            return
        self._unhealthy = True
        self._state = _State.CLOSING
        self._reject_queued("WorkerClosed")
        self._queue.put_nowait(_STOP)
        if not cmd.future.done():
            cmd.future.set_exception(DispatchError("TimeoutError"))
        if self._owner is not None:
            self._owner.cancel()

    def _reject_queued(self, name: str) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, _Command) and not item.future.done():
                item.future.set_exception(DispatchError(name))

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[None]) -> None:
        # Task errors have already become fixed DispatchError values for waiters.
        # Retrieve the raw exception solely to suppress asyncio's default log.
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _consume_command_failure(future: asyncio.Future[RawResult]) -> None:
        # A caller can be cancelled after settlement but before awaiting the
        # sanitized exception. Do not let asyncio log even that abandoned item.
        if not future.cancelled():
            future.exception()


class McpTransport:
    """Guard Transport backed by one worker per prevalidated server stream.

    C's launcher and endpoint validators must supply the stream factories. A
    factory is opened only from its worker task and is never exposed to the
    Runner, UI, or model client. No transport-specific trust tier is inferred.
    """

    def __init__(
        self,
        server_streams: Mapping[str, StreamFactory],
        *,
        call_timeout_s: float = 30.0,
        startup_timeout_s: float = 10.0,
        close_timeout_s: float = 60.0,
    ) -> None:
        if not isinstance(server_streams, Mapping):
            raise TypeError("server stream mapping required")
        if any(
            type(server_id) is not str or not server_id for server_id in server_streams
        ):
            raise ValueError("nonempty server IDs required")
        self._workers = {
            server_id: ServerWorker(
                _sdk_session_factory(stream_factory),
                call_timeout_s=call_timeout_s,
                startup_timeout_s=startup_timeout_s,
                close_timeout_s=close_timeout_s,
            )
            for server_id, stream_factory in server_streams.items()
        }
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("transport already started or closed")
        self._started = True
        try:
            for worker in self._workers.values():
                if self._closed:
                    raise DispatchError("WorkerClosed")
                await worker.start()
        except (DispatchError, asyncio.CancelledError):
            await self.close()
            raise

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

    async def close(self) -> None:
        self._closed = True
        failure: DispatchError | None = None
        for worker in self._workers.values():
            try:
                await worker.close()
            except DispatchError as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure
