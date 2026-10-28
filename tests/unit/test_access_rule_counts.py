"""C6.9: 자체 접근 규칙의 거절·오류·누락에서 실제 MCP 서버에 아무것도 가지 않는지 계수로 본다.

제품 조립(`assemble_guard`)이 설정 문서의 `access_rules`로 만든 실제 `AccessRulesChecker`와,
B SQLiteStore 위의 C `ServerRegistry`(C4.10)를 쓴다. 서버는 진짜로 띄운다.

- stdio: C2.6 `launch_mcp_server`로 이 Python의 python.exe를 띄운 표준 라이브러리 MCP 서버
- HTTP: 같은 프로세스의 루프백 FastMCP(Streamable HTTP, JSON 응답)

계수 네 개를 도구 이름별로 센다.

- 승인 소비: DB `approvals`의 CONSUMED 행 수(읽기 전용 연결)
- host_dispatch: Guard가 부른 `McpTransport.call` 수
- wire `tools/call`: 서버가 받은 `tools/call` 메시지 수
- 수신 효과: 서버가 도구를 실제로 실행한 수

대조군(시나리오 0)에서 계수가 실제로 올라가는 것을 보여, 다른 시나리오의 0이 의미 있게 한다.
사유 코드는 AGT-04(2026-10-28) 전후 이름을 모두 받는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sqlite3
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import (
    McpTransport,
    StreamFactory,
    ToolListLimits,
    http_stream_factory,
    validated_stdio_stream_factory,
)
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.domain.models import AccessResult, ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.launcher import ExecutablePin, LaunchedProcess, launch_mcp_server
from guard_app.security.paths import AppFolders, prepare_app_folders
from guard_app.services.catalog import ServerSettings, prepare_server_access_change
from guard_app.services.guard import FailureEvent, GuardConfigError, PolicyChangeError
from guard_app.services.product_assembly import AssembledGuard, ProductInputs, assemble_guard
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")

PYTHON = Path(sys.base_prefix) / "python.exe"
KINDS = ("stdio", "http")
DENY_REASONS = {getattr(Reason, n) for n in ("AGT_DENY", "ACCESS_DENY") if hasattr(Reason, n)}
ERROR_REASONS = {getattr(Reason, n) for n in ("AGT_ERROR", "ACCESS_ERROR") if hasattr(Reason, n)}

# 받은 tools/call을 먼저 wire 파일에, 실행한 도구를 effect 파일에 한 줄씩 남긴다.
STDIO_SERVER = r"""
import json, sys
wire, effect = sys.argv[1], sys.argv[2]
TOOLS = [
    {"name": "read", "description": "Read a document", "inputSchema": {"type": "object",
     "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "send", "description": "Send a message", "inputSchema": {"type": "object",
     "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
     "required": ["to", "body"]}},
]
def log(path, name):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(name + "\n")
for raw in sys.stdin.buffer:
    message = json.loads(raw)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "c69", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message["params"]
        log(wire, str(params.get("name")))
        if params.get("name") == "read":
            log(effect, "read")
            result = {"content": [{"type": "text", "text": "document"}],
                      "structuredContent": {"resource_id": params["arguments"]["path"]}}
        elif params.get("name") == "send":
            log(effect, "send")
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
PUBLIC_SOURCE = {"server": "docs", "tool": "read", "prefix": ["public"], "label": "PUBLIC"}
INTERNAL_SOURCE = {"server": "docs", "tool": "read", "prefix": ["internal"], "label": "INTERNAL"}
# INTERNAL 자료를 승인된 외부 목적지로 보내면 ASK다(5.1 행렬).
PARTNER = {"id": "partner", "tier": "approved_external", "max_label": "INTERNAL"}


def _identity(_tool: object, value: str) -> str:
    return value


def read(path: str, call_id: str) -> ProposedCall:
    return ProposedCall(tool_ref="docs__read", arguments={"path": path}, model_call_id=call_id)


def send(call_id: str) -> ProposedCall:
    return ProposedCall(
        tool_ref="docs__send", arguments={"to": "partner", "body": "hello"}, model_call_id=call_id
    )


@dataclass
class Counters:
    """서버 쪽 계수. stdio는 서버 프로세스가 쓴 파일에서, HTTP는 같은 프로세스 목록에서 읽는다."""

    wire_file: Path | None = None
    effect_file: Path | None = None
    wire: list[str] = field(default_factory=list)
    effect: list[str] = field(default_factory=list)

    @staticmethod
    def _lines(path: Path | None, fallback: list[str]) -> list[str]:
        if path is None:
            return list(fallback)
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    def wire_names(self) -> list[str]:
        return self._lines(self.wire_file, self.wire)

    def effect_names(self) -> list[str]:
        return self._lines(self.effect_file, self.effect)


class _WireCount:
    """HTTP 요청 본문에서 `tools/call`을 센 뒤 그대로 FastMCP 앱에 넘기는 ASGI 감싸개."""

    def __init__(self, app: Any, wire: list[str]) -> None:
        self._app = app
        self._wire = wire

    async def __call__(self, scope: Any, receive: Any, send_: Any) -> None:
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
        for item in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(item, dict) and item.get("method") == "tools/call":
                self._wire.append(str(item.get("params", {}).get("name")))
        replay = iter(messages)

        async def again() -> Any:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        await self._app(scope, again, send_)


@asynccontextmanager
async def http_server(counters: Counters) -> AsyncIterator[str]:
    server = FastMCP("c69-http", log_level="WARNING", host="127.0.0.1", json_response=True)

    @server.tool()
    async def read(path: str) -> dict[str, str]:
        """Read a document"""
        counters.effect.append("read")
        return {"resource_id": path}

    @server.tool()
    async def send(to: str, body: str) -> str:
        """Send a message"""
        counters.effect.append("send")
        return "sent"

    app = _WireCount(server.streamable_http_app(), counters.wire)
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
    dispatched: list[str]
    connect: Callable[[], Awaitable[StreamFactory]]
    assembled: AssembledGuard | None = None
    audits: list[FailureEvent] = field(default_factory=list)

    @property
    def guard(self):
        assert self.assembled is not None
        return self.assembled.guard

    async def assemble(self) -> AssembledGuard:
        sequence = iter(range(1, 10_000))

        async def next_call_seq(_task_id: str) -> int:
            return next(sequence)

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
                resource_resolver=_identity,
            ),
        )
        settings = self.assembled.settings
        for task_id in ("task-1", "task-2"):
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

    def counts(self, tool: str) -> dict[str, int]:
        with sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True) as db:
            consumed = db.execute(
                "SELECT COUNT(*) FROM approvals WHERE status = 'CONSUMED'"
            ).fetchone()[0]
        return {
            "approval_consumed": consumed,
            "host_dispatch": self.dispatched.count(tool),
            "wire": self.counters.wire_names().count(tool),
            "effect": self.counters.effect_names().count(tool),
        }


def document(kind: str, endpoint: str, counters: Counters, *, allow, deny) -> dict[str, object]:
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
    return {
        "profile": {
            "id": "c69",
            "servers": ["docs"],
            "sources": [PUBLIC_SOURCE, INTERNAL_SOURCE],
            "destinations": ["partner"],
        },
        "servers": [server],
        "destinations": [PARTNER],
        "access_rules": {
            "allow": [["docs", name] for name in allow],
            "deny": [["docs", name] for name in deny],
        },
    }


@pytest.fixture(scope="module")
def pin() -> ExecutablePin:
    data = PYTHON.read_bytes()
    return ExecutablePin(len(data), hashlib.sha256(data).hexdigest())


@pytest.fixture
def dispatched(monkeypatch) -> list[str]:
    names: list[str] = []
    original = McpTransport.call

    async def counted(self, req):
        names.append(req.tool.tool_name)
        return await original(self, req)

    monkeypatch.setattr(McpTransport, "call", counted)
    return names


@asynccontextmanager
async def harness(
    tmp_path: Path,
    kind: str,
    pin: ExecutablePin,
    dispatched: list[str],
    *,
    allow=("read", "send"),
    deny=(),
    assemble: bool = True,
) -> AsyncIterator[Run]:
    """서버를 띄워 실제 목록으로 등록·활성화하고(C4.10), 제품 조립으로 Guard를 만든다."""
    counters = Counters()
    if kind == "stdio":
        counters.wire_file = tmp_path / "wire.log"
        counters.effect_file = tmp_path / "effect.log"
    folders = prepare_app_folders(tmp_path / "app")
    db = tmp_path / "guard.db"
    audits = iter(range(1_000_000))
    store = SQLiteStore(db, audit_id_factory=lambda: f"audit-{next(audits)}")
    launched: list[LaunchedProcess] = []
    run: Run | None = None
    try:
        async with _endpoint(kind, counters) as endpoint:
            doc = document(kind, endpoint, counters, allow=allow, deny=deny)

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
            if assemble:
                await run.assemble()
            try:
                yield run
            finally:
                if run.assembled is not None:
                    await run.assembled.aclose()
    finally:
        for process in launched:
            assert await process.close() is True
        store.close()
        folders.close()


@asynccontextmanager
async def _endpoint(kind: str, counters: Counters) -> AsyncIterator[str]:
    if kind == "http":
        async with http_server(counters) as endpoint:
            yield endpoint
    else:
        yield ""


ZERO = {"approval_consumed": 0, "host_dispatch": 0, "wire": 0, "effect": 0}


# 0. 대조군: 허용된 READ와 승인된 SEND는 실제로 서버에 닿는다


@pytest.mark.parametrize("kind", KINDS)
def test_allowed_read_and_approved_send_reach_the_server(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            public = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            internal = await run.guard.submit(
                "task-1", read("internal/b.txt", "c2"), expected_epoch=1
            )
            pending = await run.guard.submit("task-1", send("c3"), expected_epoch=1)
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            return public, internal, pending, approved, run.counts("read"), run.counts("send")

    public, internal, pending, approved, reads, sends = asyncio.run(scenario())
    assert type(public) is Released
    assert type(internal) is Released
    assert type(pending) is Pending
    assert type(approved) is Released
    assert reads == {"approval_consumed": 1, "host_dispatch": 2, "wire": 2, "effect": 2}
    assert sends == {"approval_consumed": 1, "host_dispatch": 1, "wire": 1, "effect": 1}


# 1·2. deny 규칙에 걸린 도구, allow에 없는 도구(활성화 때 끈 도구)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("allow", "deny"), [(("read", "send"), ("send",)), (("read",), ())], ids=["deny", "missing"]
)
def test_denied_or_missing_tool_never_reaches_the_server(
    tmp_path, kind, allow, deny, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=allow, deny=deny) as run:
            control = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            # PUBLIC 등급이면 경계만으로는 ALLOW다. 막는 것은 접근 규칙뿐이다.
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            state = await run.store.load_task("task-1")
            return control, denied, state, run.counts("read"), run.counts("send")

    control, denied, state, reads, sends = asyncio.run(scenario())
    assert type(control) is Released
    assert reads["host_dispatch"] == reads["wire"] == reads["effect"] == 1
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert sends == ZERO
    assert state.status is TaskStatus.RUNNING


@pytest.mark.parametrize("kind", KINDS)
def test_tool_missing_from_allow_is_denied_even_for_an_ask_call(tmp_path, kind, pin, dispatched):
    """ASK가 될 SEND도 접근 규칙이 먼저 거부한다. 승인 대기가 만들어지지 않는다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=("read",)) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            return denied, run.counts("send")

    denied, sends = asyncio.run(scenario())
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert sends == ZERO


# 3. 판정기 예외: 예약 전 거절. 일반 예외는 INTERNAL_ERROR DENY, CancelledError는 그 Task만 정지
#    (ports.AccessChecker 설명, guard._pre_dispatch_failure). 둘 다 I6을 만족한다.


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("error", "status"),
    [(RuntimeError, TaskStatus.RUNNING), (asyncio.CancelledError, TaskStatus.HALTED)],
    ids=["exception", "cancelled"],
)
def test_checker_exception_is_denied_before_dispatch(
    tmp_path, kind, error, status, pin, dispatched, monkeypatch
):
    original = AccessRulesChecker.check_access
    marker = "checker-exception-secret-marker"

    def failing(self, req, ctx):
        if req.task_id == "task-1" and req.tool.tool_name == "send":
            raise error(marker)
        return original(self, req, ctx)

    monkeypatch.setattr(AccessRulesChecker, "check_access", failing)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            first = await run.store.load_task("task-1")
            other = await run.guard.submit("task-2", send("c3"), expected_epoch=1)
            second = await run.store.load_task("task-2")
            return denied, first, other, second, run.counts("send"), run.audits

    denied, first, other, second, sends, audits = asyncio.run(scenario())
    assert type(denied) is Denied
    assert denied.reasons == (Reason.INTERNAL_ERROR,)
    assert first.status is status
    # 감사에는 예외 형식만 남고 메시지는 남지 않는다(I9).
    assert [(event.phase, event.exception_type) for event in audits] == [
        ("before_dispatch", error.__name__)
    ]
    assert marker not in repr(audits)
    assert marker not in repr(denied)
    # 다른 Task는 같은 Guard로 계속 실행된다. 그 SEND 1건이 대조군이다.
    assert type(other) is Released
    assert second.status is TaskStatus.RUNNING
    assert sends == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}


# 4. 판정기가 AccessResult가 아닌 값을 돌려줌


class _Lookalike(AccessResult):
    """AccessResult처럼 보이는 하위 클래스도 정확한 형식이 아니다."""


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "value",
    [
        {"allowed": True, "reason": "", "error": False},
        True,
        None,
        _Lookalike(allowed=True, reason="", error=False),
    ],
    ids=["dict", "bool", "none", "subclass"],
)
def test_checker_returning_a_non_result_is_an_error_denial(
    tmp_path, kind, value, pin, dispatched, monkeypatch
):
    original = AccessRulesChecker.check_access

    def odd(self, req, ctx):
        return value if req.tool.tool_name == "send" else original(self, req, ctx)

    monkeypatch.setattr(AccessRulesChecker, "check_access", odd)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            control = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            return control, denied, run.counts("send")

    control, denied, sends = asyncio.run(scenario())
    assert type(control) is Released
    assert type(denied) is Denied
    assert set(denied.reasons) & ERROR_REASONS
    assert sends == ZERO


# 5. ASK 대기 중 정책을 deny로 바꾼 뒤 승인


@pytest.mark.parametrize("kind", KINDS)
def test_policy_change_to_deny_while_waiting_blocks_the_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            pending = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            assert type(pending) is Pending
            change = prepare_server_access_change(
                run.document,
                run.assembled.registry,
                server_id="docs",
                tool_permissions={"read": True, "send": False},
                resource_resolver=_identity,
            )
            await run.guard.apply_policy_change(
                change,
                AccessRulesChecker(change.loaded_profile.access_rules),
                task_source=run.store.list_tasks,
            )
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            record = await run.store.get_approval(pending.approval_id)
            settings = change.loaded_profile.guard_settings
            await run.store.create_task(
                TaskState(
                    task_id="task-3",
                    epoch=1,
                    profile_id=settings.profile_id,
                    policy_version=settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
            after = await run.guard.submit("task-3", send("c3"), expected_epoch=1)
            return approved, record, after, run.counts("send")

    approved, record, after, sends = asyncio.run(scenario())
    assert type(approved) is Denied
    # 정책 변경이 대기 승인을 먼저 무효화했다. 사람의 승인은 이미 처리된 승인에 닿는다.
    assert approved.reasons == (Reason.APPROVAL_RACE,)
    assert record is not None
    # Task가 POLICY_CHANGED로 정지하면서 대기 승인이 취소됐다.
    assert record.status is ApprovalStatus.CANCELLED
    # 바뀐 규칙으로 만든 새 Task에서도 SEND는 접근 규칙이 막는다.
    assert type(after) is Denied
    assert set(after.reasons) & DENY_REASONS
    assert sends == ZERO


# 6. async 판정기


@pytest.mark.parametrize("kind", KINDS)
def test_async_checker_is_refused_when_guard_is_built(tmp_path, kind, pin, dispatched, monkeypatch):
    async def coroutine_check(self, req, ctx):
        return AccessResult(allowed=True, reason="", error=False)

    monkeypatch.setattr(AccessRulesChecker, "check_access", coroutine_check)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, assemble=False) as run:
            with pytest.raises(GuardConfigError):
                await run.assemble()
            return run.counts("read"), run.counts("send")

    reads, sends = asyncio.run(scenario())
    assert reads == ZERO
    assert sends == ZERO


class _AsyncChecker:
    async def check_access(self, req, ctx):
        return AccessResult(allowed=True, reason="", error=False)


@pytest.mark.parametrize("kind", KINDS)
def test_async_checker_is_refused_by_a_policy_change(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=("read",)) as run:
            change = prepare_server_access_change(
                run.document,
                run.assembled.registry,
                server_id="docs",
                tool_permissions={"read": True, "send": True},
                resource_resolver=_identity,
            )
            with pytest.raises(PolicyChangeError):
                await run.guard.apply_policy_change(
                    change, _AsyncChecker(), task_source=run.store.list_tasks
                )
            state = await run.store.load_task("task-1")
            # 거부된 변경은 적용되지 않았다. SEND는 여전히 allow에 없다.
            denied = await run.guard.submit("task-1", send("c1"), expected_epoch=1)
            control = await run.guard.submit("task-1", read("public/a.txt", "c2"), expected_epoch=1)
            return state, denied, control, run.counts("send")

    state, denied, control, sends = asyncio.run(scenario())
    assert state.status is TaskStatus.RUNNING
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert type(control) is Released
    assert sends == ZERO


# 7. 승인 대기 중 서버 BLOCKED(실제 ServerRegistry). 승인 재판정이 등록 상태를 다시 보고 거부한다.
#    판정기 실행 도중 바뀐 경우의 APPROVAL_STALE은 B test_sync_access가 본다.


@pytest.mark.parametrize("kind", KINDS)
def test_server_blocked_while_waiting_rejects_the_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            pending = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            assert type(pending) is Pending
            entry = run.registry.entry("docs")
            await run.registry.block("docs", expected_row_revision=entry.registration.row_revision)
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            record = await run.store.get_approval(pending.approval_id)
            state = await run.store.load_task("task-1")
            return approved, record, state, run.counts("send")

    approved, record, state, sends = asyncio.run(scenario())
    assert type(approved) is Denied
    assert approved.reasons == (Reason.REG_NOT_ACTIVE,)
    assert record is not None
    assert record.status is ApprovalStatus.INVALIDATED
    assert state.status is TaskStatus.HALTED
    assert sends == ZERO
