"""Synthetic byte framing checks (10.4, I6/I8/I9); no C launcher/T22 acceptance."""

from __future__ import annotations

import asyncio
import json

import pytest

from guard_app.adapters.codex_stream import CodexStreamChannel, CodexStreamError
from guard_app.adapters.jsonrpc_client import FrameChannel, JsonRpcClient, JsonRpcError

from .test_jsonrpc_client import check_case

MARKER = "PRIVATE_SOURCE_PAYLOAD"


class BytePipe:
    """Injected in-memory byte source, with read sizes and write/drain counters."""

    def __init__(self) -> None:
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self.remaining = b""
        self.read_limits: list[int] = []
        self.writes: asyncio.Queue[bytes] = asyncio.Queue()
        self.write_count = 0
        self.drain_count = 0

    async def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        if not self.remaining:
            self.remaining = await self.chunks.get()
        result, self.remaining = self.remaining[:limit], self.remaining[limit:]
        return result

    async def write(self, data: bytes) -> None:
        self.write_count += 1
        self.writes.put_nowait(data)

    async def drain(self) -> None:
        self.drain_count += 1

    def push(self, data: bytes) -> None:
        self.chunks.put_nowait(data)

    async def next_write(self) -> bytes:
        return await asyncio.wait_for(self.writes.get(), timeout=1)


def _channel(pipe: BytePipe, *, limit: int = 512, chunk: int = 8) -> CodexStreamChannel:
    return CodexStreamChannel(
        pipe.read, pipe.write, pipe.drain, max_frame_bytes=limit, read_chunk_bytes=chunk
    )


@pytest.mark.asyncio
async def test_split_utf8_and_multiple_frames_keep_bytes_and_order() -> None:
    pipe, channel = stream_pair(chunk=1)
    first = '{"method":"가"}'.encode()
    second = b'{"id":1,"result":null}'
    pipe.push(first + b"\n" + second + b"\r\n")
    pipe.push(b"")
    assert await channel.receive() == first
    assert await channel.receive() == second + b"\r"
    assert await channel.receive() is None
    assert await channel.receive() is None
    assert channel.closed_reason == "EOF"
    assert max(pipe.read_limits) == 1
    with pytest.raises(CodexStreamError, match="EOF"):
        await channel.send(b"{}")


@pytest.mark.asyncio
async def test_exact_frame_limit_and_preparse_buffer_bound() -> None:
    pipe, channel = stream_pair(limit=16, chunk=1000)
    pipe.push(b"x" * 16 + b"\n" + b"y" * 17 + b"\n")
    assert await channel.receive() == b"x" * 16
    with pytest.raises(CodexStreamError, match="FRAME_TOO_LARGE"):
        await channel.receive()
    assert max(pipe.read_limits) <= 17
    assert not channel._buffer
    assert len(pipe.read_limits) == 2  # No unbounded readline or further source read.


@pytest.mark.asyncio
async def test_empty_lines_and_partial_eof_fail_closed() -> None:
    for data in [b"\n", b"\r\n", b" \t\n", b"unfinished"]:
        pipe, channel = stream_pair()
        pipe.push(data)
        pipe.push(b"")
        reason = "EMPTY_FRAME" if data.endswith(b"\n") else "PARTIAL_EOF"
        with pytest.raises(CodexStreamError, match=reason):
            await channel.receive()
        with pytest.raises(CodexStreamError, match=reason):
            await channel.receive()
        with pytest.raises(CodexStreamError, match=reason):
            await channel.send(b"{}")
        assert not channel._buffer


@pytest.mark.asyncio
async def test_source_read_contract_is_checked_before_buffering() -> None:
    pipe = BytePipe()

    async def oversized_read(limit: int) -> bytes:
        return b"x" * (limit + 1)

    channel = CodexStreamChannel(
        oversized_read, pipe.write, pipe.drain, max_frame_bytes=16, read_chunk_bytes=8
    )
    with pytest.raises(CodexStreamError, match="READ_CONTRACT"):
        await channel.receive()
    assert not channel._buffer


@pytest.mark.asyncio
async def test_writes_append_one_newline_and_hold_serialization_through_drain() -> None:
    pipe = BytePipe()
    drain_entered, release = asyncio.Event(), asyncio.Event()

    async def drain() -> None:
        pipe.drain_count += 1
        drain_entered.set()
        await release.wait()

    channel = CodexStreamChannel(pipe.read, pipe.write, drain, max_frame_bytes=512)
    first = asyncio.create_task(channel.send(b'{"method":"first"}'))
    assert await pipe.next_write() == b'{"method":"first"}\n'
    await asyncio.wait_for(drain_entered.wait(), timeout=1)
    second = asyncio.create_task(channel.send(b'{"method":"second"}'))
    await asyncio.sleep(0)
    assert pipe.write_count == 1
    assert not first.done()
    assert not second.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert await pipe.next_write() == b'{"method":"second"}\n'
    assert pipe.drain_count == 2
    channel.close()


@pytest.mark.asyncio
async def test_io_failure_hides_raw_error_and_never_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for failure_stage in ["read", "write", "drain"]:
        pipe = BytePipe()

        async def read(limit: int, *, failure_stage=failure_stage, pipe=pipe) -> bytes:
            if failure_stage == "read":
                raise OSError(MARKER)
            return await pipe.read(limit)

        async def write(frame: bytes, *, failure_stage=failure_stage, pipe=pipe) -> None:
            await pipe.write(frame)
            if failure_stage == "write":
                raise RuntimeError(MARKER)

        async def drain(*, failure_stage=failure_stage, pipe=pipe) -> None:
            await pipe.drain()
            if failure_stage == "drain":
                raise ValueError(MARKER)

        channel = CodexStreamChannel(read, write, drain, max_frame_bytes=512)
        action = channel.receive() if failure_stage == "read" else channel.send(b"{}")
        with pytest.raises(CodexStreamError, match="IO_FAILED") as failed:
            await action
        with pytest.raises(CodexStreamError, match="IO_FAILED"):
            await channel.send(b"{}")
        assert pipe.write_count == (failure_stage != "read")
        assert MARKER not in str(failed.value)
        assert failed.value.__context__ is None
        assert MARKER not in caplog.text


@pytest.mark.asyncio
async def test_invalid_outgoing_frame_is_rejected_before_writing() -> None:
    for frame in [b"", b" \t", b"{}\n{}", b"x" * 17]:
        pipe, channel = stream_pair(limit=16)
        with pytest.raises(CodexStreamError):
            await channel.send(frame)
        assert pipe.write_count == 0
        assert pipe.drain_count == 0
        assert channel.closed_reason is not None


@pytest.mark.asyncio
async def test_cancel_and_local_close_release_waiters_without_source_close() -> None:
    for operation in ["receive", "send"]:
        pipe = BytePipe()
        entered = asyncio.Event()

        async def read(_limit: int, *, entered=entered) -> bytes:
            entered.set()
            await asyncio.Event().wait()
            return b""

        async def write(_frame: bytes, *, entered=entered) -> None:
            entered.set()
            await asyncio.Event().wait()

        channel = CodexStreamChannel(read, write, pipe.drain, max_frame_bytes=512)
        waiting = asyncio.create_task(
            channel.receive() if operation == "receive" else channel.send(b"{}")
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, timeout=1)
        assert channel.closed_reason == "CANCELLED"
        with pytest.raises(CodexStreamError, match="CANCELLED"):
            await channel.send(b"{}")

        second = _channel(pipe)
        blocked = asyncio.create_task(second.receive())
        await asyncio.sleep(0)
        second.close()
        with pytest.raises(CodexStreamError, match="CLOSED"):
            await asyncio.wait_for(blocked, timeout=1)


@pytest.mark.asyncio
async def test_jsonrpc_round_trip_over_synthetic_codex_bytes() -> None:
    pipe, channel = stream_pair(chunk=3)
    frames: FrameChannel = channel  # Structural compatibility, no client API change.
    notes: list[tuple[str, object]] = []
    calls: list[tuple[str, object]] = []

    async def handler(method: str, params: object) -> object:
        calls.append((method, params))
        return {"success": True, "contentItems": [{"type": "inputText", "text": "가"}]}

    client = JsonRpcClient(
        frames,
        request_timeout_s=1,
        max_frame_bytes=512,
        max_pending=4,
        version_field=False,
        on_notification=lambda method, params: notes.append((method, params)),
        on_server_request=handler,
    )
    client.start()
    pending = asyncio.create_task(client.request("initialize", {"capabilities": {}}))
    assert json.loads(await pipe.next_write()) == {
        "id": 1,
        "method": "initialize",
        "params": {"capabilities": {}},
    }
    params = {
        "callId": "call-1",
        "threadId": "thread-1",
        "turnId": "turn-1",
        "namespace": "docs",
        "tool": "read",
        "arguments": {"name": "가"},
    }
    messages = [
        {"method": "thread/started", "params": {"threadId": "thread-1"}},
        {"id": "server-1", "method": "item/tool/call", "params": params},
        {"id": 1, "result": {"ok": True}},
    ]
    pipe.push(
        b"".join(json.dumps(message, ensure_ascii=False).encode() + b"\n" for message in messages)
    )
    assert await pending == {"ok": True}
    assert json.loads(await pipe.next_write()) == {
        "id": "server-1",
        "result": {"success": True, "contentItems": [{"type": "inputText", "text": "가"}]},
    }
    assert notes == [("thread/started", {"threadId": "thread-1"})]
    assert calls == [("item/tool/call", params)]
    await client.notify("initialized")
    assert await pipe.next_write() == b'{"method":"initialized"}\n'
    await client.close()
    channel.close()


@pytest.mark.asyncio
async def test_malformed_json_is_fail_closed_by_existing_client_codec() -> None:
    for line in [b"not-json\n", b"\xff\n", b"[]\n", b"{\n"]:
        pipe, channel = stream_pair()
        client = JsonRpcClient(
            channel, request_timeout_s=1, max_frame_bytes=512, max_pending=1, version_field=False
        )
        client.start()
        pending = asyncio.create_task(client.request("pending"))
        await pipe.next_write()
        pipe.push(line)
        with pytest.raises(JsonRpcError, match="PROTOCOL_ERROR"):
            await pending
        await client.close()
        channel.close()


def test_invalid_limits_are_rejected() -> None:
    failures = []
    for limit, chunk in [(0, 1), (True, 1), (16, 0), (16, -1)]:
        with (
            check_case(failures, (limit, chunk)),
            pytest.raises(ValueError, match="positive integer"),
        ):
            _channel(BytePipe(), limit=limit, chunk=chunk)
    assert not failures, failures


@pytest.mark.asyncio
async def test_crlf_retains_cr_and_counts_it_toward_frame_limit() -> None:
    for limit, accepted in [(16, False), (17, True)]:
        pipe, channel = stream_pair(limit=limit)
        pipe.push(b"x" * 16 + b"\r\n")
        if accepted:
            assert await channel.receive() == b"x" * 16 + b"\r"
        else:
            with pytest.raises(CodexStreamError, match="FRAME_TOO_LARGE"):
                await channel.receive()
        channel.close()


@pytest.mark.asyncio
async def test_close_wins_when_injected_io_completes_in_the_same_turn() -> None:
    for stage in ["read", "write", "drain"]:
        pipe = BytePipe()
        holder: list[CodexStreamChannel] = []

        async def read(_limit: int, *, holder=holder) -> bytes:
            holder[0].close()
            return b"{}\n"

        async def write(frame: bytes, *, holder=holder, pipe=pipe, stage=stage) -> None:
            await pipe.write(frame)
            if stage == "write":
                holder[0].close()

        async def drain(*, holder=holder, pipe=pipe) -> None:
            await pipe.drain()
            holder[0].close()

        channel = CodexStreamChannel(read, write, drain, max_frame_bytes=512)
        holder.append(channel)
        action = channel.receive() if stage == "read" else channel.send(b"{}")
        with pytest.raises(CodexStreamError, match="CLOSED"):
            await asyncio.wait_for(action, timeout=1)
        assert pipe.write_count == (stage != "read")
        assert pipe.drain_count == (stage == "drain")
        assert not channel._buffer


@pytest.mark.asyncio
async def test_cancel_ignoring_source_late_error_is_retrieved_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipe = BytePipe()
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    unhandled: list[dict[str, object]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    async def read(_limit: int) -> bytes:
        entered.set()
        try:
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            raise OSError(MARKER)
        finally:
            finished.set()

    channel = CodexStreamChannel(read, pipe.write, pipe.drain, max_frame_bytes=512)
    waiting = asyncio.create_task(channel.receive())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        channel.close()
        with pytest.raises(CodexStreamError, match="CLOSED"):
            await asyncio.wait_for(waiting, timeout=1)
        assert channel._operations  # Still owns an uncooperative source, without awaiting it.
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)  # Let the source's done callback consume its exception.
        assert not channel._operations
        assert not unhandled
        assert MARKER not in caplog.text
    finally:
        release.set()
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_jsonrpc_pending_request_fails_on_clean_or_partial_eof() -> None:
    for data, reason in [(b"", "EOF"), (b"partial", "PARTIAL_EOF")]:
        pipe, channel = stream_pair()
        client = JsonRpcClient(
            channel, request_timeout_s=1, max_frame_bytes=512, max_pending=1, version_field=False
        )
        client.start()
        pending = asyncio.create_task(client.request("pending"))
        await pipe.next_write()
        pipe.push(data)
        pipe.push(b"")
        with pytest.raises(JsonRpcError, match="EOF" if not data else "CHANNEL_FAILED"):
            await pending
        assert channel.closed_reason == reason
        assert pipe.write_count == 1
        await client.close()


@pytest.mark.asyncio
async def test_jsonrpc_deadline_cancels_stuck_drain_without_retrying_write() -> None:
    pipe = BytePipe()
    draining = asyncio.Event()

    async def drain() -> None:
        draining.set()
        await asyncio.Event().wait()

    channel = CodexStreamChannel(pipe.read, pipe.write, drain, max_frame_bytes=512)
    # 0.3s so a busy loop still reaches drain before the deadline (#109 A review 5).
    client = JsonRpcClient(
        channel, request_timeout_s=0.3, max_frame_bytes=512, max_pending=1, version_field=False
    )
    client.start()
    pending = asyncio.create_task(client.request("pending"))
    await asyncio.wait_for(draining.wait(), timeout=1)
    with pytest.raises(JsonRpcError, match="SEND_TIMEOUT"):
        await asyncio.wait_for(pending, timeout=1)
    await client.close()
    assert channel.closed_reason == "CANCELLED"
    assert pipe.write_count == 1
    with pytest.raises(JsonRpcError, match="SEND_TIMEOUT"):
        await client.request("retry")
    assert pipe.write_count == 1


def stream_pair(**options):
    pipe = BytePipe()
    return pipe, _channel(pipe, **options)
