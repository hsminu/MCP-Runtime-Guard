"""Stream factories for C-validated stdio pipes and preapproved HTTP endpoints.

The SDK factories enforce transport settings; they do not replace
registration, endpoint approval or Guard's tools/call entry.
"""

from __future__ import annotations

import asyncio
import math
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, TextIO, cast

import anyio
import httpx

# A46: httpx imports httpcore lazily inside AsyncHTTPTransport(), which stalled
# the backend loop on the first HTTP connection. Import it with this module
# instead (httpx's own locked dependency), before any backend loop runs.
import httpcore  # noqa: F401  # isort: skip
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from guard_app.adapters.mcp_http_ingress import BoundedHttpTransport
from guard_app.adapters.mcp_ingress import IngressError, IngressGate, PipeFrameReader
from guard_app.adapters.mcp_transport.sdk_logging import SDK_LOG_SCOPE, install_sdk_log_containment

type StreamFactory = Callable[[], AbstractAsyncContextManager[tuple[Any, ...]]]


class HttpStreamFactory:
    """Stream factory that exposes its HTTP read bound for McpTransport checks."""

    __slots__ = ("_connect", "read_timeout_s")

    def __init__(self, connect: StreamFactory, read_timeout_s: float) -> None:
        self._connect = connect
        self.read_timeout_s = read_timeout_s

    def __call__(self) -> AbstractAsyncContextManager[tuple[Any, ...]]:
        return self._connect()


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
    install_sdk_log_containment()

    @asynccontextmanager
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        token = SDK_LOG_SCOPE.set(True)
        try:
            async with stdio_client(parameters, errlog=errlog) as streams:
                yield streams
        finally:
            SDK_LOG_SCOPE.reset(token)

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
    install_sdk_log_containment()

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

        token = SDK_LOG_SCOPE.set(True)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(receive)
                group.start_soon(send)
                try:
                    yield read_receive, write_send, gate
                finally:
                    group.cancel_scope.cancel()
        finally:
            SDK_LOG_SCOPE.reset(token)
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
    install_sdk_log_containment()

    @asynccontextmanager
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        token = SDK_LOG_SCOPE.set(True)
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
            SDK_LOG_SCOPE.reset(token)

    return HttpStreamFactory(connect, float(read_timeout_s))
