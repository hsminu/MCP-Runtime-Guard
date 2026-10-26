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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, auto
from types import TracebackType
from typing import Any, Protocol, TextIO, cast

import anyio
import httpx
from anyio.abc import ObjectReceiveStream

# A46: httpx imports httpcore lazily inside AsyncHTTPTransport(), which stalled
# the backend loop on the first HTTP connection. Import it with this module
# instead (httpx's own locked dependency), before any backend loop runs.
import httpcore  # noqa: F401  # isort: skip
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.shared.message import MessageMetadata, SessionMessage
from mcp.shared.session import ProgressFnT
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ClientRequest,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCResponse,
    ListToolsResult,
    PaginatedRequestParams,
    RequestParams,
)
from pydantic import AnyUrl, BaseModel

from guard_app.adapters.mcp_http_ingress import BoundedHttpTransport
from guard_app.adapters.mcp_ingress import IngressError, IngressGate, PipeFrameReader
from guard_app.domain.contracts import RawResult
from guard_app.domain.errors import DispatchError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ResolvedRequest


class _ToolSession(Protocol):
    async def initialize(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult: ...

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult: ...


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

    listing_ingress: IngressGate | None = None

    async def send_request[T: BaseModel](
        self,
        request: ClientRequest,
        result_type: type[T],
        request_read_timeout_seconds: timedelta | None = None,
        metadata: MessageMetadata = None,
        progress_callback: ProgressFnT | None = None,
    ) -> T:
        gate = self.listing_ingress
        if gate is not None and gate.reason is not None:
            raise IngressError(gate.reason)
        if request.root.method == "tools/list":
            if gate is None or not gate.active:
                raise IngressError("TOOLS_LIST_INGRESS_REQUIRED")
            # Pinned SDK 1.29 assigns/increments this integer before its first
            # await. Enter super in this same task, with no scheduling point
            # between registration and assignment, including concurrent pings.
            gate.expect(self._request_id)
        return await super().send_request(
            request,
            result_type,
            request_read_timeout_seconds,
            metadata,
            progress_callback,
        )

    async def list_tools(
        self,
        cursor: str | None = None,
        *,
        params: PaginatedRequestParams | None = None,
    ) -> ListToolsResult:
        gate = self.listing_ingress
        if gate is None or not gate.active:
            raise IngressError("TOOLS_LIST_INGRESS_REQUIRED")
        if cursor is not None and params is not None:
            raise ValueError("Cannot specify both cursor and params")
        operation = (
            super().list_tools(params=params) if params is not None else super().list_tools(cursor)
        )
        pending = asyncio.create_task(operation)
        failed = asyncio.create_task(gate.failed.wait())
        try:
            await asyncio.wait((pending, failed), return_when=asyncio.FIRST_COMPLETED)
            if gate.reason is not None:
                raise IngressError(gate.reason)
            return await pending
        finally:
            failed.cancel()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, failed, return_exceptions=True)

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

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
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
    This legacy SDK-spawned path has no raw ingress gate. Ordinary demo calls
    remain supported; tools/list collection explicitly refuses it. Product
    collection uses validated_stdio_stream_factory with C-owned pipes.
    """
    if errlog is sys.stderr or errlog is sys.stdout or errlog.closed:
        raise ValueError("An isolated server stderr sink is required")
    if errlog.fileno() in (1, 2):
        raise ValueError("An isolated server stderr sink is required")
    parameters = server.model_copy(deep=True)
    _install_sdk_log_containment()

    @asynccontextmanager
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        token = _SDK_LOG_SCOPE.set(True)
        try:
            async with stdio_client(parameters, errlog=errlog) as streams:
                yield streams
        finally:
            _SDK_LOG_SCOPE.reset(token)

    return connect


def validated_stdio_stream_factory(
    read_bytes: Callable[[int], Awaitable[bytes]],
    write_bytes: Callable[[bytes], Awaitable[None]],
    drain: Callable[[], Awaitable[None]],
) -> StreamFactory:
    """Adapt C-validated, externally owned UTF-8 MCP stdin/stdout operations.

    The launcher and isolated stderr sink remain C/composition-root concerns.
    read(n) returns at most n bytes; write_bytes writes all supplied bytes or
    fails; drain confirms backpressure. Context exit cancels local operations,
    but does not close pipes, terminate a process, or prove launcher cleanup.
    """
    if not all(callable(operation) for operation in (read_bytes, write_bytes, drain)):
        raise TypeError("validated pipe byte operations required")
    _install_sdk_log_containment()

    @asynccontextmanager
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        gate = IngressGate()
        reader = PipeFrameReader(read_bytes, gate)
        read_send, read_receive = anyio.create_memory_object_stream[SessionMessage](0)
        write_send, write_receive = anyio.create_memory_object_stream[SessionMessage](0)

        async def receive() -> None:
            async with read_send:
                try:
                    while (frame := await reader.receive()) is not None:
                        try:
                            message = JSONRPCMessage.model_validate_json(frame)
                        except ValueError:
                            gate.fail("TOOLS_LIST_MALFORMED")
                        await read_send.send(SessionMessage(message))
                except (IngressError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                    return

        async def send() -> None:
            async with write_receive:
                try:
                    async for message in write_receive:
                        payload = message.message.model_dump_json(by_alias=True, exclude_none=True)
                        await write_bytes(payload.encode("utf-8") + b"\n")
                        await drain()
                except (OSError, EOFError, RuntimeError, anyio.BrokenResourceError):
                    try:
                        gate.fail("TOOLS_LIST_INGRESS_IO_FAILED")
                    except IngressError:
                        return
                except anyio.ClosedResourceError:
                    return

        token = _SDK_LOG_SCOPE.set(True)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(receive)
                group.start_soon(send)
                try:
                    yield read_receive, write_send, gate
                finally:
                    group.cancel_scope.cancel()
        finally:
            _SDK_LOG_SCOPE.reset(token)
            read_receive.close()
            write_send.close()

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
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        token = _SDK_LOG_SCOPE.set(True)
        get_started = False
        gate = IngressGate()

        async def constrain_request(request: httpx.Request) -> None:
            nonlocal get_started
            if gate.reason is not None and request.method != "DELETE":
                raise httpx.RequestError("MCP_INGRESS_CLOSED")
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
                    transport=BoundedHttpTransport(
                        httpx.AsyncHTTPTransport(
                            retries=0,
                            trust_env=False,
                            verify=ssl_context,
                            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                        ),
                        gate,
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
                yield (*streams, gate)
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
_MAX_TOOL_JSON_DEPTH = 64
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


@dataclass(frozen=True, slots=True)
class ToolListLimits:
    """tools/list collection bounds (contracts 2026-10-15 Scanner operating limits).

    Defaults are the decided values and stay configurable. Their meaning follows
    C's operating proposal v1 that the decision adopted: the Tool root object is
    depth 1; the 10 s initialization bound covers connecting and ``initialize``
    from the start of the wait; the 30 s collection bound runs from the start
    of initialization to the last page. The 1 MiB page and 8 MiB per-listing
    byte limits (contracts 2026-10-13) are measured before parsing, after
    framing/decompression. An actual SDK session requires an A ingress gate;
    legacy SDK-spawned stdio cannot collect. Session test doubles can exercise
    semantic collection independently and cannot prove raw ingress enforcement.
    """

    max_tools: int = 256
    max_pages: int = 32
    max_json_depth: int = 64
    page_timeout_s: float = 10.0
    collection_timeout_s: float = 30.0
    max_page_bytes: int = 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        for count in (
            self.max_tools,
            self.max_pages,
            self.max_json_depth,
            self.max_page_bytes,
            self.max_total_bytes,
        ):
            if type(count) is not int or count < 1:
                raise ValueError("positive tools/list count limits required")
        if self.max_json_depth > _MAX_TOOL_JSON_DEPTH:
            # FrozenJsonObject itself stops at this depth; a higher setting would
            # be reported as malformed instead of too deep.
            raise ValueError("tools/list JSON depth limit cannot exceed 64")
        for timeout in (self.page_timeout_s, self.collection_timeout_s):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("positive finite tools/list timeouts required")


@dataclass(frozen=True, slots=True, repr=False)
class ToolListing:
    """One server's collected tool definitions, or why the listing is incomplete.

    ``tools`` holds every definition only when ``complete``. An incomplete
    listing keeps no partial definitions and is never "0 tools, done, safe";
    ``reason`` is a fixed code without server text. Definitions are untrusted
    server data for C's Registry/Scanner, not Guard policy input.
    """

    complete: bool
    tools: tuple[FrozenJsonObject, ...]
    reason: str | None

    def __repr__(self) -> str:
        count = len(self.tools)
        return f"ToolListing(complete={self.complete}, tools={count}, reason={self.reason})"


def _incomplete_listing(reason: str) -> ToolListing:
    return ToolListing(complete=False, tools=(), reason=reason)


def _json_depth_exceeds(value: object, limit: int) -> bool:
    """Whether container nesting is deeper than ``limit`` (the Tool object is 1)."""

    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if type(item) is dict:
            children: list[object] = list(cast("dict[str, object]", item).values())
        elif type(item) is list:
            children = cast("list[object]", item)
        else:
            continue
        if depth > limit:
            return True
        pending.extend((child, depth + 1) for child in children)
    return False


async def collect_tool_definitions(
    session: _ToolSession, limits: ToolListLimits, *, deadline: float | None = None
) -> ToolListing:
    """Collect one listing; actual SDK sessions require pre-parse ingress.

    Pure session doubles can test the semantic limits without claiming a wire
    guarantee. One gate scope spans every page and is reset only after the
    collection finishes. A failed/abandoned real listing invalidates its
    connection so a late unbounded page cannot be parsed after scope exit.
    """
    gate = getattr(session, "listing_ingress", None)
    if isinstance(session, ClientSession) and not isinstance(gate, IngressGate):
        return _incomplete_listing("TOOLS_LIST_INGRESS_REQUIRED")
    if not isinstance(gate, IngressGate):
        return await _collect_tool_definitions(session, limits, deadline=deadline)
    try:
        gate.begin(limits.max_page_bytes, limits.max_total_bytes)
    except IngressError as error:
        return _incomplete_listing(error.reason)
    try:
        listing = await _collect_tool_definitions(session, limits, deadline=deadline)
        if gate.reason is not None:
            return _incomplete_listing(gate.reason)
        if listing.reason in {"TOOLS_LIST_TIMEOUT", "TOOLS_LIST_FAILED", "TOOLS_LIST_MALFORMED"}:
            gate.fail(listing.reason)
        return listing
    except IngressError as error:
        return _incomplete_listing(error.reason)
    except asyncio.CancelledError:
        with suppress(IngressError):
            gate.fail("TOOLS_LIST_CANCELLED")
        raise
    finally:
        gate.end()


async def _collect_tool_definitions(
    session: _ToolSession, limits: ToolListLimits, *, deadline: float | None = None
) -> ToolListing:
    """Page through tools/list within ``limits`` and keep each Tool object whole.

    Each definition is the SDK's parse of the wire object with only the fields
    the server sent (``exclude_unset``), including unknown extra fields. Byte-
    exact originals are not available after SDK parsing. Exceeding any bound is
    an incomplete listing: definitions are never truncated or guessed. A cursor
    seen before or a tool name repeated within the listing also ends it as
    incomplete (C operating proposal v1). ``deadline`` is the loop time at
    which the whole collection ends; by default it is ``collection_timeout_s``
    from now.
    """

    if deadline is None:
        deadline = asyncio.get_running_loop().time() + limits.collection_timeout_s
    tools: list[FrozenJsonObject] = []
    names: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    try:
        async with asyncio.timeout_at(deadline):
            for _page in range(limits.max_pages):
                async with asyncio.timeout(limits.page_timeout_s):
                    page = await session.list_tools(cursor)
                if not isinstance(page, ListToolsResult):
                    return _incomplete_listing("TOOLS_LIST_MALFORMED")
                # Count every listed entry before any de-duplication.
                if len(tools) + len(page.tools) > limits.max_tools:
                    return _incomplete_listing("TOOLS_LIST_TOO_MANY_TOOLS")
                for tool in page.tools:
                    if tool.name in names:
                        return _incomplete_listing("TOOLS_LIST_DUPLICATE_NAME")
                    names.add(tool.name)
                    definition = tool.model_dump(mode="json", by_alias=True, exclude_unset=True)
                    if _json_depth_exceeds(definition, limits.max_json_depth):
                        return _incomplete_listing("TOOL_DEFINITION_TOO_DEEP")
                    tools.append(FrozenJsonObject(definition))
                cursor = page.nextCursor
                if cursor is None:
                    return ToolListing(complete=True, tools=tuple(tools), reason=None)
                if cursor in cursors:
                    return _incomplete_listing("TOOLS_LIST_CURSOR_REPEATED")
                cursors.add(cursor)
                # Let shutdown/control run between pages of a long listing.
                await asyncio.sleep(0)
            return _incomplete_listing("TOOLS_LIST_TOO_MANY_PAGES")
    except TimeoutError:
        return _incomplete_listing("TOOLS_LIST_TIMEOUT")
    except McpError:
        return _incomplete_listing("TOOLS_LIST_FAILED")
    except ValueError:
        # Pydantic ValidationError and FrozenJsonObject rejections are ValueError.
        return _incomplete_listing("TOOLS_LIST_MALFORMED")


def _exception_name(exc: Exception) -> str:
    name = type(exc).__name__
    return name if name.isascii() and name.isidentifier() and len(name) <= 64 else "Exception"


async def _discard_logging(params: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


async def _discard_message(message: object) -> None:
    """Server notifications stay out of UI/model/logs until A14's sink exists."""


class _DeliveryCheckedStream(ObjectReceiveStream[Any]):
    """SDK read stream that re-checks each response as the SDK takes it.

    mcp 1.29 ``_receive_loop`` reaches ``_response_streams.pop`` without an
    await after this returns, so no tools/list ID can be registered between
    the check and the SDK's association.
    """

    def __init__(self, inner: ObjectReceiveStream[Any], gate: IngressGate) -> None:
        self._inner = inner
        self._gate = gate

    async def receive(self) -> Any:
        item = await self._inner.receive()
        if isinstance(item, SessionMessage):
            root = item.message.root
            if isinstance(root, JSONRPCResponse | JSONRPCError):
                try:
                    self._gate.check_delivery(root.id)
                except IngressError:
                    raise anyio.EndOfStream from None
        return item

    async def aclose(self) -> None:
        await self._inner.aclose()


def _sdk_session_factory(stream_factory: StreamFactory) -> SessionFactory:
    """Wrap only streams supplied by a verified composition root.

    C's connection validation and isolated output sinks precede wiring. SDK
    logger containment covers the streams, session and their child tasks.
    """

    _install_sdk_log_containment()

    @asynccontextmanager
    async def open_session() -> AsyncIterator[_ToolSession]:
        token = _SDK_LOG_SCOPE.set(True)
        try:
            async with stream_factory() as streams:
                if len(streams) < 2:
                    raise ValueError("MCP stream pair required")
                gate = next((item for item in streams[2:] if isinstance(item, IngressGate)), None)
                # The SDK only iterates, enters and closes its read stream.
                read_stream: Any = streams[0]
                if gate is not None:
                    read_stream = _DeliveryCheckedStream(read_stream, gate)
                async with _UnvalidatedClientSession(
                    read_stream,
                    streams[1],
                    logging_callback=_discard_logging,
                    message_handler=_discard_message,
                ) as session:
                    if isinstance(session, ClientSession):
                        session.listing_ingress = gate
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
        tool_list_limits: ToolListLimits | None = None,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session factory required")
        if tool_list_limits is not None and type(tool_list_limits) is not ToolListLimits:
            raise TypeError("tools/list limits have invalid type")
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
        self._tool_list_limits = tool_list_limits
        self._tool_listing: ToolListing | None = None
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
        self._ingress_gate: IngressGate | None = None

    async def start(self) -> None:
        if self._state is not _State.NEW:
            raise RuntimeError("server worker already started")
        self._state = _State.STARTING
        self._owner = asyncio.create_task(self._run(), name="mcp-server-worker")
        self._owner.add_done_callback(self._consume_task_exception)
        start_budget = self._startup_timeout_s
        if self._tool_list_limits is not None:
            # Both bounds run from the start in _run. The extra second lets an
            # expired listing report itself as incomplete before this outer
            # wait gives up on the start.
            start_budget = max(start_budget, self._tool_list_limits.collection_timeout_s) + 1.0
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=start_budget)
        except TimeoutError:
            self._state = _State.CLOSING
            self._owner_cancel_expected = True
            self._owner.cancel()
            raise DispatchError("SessionStartTimeout") from None
        if self._state is not _State.READY:
            raise DispatchError("SessionStartFailed")

    @property
    def tool_listing(self) -> ToolListing | None:
        """The startup tools/list result, or None when collection is not enabled."""

        return self._tool_listing

    async def call(self, req: ResolvedRequest) -> RawResult:
        if self._ingress_gate is not None and self._ingress_gate.reason is not None:
            self._quiesce_failed_ingress()
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
                # Cancelling a start (initialize or tools/list) is this close's
                # own action, not an owner failure.
                self._owner_cancel_expected = True
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
                "SessionCloseFailed"
                if teardown_failed
                or teardown_missing
                or self._ingress_gate is not None
                and self._ingress_gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED"
                else "WorkerOwnerFailed"
            )
            raise DispatchError(name) from None
        if self._state is _State.CLOSING:
            self._state = _State.CLOSED

    async def _run(self) -> None:
        tracker: _SessionExitTracker | None = None
        ingress_watch: asyncio.Task[None] | None = None
        limits = self._tool_list_limits
        started = asyncio.get_running_loop().time()
        # With tools/list collection on, the initialization bound covers
        # connecting and initialize from the start of the wait, and the
        # collection bound runs from the same start (C proposal v1).
        init_bound = None if limits is None else self._startup_timeout_s
        try:
            tracker = _SessionExitTracker(self._session_factory())
            async with asyncio.timeout(init_bound) as init_window:
                async with tracker as session:
                    candidate_gate = getattr(session, "listing_ingress", None)
                    if isinstance(candidate_gate, IngressGate):
                        self._ingress_gate = candidate_gate
                    await session.initialize()
                    init_window.reschedule(None)
                    self._initialized = True
                    if limits is not None:
                        # Before READY, so no Guard call reaches a session whose
                        # listing is still running. An incomplete listing is a
                        # warning for Registry/Scanner and does not stop the worker.
                        self._tool_listing = await collect_tool_definitions(
                            session, limits, deadline=started + limits.collection_timeout_s
                        )
                        gate = getattr(session, "listing_ingress", None)
                        if (
                            self._tool_listing.reason == "TOOLS_LIST_INGRESS_REQUIRED"
                            or isinstance(gate, IngressGate)
                            and gate.reason is not None
                        ):
                            # Preserve the fixed incomplete reason for Registry,
                            # but never advertise a dead/gateless session READY.
                            self._state = _State.FAILED
                            return
                    if self._state is _State.STARTING:
                        self._state = _State.READY
                    if self._ingress_gate is not None:
                        ingress_watch = asyncio.create_task(self._watch_ingress())
                    self._ready.set()
                    while True:
                        item = await self._queue.get()
                        if item is _STOP:
                            break
                        if not isinstance(item, _Command) or item.future.done():
                            continue
                        self._active = item
                        try:
                            await self._execute(session, item)
                        finally:
                            if not item.future.done():
                                item.future.set_exception(DispatchError("WorkerInterrupted"))
                            self._active = None
        finally:
            if ingress_watch is not None:
                ingress_watch.cancel()
                await asyncio.gather(ingress_watch, return_exceptions=True)
            if (
                self._ingress_gate is not None
                and self._ingress_gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED"
            ):
                self._close_failed = True
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

    def _quiesce_failed_ingress(self) -> None:
        gate = self._ingress_gate
        if gate is None or gate.reason is None:
            return
        if gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED":
            self._close_failed = True
        if self._state is _State.READY:
            self._unhealthy = True
            self._state = _State.CLOSING
            self._reject_queued("WorkerClosed")
            self._queue.put_nowait(_STOP)

    async def _watch_ingress(self) -> None:
        gate = self._ingress_gate
        if gate is None:
            return
        await gate.failed.wait()
        self._quiesce_failed_ingress()

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
        tool_list_limits: ToolListLimits | None = None,
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
                tool_list_limits=tool_list_limits,
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
