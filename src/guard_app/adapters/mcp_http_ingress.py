"""HTTP byte admission before MCP's JSON and SSE decoders (I6/I9, T14).

Limits apply during tools/list collection. SSE framing is always tracked because
a session GET may already be waiting when collection starts. Content decoding is
owned here for gzip/deflate, so compressed input cannot expand inside HTTPX before
the listing gate sees it. Ordinary tool results have no listing size limit, but
A221's per-message cap (MAX_MESSAGE_BYTES) applies to every JSON body, SSE event
and other response body, decoded, before the SDK reads it.
"""

from __future__ import annotations

import codecs
import zlib
from collections.abc import AsyncIterator, Iterator
from email.message import Message

import anyio
import httpx

from guard_app.adapters.mcp_ingress import IngressGate

_CHUNK_BYTES = 16384
_CLOSE_TIMEOUT_S = 1.0
_DEFLATE_REPLAY_BYTES = 65536


class BoundedHttpTransport(httpx.AsyncBaseTransport):
    """Wrap a trusted HTTP transport without adding requests or retries."""

    def __init__(self, inner: httpx.AsyncBaseTransport, gate: IngressGate) -> None:
        self._inner = inner
        self._gate = gate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if not isinstance(response.stream, httpx.AsyncByteStream):
            raise TypeError("asynchronous HTTP response stream required")
        encoding = (
            response.headers.get("content-encoding", "identity").strip().lower() or "identity"
        )
        headers = response.headers.copy()
        if encoding in {"identity", "gzip", "deflate"}:
            # The returned stream is already decoded. In particular HTTPX must
            # not invoke its unbounded gzip decoder on these bytes a second time.
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
        return httpx.Response(
            response.status_code,
            headers=headers,
            stream=_BoundedHttpStream(
                response, self._gate, encoding, close_on_response=request.method == "POST"
            ),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class _ContentDecoder:
    """Emit bounded expansion blocks; never call zlib's unbounded flush."""

    def __init__(self, encoding: str, gate: IngressGate) -> None:
        self._encoding = encoding
        self._gate = gate
        self._decoder = zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
        self._prefix = bytearray()
        self._started = encoding != "deflate"
        self._raw_fallback: bytearray | None = None

    def decode(self, data: bytes) -> Iterator[bytes]:
        if not self._started:
            needed = 2 - len(self._prefix)
            self._prefix.extend(data[:needed])
            data = data[needed:]
            if len(self._prefix) < 2:
                return
            header = int.from_bytes(self._prefix, "big")
            wrapped = self._prefix[0] & 15 == 8 and header % 31 == 0
            self._decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
            if wrapped:
                # A valid raw-deflate stored block can share a zlib header.
                # Retain only a bounded prefix until the first output so a
                # zlib rejection can fall back without reparsing output.
                self._raw_fallback = bytearray()
            self._started = True
            data = bytes(self._prefix) + data
            self._prefix.clear()
        if self._raw_fallback is not None:
            if len(self._raw_fallback) + len(data) <= _DEFLATE_REPLAY_BYTES:
                self._raw_fallback.extend(data)
            else:
                self._raw_fallback = None
        while data:
            limit = _CHUNK_BYTES
            if self._gate.active:
                limit = min(limit, self._gate.candidate_limit + 1)
            try:
                block = self._decoder.decompress(data, limit)
            except zlib.error:
                if self._raw_fallback is not None:
                    data = bytes(self._raw_fallback)
                    self._raw_fallback = None
                    self._decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                    continue
                if self._gate.active:
                    self._gate.fail("TOOLS_LIST_INGRESS_INVALID")
                raise httpx.DecodingError("Invalid compressed HTTP response") from None
            if block:
                self._raw_fallback = None
                yield block
            if self._decoder.unused_data:
                # HTTPX ignores trailing members, but while listing they cannot
                # silently disappear from the admitted response representation.
                if self._gate.active:
                    self._gate.fail("TOOLS_LIST_INGRESS_INVALID")
                return
            data = self._decoder.unconsumed_tail

    def finish(self) -> None:
        if self._gate.active and not self._decoder.eof:
            self._gate.fail("TOOLS_LIST_INGRESS_INVALID")


class _SseFramer:
    """Frame SSE as bytes, without UTF-8 decoding or JSON normalization."""

    def __init__(self, gate: IngressGate) -> None:
        self._gate = gate
        self._line = bytearray()
        self._wire = bytearray()
        self._data: list[bytes] = []
        self._data_size = 0
        self._metadata_size = 0
        self._event = b""
        self._pending_cr = False
        self.accepted_response = False

    @staticmethod
    def _value(line: bytes | bytearray) -> bytes | bytearray | None:
        if line == b"data":
            return b""
        if line.startswith(b"data:"):
            value = line[5:]
            return value[1:] if value.startswith(b" ") else value
        return None

    def _check(self) -> None:
        # A gate activated between chunks also checks bytes collected before it.
        value = self._value(self._line)
        if value is not None:
            size = self._data_size + bool(self._data) + len(value)
            self._gate.check_size(size)
            metadata = self._metadata_size
        else:
            self._gate.check_size(self._data_size)
            # A fragmented field name may still become "data:"; framing
            # itself is excluded even for a one-byte candidate budget.
            partial_data_field = b"data:".startswith(self._line)
            metadata = self._metadata_size + (0 if partial_data_field else len(self._line))
        self._gate.check_message(metadata)
        if self._gate.active and metadata > self._gate.candidate_limit:
            self._gate.fail("TOOLS_LIST_FRAMING_TOO_LARGE")

    def _append_line(self, part: bytes) -> None:
        size = len(self._line) + len(part)
        prefix = bytes(self._line[:6])
        if len(prefix) < 6:
            prefix += part[: 6 - len(prefix)]
        if prefix.startswith(b"data:"):
            value_size = size - 5 - int(prefix[5:6] == b" ")
            self._gate.check_size(self._data_size + bool(self._data) + value_size)
        elif not b"data:".startswith(prefix):
            self._gate.check_message(self._metadata_size + size)
            if self._gate.active and self._metadata_size + size > self._gate.candidate_limit:
                self._gate.fail("TOOLS_LIST_FRAMING_TOO_LARGE")
        self._line.extend(part)

    def _finish_line(self, ending: bytes) -> bytes | None:
        self._check()
        line = bytes(self._line)
        self._line.clear()
        self._wire.extend(line)
        self._wire.extend(ending)
        if line:
            value = self._value(line)
            if value is not None:
                self._data_size += len(value) + bool(self._data)
                self._data.append(bytes(value))
            else:
                self._metadata_size += len(line) + len(ending)
                field, _, value = line.partition(b":")
                if field == b"event":
                    self._event = value[1:] if value.startswith(b" ") else value
            self._check()
            return None
        self.accepted_response = False
        if self._data and self._event in {b"", b"message"}:
            payload = b"\n".join(self._data)
            # The SDK treats an empty data payload as an SSE priming event.
            if payload:
                self.accepted_response = self._gate.accept(payload)
        wire = bytes(self._wire)
        self._wire.clear()
        self._data.clear()
        self._data_size = self._metadata_size = 0
        self._event = b""
        return wire

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        offset = 0
        self._check()
        if self._pending_cr:
            self._pending_cr = False
            crlf = chunk.startswith(b"\n")
            wire = self._finish_line(b"\r\n" if crlf else b"\r")
            if wire is not None:
                yield wire
            offset = int(crlf)
        while offset < len(chunk):
            cr, lf = chunk.find(b"\r", offset), chunk.find(b"\n", offset)
            indexes = [index for index in (cr, lf) if index >= 0]
            end = min(indexes) if indexes else len(chunk)
            self._append_line(chunk[offset:end])
            self._check()
            if end == len(chunk):
                return
            if chunk[end] == 13 and end + 1 == len(chunk):
                self._pending_cr = True
                return
            crlf = chunk[end : end + 2] == b"\r\n"
            ending = chunk[end : end + 2] if crlf else chunk[end : end + 1]
            wire = self._finish_line(ending)
            if wire is not None:
                yield wire
            offset = end + len(ending)

    def finish(self) -> Iterator[bytes]:
        if self._pending_cr:
            self._pending_cr = False
            wire = self._finish_line(b"\r")
            if wire is not None:
                yield wire
        self._check()
        if self._line or self._wire:
            if self._gate.active:
                self._gate.fail("TOOLS_LIST_INGRESS_EOF")
            # Preserve SDK handling of incomplete events outside collection.
            yield bytes(self._wire + self._line)


class _BoundedHttpStream(httpx.AsyncByteStream):
    def __init__(
        self,
        source: httpx.Response,
        gate: IngressGate,
        encoding: str,
        *,
        close_on_response: bool,
    ) -> None:
        self._source = source
        self._gate = gate
        self._encoding = encoding
        self._closed = False
        self._close_on_response = close_on_response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        content_type = self._source.headers.get("content-type", "")
        media = content_type.split(";", 1)[0].strip().lower()
        invalid_media = (media.startswith("application/json") and media != "application/json") or (
            "text/event-stream" in media and media != "text/event-stream"
        )
        message = Message()
        message["content-type"] = content_type
        charset = message.get_content_charset() or "utf-8"
        try:
            utf8 = codecs.lookup(charset).name == "utf-8"
        except LookupError:
            utf8 = False
        sse = _SseFramer(self._gate) if media == "text/event-stream" else None
        json_body = bytearray() if media == "application/json" else None
        decoder = (
            _ContentDecoder(self._encoding, self._gate)
            if self._encoding in {"gzip", "deflate"}
            else None
        )
        try:

            def check_representation() -> None:
                if self._gate.active and (
                    self._encoding not in {"identity", "gzip", "deflate"}
                    or invalid_media
                    or (sse is not None and not utf8)
                ):
                    self._gate.fail("TOOLS_LIST_INGRESS_INVALID")

            check_representation()
            source_stream = self._source.stream
            if not isinstance(source_stream, httpx.AsyncByteStream):
                raise TypeError("asynchronous HTTP response stream required")
            # A221: bodies passed through without JSON or SSE framing (other
            # media, other encodings) count as one message for the cap.
            passed = 0
            async for raw in source_stream:
                check_representation()
                if self._encoding not in {"identity", "gzip", "deflate"}:
                    if self._gate.active:
                        self._gate.fail("TOOLS_LIST_INGRESS_INVALID")
                    passed += len(raw)
                    self._gate.check_message(passed)
                    yield raw
                    continue
                blocks = decoder.decode(raw) if decoder is not None else _chunks(raw)
                for block in blocks:
                    check_representation()
                    if sse is not None:
                        for event in sse.feed(block):
                            check_representation()
                            if self._close_on_response and sse.accepted_response:
                                # A terminal POST response must not become a
                                # successful listing before its source closes.
                                # Session GET remains a long-lived event stream.
                                await self.aclose()
                                yield event
                                return
                            yield event
                    elif json_body is not None:
                        self._gate.check_size(len(json_body) + len(block))
                        json_body.extend(block)
                    else:
                        passed += len(block)
                        self._gate.check_message(passed)
                        yield block
            if decoder is not None:
                decoder.finish()
            if sse is not None:
                for event in sse.finish():
                    if self._close_on_response and sse.accepted_response:
                        await self.aclose()
                    yield event
            elif json_body is not None:
                self._gate.accept(bytes(json_body))
                await self.aclose()
                if json_body:
                    yield bytes(json_body)
        except (httpx.RequestError, OSError, EOFError):
            if self._gate.active:
                self._gate.fail("TOOLS_LIST_INGRESS_IO_FAILED")
            raise
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                with anyio.move_on_after(_CLOSE_TIMEOUT_S, shield=True) as scope:
                    await self._source.aclose()
            except (httpx.RequestError, OSError, EOFError, RuntimeError):
                self._gate.fail("TOOLS_LIST_INGRESS_CLOSE_FAILED")
            if scope.cancel_called:
                self._gate.fail("TOOLS_LIST_INGRESS_CLOSE_FAILED")


def _chunks(data: bytes) -> Iterator[bytes]:
    for offset in range(0, len(data), _CHUNK_BYTES):
        yield data[offset : offset + _CHUNK_BYTES]
