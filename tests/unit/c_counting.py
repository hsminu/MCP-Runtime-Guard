"""C 계수 하니스(C5.4): 제품 조립 Guard와 실제로 띄운 MCP 서버로 전송을 끝까지 센다.

C6.9(`test_access_rule_counts`)와 인수 기준 1·2(`test_approval_counts`)가 함께 쓴다.

- Guard: 제품 조립(`assemble_guard`)이 설정 문서로 만든 실제 `AccessRulesChecker`, B SQLiteStore
  위의 C `ServerRegistry`(C4.10). 서버는 실제 목록으로 등록·활성화한다.
- 서버: stdio는 C2.6 `launch_mcp_server`로 이 Python의 python.exe를 띄운 표준 라이브러리 MCP
  서버(별도 프로세스), HTTP는 같은 프로세스의 루프백 FastMCP(Streamable HTTP, JSON 응답)다.
- 계수 네 개
  - 승인 소비: DB `approvals`의 CONSUMED 행(읽기 전용 연결)
  - host_dispatch: Guard가 부른 `McpTransport.call`
  - wire `tools/call`: 서버가 받은 `tools/call` 메시지
  - 수신 효과: 서버가 도구를 실제로 실행한 수

서버 동작은 SEND `body` 표지의 머리말로 고른다(C5.6).

- `hang-`: 실행한 뒤 응답하지 않는다.
- `late-`: 실행한 뒤 3초 늦게 응답한다(호출 시간 제한 뒤 도착).
- `exit-`(stdio): 실행한 뒤 프로세스를 끝낸다.
- `error500-`(HTTP): 실행한 뒤 500을 돌려준다.
- `redirect307-`·`redirect308-`(HTTP): 실행하지 않고 다른 경로로 보낸다. 그 경로의 요청은 따로 센다.

호출마다 인자에 고유 표지(`path`·`body`)를 실어, 계수를 도구별로도 요청별로도 센다. 요청별
계수는 승인 소비와 host_dispatch를 Guard `request_id`로, wire와 수신 효과를 그 표지로 잇는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import socket
import sqlite3
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP

from guard_app.adapters.mcp_transport import (
    McpTransport,
    StreamFactory,
    ToolListLimits,
    http_stream_factory,
    validated_stdio_stream_factory,
)
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import DispatchError
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.launcher import ExecutablePin, LaunchedProcess, launch_mcp_server
from guard_app.security.paths import AppFolders, prepare_app_folders
from guard_app.services.catalog import ServerSettings
from guard_app.services.guard import FailureEvent
from guard_app.services.product_assembly import AssembledGuard, ProductInputs, assemble_guard
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore

PYTHON = Path(sys.base_prefix) / "python.exe"
KINDS = ("stdio", "http")
DENY_REASONS = {getattr(Reason, n) for n in ("AGT_DENY", "ACCESS_DENY") if hasattr(Reason, n)}
ERROR_REASONS = {getattr(Reason, n) for n in ("AGT_ERROR", "ACCESS_ERROR") if hasattr(Reason, n)}
ZERO = {"approval_consumed": 0, "host_dispatch": 0, "wire": 0, "effect": 0}
ONCE = {"approval_consumed": 1, "host_dispatch": 1, "wire": 1, "effect": 1}

# 받은 tools/call을 먼저 wire 파일에, 실행한 도구를 effect 파일에 JSON 한 줄씩 남긴다.
STDIO_SERVER = r"""
import json, os, sys, time
wire, effect = sys.argv[1], sys.argv[2]
TOOLS = [
    {"name": "read", "description": "Read a document", "inputSchema": {"type": "object",
     "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "send", "description": "Send a message", "inputSchema": {"type": "object",
     "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
     "required": ["to", "body"]}},
]
TOKEN = {"read": "path", "send": "body"}
def log(path, name, arguments):
    token = arguments.get(TOKEN.get(name, ""), "") if isinstance(arguments, dict) else ""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tool": name, "token": token}) + "\n")
for raw in sys.stdin.buffer:
    message = json.loads(raw)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "c-count", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message["params"]
        name, arguments = str(params.get("name")), params.get("arguments", {})
        log(wire, name, arguments)
        if name == "read":
            log(effect, name, arguments)
            result = {"content": [{"type": "text", "text": "document"}],
                      "structuredContent": {"resource_id": arguments["path"]}}
        elif name == "send":
            log(effect, name, arguments)
            body = arguments.get("body", "")
            if body.startswith("hang-"):
                continue
            if body.startswith("late-"):
                time.sleep(3)
            if body.startswith("exit-"):
                os._exit(0)
            result = {"content": [{"type": "text", "text": "sent"}]}
        else:
            result = {"content": [], "isError": True}
    else:
        result = {}
    reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    sys.stdout.buffer.write(json.dumps(reply).encode() + b"\n")
    sys.stdout.buffer.flush()
"""

READ_SETTINGS = {"name": "read", "kind": "read", "resource_arg": "path"}
SEND_SETTINGS = {"name": "send", "kind": "send", "destination_arg": "to"}
SOURCES = (
    {"server": "docs", "tool": "read", "prefix": ["public"], "label": "PUBLIC"},
    {"server": "docs", "tool": "read", "prefix": ["internal"], "label": "INTERNAL"},
    {"server": "docs", "tool": "read", "prefix": ["private"], "label": "CONFIDENTIAL"},
)
DESTINATIONS = (
    # INTERNAL 자료를 승인된 외부 목적지로 보내면 ASK다(5.1 행렬).
    {"id": "partner", "tier": "approved_external", "max_label": "INTERNAL"},
    # CONFIDENTIAL 자료는 max_label을 넘어 DENY다.
    {"id": "issue", "tier": "general_external", "max_label": "INTERNAL"},
)
_TOKEN_ARG = {"read": "path", "send": "body"}


def identity(_tool: object, value: str) -> str:
    return value


def read(path: str, call_id: str) -> ProposedCall:
    return ProposedCall(tool_ref="docs__read", arguments={"path": path}, model_call_id=call_id)


def send(call_id: str, *, to: str = "partner", body: str | None = None) -> ProposedCall:
    """`body`는 요청별 표지다. 주지 않으면 호출 ID로 만든다."""
    return ProposedCall(
        tool_ref="docs__send",
        arguments={"to": to, "body": f"body-{call_id}" if body is None else body},
        model_call_id=call_id,
    )


def executable_pin() -> ExecutablePin:
    data = PYTHON.read_bytes()
    return ExecutablePin(len(data), hashlib.sha256(data).hexdigest())


@dataclass(frozen=True, slots=True)
class Dispatch:
    request_id: str
    tool: str
    token: str


def count_dispatches(monkeypatch: Any) -> list[Dispatch]:
    """Guard가 부르는 `McpTransport.call`을 감싸 기록한다. 테스트 끝에 monkeypatch가 되돌린다."""
    calls: list[Dispatch] = []
    original = McpTransport.call

    async def counted(self, req):
        name = req.tool.tool_name
        token = req.arguments.to_dict().get(_TOKEN_ARG.get(name, ""), "")
        calls.append(Dispatch(req.request_id, name, str(token)))
        return await original(self, req)

    monkeypatch.setattr(McpTransport, "call", counted)
    return calls


@dataclass
class Counters:
    """서버 쪽 계수. stdio는 서버 프로세스가 쓴 파일에서, HTTP는 같은 프로세스 목록에서 읽는다."""

    wire_file: Path | None = None
    effect_file: Path | None = None
    wire: list[tuple[str, str]] = field(default_factory=list)
    effect: list[tuple[str, str]] = field(default_factory=list)
    # HTTP 리다이렉트 대상 경로에 온 요청(따라가면 안 된다)
    redirected: list[str] = field(default_factory=list)

    @staticmethod
    def _entries(path: Path | None, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if path is None:
            return list(fallback)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [(item["tool"], item["token"]) for item in map(json.loads, lines)]

    def wire_entries(self) -> list[tuple[str, str]]:
        return self._entries(self.wire_file, self.wire)

    def effect_entries(self) -> list[tuple[str, str]]:
        return self._entries(self.effect_file, self.effect)


class _WireCount:
    """HTTP 요청 본문에서 `tools/call`을 센 뒤 그대로 FastMCP 앱에 넘기는 ASGI 감싸개."""

    def __init__(self, app: Any, counters: Counters) -> None:
        self._app = app
        self._counters = counters

    async def __call__(self, scope: Any, receive: Any, send_: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == REDIRECT_PATH:
            self._counters.redirected.append(str(scope.get("method")))
            await _respond(send_, 404)
            return
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send_)
            return
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request" or not message.get("more_body"):
                break
        body = b"".join(m.get("body", b"") for m in messages)
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        token = ""
        for item in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(item, dict) and item.get("method") == "tools/call":
                params = item.get("params", {})
                name = str(params.get("name"))
                arguments = params.get("arguments", {})
                token = str(
                    arguments.get(_TOKEN_ARG.get(name, ""), "")
                    if isinstance(arguments, dict)
                    else ""
                )
                self._counters.wire.append((name, token))
        if token.startswith(("redirect307-", "redirect308-")):
            status = 307 if token.startswith("redirect307-") else 308
            await _respond(send_, status, location=REDIRECT_PATH)
            return
        replay = iter(messages)

        async def again() -> Any:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        if token.startswith("error500-"):

            async def swallow(_message: Any) -> None:
                return None

            await self._app(scope, again, swallow)
            await _respond(send_, 500)
            return
        await self._app(scope, again, send_)


REDIRECT_PATH = "/redirected"


async def _respond(send_: Any, status: int, *, location: str | None = None) -> None:
    headers = [(b"content-length", b"0")]
    if location is not None:
        headers.append((b"location", location.encode()))
    await send_({"type": "http.response.start", "status": status, "headers": headers})
    await send_({"type": "http.response.body", "body": b""})


@asynccontextmanager
async def http_server(counters: Counters) -> AsyncIterator[str]:
    server = FastMCP("c-count-http", log_level="WARNING", host="127.0.0.1", json_response=True)

    @server.tool()
    async def read(path: str) -> dict[str, str]:
        """Read a document"""
        counters.effect.append(("read", path))
        return {"resource_id": path}

    @server.tool()
    async def send(to: str, body: str) -> str:
        """Send a message"""
        counters.effect.append(("send", body))
        if body.startswith("hang-"):
            # 실행은 끝났지만 응답이 오지 않는다(응답 유실).
            await asyncio.sleep(3600)
        if body.startswith("late-"):
            await asyncio.sleep(3)
        return "sent"

    app = _WireCount(server.streamable_http_app(), counters)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            lifespan="on",
            ws="none",
            timeout_graceful_shutdown=1,
        )
        served = uvicorn.Server(config)
        task = asyncio.create_task(served.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not served.started:
                    if task.done():
                        await task
                        raise RuntimeError("HTTP server did not start")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            served.should_exit = True
            await asyncio.wait_for(task, 10)


@dataclass
class Run:
    kind: str
    folders: AppFolders
    store: SQLiteStore
    db: Path
    registry: ServerRegistry
    document: dict[str, object]
    counters: Counters
    dispatched: list[Dispatch]
    connect: Callable[[], Awaitable[StreamFactory]]
    assembled: AssembledGuard | None = None
    audits: list[FailureEvent] = field(default_factory=list)
    call_timeout_s: float = 30.0
    # 다시 조립해도 같은 Task의 call_seq가 겹치지 않게 실행 전체에서 하나를 쓴다.
    sequence: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    # 조립한 Guard를 닫을 때 transport가 알린 실패 코드(DispatchError)
    close_errors: list[str] = field(default_factory=list)

    @property
    def guard(self):
        assert self.assembled is not None
        return self.assembled.guard

    async def close_guard(self) -> None:
        """조립한 Guard를 닫는다. transport가 실패를 알리면 코드를 남기고 계속한다."""
        assembled, self.assembled = self.assembled, None
        if assembled is None:
            return
        try:
            await assembled.aclose()
        except DispatchError as error:
            self.close_errors.append(str(error))

    def human(self):
        return self.guard.human_control("local-operator")

    async def assemble(self, *, create_tasks: bool = True) -> AssembledGuard:
        """Guard를 조립한다. 다시 부르면 새 연결(stdio는 새 프로세스)로 같은 Store에 다시 만든다."""

        async def next_call_seq(_task_id: str) -> int:
            return next(self.sequence)

        async def failure_audit(event: FailureEvent) -> None:
            self.audits.append(event)

        self.assembled = await assemble_guard(
            self.store,
            ProductInputs(
                profile_document=self.document,
                registry=self.registry,
                server_streams={"docs": await self.connect()},
                next_call_seq=next_call_seq,
                failure_audit=failure_audit,
                operator_id="local-operator",
                resource_resolver=identity,
                call_timeout_s=self.call_timeout_s,
            ),
        )
        settings = self.assembled.settings
        for task_id in ("task-1", "task-2") if create_tasks else ():
            await self.store.create_task(
                TaskState(
                    task_id=task_id,
                    epoch=1,
                    profile_id=settings.profile_id,
                    policy_version=settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
        return self.assembled

    def _read_db(self) -> sqlite3.Connection:
        # sqlite3의 with는 트랜잭션만 끝낸다. closing으로 연결과 파일 핸들까지 닫는다(#56 B 리뷰).
        return sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)

    def _consumed(self, request_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM approvals WHERE status = 'CONSUMED'"
        args: tuple[str, ...] = ()
        if request_id is not None:
            query += " AND request_id = ?"
            args = (request_id,)
        with closing(self._read_db()) as db:
            return int(db.execute(query, args).fetchone()[0])

    def approvals_for(self, request_id: str) -> int:
        with closing(self._read_db()) as db:
            row = db.execute("SELECT COUNT(*) FROM approvals WHERE request_id = ?", (request_id,))
            return int(row.fetchone()[0])

    def counts(self, tool: str) -> dict[str, int]:
        """도구별 계수. 승인 소비는 이 실행 전체의 CONSUMED 행이다."""
        return {
            "approval_consumed": self._consumed(),
            "host_dispatch": sum(1 for d in self.dispatched if d.tool == tool),
            "wire": sum(1 for name, _ in self.counters.wire_entries() if name == tool),
            "effect": sum(1 for name, _ in self.counters.effect_entries() if name == tool),
        }

    def request_counts(self, request_id: str | None, token: str) -> dict[str, int]:
        """요청 하나의 계수(C5.4). 승인 소비·dispatch는 request_id로, wire·효과는 표지로 센다."""
        return {
            "approval_consumed": 0 if request_id is None else self._consumed(request_id),
            "host_dispatch": sum(1 for d in self.dispatched if d.request_id == request_id),
            "wire": sum(1 for _, t in self.counters.wire_entries() if t == token),
            "effect": sum(1 for _, t in self.counters.effect_entries() if t == token),
        }


def document(
    kind: str,
    endpoint: str,
    counters: Counters,
    *,
    allow,
    deny,
    approval_ttl_seconds: int | None = None,
) -> dict[str, object]:
    server: dict[str, object] = {
        "id": "docs",
        "tier": "internal",
        "max_label": "CONFIDENTIAL",
        "tools": [READ_SETTINGS, SEND_SETTINGS],
    }
    if kind == "stdio":
        server |= {
            "transport": "stdio",
            "command": str(PYTHON),
            "args": ["-I", "-c", STDIO_SERVER, str(counters.wire_file), str(counters.effect_file)],
        }
    else:
        server |= {"transport": "streamable_http", "url": endpoint}
    doc: dict[str, object] = {
        "profile": {
            "id": "c-count",
            "servers": ["docs"],
            "sources": list(SOURCES),
            "destinations": [item["id"] for item in DESTINATIONS],
        },
        "servers": [server],
        "destinations": list(DESTINATIONS),
        "access_rules": {
            "allow": [["docs", name] for name in allow],
            "deny": [["docs", name] for name in deny],
        },
    }
    if approval_ttl_seconds is not None:
        doc["policy"] = {"approval_ttl_seconds": approval_ttl_seconds}
    return doc


@asynccontextmanager
async def _endpoint(kind: str, counters: Counters) -> AsyncIterator[str]:
    if kind == "http":
        async with http_server(counters) as endpoint:
            yield endpoint
    else:
        yield ""


@asynccontextmanager
async def harness(
    tmp_path: Path,
    kind: str,
    pin: ExecutablePin,
    dispatched: list[Dispatch],
    *,
    allow=("read", "send"),
    deny=(),
    approval_ttl_seconds: int | None = None,
    call_timeout_s: float = 30.0,
    assemble: bool = True,
) -> AsyncIterator[Run]:
    """서버를 띄워 실제 목록으로 등록·활성화하고(C4.10), 제품 조립으로 Guard를 만든다."""
    counters = Counters()
    if kind == "stdio":
        counters.wire_file = tmp_path / "wire.jsonl"
        counters.effect_file = tmp_path / "effect.jsonl"
    folders = prepare_app_folders(tmp_path / "app")
    db = tmp_path / "guard.db"
    audits = iter(range(1_000_000))
    store = SQLiteStore(db, audit_id_factory=lambda: f"audit-{next(audits)}")
    launched: list[LaunchedProcess] = []
    try:
        async with _endpoint(kind, counters) as endpoint:
            doc = document(
                kind,
                endpoint,
                counters,
                allow=allow,
                deny=deny,
                approval_ttl_seconds=approval_ttl_seconds,
            )

            async def connect() -> StreamFactory:
                if kind == "http":
                    return http_stream_factory(endpoint)
                server = doc["servers"][0]
                process = await launch_mcp_server(
                    folders.server_folder("docs"), server["command"], server["args"], (), pin
                )
                launched.append(process)
                return validated_stdio_stream_factory(
                    process.read_bytes, process.write_bytes, process.drain
                )

            registry = ServerRegistry(store)
            await registry.load()
            listing_transport = McpTransport(
                {"docs": await connect()}, tool_list_limits=ToolListLimits()
            )
            try:
                await listing_transport.start()
                listing = listing_transport.tool_listing("docs")
            finally:
                await listing_transport.close()
            settings = ServerSettings.model_validate_json(
                json.dumps(doc["servers"][0]), strict=True
            )
            entry = await registry.register(settings, listing)
            await registry.activate(
                "docs", expected_row_revision=entry.registration.row_revision, reviewed_by="op"
            )
            run = Run(kind, folders, store, db, registry, doc, counters, dispatched, connect)
            run.call_timeout_s = call_timeout_s
            if assemble:
                await run.assemble()
            try:
                yield run
            finally:
                await run.close_guard()
    finally:
        for process in launched:
            assert await process.close() is True
        store.close()
        folders.close()
