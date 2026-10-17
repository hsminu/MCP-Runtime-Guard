"""Transport-neutral JSON-RPC 2.0 client for the future Codex session manager.

It owns request/response correlation, notification dispatch, replies to
server-initiated requests, a per-request deadline and a frame size bound.
It never starts a process: the caller injects a ``FrameChannel`` built over
streams that ``security.launcher`` opened. Message framing on the wire and the
Codex method schema (``thread/start``, ``item/tool/call`` ...) are not fixed
until the pinned Codex version is chosen (GitHub #21 item 1), so framing lives
in the channel and the JSON codec is replaceable. No Codex method is
implemented here.

The ``"jsonrpc": "2.0"`` member is a constructor choice. The locally installed
Codex 0.159.0 schema (``codex app-server generate-json-schema``) has no such
member in any envelope, so a client for that protocol is built with
``version_field=False``: the member is never sent and a peer message carrying
it is rejected. The pinned version must be rechecked before relying on this.

Fail closed: a malformed frame, an oversize frame, a broken handler or EOF
closes the client and fails every pending request with a fixed reason.
Message bodies, parameters and remote error text are never logged or kept in
exception messages.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from itertools import count
from typing import Protocol, cast

type JsonId = int | str


class FrameChannel(Protocol):
    """One complete encoded message per call; ``receive`` returns None at EOF."""

    async def receive(self) -> bytes | None: ...

    async def send(self, frame: bytes) -> None: ...


class MessageCodec(Protocol):
    def decode(self, frame: bytes) -> dict[str, object]: ...

    def encode(self, message: dict[str, object]) -> bytes: ...


class JsonRpcError(Exception):
    """Fixed-reason failure; never carries a message body or remote text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class JsonRpcRemoteError(JsonRpcError):
    """The peer answered with a JSON-RPC error; only its integer code is kept."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__("REMOTE_ERROR")


class InvalidMessageError(ValueError):
    """A frame that is not one acceptable JSON-RPC 2.0 message."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidMessageError()
        result[key] = value
    return result


def _reject_constant(_name: str) -> object:
    raise InvalidMessageError()


class JsonCodec:
    """UTF-8 JSON objects only: no duplicate keys, NaN or Infinity."""

    def decode(self, frame: bytes) -> dict[str, object]:
        try:
            text = frame.decode("utf-8")
            value = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, RecursionError, ValueError):
            raise InvalidMessageError() from None
        if type(value) is not dict:
            raise InvalidMessageError()
        return cast("dict[str, object]", value)

    def encode(self, message: dict[str, object]) -> bytes:
        return json.dumps(
            message, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")


def _valid_id(value: object) -> bool:
    return (type(value) is int) or (type(value) is str and bool(value))


type NotificationHandler = Callable[[str, object], None]
type ServerRequestHandler = Callable[[str, object], Awaitable[object]]


class JsonRpcClient:
    """Correlate requests and responses over one injected frame channel.

    Notifications reach ``on_notification`` synchronously in arrival order.
    Each server-initiated request runs ``on_server_request`` in its own task so
    reading continues while it waits (for example on a human approval); the
    handler's return value becomes the ``result``. The session manager, not
    this client, decides ordering and which methods it accepts. A handler that
    raises closes the client.
    """

    def __init__(
        self,
        channel: FrameChannel,
        *,
        request_timeout_s: float,
        max_frame_bytes: int,
        max_pending: int,
        on_notification: NotificationHandler | None = None,
        on_server_request: ServerRequestHandler | None = None,
        codec: MessageCodec | None = None,
        version_field: bool = True,
    ) -> None:
        if type(version_field) is not bool:
            raise ValueError("boolean version_field required")
        if (
            type(request_timeout_s) not in (int, float)
            or not math.isfinite(request_timeout_s)
            or request_timeout_s <= 0
        ):
            raise ValueError("positive finite request timeout required")
        for limit in (max_frame_bytes, max_pending):
            if type(limit) is not int or limit < 1:
                raise ValueError("positive integer limits required")
        self._channel = channel
        self._codec: MessageCodec = codec if codec is not None else JsonCodec()
        self._version_field = version_field
        self._timeout = float(request_timeout_s)
        self._max_frame_bytes = max_frame_bytes
        self._max_pending = max_pending
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._ids = count(1)
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._server_tasks: set[asyncio.Task[None]] = set()
        self._send_lock = asyncio.Lock()
        self._reader: asyncio.Task[None] | None = None
        self._closed_reason: str | None = None
        self._in_notification = False

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def start(self) -> None:
        if self._reader is not None or self._closed_reason is not None:
            raise RuntimeError("JSON-RPC client already started")
        self._reader = asyncio.create_task(self._read_loop(), name="jsonrpc-reader")
        self._reader.add_done_callback(self._reader_done)

    async def request(self, method: str, params: object = None) -> object:
        self._require_open()
        if len(self._pending) >= self._max_pending:
            raise JsonRpcError("TOO_MANY_PENDING")
        request_id = next(self._ids)
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(self._message(id=request_id, method=method, params=params))
            async with asyncio.timeout(self._timeout):
                return await asyncio.shield(future)
        except TimeoutError:
            raise JsonRpcError("REQUEST_TIMEOUT") from None
        finally:
            # A late response for this id is dropped by the reader.
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def notify(self, method: str, params: object = None) -> None:
        self._require_open()
        await self._send(self._message(method=method, params=params))

    async def close(self) -> None:
        self._shutdown("CLOSED")
        tasks = [task for task in (self._reader, *self._server_tasks) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _require_open(self) -> None:
        if self._reader is None:
            raise JsonRpcError("NOT_STARTED")
        if self._closed_reason is not None:
            raise JsonRpcError(self._closed_reason)

    def _message(self, **fields: object) -> dict[str, object]:
        if "method" in fields and (type(fields["method"]) is not str or not fields["method"]):
            raise ValueError("method name required")
        message: dict[str, object] = {"jsonrpc": "2.0"} if self._version_field else {}
        message.update({key: value for key, value in fields.items() if value is not None})
        return message

    async def _send(self, message: dict[str, object]) -> None:
        frame = self._codec.encode(message)
        if len(frame) > self._max_frame_bytes:
            raise JsonRpcError("FRAME_TOO_LARGE")
        async with self._send_lock:
            if self._closed_reason is not None:
                raise JsonRpcError(self._closed_reason)
            await self._channel.send(frame)

    async def _read_loop(self) -> None:
        while self._closed_reason is None:
            frame = await self._channel.receive()
            if frame is None:
                self._shutdown("EOF")
                return
            if type(frame) is not bytes or len(frame) > self._max_frame_bytes:
                self._shutdown("FRAME_TOO_LARGE")
                return
            try:
                message = self._codec.decode(frame)
                self._dispatch(message)
            except InvalidMessageError:
                self._shutdown("PROTOCOL_ERROR")
                return

    def _dispatch(self, message: dict[str, object]) -> None:
        if self._version_field:
            if message.get("jsonrpc") != "2.0":
                raise InvalidMessageError()
        elif "jsonrpc" in message:
            raise InvalidMessageError()
        method = message.get("method")
        has_id = "id" in message
        if method is None:
            self._dispatch_response(message)
            return
        if type(method) is not str or not method:
            raise InvalidMessageError()
        params = message.get("params")
        if not has_id:
            if self._on_notification is not None:
                # If the handler raises, the reader ends with this flag still set
                # and _reader_done reports HANDLER_FAILED instead of CHANNEL_FAILED.
                self._in_notification = True
                self._on_notification(method, params)
                self._in_notification = False
            return
        request_id = message["id"]
        if not _valid_id(request_id):
            raise InvalidMessageError()
        if len(self._server_tasks) >= self._max_pending:
            raise InvalidMessageError()
        task = asyncio.create_task(
            self._answer(cast("JsonId", request_id), method, params), name="jsonrpc-server-request"
        )
        self._server_tasks.add(task)
        task.add_done_callback(self._server_task_done)

    def _dispatch_response(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        has_result = "result" in message
        error = message.get("error")
        if type(request_id) is not int or has_result == ("error" in message):
            raise InvalidMessageError()
        future = self._pending.get(request_id)
        if future is None or future.done():
            return  # Unknown, timed-out or duplicate response: never a new result.
        if has_result:
            future.set_result(message["result"])
            return
        if type(error) is not dict or type(error.get("code")) is not int:
            raise InvalidMessageError()
        future.set_exception(JsonRpcRemoteError(cast("int", error["code"])))

    async def _answer(self, request_id: JsonId, method: str, params: object) -> None:
        if self._on_server_request is None:
            reply = self._message(id=request_id, error={"code": -32601, "message": "Not found"})
        else:
            result = await self._on_server_request(method, params)
            reply = self._message(id=request_id)
            reply["result"] = result
        await self._send(reply)

    def _server_task_done(self, task: asyncio.Task[None]) -> None:
        self._server_tasks.discard(task)
        if task.cancelled():
            return
        if task.exception() is not None:
            # Retrieve to keep the raw exception out of asyncio's default log.
            self._shutdown("HANDLER_FAILED")

    def _reader_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            self._shutdown("CLOSED")
        elif task.exception() is not None:
            self._shutdown("HANDLER_FAILED" if self._in_notification else "CHANNEL_FAILED")

    def _shutdown(self, reason: str) -> None:
        if self._closed_reason is None:
            self._closed_reason = reason
        for future in self._pending.values():
            if not future.done():
                future.set_exception(JsonRpcError(self._closed_reason))
                # Mark the exception retrieved if nobody awaits it any more.
                future.exception()
        current = asyncio.current_task()
        if self._reader is not None and self._reader is not current and not self._reader.done():
            self._reader.cancel()
        for task in tuple(self._server_tasks):
            if task is not current and not task.done():
                task.cancel()
