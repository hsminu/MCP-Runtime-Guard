"""I6/I9, T14: bound untrusted listing bytes before SDK JSON/SSE parsing."""

from __future__ import annotations

import asyncio
import gzip
import json
import zlib
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx_sse import EventSource

from guard_app.adapters import mcp_http_ingress
from guard_app.adapters.mcp_http_ingress import BoundedHttpTransport
from guard_app.adapters.mcp_ingress import IngressError, IngressGate


class Source(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = 0
        self.reads = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed += 1


class Inner(httpx.AsyncBaseTransport):
    def __init__(self, source: Source, media: str, encoding: str = "identity") -> None:
        self.source = source
        self.media = media
        self.encoding = encoding
        self.requests = 0
        self.closed = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(
            200,
            headers={"content-type": self.media, "content-encoding": self.encoding},
            stream=self.source,
        )

    async def aclose(self) -> None:
        self.closed += 1


def listing(limit: int, total: int | None = None) -> IngressGate:
    gate = IngressGate()
    gate.begin(limit, limit if total is None else total)
    gate.expect(1)
    return gate


def payload(request_id: int = 1) -> bytes:
    return b'  {"jsonrpc":"2.0","id":' + str(request_id).encode() + b',"result":{"tools":[]}} \r\n'


async def response_for(
    source: Source, gate: IngressGate, media: str = "application/json", encoding: str = "identity"
) -> tuple[httpx.Response, Inner]:
    inner = Inner(source, media, encoding)
    transport = BoundedHttpTransport(inner, gate)
    response = await transport.handle_async_request(httpx.Request("POST", "https://mcp.test/"))
    return response, inner


@pytest.mark.asyncio
async def test_exact_json_preserves_raw_whitespace_and_debits_once() -> None:
    body = payload()
    gate = listing(len(body))
    source = Source([body[:13], body[13:]])
    response, inner = await response_for(source, gate)
    assert await response.aread() == body
    assert gate.candidate_limit == 0
    assert source.closed == 1
    assert inner.requests == 1


@pytest.mark.asyncio
async def test_oversize_json_never_reaches_json_parser_or_yields(monkeypatch) -> None:
    body = payload()
    gate = listing(len(body) - 1)
    source = Source([body])
    response, inner = await response_for(source, gate)
    parsed = []
    real_loads = json.loads

    def record_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr(json, "loads", record_loads)
    admitted = []

    async def receive():
        async for block in response.aiter_bytes():
            admitted.append(block)

    with pytest.raises(IngressError, match="^TOOLS_LIST_PAGE_TOO_LARGE$"):
        await receive()
    assert parsed == []
    assert admitted == []
    assert gate.failed.is_set()
    assert source.closed == inner.requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [b"\n", b"\r", b"\r\n"])
async def test_fragmented_sse_multiline_counts_utf8_payload_and_preserves_framing(ending) -> None:
    body = b'{"jsonrpc":"2.0",\n"id":1,"result":{"tools":[],"x":"' + "한".encode() + b'"}}'
    first, second = body.split(b"\n")
    wire = (
        b": comment"
        + ending
        + b"event: message"
        + ending
        + b"data: "
        + first
        + ending
        + b"data:"
        + second
        + ending
        + ending
    )
    gate = listing(len(body))
    source = Source([wire[index : index + 1] for index in range(len(wire))])
    response, _ = await response_for(source, gate, "text/event-stream; charset=utf-8")
    events = [event async for event in EventSource(response).aiter_sse()]
    assert len(events) == 1
    assert events[0].data.encode() == body
    assert gate.candidate_limit == 0
    assert source.closed == 1


@pytest.mark.asyncio
async def test_sse_budget_is_per_event_and_notifications_do_not_debit() -> None:
    notification = b'{"jsonrpc":"2.0","method":"notifications/progress"}'
    body = payload().rstrip(b"\r\n") + b" " * 10
    wire = b"".join(b"data: " + notification + b"\n\n" for _ in range(25))
    wire += b"data: " + body + b"\n\n"
    gate = listing(max(len(body), len(notification)), len(body))
    source = Source([wire])
    response, _ = await response_for(source, gate, "text/event-stream")
    events = [event async for event in EventSource(response).aiter_sse()]
    assert len(events) == 26
    assert gate.candidate_limit == 0
    assert source.closed == 1


@pytest.mark.asyncio
async def test_sse_data_oversize_rejected_before_utf8_decode() -> None:
    gate = listing(20)
    source = Source([b"data: " + b"\xff" * 21 + b"\n\n"])
    response, _ = await response_for(source, gate, "text/event-stream")
    with pytest.raises(IngressError, match="^TOOLS_LIST_PAGE_TOO_LARGE$"):
        await response.aread()
    assert source.closed == 1


@pytest.mark.asyncio
async def test_existing_get_event_started_before_scope_cannot_bypass_gate() -> None:
    gate = IngressGate()

    class ActivatingSource(Source):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"data: " + b" " * 30
            gate.begin(20, 20)
            gate.expect(1)
            yield b"{}\n\n"

    source = ActivatingSource([])
    response, _ = await response_for(source, gate, "text/event-stream")
    with pytest.raises(IngressError, match="^TOOLS_LIST_PAGE_TOO_LARGE$"):
        await response.aread()
    assert source.closed == 1


@pytest.mark.asyncio
async def test_custom_event_payload_is_bounded_but_not_json_parsed() -> None:
    source = Source([b"event: custom\ndata: not-json\n\n"])
    gate = listing(100)
    response, _ = await response_for(source, gate, "text/event-stream")
    assert await response.aread() == b"event: custom\ndata: not-json\n\n"
    assert gate.candidate_limit == 100


@pytest.mark.asyncio
async def test_sse_metadata_has_separate_bound() -> None:
    gate = listing(10)
    source = Source([b":" + b"x" * 11])
    response, _ = await response_for(source, gate, "text/event-stream")
    with pytest.raises(IngressError, match="^TOOLS_LIST_FRAMING_TOO_LARGE$"):
        await response.aread()
    assert source.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["application/json", "text/event-stream"])
async def test_ordinary_call_body_is_not_capped(media) -> None:
    body = payload() + b" " * 100000
    if media == "text/event-stream":
        body = b"data: " + body.replace(b"\r\n", b"") + b"\n\n"
    source = Source([body])
    gate = IngressGate()
    response, _ = await response_for(source, gate, media)
    assert await response.aread() == body
    assert gate.reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
async def test_compressed_json_decodes_once_even_when_fragmented(encoding) -> None:
    body = payload()
    if encoding == "gzip":
        compressed = gzip.compress(body)
    elif encoding == "deflate":
        compressed = zlib.compress(body)
    else:
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        compressed = compressor.compress(body) + compressor.flush()
    gate = listing(len(body))
    source = Source([compressed[index : index + 1] for index in range(len(compressed))])
    response, _ = await response_for(
        source, gate, encoding="deflate" if encoding == "raw-deflate" else encoding
    )
    assert "content-encoding" not in response.headers
    assert "content-length" not in response.headers
    assert await response.aread() == body
    assert gate.candidate_limit == 0


@pytest.mark.asyncio
async def test_gzip_bomb_uses_bounded_zlib_outputs_before_gate_rejection(monkeypatch) -> None:
    compressed = gzip.compress(b" " * 1000000)
    gate = listing(100)
    limits = []
    real_decompressobj = zlib.decompressobj

    class Decoder:
        def __init__(self, *args):
            self.inner = real_decompressobj(*args)

        def decompress(self, data, max_length):
            limits.append(max_length)
            return self.inner.decompress(data, max_length)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(mcp_http_ingress.zlib, "decompressobj", Decoder)
    source = Source([compressed])
    response, _ = await response_for(source, gate, encoding="gzip")
    with pytest.raises(IngressError, match="^TOOLS_LIST_PAGE_TOO_LARGE$"):
        await response.aread()
    assert limits == [101]
    assert source.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"bad", gzip.compress(payload())[:-5]])
async def test_invalid_or_truncated_compressed_body_fails_with_fixed_reason(body) -> None:
    gate = listing(200)
    source = Source([body])
    response, _ = await response_for(source, gate, encoding="gzip")
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_INVALID$"):
        await response.aread()
    assert source.closed == 1


@pytest.mark.asyncio
async def test_unsupported_encoding_failure_is_deferred_until_body_iteration() -> None:
    gate = listing(100)
    source = Source([b"secret remote error"])
    response, inner = await response_for(source, gate, encoding="br")
    assert gate.reason is None
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_INVALID$"):
        await response.aread()
    assert source.closed == inner.requests == 1
    assert source.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media",
    [
        "application/jsonBAD",
        "text/event-streamBAD",
        "x-text/event-stream",
        "application/x-text/event-stream",
        "text/event-stream; charset=latin1",
        "text/event-stream; charset=utf-8-sig",
    ],
)
async def test_ambiguous_sdk_media_or_non_utf8_sse_fails_before_iteration(media) -> None:
    gate = listing(100)
    source = Source([b"data: {}\n\n"])
    response, _ = await response_for(source, gate, media)
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_INVALID$"):
        await response.aread()
    assert source.reads == 0
    assert source.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["x-text/event-stream", "application/x-text/event-stream"])
async def test_get_sse_substring_media_is_rejected_before_eventsource_parses(media: str) -> None:
    source = Source([b"data: " + payload() + b"\n\n"])
    response, _ = await response_for(source, listing(1), media)
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_INVALID$"):
        _events = [event async for event in EventSource(response).aiter_sse()]
    assert source.reads == 0


@pytest.mark.asyncio
async def test_empty_data_priming_event_does_not_parse_or_debit() -> None:
    page = payload()
    gate = listing(len(page), len(page))
    source = Source([b"id: start\ndata:\n\ndata: " + page + b"\n\n"])
    response, _ = await response_for(source, gate, "text/event-stream")
    events = [event async for event in EventSource(response).aiter_sse()]
    assert events[0].data == ""
    assert events[1].data.encode() == page.rstrip(b"\r\n")
    # CR/LF in page is SSE framing; its two bytes were not part of payload.
    assert gate.candidate_limit == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment", [1, 2, 17, 4096])
async def test_raw_deflate_ambiguous_zlib_prefix_has_bounded_fallback(fragment: int) -> None:
    page = payload() + b" " * (156 - len(payload()))
    compressed = b"\x78\x9c\x00\x63\xff" + page + b"\x01\x00\x00\xff\xff"
    assert zlib.decompress(compressed, -zlib.MAX_WBITS) == page
    source = Source(
        [compressed[offset : offset + fragment] for offset in range(0, len(compressed), fragment)]
    )
    response, _ = await response_for(source, listing(len(page)), encoding="deflate")
    assert await response.aread() == page


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", [b"data: {}", b"data: {}\n", b": comment\n"])
async def test_active_sse_partial_event_eof_is_not_admitted(wire) -> None:
    gate = listing(100)
    source = Source([wire])
    response, _ = await response_for(source, gate, "text/event-stream")
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_EOF$"):
        await response.aread()
    assert source.closed == 1


@pytest.mark.asyncio
async def test_source_read_failure_marks_gate_and_closes_without_retry() -> None:
    gate = listing(100)
    source = Source([b"{"], httpx.ReadError("RAW_SECRET"))
    response, inner = await response_for(source, gate)
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_IO_FAILED$"):
        await response.aread()
    assert source.closed == inner.requests == 1
    assert gate.failed.is_set()


@pytest.mark.asyncio
async def test_cancellation_closes_source_without_retry() -> None:
    waiting = asyncio.Event()

    class WaitingSource(Source):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            waiting.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    source = WaitingSource([])
    response, inner = await response_for(source, listing(100))
    task = asyncio.create_task(response.aread())
    await asyncio.wait_for(waiting.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed == inner.requests == 1


@pytest.mark.asyncio
async def test_transport_close_delegates_once() -> None:
    inner = Inner(Source([]), "application/json")
    transport = BoundedHttpTransport(inner, IngressGate())
    await transport.aclose()
    assert inner.closed == 1


@pytest.mark.asyncio
async def test_source_close_timeout_is_bounded_and_marks_connection_failed(monkeypatch) -> None:
    class StalledClose(Source):
        async def aclose(self) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(mcp_http_ingress, "_CLOSE_TIMEOUT_S", 0.01)
    source = StalledClose([payload()])
    gate = listing(100)
    response, _ = await response_for(source, gate)
    with pytest.raises(IngressError, match="^TOOLS_LIST_INGRESS_CLOSE_FAILED$"):
        await asyncio.wait_for(response.aread(), 1)
    assert gate.failed.is_set()
