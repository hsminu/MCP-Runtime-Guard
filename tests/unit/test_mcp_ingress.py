"""A tools/list raw receive limits: I6/I9/I10, T10 discovery groundwork."""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from guard_app.adapters import mcp_http_ingress, mcp_ingress
from guard_app.adapters.mcp_ingress import IngressError, IngressGate, PipeFrameReader
from guard_app.adapters.mcp_transport import (
    McpTransport,
    ToolListLimits,
    collect_tool_definitions,
    http_stream_factory,
    validated_stdio_stream_factory,
)
from guard_app.adapters.mcp_transport.sdk_session import (
    _UnvalidatedClientSession,
    sdk_session_factory,
)
from guard_app.domain.errors import DispatchError

from .test_mcp_transport import _request


def _page(request_id: int, *, cursor: str | None = None, extra: str = "") -> bytes:
    result: dict[str, object] = {"tools": []}
    if cursor is not None:
        result["nextCursor"] = cursor
    return (
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}).encode() + extra.encode()
    )


def _gate(page: int, total: int | None = None, *, request_id: int = 1) -> IngressGate:
    gate = IngressGate()
    gate.begin(page, page if total is None else total)
    gate.expect(request_id)
    return gate


class _Source:
    def __init__(self, data: bytes, *, fragment: int = 4096) -> None:
        self.data = data
        self.fragment = fragment
        self.reads: list[int] = []

    async def read(self, size: int) -> bytes:
        self.reads.append(size)
        count = min(size, self.fragment)
        chunk, self.data = self.data[:count], self.data[count:]
        return chunk


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [b"", b" ", "한글".encode()])
async def test_pipe_exact_boundary_counts_whitespace_envelope_utf8(suffix: bytes) -> None:
    payload = (
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": [], "note": suffix.decode()}},
            ensure_ascii=False,
        ).encode()
        + b" \r"
    )
    gate = _gate(len(payload))
    source = _Source(payload + b"\n", fragment=1)
    assert await PipeFrameReader(source.read, gate).receive() == payload
    assert gate.candidate_limit == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"x" * 80, _page(1) + b" " * 80])
async def test_oversize_pipe_stops_before_any_json_parse(
    payload: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed: list[object] = []
    monkeypatch.setattr(mcp_ingress.json, "loads", lambda value: parsed.append(value))
    gate = _gate(20, 100)
    source = _Source(payload + b"\n", fragment=7)
    with pytest.raises(IngressError, match="TOOLS_LIST_PAGE_TOO_LARGE"):
        await PipeFrameReader(source.read, gate).receive()
    assert parsed == []
    assert len(payload + b"\n") - len(source.data) == 21
    assert max(source.reads) <= 21


@pytest.mark.asyncio
async def test_pipe_fragmented_multiple_frames_and_cr_whitespace() -> None:
    one = _page(1) + b"\r"
    two = _page(2)
    gate = _gate(len(one), len(one) + len(two))
    source = _Source(one + b"\n" + two + b"\n", fragment=4096)
    reader = PipeFrameReader(source.read, gate)
    assert await reader.receive() == one
    gate.expect(2)
    assert await reader.receive() == two
    assert gate.candidate_limit == 0


def test_total_budget_checks_candidates_before_parse_and_resets_only_new_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _page(1)
    gate = _gate(len(payload) + 1, 2 * len(payload) - 1)
    gate.accept(payload)
    gate.expect(2)
    parsed: list[object] = []
    monkeypatch.setattr(mcp_ingress.json, "loads", lambda value: parsed.append(value))
    with pytest.raises(IngressError, match="TOOLS_LIST_TOTAL_TOO_LARGE"):
        gate.accept(_page(2))
    assert parsed == []
    gate.end()
    with pytest.raises(IngressError, match="TOOLS_LIST_TOTAL_TOO_LARGE"):
        gate.begin(1000, 1000)


def test_successful_independent_listings_reset_budget_but_ignore_late_old_ids() -> None:
    first, second = _page(1), _page(2)
    gate = _gate(1000, 1000)
    gate.accept(first)
    gate.end()
    gate.begin(1000, len(first) + len(second))
    gate.expect(2)
    gate.accept(first)  # a late duplicate from the completed old listing
    assert gate.candidate_limit == len(first) + len(second)
    gate.accept(second)
    assert gate.candidate_limit == len(first)
    gate.end()
    gate.begin(1000, 1000)
    with pytest.raises(IngressError, match="REQUEST_ID_INVALID"):
        gate.expect(1)


@pytest.mark.parametrize("response_id", [True, 1.0, "1", "01"])
def test_response_id_sdk_coercions_cannot_bypass_total(response_id: object) -> None:
    gate = _gate(1000)
    payload = json.dumps({"jsonrpc": "2.0", "id": response_id, "result": {"tools": []}})
    with pytest.raises(IngressError, match="RESPONSE_ID_INVALID"):
        gate.accept(payload.encode())


@pytest.mark.parametrize("response_id", [1, "1", "01"])
def test_delivery_of_still_pending_id_was_never_admitted(response_id: object) -> None:
    gate = IngressGate()
    gate.accept(_page(1))  # inactive: no association, no debit
    gate.begin(1000, 1000)
    gate.expect(1)
    with pytest.raises(IngressError, match="TOOLS_LIST_RESPONSE_UNADMITTED"):
        gate.check_delivery(response_id)
    assert gate.failed.is_set()


@pytest.mark.parametrize("response_id", [1, 2, "x", None])
def test_delivery_after_admitted_association_or_of_unknown_id_passes(response_id: object) -> None:
    gate = _gate(1000)
    assert gate.accept(_page(1))
    gate.check_delivery(response_id)
    assert gate.reason is None


@pytest.mark.parametrize("extra", [{"method": "x"}, {"method": None}, {"error": {}}])
def test_ambiguous_response_envelopes_are_rejected(extra: dict[str, object]) -> None:
    gate = _gate(1000)
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}, **extra}
    with pytest.raises(IngressError, match="TOOLS_LIST_MALFORMED"):
        gate.accept(json.dumps(payload).encode())


def test_notifications_are_candidate_bounded_and_do_not_debit_total() -> None:
    gate = _gate(1000, 1000)
    payload = b'{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}'
    gate.accept(payload)
    assert gate.candidate_limit == 1000
    with pytest.raises(IngressError, match="PAGE_TOO_LARGE"):
        gate.accept(payload + b" " * 1000)


@pytest.mark.asyncio
async def test_no_collection_does_not_cap_ordinary_messages() -> None:
    payload = b"x" * 10000
    source = _Source(payload + b"\n")
    gate = IngressGate()
    assert await PipeFrameReader(source.read, gate).receive() == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"", b'{"jsonrpc":'])
async def test_eof_during_listing_is_sticky_and_payload_free(data: bytes) -> None:
    gate = _gate(1000)
    reader = PipeFrameReader(_Source(data).read, gate)
    with pytest.raises(IngressError, match="TOOLS_LIST_INGRESS_EOF"):
        await reader.receive()
    assert gate.failed.is_set()
    assert gate.reason == "TOOLS_LIST_INGRESS_EOF"


@pytest.mark.asyncio
async def test_cancellation_discards_partial_pipe_frame() -> None:
    entered = asyncio.Event()

    async def read(_size: int) -> bytes:
        entered.set()
        await asyncio.Event().wait()
        return b""

    gate = _gate(1000)
    reader = PipeFrameReader(read, gate)
    task = asyncio.create_task(reader.receive())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await reader.receive() is None


class _Peer:
    """Byte-only synthetic pipe peer; no launcher/process ownership claim."""

    def __init__(self, pages: Callable[[int, int], bytes], *, fragment: int = 3) -> None:
        self.pages = pages
        self.fragment = fragment
        self.output = bytearray()
        self.available = asyncio.Event()
        self.list_requests = 0
        self.ping_requests = 0
        self.writes = 0
        self.drains = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        while not self.output:
            self.available.clear()
            await self.available.wait()
        count = min(size, self.fragment)
        chunk = bytes(self.output[:count])
        del self.output[:count]
        return chunk

    async def write(self, frame: bytes) -> None:
        self.writes += 1
        request = json.loads(frame)
        if request.get("method") == "initialize":
            response = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "protocolVersion": request["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "unit", "version": "1"},
                    },
                }
            ).encode()
        elif request.get("method") == "tools/list":
            self.list_requests += 1
            response = self.pages(request["id"], self.list_requests)
        elif request.get("method") == "ping":
            self.ping_requests += 1
            response = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}).encode()
        else:
            return
        self.output.extend(response + b"\n")
        self.available.set()

    async def drain(self) -> None:
        self.drains += 1


@pytest.mark.asyncio
async def test_sdk_pipe_listing_exact_and_two_pages_share_total() -> None:
    first = _page(1, cursor="x", extra="  ")
    second = _page(2, extra=" \r")
    peer = _Peer(lambda request_id, _number: first if request_id == 1 else second)
    transport = McpTransport(
        {"unit": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        tool_list_limits=ToolListLimits(
            max_page_bytes=max(len(first), len(second)), max_total_bytes=len(first) + len(second)
        ),
    )
    await transport.start()
    try:
        listing = transport.tool_listing("unit")
        assert listing is not None
        assert listing.complete
        assert peer.list_requests == 2
    finally:
        await transport.close()
    assert peer.drains == peer.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_sdk_oversize_never_parses_or_retries_and_worker_is_not_ready(
    malformed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"private" * 20 if malformed else _page(1, extra=" " * 140)
    peer = _Peer(lambda _request_id, _number: payload)
    parsed: list[bytes] = []
    original = JSONRPCMessage.model_validate_json

    def parse(_cls: object, value: bytes, **kwargs: Any) -> JSONRPCMessage:
        parsed.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(JSONRPCMessage, "model_validate_json", classmethod(parse))
    transport = McpTransport(
        {"unit": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        tool_list_limits=ToolListLimits(max_page_bytes=80),
    )
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionStartFailed"
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert not listing.complete
    assert listing.reason == "TOOLS_LIST_PAGE_TOO_LARGE"
    assert listing.tools == ()
    assert len(parsed) == 1  # initialization only; oversized page never reaches SDK
    assert peer.list_requests == 1
    with pytest.raises(DispatchError) as closed:
        await transport.call(_request("unit", seq=1))
    assert closed.value.exception_type == "WorkerClosed"
    await transport.close()


@pytest.mark.asyncio
async def test_sdk_page_two_over_remaining_never_reaches_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _page(1, cursor="x"), _page(2)
    peer = _Peer(lambda request_id, _number: first if request_id == 1 else second)
    parsed: list[bytes] = []
    original = JSONRPCMessage.model_validate_json

    def parse(_cls: object, value: bytes, **kwargs: Any) -> JSONRPCMessage:
        parsed.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(JSONRPCMessage, "model_validate_json", classmethod(parse))
    transport = McpTransport(
        {"unit": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        tool_list_limits=ToolListLimits(
            max_page_bytes=1000, max_total_bytes=len(first) + len(second) - 1
        ),
    )
    with pytest.raises(DispatchError):
        await transport.start()
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert listing.reason == "TOOLS_LIST_TOTAL_TOO_LARGE"
    assert len(parsed) == 2  # initialization + page one, never page two
    assert peer.list_requests == 2
    await transport.close()


@pytest.mark.asyncio
async def test_actual_sdk_session_without_ingress_refuses_list_without_dispatch() -> None:
    import anyio

    read_send, read_receive = anyio.create_memory_object_stream(0)
    write_send, write_receive = anyio.create_memory_object_stream(0)
    session = _UnvalidatedClientSession(read_receive, write_send)
    try:
        listing = await collect_tool_definitions(session, ToolListLimits())
        assert not listing.complete
        assert listing.reason == "TOOLS_LIST_INGRESS_REQUIRED"
        assert write_receive.statistics().current_buffer_used == 0
        assert session._request_id == 0
    finally:
        for stream in (read_send, read_receive, write_send, write_receive):
            stream.close()


@pytest.mark.asyncio
async def test_real_sdk_session_without_a_gate_is_never_ready_for_calls() -> None:
    """Legacy SDK-spawned stdio has no raw ingress gate, so it cannot collect.

    The listing reports that, and the worker must not advertise the session
    READY either: Registry gets the fixed reason and the Guard gets no server.
    """
    peer = _Peer(lambda request_id, _number: _page(request_id))
    gated = validated_stdio_stream_factory(peer.read, peer.write, peer.drain)

    @asynccontextmanager
    async def without_gate() -> AsyncIterator[tuple[Any, ...]]:
        async with gated() as (read_stream, write_stream, _gate):
            yield read_stream, write_stream

    transport = McpTransport({"unit": without_gate}, tool_list_limits=ToolListLimits())
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionStartFailed"
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert listing.reason == "TOOLS_LIST_INGRESS_REQUIRED"
    assert peer.list_requests == 0  # tools/list was never sent


@pytest.mark.asyncio
async def test_pipe_write_failure_fails_the_connection_gate_without_its_text() -> None:
    async def read_bytes(_size: int) -> bytes:
        await asyncio.Event().wait()  # the peer never answers
        return b""

    async def write_bytes(_frame: bytes) -> None:
        raise OSError("private-pipe-detail")

    async def drain() -> None:
        pass

    factory = validated_stdio_stream_factory(read_bytes, write_bytes, drain)
    async with factory() as (_read_stream, write_stream, gate):
        ping = JSONRPCMessage.model_validate({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        await write_stream.send(SessionMessage(ping))
        await asyncio.wait_for(gate.failed.wait(), 1)
    assert gate.reason == "TOOLS_LIST_INGRESS_IO_FAILED"


@pytest.mark.asyncio
async def test_malformed_pipe_frame_fails_the_connection_gate_and_reaches_no_reader() -> None:
    frames = [b"not-json-rpc\n"]

    async def read_bytes(_size: int) -> bytes:
        if frames:
            return frames.pop()
        await asyncio.Event().wait()
        return b""

    async def write_bytes(_frame: bytes) -> None:
        pass

    async def drain() -> None:
        pass

    factory = validated_stdio_stream_factory(read_bytes, write_bytes, drain)
    async with factory() as (read_stream, _write_stream, gate):
        await asyncio.wait_for(gate.failed.wait(), 1)
        with pytest.raises(anyio.EndOfStream):
            await read_stream.receive()
    assert gate.reason == "TOOLS_LIST_MALFORMED"


@pytest.mark.asyncio
async def test_two_real_sdk_collections_get_independent_budgets() -> None:
    peer = _Peer(lambda request_id, _number: _page(request_id))
    factory = validated_stdio_stream_factory(peer.read, peer.write, peer.drain)
    size = len(_page(1))
    async with sdk_session_factory(factory)() as session:
        await session.initialize()
        limits = ToolListLimits(max_page_bytes=size, max_total_bytes=size)
        assert (await collect_tool_definitions(session, limits)).complete
        assert (await collect_tool_definitions(session, limits)).complete
    assert peer.list_requests == 2


@pytest.mark.asyncio
async def test_concurrent_ping_cannot_steal_listing_response_budget_association() -> None:
    peer = _Peer(lambda request_id, number: _page(request_id, cursor="x" if number == 1 else None))
    factory = validated_stdio_stream_factory(peer.read, peer.write, peer.drain)
    total = len(_page(2, cursor="x")) + len(_page(3)) - 1
    async with sdk_session_factory(factory)() as session:
        await session.initialize()
        listing, _ping = await asyncio.gather(
            collect_tool_definitions(session, ToolListLimits(max_total_bytes=total)),
            session.send_ping(),  # type: ignore[attr-defined]
        )
        assert listing.reason == "TOOLS_LIST_TOTAL_TOO_LARGE"
        assert not listing.complete
    assert peer.list_requests == 2
    assert peer.ping_requests == 1


@pytest.mark.asyncio
async def test_real_sdk_session_reports_only_that_the_tool_list_changed() -> None:
    """LIST-01: tools/list_changed reaches the callback as a bare call; params
    and every other notification stay inside the SDK session."""
    peer = _Peer(lambda request_id, _number: _page(request_id))
    factory = validated_stdio_stream_factory(peer.read, peer.write, peer.drain)
    seen: list[str] = []
    async with sdk_session_factory(factory, lambda: seen.append("changed"))() as session:
        await session.initialize()
        for method in (
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
            "notifications/tools/list_changed",
            "notifications/tools/list_changed",
        ):
            notice = {"jsonrpc": "2.0", "method": method, "params": {"_meta": {"note": "x"}}}
            peer.output.extend(json.dumps(notice).encode() + b"\n")
        peer.available.set()
        # The ping answer follows the notices, and the SDK handles them in order.
        await session.send_ping()  # type: ignore[attr-defined]
    assert seen == ["changed", "changed"]


def test_transport_rejects_a_tools_changed_callback_that_is_not_callable() -> None:
    with pytest.raises(TypeError, match="tools changed callback must be callable"):
        McpTransport({}, on_tools_changed="not callable")  # type: ignore[arg-type]


class _PredictingPeer(_Peer):
    """Pre-sends an oversized answer to the predicted first tools/list ID.

    The first drain stalls, so the SDK receive loop blocks answering the ping
    while the predicted page is admitted with the gate still inactive.
    """

    def __init__(self, page_bytes: int) -> None:
        super().__init__(lambda request_id, _number: _page(request_id), fragment=4096)
        self.predicted = _page(1, extra=" " * page_bytes)

    async def write(self, frame: bytes) -> None:
        await super().write(frame)
        if json.loads(frame).get("method") == "initialize":
            ping = b'{"jsonrpc":"2.0","id":"p0","method":"ping"}'
            self.output.extend(ping + b"\n" + self.predicted + b"\n")
            self.available.set()

    async def drain(self) -> None:
        await super().drain()
        if self.drains == 1:
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_predicted_response_held_behind_backpressure_cannot_complete_listing() -> None:
    limit = 300
    peer = _PredictingPeer(limit)
    transport = McpTransport(
        {"unit": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        tool_list_limits=ToolListLimits(max_page_bytes=limit, max_total_bytes=limit),
    )
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionStartFailed"
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert not listing.complete
    assert listing.reason == "TOOLS_LIST_RESPONSE_UNADMITTED"
    assert peer.list_requests == 1
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["eof", "io", "contract"])
async def test_sdk_pipe_receive_failure_is_incomplete_and_no_replay(failure: str) -> None:
    peer = _Peer(lambda request_id, _number: _page(request_id))

    async def read(size: int) -> bytes:
        if peer.list_requests:
            if failure == "io":
                raise OSError("private source text")
            if failure == "contract":
                return b"x" * (size + 1)
            return b""
        return await peer.read(size)

    transport = McpTransport(
        {"unit": validated_stdio_stream_factory(read, peer.write, peer.drain)},
        tool_list_limits=ToolListLimits(),
    )
    with pytest.raises(DispatchError):
        await transport.start()
    listing = transport.tool_listing("unit")
    assert listing is not None
    expected = {
        "eof": "TOOLS_LIST_INGRESS_EOF",
        "io": "TOOLS_LIST_INGRESS_IO_FAILED",
        "contract": "TOOLS_LIST_PIPE_READ_INVALID",
    }
    assert listing.reason == expected[failure]
    assert peer.list_requests == 1
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_abandoned_sdk_listing_invalidates_connection(cancel: bool) -> None:
    peer = _Peer(lambda request_id, _number: _page(request_id))
    waiting = asyncio.Event()

    async def write(frame: bytes) -> None:
        request = json.loads(frame)
        if request.get("method") == "tools/list":
            peer.list_requests += 1
            waiting.set()
            return
        await peer.write(frame)

    factory = validated_stdio_stream_factory(peer.read, write, peer.drain)
    async with sdk_session_factory(factory)() as session:
        await session.initialize()
        limits = ToolListLimits(page_timeout_s=0.02)
        task = asyncio.create_task(collect_tool_definitions(session, limits))
        await waiting.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            expected = "TOOLS_LIST_CANCELLED"
        else:
            assert (await task).reason == "TOOLS_LIST_TIMEOUT"
            expected = "TOOLS_LIST_TIMEOUT"
        listing = await collect_tool_definitions(session, limits)
        assert listing.reason == expected
        assert peer.list_requests == 1


def test_configured_byte_limits_are_strict_positive_integers() -> None:
    assert ToolListLimits().max_page_bytes == 1024 * 1024
    assert ToolListLimits().max_total_bytes == 8 * 1024 * 1024
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive"):
            ToolListLimits(max_page_bytes=value)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="positive"):
            ToolListLimits(max_total_bytes=value)  # type: ignore[arg-type]


class _HttpBytes(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.body), 3):
            yield self.body[offset : offset + 3]

    async def aclose(self) -> None:
        self.closes += 1


class _HttpPeer(httpx.AsyncBaseTransport):
    def __init__(self, *, sse: bool, compressed: bool, malformed: bool) -> None:
        self.sse = sse
        self.compressed = compressed
        self.malformed = malformed
        self.list_requests = 0
        self.sources: list[_HttpBytes] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        value = json.loads(request.content)
        if value.get("method") == "initialize":
            result = {
                "protocolVersion": value["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "unit", "version": "1"},
            }
            body = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
            media, encoding = "application/json", "identity"
        elif value.get("method") == "tools/list":
            self.list_requests += 1
            body = b"private" * 30 if self.malformed else _page(value["id"], extra=" " * 200)
            if self.sse:
                body = b"event: message\r\ndata: " + body + b"\r\n\r\n"
            media = "text/event-stream" if self.sse else "application/json"
            encoding = "gzip" if self.compressed else "identity"
            if self.compressed:
                body = gzip.compress(body)
        else:
            return httpx.Response(202)
        source = _HttpBytes(body)
        self.sources.append(source)
        return httpx.Response(
            200, headers={"content-type": media, "content-encoding": encoding}, stream=source
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
async def test_actual_http_sdk_rejects_oversize_before_its_parser_without_retry(
    sse: bool, compressed: bool, malformed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    peer = _HttpPeer(sse=sse, compressed=compressed, malformed=malformed)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_kwargs: peer)
    parsed: list[object] = []
    original = JSONRPCMessage.model_validate_json

    def parse(_cls: object, value: bytes | str, **kwargs: Any) -> JSONRPCMessage:
        parsed.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(JSONRPCMessage, "model_validate_json", classmethod(parse))
    transport = McpTransport(
        {"unit": http_stream_factory("http://127.0.0.1:9999/mcp")},
        tool_list_limits=ToolListLimits(max_page_bytes=80),
    )
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionStartFailed"
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert listing.reason == "TOOLS_LIST_PAGE_TOO_LARGE"
    assert listing.tools == ()
    assert peer.list_requests == 1
    assert len(parsed) == 1  # initialization only, never the oversize listing
    assert all(source.closes == 1 for source in peer.sources)
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_http_post_source_close_precedes_listing_success_and_worker_ready(
    sse: bool, failure: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FailedClose(_HttpBytes):
        async def aclose(self) -> None:
            self.closes += 1
            if failure == "timeout":
                await asyncio.Event().wait()
            await asyncio.sleep(0.01)
            raise OSError("PRIVATE_CLOSE_TEXT")

    class Peer(_HttpPeer):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            response = await super().handle_async_request(request)
            if json.loads(request.content).get("method") == "tools/list":
                source = FailedClose(self.sources[-1].body)
                self.sources[-1] = source
                response.stream = source
            return response

    peer = Peer(sse=sse, compressed=False, malformed=False)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_kwargs: peer)
    monkeypatch.setattr(mcp_http_ingress, "_CLOSE_TIMEOUT_S", 0.02)
    transport = McpTransport(
        {"unit": http_stream_factory("http://127.0.0.1:9999/mcp")},
        tool_list_limits=ToolListLimits(),
    )
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionCloseFailed"
    listing = transport.tool_listing("unit")
    assert listing is not None
    assert not listing.complete
    assert listing.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED"
    assert transport._workers["unit"]._state.name == "FAILED"
    with pytest.raises(DispatchError) as close_failed:
        await transport.close()
    assert close_failed.value.exception_type == "SessionCloseFailed"
    assert peer.list_requests == 1
    assert "PRIVATE_CLOSE_TEXT" not in caplog.text


@pytest.mark.asyncio
async def test_late_get_source_close_failure_quiesces_ready_worker(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class LateGet(_HttpBytes):
        def __init__(self) -> None:
            super().__init__(b"")
            self.entered = asyncio.Event()
            self.finish = asyncio.Event()

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.entered.set()
            yield b": priming\n\n"
            await self.finish.wait()

        async def aclose(self) -> None:
            self.closes += 1
            raise OSError("PRIVATE_GET_CLOSE")

    get_source = LateGet()

    class Peer(_HttpPeer):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=get_source
                )
            if request.method == "DELETE":
                return httpx.Response(200)
            response = await super().handle_async_request(request)
            if json.loads(request.content).get("method") == "initialize":
                response.headers["mcp-session-id"] = "unit"
            return response

    peer = Peer(sse=False, compressed=False, malformed=False)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_kwargs: peer)
    transport = McpTransport(
        {"unit": http_stream_factory("http://127.0.0.1:9999/mcp")},
        tool_list_limits=ToolListLimits(),
    )
    await transport.start()
    async with asyncio.timeout(1):
        await get_source.entered.wait()
        get_source.finish.set()
        await transport._workers["unit"]._stopped.wait()
    worker = transport._workers["unit"]
    assert worker._state.name == "FAILED"
    assert worker._ingress_gate is not None
    assert worker._ingress_gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED"
    with pytest.raises(DispatchError) as closed:
        await transport.call(_request("unit", seq=1))
    assert closed.value.exception_type == "WorkerClosed"
    with pytest.raises(DispatchError) as close_failed:
        await transport.close()
    assert close_failed.value.exception_type == "SessionCloseFailed"
    assert peer.list_requests == 1
    assert get_source.closes == 1
    assert "PRIVATE_GET_CLOSE" not in caplog.text
