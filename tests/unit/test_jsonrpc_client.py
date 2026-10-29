"""JSON-RPC 클라이언트 뼈대: 짝 맞추기·알림·서버 요청·시간·크기 상한, fail-closed, 원문 비노출."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager

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
async def test_duplicate_inflight_server_request_closes_without_a_second_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for request_id in [7, "7"]:
        for version_field in [True, False]:
            # I6/I9: fail closed before a second dispatch, without logging peer data.
            channel = FakeChannel()
            entered = asyncio.Event()
            cancelled = asyncio.Event()
            calls = 0

            async def handler(
                _method: str, _params: object, *, cancelled=cancelled, entered=entered
            ) -> object:
                nonlocal calls
                calls += 1
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            client = _client(channel, version_field=version_field, on_server_request=handler)
            waiting = asyncio.create_task(client.request("pending"))
            await channel.next_sent()
            request: dict[str, object] = {"id": request_id, "method": "run", "params": MARKER}
            if version_field:
                request["jsonrpc"] = "2.0"
            channel.push(request)
            await asyncio.wait_for(entered.wait(), timeout=1)
            # A different method still cannot reuse the outstanding request ID.
            channel.push({**request, "method": "different"})
            with pytest.raises(JsonRpcError) as failed:
                await waiting
            assert failed.value.reason == "PROTOCOL_ERROR"
            await client.close()
            assert calls == 1
            assert cancelled.is_set()
            assert channel.sent == []
            assert not client._server_tasks
            assert not client._server_request_ids
            assert MARKER not in str(failed.value)
            assert MARKER not in caplog.text


@pytest.mark.asyncio
async def test_duplicate_is_reserved_before_the_handler_task_starts() -> None:
    channel = FakeChannel()
    calls = 0

    async def handler(_method: str, _params: object) -> object:
        nonlocal calls
        calls += 1
        return None

    client = _client(channel, on_server_request=handler)
    # Both queued frames are read without yielding to the newly created task.
    for _ in range(2):
        channel.push({"jsonrpc": "2.0", "id": "same", "method": "run"})
    await asyncio.wait_for(client._closed.wait(), timeout=1)
    await client.close()
    assert client.closed_reason == "PROTOCOL_ERROR"
    assert calls == 0
    assert not client._server_tasks
    assert not client._server_request_ids


@pytest.mark.asyncio
async def test_server_ids_preserve_types_and_are_separate_from_outgoing_ids() -> None:
    channel = FakeChannel()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[object] = []

    async def handler(_method: str, params: object) -> object:
        calls.append(params)
        if len(calls) == 2:
            entered.set()
        await release.wait()
        return params

    client = _client(channel, on_server_request=handler)
    waiting = asyncio.create_task(client.request("outgoing"))
    assert (await channel.next_sent())["id"] == 1
    channel.push({"jsonrpc": "2.0", "id": 1, "method": "run", "params": "integer"})
    channel.push({"jsonrpc": "2.0", "id": "1", "method": "run", "params": "string"})
    await asyncio.wait_for(entered.wait(), timeout=1)
    channel.push({"jsonrpc": "2.0", "id": 1, "result": "outgoing result"})
    assert await waiting == "outgoing result"
    release.set()
    replies = [await channel.next_sent(), await channel.next_sent()]
    assert {(type(reply["id"]), reply["id"], reply["result"]) for reply in replies} == {
        (int, 1, "integer"),
        (str, "1", "string"),
    }
    assert calls == ["integer", "string"]
    assert client.closed_reason is None
    await client.close()
    assert not client._server_request_ids


@pytest.mark.asyncio
async def test_server_request_id_can_be_reused_after_completion() -> None:
    for request_id in [0, "reuse"]:
        channel = FakeChannel()
        calls: list[object] = []

        async def handler(_method: str, params: object, *, calls=calls) -> object:
            calls.append(params)
            return params

        client = _client(channel, max_pending=1, on_server_request=handler)
        for value in ("first", "second"):
            channel.push({"jsonrpc": "2.0", "id": request_id, "method": "run", "params": value})
            assert await channel.next_sent() == {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": value,
            }
            await asyncio.gather(*client._server_tasks)
            assert not client._server_request_ids
        assert calls == ["first", "second"]
        assert client.closed_reason is None
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
    assert not client._server_request_ids


@pytest.mark.asyncio
async def test_failing_notification_handler_is_reported_apart_from_channel_failure() -> None:
    def handler(_method: str, _params: object) -> None:
        raise RuntimeError(MARKER)

    channel = FakeChannel()
    client = _client(channel, on_notification=handler)
    waiting = asyncio.create_task(client.request("pending"))
    await channel.next_sent()
    channel.push({"jsonrpc": "2.0", "method": "note"})
    with pytest.raises(JsonRpcError) as failed:
        await waiting
    assert failed.value.reason == "HANDLER_FAILED"
    assert MARKER not in str(failed.value)
    await client.close()

    class BrokenChannel(FakeChannel):
        async def receive(self) -> bytes | None:
            raise OSError(MARKER)

    broken = BrokenChannel()
    broken_client = _client(broken)
    for _ in range(20):
        if broken_client.closed_reason is not None:
            break
        await asyncio.sleep(0.01)
    assert broken_client.closed_reason == "CHANNEL_FAILED"
    await broken_client.close()


@pytest.mark.asyncio
async def test_invalid_frames_close_the_client() -> None:
    for frame in [
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
    ]:
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


def test_invalid_configuration_is_rejected() -> None:
    failures = []
    for kwargs in [
        {"request_timeout_s": 0, "max_frame_bytes": 1, "max_pending": 1},
        {"request_timeout_s": float("nan"), "max_frame_bytes": 1, "max_pending": 1},
        {"request_timeout_s": 1, "max_frame_bytes": 0, "max_pending": 1},
        {"request_timeout_s": 1, "max_frame_bytes": 1, "max_pending": True},
    ]:
        with check_case(failures, (kwargs)), pytest.raises(ValueError, match="required"):
            JsonRpcClient(FakeChannel(), **kwargs)  # type: ignore[arg-type]
    assert not failures, failures


@pytest.mark.asyncio
async def test_codex_envelope_without_version_field() -> None:
    # Official rust-v0.162.1 app-server-protocol/src/rpc.rs omits "jsonrpc".
    channel = FakeChannel()
    seen: list[str] = []
    client = _client(
        channel, version_field=False, on_notification=lambda method, _p: seen.append(method)
    )
    waiting = asyncio.create_task(client.request("initialize", {"x": 1}))
    assert await channel.next_sent() == {"id": 1, "method": "initialize", "params": {"x": 1}}
    channel.push({"method": "note"})
    channel.push({"id": 1, "result": {"ok": True}})
    assert await waiting == {"ok": True}
    assert seen == ["note"]
    second = asyncio.create_task(client.request("next"))
    await channel.next_sent()
    channel.push({"jsonrpc": "2.0", "id": 2, "result": None})
    with pytest.raises(JsonRpcError) as failed:
        await second
    assert failed.value.reason == "PROTOCOL_ERROR"
    await client.close()
    with pytest.raises(ValueError, match="version_field"):
        JsonRpcClient(
            FakeChannel(),
            request_timeout_s=1,
            max_frame_bytes=1,
            max_pending=1,
            version_field=1,  # type: ignore[arg-type]
        )


def test_codec_round_trip_keeps_unicode_and_rejects_nan() -> None:
    codec = JsonCodec()
    frame = codec.encode({"jsonrpc": "2.0", "method": "가", "params": {"v": 0.5}})
    assert codec.decode(frame) == {"jsonrpc": "2.0", "method": "가", "params": {"v": 0.5}}
    with pytest.raises(ValueError, match="JSON compliant"):
        codec.encode({"v": float("nan")})


class StuckChannel(FakeChannel):
    """A peer that stopped reading: every send blocks."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def send(self, frame: bytes) -> None:
        self.entered.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_blocked_send_is_bounded_by_the_request_deadline_and_closes() -> None:
    channel = StuckChannel()
    client = _client(channel, request_timeout_s=0.05)
    with pytest.raises(JsonRpcError) as failed:
        await asyncio.wait_for(client.request("x"), timeout=1)
    assert failed.value.reason == "SEND_TIMEOUT"
    assert client.closed_reason == "SEND_TIMEOUT"  # a partial frame may be on the wire
    await client.close()


@pytest.mark.asyncio
async def test_close_releases_a_request_stuck_in_send() -> None:
    channel = StuckChannel()
    client = _client(channel, request_timeout_s=10.0)
    waiting = asyncio.create_task(client.request("x"))
    await asyncio.wait_for(channel.entered.wait(), timeout=1)
    await asyncio.wait_for(client.close(), timeout=1)
    with pytest.raises(JsonRpcError) as failed:
        await asyncio.wait_for(waiting, timeout=1)
    assert failed.value.reason == "CLOSED"


class CountingStuckChannel(StuckChannel):
    """Blocks every send and records overlap and frames that finished writing."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.written: list[bytes] = []

    async def send(self, frame: bytes) -> None:
        self.entered.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.release.wait()
            self.written.append(frame)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_cancelled_caller_during_send_closes_and_its_frame_is_never_finished() -> None:
    channel = CountingStuckChannel()
    client = _client(channel, request_timeout_s=10.0)
    first = asyncio.create_task(client.request("turn/start", {"n": 1}))
    await asyncio.wait_for(channel.entered.wait(), timeout=1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert client.closed_reason == "SEND_CANCELLED"
    with pytest.raises(JsonRpcError) as refused:
        await client.request("turn/start", {"n": 2})
    assert refused.value.reason == "SEND_CANCELLED"
    channel.release.set()
    await asyncio.wait_for(client.close(), timeout=1)
    assert channel.written == []
    assert channel.max_active == 1
    await client.close()


@pytest.mark.asyncio
async def test_handler_may_close_the_client_without_deadlock() -> None:
    channel = FakeChannel()
    holder: list[JsonRpcClient] = []

    async def handler(_method: str, _params: object) -> object:
        await holder[0].close()
        return None

    client = _client(channel, on_server_request=handler)
    holder.append(client)
    channel.push({"jsonrpc": "2.0", "id": 1, "method": "stop"})
    for _ in range(50):
        if client.closed_reason is not None and not client._server_tasks:
            break
        await asyncio.sleep(0.01)
    assert client.closed_reason == "CLOSED"
    assert not client._server_tasks
    assert not client._server_request_ids


@pytest.mark.asyncio
async def test_overflow_and_handler_raised_invalid_message_have_their_own_reasons() -> None:
    from guard_app.adapters.jsonrpc_client import InvalidMessageError

    channel = FakeChannel()
    release = asyncio.Event()

    async def slow(_method: str, _params: object) -> object:
        await release.wait()
        return None

    client = _client(channel, max_pending=1, on_server_request=slow)
    channel.push({"jsonrpc": "2.0", "id": 1, "method": "a"})
    channel.push({"jsonrpc": "2.0", "id": 2, "method": "b"})
    for _ in range(50):
        if client.closed_reason is not None:
            break
        await asyncio.sleep(0.01)
    assert client.closed_reason == "TOO_MANY_SERVER_REQUESTS"
    await client.close()

    def raising(_method: str, _params: object) -> None:
        raise InvalidMessageError()

    second = FakeChannel()
    noisy = _client(second, on_notification=raising)
    second.push({"jsonrpc": "2.0", "method": "n"})
    for _ in range(50):
        if noisy.closed_reason is not None:
            break
        await asyncio.sleep(0.01)
    assert noisy.closed_reason == "HANDLER_FAILED"
    await noisy.close()


@contextmanager
def check_case(failures, case):
    try:
        yield
    except (AssertionError, pytest.fail.Exception) as error:
        failures.append((case, str(error)))
