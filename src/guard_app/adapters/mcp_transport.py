"""MCP session ownership and the Guard's queued Transport implementation.

Stream factories are supplied by the trusted composition root after C's
launcher/endpoint validation. The SDK factories below enforce transport settings;
they do not replace registration, endpoint approval or Guard's tools/call entry.
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, auto
from types import TracebackType
from typing import Any, Protocol, TextIO, cast

import anyio
import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.session import ProgressFnT
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ClientRequest,
    RequestParams,
)
from pydantic import AnyUrl, BaseModel

from guard_app.domain.contracts import RawResult
from guard_app.domain.errors import DispatchError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ResolvedRequest


class _ToolSession(Protocol):
    async def initialize(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult: ...


class _UnvalidatedClientSession(ClientSession):
    """Send tools/call without the SDK's server-schema validation.

    mcp 1.29.0 ``ClientSession.call_tool`` lists tools and runs
    ``jsonschema.validate`` with the server's ``outputSchema`` on the event loop.
    A server ``$ref`` can make the Guard fetch arbitrary URLs outside the
    restricted HTTP client, and a slow ref or pattern stalls cancel/halt. This
    override mirrors the SDK request without that step. The result stays
    untrusted and reaches B's ResponseGate unchanged; A never validates
    structured content with a server-supplied schema.
    """

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: ProgressFnT | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        request_meta = None if meta is None else RequestParams.Meta(**meta)
        return await self.send_request(
            ClientRequest(
                CallToolRequest(
                    params=CallToolRequestParams(
                        name=name, arguments=arguments, _meta=request_meta
                    ),
                )
            ),
            CallToolResult,
            request_read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
        )


type StreamFactory = Callable[[], AbstractAsyncContextManager[tuple[Any, ...]]]
type SessionFactory = Callable[[], AbstractAsyncContextManager[_ToolSession]]


class _HttpStreamFactory:
    """Stream factory that exposes its HTTP read bound for McpTransport checks."""

    __slots__ = ("_connect", "read_timeout_s")

    def __init__(self, connect: StreamFactory, read_timeout_s: float) -> None:
        self._connect = connect
        self.read_timeout_s = read_timeout_s

    def __call__(self) -> AbstractAsyncContextManager[tuple[Any, ...]]:
        return self._connect()


_SDK_LOG_SCOPE: ContextVar[bool] = ContextVar("guard_sdk_log_scope", default=False)


def _install_sdk_log_containment() -> None:
    """Sanitize before *any* handler, including MCP's root logging calls.

    SDK worker tasks inherit this ContextVar. Other application tasks retain
    their logging behavior. Keep the wrapper installed to contain late tasks;
    restoring a process-global factory while another worker lives is unsafe.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_guard_sdk_containment", False):
        return

    def record_factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if _SDK_LOG_SCOPE.get():
            record.msg = "MCP_LOG_WITHHELD"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    record_factory._guard_sdk_containment = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


def stdio_stream_factory(server: StdioServerParameters, *, errlog: TextIO) -> StreamFactory:
    """C-validated launch parameters, explicit non-host stderr destination.

    The caller owns the sink until transport shutdown. SDK default environment
    merging still applies; this wrapper is not launcher or environment validation.
    """
    if errlog is sys.stderr or errlog is sys.stdout or errlog.closed:
        raise ValueError("An isolated server stderr sink is required")
    if errlog.fileno() in (1, 2):
        raise ValueError("An isolated server stderr sink is required")
    parameters = server.model_copy(deep=True)
    _install_sdk_log_containment()

    @asynccontextmanager
    async def connect():
        token = _SDK_LOG_SCOPE.set(True)
        try:
            async with stdio_client(parameters, errlog=errlog) as streams:
                yield streams
        finally:
            _SDK_LOG_SCOPE.reset(token)

    return connect


def _valid_http_timeout(value: object) -> bool:
    return (
        type(value) in (int, float)
        and 0 < cast("float", value) <= 300
        and math.isfinite(cast("float", value))
    )


def http_stream_factory(
    endpoint: str, *, io_timeout_s: float = 5.0, read_timeout_s: float = 60.0
) -> StreamFactory:
    """Open a preapproved Streamable HTTP endpoint with no write replay.

    The composition root must supply C-validated endpoint/registration data.
    These checks only bound the supported URL shape: HTTPS, or literal loopback
    HTTP for a local server. DNS/IP policy and credential provisioning are not
    implemented here. This initial transport supports unauthenticated servers.

    ``io_timeout_s`` bounds connect, write and pool waits. ``read_timeout_s``
    bounds response reads separately: in JSON-response mode the server sends
    headers only after the tool finishes, and an HTTPX read timeout there
    escapes the SDK's request task and tears down the whole session. McpTransport
    therefore requires it to be no shorter than its per-call timeout, which
    remains the effective call bound. A server silent for longer than this read
    bound still ends the session; later calls then fail closed.
    """
    if (
        type(endpoint) is not str
        or not 1 <= len(endpoint) <= 2048
        or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint)
        or "\\" in endpoint
    ):
        raise ValueError("Invalid HTTP MCP endpoint")
    try:
        url = httpx.URL(endpoint)
    except httpx.InvalidURL:
        raise ValueError("Invalid HTTP MCP endpoint") from None
    if (
        not url.is_absolute_url
        or not url.host
        or any(char in url.host for char in "[]%")
        or url.scheme not in {"http", "https"}
        or url.userinfo
        or "?" in endpoint
        or "#" in endpoint
        or (url.scheme == "http" and url.host not in {"127.0.0.1", "::1"})
    ):
        raise ValueError("Invalid HTTP MCP endpoint")
    if not _valid_http_timeout(io_timeout_s) or not _valid_http_timeout(read_timeout_s):
        raise ValueError("Invalid HTTP MCP timeout")
    _install_sdk_log_containment()

    @asynccontextmanager
    async def connect():
        token = _SDK_LOG_SCOPE.set(True)
        get_started = False

        async def constrain_request(request: httpx.Request) -> None:
            nonlocal get_started
            if request.url != url or request.method not in {"POST", "GET", "DELETE"}:
                raise httpx.RequestError("MCP_ENDPOINT_MISMATCH")
            if "authorization" in request.headers or "proxy-authorization" in request.headers:
                raise httpx.RequestError("MCP_AUTH_UNSUPPORTED")
            # HTTPX stores server cookies. They are not part of this unauthenticated
            # transport's contract; never turn Set-Cookie into ambient credentials.
            request.headers.pop("cookie", None)
            if request.method == "GET":
                # One notification stream is supported. No automatic GET resume
                # or reconnect may recover a lost SEND as an apparent success.
                if get_started or "last-event-id" in request.headers:
                    raise httpx.RequestError("MCP_RESUME_DISABLED")
                get_started = True

        try:
            # AsyncHTTPTransport(verify=True)는 인증서 묶음을 읽어 SSL 문맥을 동기로 만든다
            # (A46). 같은 trust_env=False 기본 문맥을 작업 스레드에서 만들어 루프를 막지 않는다.
            ssl_context = await asyncio.to_thread(httpx.create_ssl_context, trust_env=False)
            async with (
                httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(
                        retries=0,
                        trust_env=False,
                        verify=ssl_context,
                        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                    ),
                    timeout=httpx.Timeout(float(io_timeout_s), read=float(read_timeout_s)),
                    follow_redirects=False,
                    trust_env=False,
                    auth=None,
                    event_hooks={"request": [constrain_request]},
                ) as client,
                streamable_http_client(
                    str(url), http_client=client, terminate_on_close=True
                ) as streams,
            ):
                yield streams
        finally:
            _SDK_LOG_SCOPE.reset(token)

    return _HttpStreamFactory(connect, float(read_timeout_s))


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


class _SessionExitTracker:
    """Observe successful context exit even when the owner was cancelled."""

    def __init__(self, context: AbstractAsyncContextManager[_ToolSession]) -> None:
        self._context = context
        self.entered = False
        self.exit_attempted = False
        self.exited = False

    async def __aenter__(self) -> _ToolSession:
        session = await self._context.__aenter__()
        self.entered = True
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self.exit_attempted = True
        suppressed = await self._context.__aexit__(exc_type, exc, tb)
        self.exited = True
        return suppressed


_STOP = object()
_MAX_RESULT_CONTENT_BLOCKS = 256
_MAX_RESULT_VALUE_NODES = 4096
# The ResponseGate's default text/structured limit is 256 KiB. Leave room for
# the SDK envelope while bounding the copy that precedes its semantic checks.
_MAX_RESULT_COPY_BYTES = 512 * 1024
_MAX_CONFIGURED_RESULT_COPY_BYTES = 8 * 1024 * 1024
_DEFAULT_PENDING_COMMANDS = 64
_MAX_CONFIGURED_PENDING_COMMANDS = 1024
_MAX_CONCURRENT_STARTS = 4


class ResultTooLarge(ValueError):
    """An SDK result exceeds the local pre-copy resource budget."""


async def _check_result_budget(result: object, *, max_copy_bytes: int) -> None:
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


def _exception_name(exc: Exception) -> str:
    name = type(exc).__name__
    return name if name.isascii() and name.isidentifier() and len(name) <= 64 else "Exception"


async def _discard_logging(params: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


async def _discard_message(message: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


def _sdk_session_factory(stream_factory: StreamFactory) -> SessionFactory:
    """Wrap only streams supplied by a verified composition root.

    C's connection validation and isolated output sinks precede wiring. SDK
    logger containment covers the streams, session and their child tasks.
    """

    _install_sdk_log_containment()

    @asynccontextmanager
    async def open_session():
        token = _SDK_LOG_SCOPE.set(True)
        try:
            async with stream_factory() as streams:
                if len(streams) < 2:
                    raise ValueError("MCP stream pair required")
                async with _UnvalidatedClientSession(
                    streams[0],
                    streams[1],
                    logging_callback=_discard_logging,
                    message_handler=_discard_message,
                ) as session:
                    yield session
        finally:
            _SDK_LOG_SCOPE.reset(token)

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
        max_result_copy_bytes: int,
        max_pending_commands: int = _DEFAULT_PENDING_COMMANDS,
        on_dispatch: Callable[[str], None] | None = None,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session factory required")
        for timeout in (call_timeout_s, startup_timeout_s, close_timeout_s):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("positive finite worker timeouts required")
        if (
            type(max_result_copy_bytes) is not int
            or max_result_copy_bytes < 1
            or max_result_copy_bytes > _MAX_CONFIGURED_RESULT_COPY_BYTES
        ):
            raise ValueError("bounded positive result copy budget required")
        if (
            type(max_pending_commands) is not int
            or max_pending_commands < 1
            or max_pending_commands > _MAX_CONFIGURED_PENDING_COMMANDS
        ):
            raise ValueError("bounded positive pending command limit required")
        self._session_factory = session_factory
        self._on_dispatch = on_dispatch
        self._call_timeout_s = float(call_timeout_s)
        self._startup_timeout_s = float(startup_timeout_s)
        self._close_timeout_s = float(close_timeout_s)
        self._max_result_copy_bytes = max_result_copy_bytes
        self._queue: asyncio.Queue[_Command | object] = asyncio.Queue(maxsize=max_pending_commands)
        self._state = _State.NEW
        self._owner: asyncio.Task[None] | None = None
        self._active: _Command | None = None
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._unhealthy = False
        self._initialized = False
        self._session_entered = False
        self._session_exit_attempted = False
        self._session_exited = False
        self._owner_cancel_expected = False
        self._close_failed = False

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
                "WorkerNotReady" if self._state in (_State.NEW, _State.STARTING) else "WorkerClosed"
            )
            raise DispatchError(name)
        future: asyncio.Future[RawResult] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_Command(req=req, future=future))
        except asyncio.QueueFull:
            # No worker has received this command. Keep the queue bounded and
            # never await capacity after the Guard has reserved a request.
            future.cancel()
            raise DispatchError("WorkerQueueFull") from None
        future.add_done_callback(self._consume_command_failure)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # A queued call is skipped. An active call still runs at most once;
            # cancelling its waiter cannot claim the remote side did nothing.
            future.cancel()
            raise

    async def close(self) -> None:
        self.request_close()
        await self.wait_closed()

    def request_close(self) -> None:
        """Stop admission and reject pending commands without awaiting I/O."""
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

    async def wait_closed(self) -> None:
        """Confirm that the session owner finished, including its context exit."""
        if self._owner is None:
            return
        try:
            # _stopped is set in _run's finally, before the owner task has
            # published its final result. Collect that result without leaking
            # an SDK exception or cancelling it on the first close timeout.
            outcome = await asyncio.wait_for(
                asyncio.shield(asyncio.gather(self._owner, return_exceptions=True)),
                timeout=self._close_timeout_s,
            )
        except TimeoutError:
            # Even a cancellation-resistant SDK call cannot keep the Guard's
            # command future pending forever after shutdown has failed.
            if self._active is not None and not self._active.future.done():
                self._active.future.set_exception(DispatchError("WorkerCloseTimeout"))
            if self._owner is not None:
                self._owner_cancel_expected = True
                self._owner.cancel()
            raise DispatchError("WorkerCloseTimeout") from None
        owner_error = outcome[0]
        owner_failed = isinstance(owner_error, BaseException) and (
            not isinstance(owner_error, asyncio.CancelledError) or not self._owner_cancel_expected
        )
        teardown_failed = self._session_exit_attempted and not self._session_exited
        teardown_missing = self._session_entered and not self._session_exit_attempted
        if teardown_failed or teardown_missing or (self._initialized and owner_failed):
            self._close_failed = True
        if self._close_failed:
            self._state = _State.FAILED
            name = (
                "SessionCloseFailed" if teardown_failed or teardown_missing else "WorkerOwnerFailed"
            )
            raise DispatchError(name) from None
        if self._state is _State.CLOSING:
            self._state = _State.CLOSED

    async def _run(self) -> None:
        tracker: _SessionExitTracker | None = None
        try:
            tracker = _SessionExitTracker(self._session_factory())
            async with tracker as session:
                await session.initialize()
                self._initialized = True
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
                            item.future.set_exception(DispatchError("WorkerInterrupted"))
                        self._active = None
        finally:
            if tracker is not None:
                self._session_entered = tracker.entered
                self._session_exit_attempted = tracker.exit_attempted
                self._session_exited = tracker.exited
            self._state = (
                _State.FAILED
                if self._unhealthy or self._state in (_State.STARTING, _State.READY)
                else self._state
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
                    if self._on_dispatch is not None:
                        self._on_dispatch(cmd.req.request_id)
                    result = await session.call_tool(
                        cmd.req.tool.tool_name, cmd.req.arguments.to_dict()
                    )
                    await _check_result_budget(result, max_copy_bytes=self._max_result_copy_bytes)
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
            self._owner_cancel_expected = True
            self._owner.cancel()

    def discard_queued(self, task_id: str) -> int:
        """Settle one Task's not-yet-started commands without awaiting.

        The Guard checks its stop record only before enqueueing. A human
        cancel/emergency halt recorded later must still keep a queued SEND off
        the wire. Matching commands leave the bounded queue, so their slots are
        free again, and fail with a fixed pre-wire name. The active command has
        already reached ``on_dispatch`` and is left to its bounded outcome.
        """
        if type(task_id) is not str:
            raise TypeError("task ID string required")
        kept: list[_Command | object] = []
        discarded = 0
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, _Command) and item.req.task_id == task_id:
                if not item.future.done():
                    item.future.set_exception(DispatchError("DispatchStopped"))
                    discarded += 1
                continue
            kept.append(item)
        # Only removed items were not re-queued, so this cannot exceed capacity.
        # A getter woken by an earlier put rechecks emptiness before taking one.
        for item in kept:
            self._queue.put_nowait(item)
        return discarded

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
    The composition root should set ``max_result_copy_bytes`` high enough for
    its ResponseGate limit plus envelope overhead, within the 8 MiB ceiling.
    """

    def __init__(
        self,
        server_streams: Mapping[str, StreamFactory],
        *,
        call_timeout_s: float = 30.0,
        startup_timeout_s: float = 10.0,
        close_timeout_s: float = 60.0,
        max_result_copy_bytes: int = _MAX_RESULT_COPY_BYTES,
        max_pending_commands: int = _DEFAULT_PENDING_COMMANDS,
        on_dispatch: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(server_streams, Mapping):
            raise TypeError("server stream mapping required")
        if any(type(server_id) is not str or not server_id for server_id in server_streams):
            raise ValueError("nonempty server IDs required")
        if (
            type(max_result_copy_bytes) is not int
            or max_result_copy_bytes < 1
            or max_result_copy_bytes > _MAX_CONFIGURED_RESULT_COPY_BYTES
        ):
            raise ValueError("bounded positive result copy budget required")
        if (
            type(max_pending_commands) is not int
            or max_pending_commands < 1
            or max_pending_commands > _MAX_CONFIGURED_PENDING_COMMANDS
        ):
            raise ValueError("bounded positive pending command limit required")
        self._workers = {
            server_id: ServerWorker(
                _sdk_session_factory(stream_factory),
                call_timeout_s=call_timeout_s,
                startup_timeout_s=startup_timeout_s,
                close_timeout_s=close_timeout_s,
                max_result_copy_bytes=max_result_copy_bytes,
                max_pending_commands=max_pending_commands,
                on_dispatch=on_dispatch,
            )
            for server_id, stream_factory in server_streams.items()
        }
        # Worker construction has validated the call timeout. An HTTP read
        # bound below it lets a healthy slow JSON response end the session.
        if any(
            isinstance(stream_factory, _HttpStreamFactory)
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
        workers = tuple(self._workers.values())
        starting: dict[asyncio.Task[None], int] = {}
        next_index = 0
        ready = False
        try:
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
            ready = True
        finally:
            if not ready:
                for task in starting:
                    task.cancel()
                if starting:
                    await asyncio.gather(*starting, return_exceptions=True)
                await self.close()

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
