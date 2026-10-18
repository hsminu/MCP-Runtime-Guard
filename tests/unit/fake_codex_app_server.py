"""Scripted in-memory Codex app-server 0.162.1 for A's session manager tests.

It speaks LF-delimited JSON-RPC without a `jsonrpc` field and adds `emittedAtMs`
to notifications, like the pinned upstream. A per-turn script drives what the
"model" does. Every client message is recorded so tests assert by counts. It
neither runs a model nor a process; it is not C's L2 fake model server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from itertools import count

THREAD_ID = "thread-1"

type TurnScript = Callable[["FakeAppServer", str, str], Awaitable[None]]


async def answer_only(server: FakeAppServer, turn_id: str, text: str) -> None:
    await server.agent(turn_id, f"echo:{text}")
    server.complete(turn_id)


class FakeAppServer:
    def __init__(self, script: TurnScript = answer_only) -> None:
        self.script = script
        self.output = bytearray()
        self.available = asyncio.Event()
        self.received: list[dict[str, object]] = []
        self.replies: dict[int, dict[str, object]] = {}
        self.thread_params: dict[str, object] | None = None
        self.initialize_params: dict[str, object] | None = None
        self.interrupts = 0
        self.eof = False
        self.account: dict[str, object] | None = None
        self.login_requests: list[dict[str, object]] = []
        self._ids = count(1000)
        self._turns = count(1)
        self._waiters: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._scripts: dict[str, asyncio.Task[None]] = {}

    # Pipes seen by CodexStreamChannel.
    async def read_bytes(self, limit: int) -> bytes:
        while not self.output:
            if self.eof:
                return b""
            self.available.clear()
            await self.available.wait()
        chunk = bytes(self.output[:limit])
        del self.output[:limit]
        return chunk

    async def write_bytes(self, frame: bytes) -> None:
        message = json.loads(frame)
        self.received.append(message)
        if "method" in message and "id" in message:
            self._request(message)
        elif "id" in message:
            waiter = self._waiters.pop(message["id"], None)
            self.replies[message["id"]] = message
            if waiter is not None and not waiter.done():
                waiter.set_result(message)

    async def drain(self) -> None:
        pass

    def close_stdout(self) -> None:
        self.eof = True
        self.available.set()

    def methods(self) -> list[str]:
        return [str(message["method"]) for message in self.received if "method" in message]

    # Server side helpers for turn scripts.
    def emit(self, message: dict[str, object]) -> None:
        self.output.extend(json.dumps(message).encode() + b"\n")
        self.available.set()

    def notify(self, method: str, params: dict[str, object]) -> None:
        self.emit({"method": method, "params": params, "emittedAtMs": 1})

    async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = next(self._ids)
        waiter: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._waiters[request_id] = waiter
        self.emit({"id": request_id, "method": method, "params": params})
        return await waiter

    async def agent(self, turn_id: str, text: str) -> None:
        item = {"type": "agentMessage", "id": f"msg-{turn_id}"}
        self.notify("item/started", {"item": item, "threadId": THREAD_ID, "turnId": turn_id})
        self.notify(
            "item/agentMessage/delta",
            {"threadId": THREAD_ID, "turnId": turn_id, "itemId": item["id"], "delta": text},
        )
        self.notify(
            "item/completed",
            {"item": {**item, "text": text}, "threadId": THREAD_ID, "turnId": turn_id},
        )
        await asyncio.sleep(0)

    def item(self, turn_id: str, kind: str) -> None:
        self.notify(
            "item/started",
            {"item": {"type": kind, "id": f"{kind}-1"}, "threadId": THREAD_ID, "turnId": turn_id},
        )

    async def tool_call(
        self,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, object],
        namespace: str | None = "docs",
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.item(turn_id, "dynamicToolCall")
        params: dict[str, object] = {
            "threadId": THREAD_ID,
            "turnId": turn_id,
            "callId": call_id,
            "tool": tool,
            "arguments": arguments,
            **(extra or {}),
        }
        if namespace is not None:
            params["namespace"] = namespace
        return await self.request("item/tool/call", params)

    def complete(
        self, turn_id: str, status: str = "completed", error: dict[str, object] | None = None
    ) -> None:
        self.notify(
            "turn/completed",
            {"threadId": THREAD_ID, "turn": {"id": turn_id, "status": status, "error": error}},
        )

    def _request(self, message: dict[str, object]) -> None:
        method, request_id = message["method"], message["id"]
        params = message.get("params") or {}
        assert isinstance(params, dict)
        if method == "initialize":
            self.initialize_params = params
            self.emit({"id": request_id, "result": {"userAgent": "fake/0.162.1"}})
        elif method == "thread/start":
            self.thread_params = params
            self.emit({"id": request_id, "result": {"thread": {"id": THREAD_ID}}})
        elif method == "turn/start":
            turn_id = f"turn-{next(self._turns)}"
            self.emit({"id": request_id, "result": {"turn": {"id": turn_id}}})
            text = params["input"][0]["text"]
            self._scripts[turn_id] = asyncio.ensure_future(self.script(self, turn_id, text))
        elif method == "turn/interrupt":
            self.interrupts += 1
            turn_id = str(params["turnId"])
            task = self._scripts.get(turn_id)
            if task is not None:
                task.cancel()
            self.complete(turn_id, status="interrupted")
            self.emit({"id": request_id, "result": {}})
        elif method == "account/read":
            self.emit({"id": request_id, "result": {"account": self.account}})
        elif method == "account/login/start":
            self.login_requests.append(params)
            if params.get("type") == "apiKey":
                self.account = {"type": "apiKey"}
                self.emit({"id": request_id, "result": {"type": "apiKey"}})
            else:
                device = {
                    "type": "chatgptDeviceCode",
                    "loginId": "login-1",
                    "verificationUrl": "https://auth.example/device",
                    "userCode": "ABCD-1234",
                }
                self.emit({"id": request_id, "result": device})
        elif method == "account/login/cancel":
            self.emit({"id": request_id, "result": {"status": "canceled"}})
        elif method == "account/logout":
            self.account = None
            self.emit({"id": request_id, "result": {}})
        else:
            self.emit({"id": request_id, "error": {"code": -32601, "message": "not found"}})

    def finish_device_login(self, success: bool = True) -> None:
        if success:
            self.account = {"type": "chatgpt", "email": "PRIVATE@EXAMPLE", "planType": "plus"}
        self.notify(
            "account/login/completed",
            {"loginId": "login-1", "success": success, "error": None if success else "denied"},
        )
