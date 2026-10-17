"""JSON-RPC 클라이언트 뼈대: 짝 맞추기·알림·서버 요청·시간·크기 상한, fail-closed, 원문 비노출."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from guard_app.adapters.jsonrpc_client import (
    JsonCodec,
    JsonRpcClient,
    JsonRpcError,
    JsonRpcRemoteError,
)

MARKER = "TEST_SECRET_MARKER"


class FakeChannel:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.sent_event = asyncio.Event()

    async def receive(self) -> bytes | None:
        return await self.inbound.get()

    async def send(self, frame: bytes) -> None:
        self.sent.append(json.loads(frame))
        self.sent_event.set()

    def push(self, message: object) -> None:
        self.inbound.put_nowait(json.dumps(message).encode())

    async def next_sent(self) -> dict[str, object]:
        while not self.sent:
            self.sent_event.clear()
            await asyncio.wait_for(self.sent_event.wait(), timeout=1)
        return self.sent.pop(0)


def _client(channel: FakeChannel, **kwargs: object) -> JsonRpcClient:
    options: dict[str, object] = {
        "request_timeout_s": 1.0,
        "max_frame_bytes": 4096,
        "max_pending": 4,
    }
    options.update(kwargs)
    client = JsonRpcClient(channel, **options)  # type: ignore[arg-type]
    client.start()
    return client


@pytest.mark.asyncio
async def test_concurrent_requests_match_responses_by_id_out_of_order() -> None:
    channel = FakeChannel()
    client = _client(channel)
    first = asyncio.create_task(client.request("one", {"a": 1}))
    second = asyncio.create_task(client.request("two"))
    sent_first = await channel.next_sent()
    sent_second = await channel.next_sent()
    assert sent_first == {"jsonrpc": "2.0", "id": 1, "method": "one", "params": {"a": 1}}
    assert sent_second == {"jsonrpc": "2.0", "id": 2, "method": "two"}
    channel.push({"jsonrpc": "2.0", "id": 2, "result": "second"})
    channel.push({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert await first == {"ok": True}
    assert await second == "second"
    await client.close()


@pytest.mark.asyncio
async def test_remote_error_keeps_only_the_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    channel = FakeChannel()
    client = _client(channel)
    waiting = asyncio.create_task(client.request("x"))
    await channel.next_sent()
    channel.push({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": MARKER}})
    with pytest.raises(JsonRpcRemoteError) as failed:
        await waiting
    assert failed.value.code == -32000
    assert MARKER not in str(failed.value)
    assert MARKER not in caplog.text
    assert client.closed_reason is None
    await client.close()


@pytest.mark.asyncio
async def test_timeout_drops_the_request_and_ignores_a_late_response() -> None:
    channel = FakeChannel()
    client = _client(channel, request_timeout_s=0.05)
    with pytest.raises(JsonRpcError) as failed:
        await client.request("slow")
    assert failed.value.reason == "REQUEST_TIMEOUT"
    channel.push({"jsonrpc": "2.0", "id": 1, "result": "late"})
    nxt = asyncio.create_task(client.request("next"))
    sent = [await channel.next_sent(), await channel.next_sent()]
    assert [message["id"] for message in sent] == [1, 2]
    channel.push({"jsonrpc": "2.0", "id": 2, "result": "fresh"})
    assert await nxt == "fresh"
    assert client.closed_reason is None
    await client.close()


@pytest.mark.asyncio
async def test_notifications_arrive_in_order_without_a_reply() -> None:
    channel = FakeChannel()
    seen: list[tuple[str, object]] = []
    client = _client(channel, on_notification=lambda method, params: seen.append((method, params)))
    channel.push({"jsonrpc": "2.0", "method": "a", "params": {"n": 1}})
    channel.push({"jsonrpc": "2.0", "method": "b"})
    waiting = asyncio.create_task(client.request("ping"))
    await channel.next_sent()
    channel.push({"jsonrpc": "2.0", "id": 1, "result": None})
    assert await waiting is None
    assert seen == [("a", {"n": 1}), ("b", None)]
    assert channel.sent == []
    await client.close()


@pytest.mark.asyncio
async def test_server_request_is_answered_while_reading_continues() -> None:
    channel = FakeChannel()
    release = asyncio.Event()
    notes: list[str] = []

    async def handler(method: str, params: object) -> object:
        await release.wait()
        return {"echo": method, "params": params}

    client = _client(
        channel,
        on_server_request=handler,
        on_notification=lambda method, _params: notes.append(method),
    )
    channel.push({"jsonrpc": "2.0", "id": "srv-1", "method": "item/x", "params": [1]})
    channel.push({"jsonrpc": "2.0", "method": "still-reading"})
    for _ in range(20):
        if notes:
            break
        await asyncio.sleep(0.01)
    assert notes == ["still-reading"]
    assert channel.sent == []
    release.set()
    reply = await channel.next_sent()
    assert reply == {
        "jsonrpc": "2.0",
        "id": "srv-1",
        "result": {"echo": "item/x", "params": [1]},
    }
    await client.close()


@pytest.mark.asyncio
async def test_server_request_without_handler_gets_method_not_found() -> None:
    channel = FakeChannel()
    client = _client(channel)
    channel.push({"jsonrpc": "2.0", "id": 0, "method": "unexpected"})
    reply = await channel.next_sent()
    assert reply["id"] == 0
    assert reply["error"] == {"code": -32601, "message": "Not found"}
    await client.close()


@pytest.mark.asyncio
async def test_failing_handler_closes_the_client_and_fails_pending() -> None:
    channel = FakeChannel()

    async def handler(_method: str, _params: object) -> object:
        raise RuntimeError(MARKER)

    client = _client(channel, on_server_request=handler)
    waiting = asyncio.create_task(client.request("pending"))
    await channel.next_sent()
    channel.push({"jsonrpc": "2.0", "id": 9, "method": "boom"})
    with pytest.raises(JsonRpcError) as failed:
        await waiting
    assert failed.value.reason == "HANDLER_FAILED"
    assert MARKER not in str(failed.value)
    with pytest.raises(JsonRpcError):
        await client.request("after")
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        b"not json",
        b"[]",
        b'{"jsonrpc":"2.0","id":1,"result":1,"result":2}',
        b'{"jsonrpc":"2.0","id":1,"result":NaN}',
        b'{"jsonrpc":"1.0","id":1,"result":1}',
        b'{"jsonrpc":"2.0","id":1,"result":1,"error":{"code":1}}',
        b'{"jsonrpc":"2.0","id":"1","result":1}',
        b'{"jsonrpc":"2.0","id":1,"error":{"message":"x"}}',
        b'{"jsonrpc":"2.0","id":true,"method":"m"}',
        b'{"jsonrpc":"2.0","method":""}',
        b"\xff\xfe",
    ],
)
async def test_invalid_frames_close_the_client(frame: bytes) -> None:
    channel = FakeChannel()
    client = _client(channel)
    waiting = asyncio.create_task(client.request("pending"))
    await channel.next_sent()
    channel.inbound.put_nowait(frame)
    with pytest.raises(JsonRpcError) as failed:
        await waiting
    assert failed.value.reason == "PROTOCOL_ERROR"
    await client.close()


@pytest.mark.asyncio
async def test_frame_size_is_bounded_both_ways_and_eof_fails_pending() -> None:
    channel = FakeChannel()
    client = _client(channel, max_frame_bytes=64)
    with pytest.raises(JsonRpcError) as outgoing:
        await client.request("big", {"text": "x" * 100})
    assert outgoing.value.reason == "FRAME_TOO_LARGE"
    assert channel.sent == []
    waiting = asyncio.create_task(client.request("small"))
    await channel.next_sent()
    channel.inbound.put_nowait(b"{" + b" " * 80 + b"}")
    with pytest.raises(JsonRpcError) as incoming:
        await waiting
    assert incoming.value.reason == "FRAME_TOO_LARGE"
    await client.close()

    eof_channel = FakeChannel()
    eof_client = _client(eof_channel)
    waiting = asyncio.create_task(eof_client.request("x"))
    await eof_channel.next_sent()
    eof_channel.inbound.put_nowait(None)
    with pytest.raises(JsonRpcError) as eof:
        await waiting
    assert eof.value.reason == "EOF"
    await eof_client.close()


@pytest.mark.asyncio
async def test_pending_limit_and_lifecycle_rules() -> None:
    channel = FakeChannel()
    client = JsonRpcClient(channel, request_timeout_s=1.0, max_frame_bytes=4096, max_pending=1)
    with pytest.raises(JsonRpcError) as not_started:
        await client.request("x")
    assert not_started.value.reason == "NOT_STARTED"
    client.start()
    with pytest.raises(RuntimeError):
        client.start()
    first = asyncio.create_task(client.request("one"))
    await channel.next_sent()
    with pytest.raises(JsonRpcError) as full:
        await client.request("two")
    assert full.value.reason == "TOO_MANY_PENDING"
    await client.close()
    with pytest.raises(JsonRpcError) as closed:
        await first
    assert closed.value.reason == "CLOSED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"request_timeout_s": 0, "max_frame_bytes": 1, "max_pending": 1},
        {"request_timeout_s": float("nan"), "max_frame_bytes": 1, "max_pending": 1},
        {"request_timeout_s": 1, "max_frame_bytes": 0, "max_pending": 1},
        {"request_timeout_s": 1, "max_frame_bytes": 1, "max_pending": True},
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="required"):
        JsonRpcClient(FakeChannel(), **kwargs)  # type: ignore[arg-type]


def test_codec_round_trip_keeps_unicode_and_rejects_nan() -> None:
    codec = JsonCodec()
    frame = codec.encode({"jsonrpc": "2.0", "method": "가", "params": {"v": 0.5}})
    assert codec.decode(frame) == {"jsonrpc": "2.0", "method": "가", "params": {"v": 0.5}}
    with pytest.raises(ValueError, match="JSON compliant"):
        codec.encode({"v": float("nan")})
