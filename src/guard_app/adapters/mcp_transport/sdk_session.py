"""The MCP SDK client session over a verified stream pair."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from typing import Any, Protocol

import anyio
from anyio.abc import ObjectReceiveStream
from mcp import ClientSession
from mcp.shared.message import MessageMetadata, SessionMessage
from mcp.shared.session import ProgressFnT
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ClientRequest,
    JSONRPCError,
    JSONRPCResponse,
    ListToolsResult,
    PaginatedRequestParams,
    RequestParams,
    ServerNotification,
    ToolListChangedNotification,
)
from pydantic import BaseModel

from guard_app.adapters.mcp_ingress import IngressError, IngressGate
from guard_app.adapters.mcp_transport.sdk_logging import SDK_LOG_SCOPE, install_sdk_log_containment
from guard_app.adapters.mcp_transport.streams import StreamFactory


class ToolSession(Protocol):
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


type SessionFactory = Callable[[], AbstractAsyncContextManager[ToolSession]]


async def _discard_logging(params: object) -> None:
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


def sdk_session_factory(
    stream_factory: StreamFactory, on_tools_changed: Callable[[], None] | None = None
) -> SessionFactory:
    """Wrap only streams supplied by a verified composition root.

    C's connection validation and isolated output sinks precede wiring. SDK
    logger containment covers the streams, session and their child tasks.
    """

    install_sdk_log_containment()

    async def handle_message(message: object) -> None:
        # LIST-01: only the fact that the server's tool list changed leaves the
        # SDK, never the notification's params. Everything else is discarded.
        if (
            on_tools_changed is not None
            and isinstance(message, ServerNotification)
            and isinstance(message.root, ToolListChangedNotification)
        ):
            on_tools_changed()

    @asynccontextmanager
    async def open_session() -> AsyncIterator[ToolSession]:
        token = SDK_LOG_SCOPE.set(True)
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
                    message_handler=handle_message,
                ) as session:
                    if isinstance(session, ClientSession):
                        session.listing_ingress = gate
                    yield session
        finally:
            SDK_LOG_SCOPE.reset(token)

    return open_session
