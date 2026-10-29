"""Source-checkout-only composition: real stdio/HTTP MCP, B SQLiteStore, fixed synthetic registry.

B SQLite Store를 연결한 합성 앱이다. 메모리 모드는 임시 DB 파일을 쓴다. Registry와 모델은
대역이다.
Every dependency is constructed on the backend loop. The fixed server is the
only launch target; the UI accepts no command, environment, or real document.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import (
    McpTransport,
    StdioServerParameters,
    http_stream_factory,
    stdio_stream_factory,
)
from guard_app.core.access_rules import AccessRules
from guard_app.domain.contracts import ModelTurn, RegisteredServer
from guard_app.domain.enums import ApprovalStatus, Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary, ProposedCall, TaskState, ToolSpec
from guard_app.services.bridge import BackendBindings, BackendCleanupError, TaskProjection
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import GuardService
from examples.legacy_runner import TaskRunner
from guard_app.storage.sqlite_store import SQLiteStore

from .http_spike_server import http_spike


class SyntheticRegistry:
    """Registry port with one fixed, ACTIVE synthetic server; nothing is registered or stored."""

    FINGERPRINT = "a" * 64

    def __init__(self):
        read = ToolSpec(
            server_id="docs",
            tool_name="read",
            kind=ToolKind.READ,
            input_schema={"type": "object", "properties": {}},
            destination_arg=None,
            resource_arg=None,
            fingerprint=self.FINGERPRINT,
        )
        send = read.model_copy(
            update={
                "tool_name": "send",
                "kind": ToolKind.SEND,
                "input_schema": {"type": "object", "properties": {"dest": {"type": "string"}}},
                "destination_arg": "dest",
            }
        )
        self._record = RegisteredServer(
            server_id="docs",
            status=RegStatus.ACTIVE,
            boundary=Boundary(
                boundary_id="docs", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
            ),
            tools=(read, send),
        )

    def registration(self, server_id):
        return self._record if server_id == "docs" else None

    def status(self, server_id):
        return self._record.status if server_id == "docs" else None

    def fingerprint(self, tool):
        return self.FINGERPRINT


class SyntheticModel:
    def __init__(self):
        self.called = False

    async def step(self, conversation, tools):
        if self.called:
            return ModelTurn(text="합성 실행 완료")
        self.called = True
        return ModelTurn(
            calls=(
                ProposedCall(tool_ref="docs__read", arguments={}, model_call_id="read-1"),
                ProposedCall(
                    tool_ref="docs__send", arguments={"dest": "external"}, model_call_id="send-1"
                ),
            )
        )


async def bootstrap(
    scenario="ask",
    *,
    access_policy="allow-tools",
    mcp_transport="stdio",
    store_kind: str = "memory",
    db_path: str | os.PathLike[str] | None = None,
) -> BackendBindings:
    if scenario not in {"ask", "allow", "deny"}:
        raise ValueError("합성 실행 모드가 유효하지 않다")
    if access_policy not in {"allow-tools", "deny-send", "deny-all"}:
        raise ValueError("합성 접근 정책이 유효하지 않다")
    if mcp_transport not in {"stdio", "http"}:
        raise ValueError("합성 MCP 전송 방식이 유효하지 않다")
    if store_kind not in {"memory", "sqlite"}:
        raise ValueError("합성 저장소 종류가 유효하지 않다")
    if (store_kind == "sqlite") != (db_path is not None):
        raise ValueError("저장소 종류와 DB 경로 설정이 일치하지 않는다")
    async with AsyncExitStack() as lifetime:
        if store_kind == "memory":
            # 메모리 모드도 B SQLiteStore를 쓴다. 이 실행만의 임시 폴더에 두고 Store를 닫은 뒤
            # 지운다. 지우기 실패가 Store 정리 실패를 가리지 않게 한다.
            folder = lifetime.enter_context(
                TemporaryDirectory(prefix="guard-a-demo-db-", ignore_cleanup_errors=True)
            )
            db_path = Path(folder) / "guard.sqlite3"
        # 시작 복구 전에는 이전 Task·승인 권한을 복원하거나 요청을 재전송하지 않는다.
        # 기존 행은 조회·수정하지 않고 새 실행만 허용한다. 실패 시 Guard도 만들지 않는다.
        store = SQLiteStore(os.fspath(db_path), audit_id_factory=lambda: f"audit-{uuid4().hex}")

        async def close_store():
            # close는 writer 종료만 요청한다(#58). 임시 폴더를 지우기 전에 연결 종료를 확인하고,
            # 확인하지 못하면 bootstrap 도중 실패해도 bridge가 정리 확인으로 오인하지 않게 한다.
            store.close()
            if not await store.wait_closed(5.0):
                raise BackendCleanupError()

        lifetime.push_async_callback(close_store)  # 임시 폴더 다음으로 등록해 먼저 닫는다.
        state = TaskState(
            task_id=f"synthetic-{uuid4().hex}" if store_kind == "sqlite" else "synthetic-task",
            epoch=1,
            profile_id="synthetic",
            policy_version="v1",
            status=TaskStatus.RUNNING,
        )
        await store.create_task(state, created_at_utc=datetime.now(UTC))
        if await store.load_task(state.task_id) != state:
            raise StoreError()
        # Fixed, synthetic metadata rules; no arbitrary policy expressions/files.
        allowed = []
        if access_policy != "deny-all":
            allowed.append(("docs", "read"))
        if access_policy == "allow-tools":
            allowed.append(("docs", "send"))
        access = AccessRulesChecker(AccessRules(allow=allowed))
        call_timeout = 5.0
        if mcp_transport == "http":
            spike = await lifetime.enter_async_context(http_spike())
            # JSON-response mode sends headers only after the tool finishes. A read
            # bound below the call bound would let a slow, healthy tool end the session.
            stream_factory = http_stream_factory(
                spike.endpoint, io_timeout_s=1.0, read_timeout_s=2 * call_timeout
            )

            def read_counts():
                return dict(spike.counts)
        else:
            temporary = lifetime.enter_context(TemporaryDirectory(prefix="guard-a-spike-"))
            counter_path = Path(temporary) / "counts.json"
            counter_path.write_text('{"receiver_calls":0,"receiver_effect":0}', encoding="utf-8")
            # Deliberate discard: never inherit the host's stderr.
            sink = lifetime.enter_context(open(os.devnull, "w", encoding="utf-8"))  # noqa: ASYNC230, SIM115 - ExitStack owns backend null-device lifetime
            server_path = Path(__file__).with_name("stdio_spike_server.py").resolve()
            stream_factory = stdio_stream_factory(
                StdioServerParameters(
                    command=sys.executable, args=[str(server_path), str(counter_path)], env={}
                ),
                errlog=sink,
            )

            def read_counts():
                return json.loads(counter_path.read_text(encoding="utf-8"))

        dispatches = []
        transport = McpTransport(
            {"docs": stream_factory},
            call_timeout_s=call_timeout,
            close_timeout_s=2.0,
            on_dispatch=dispatches.append,
        )
        lifetime.push_async_callback(transport.close)
        await transport.start()
        registry = SyntheticRegistry()
        catalog = Catalog(
            registry,
            profile_id="synthetic",
            destinations=(
                Boundary(
                    boundary_id="external",
                    tier=TrustTier.GENERAL_EXTERNAL,
                    max_label=Label.INTERNAL,
                ),
            ),
            source_rules=(
                SourceRule(
                    server_id="docs",
                    tool_name="read",
                    prefix=(),
                    label={
                        "ask": Label.INTERNAL,
                        "deny": Label.CONFIDENTIAL,
                        "allow": Label.PUBLIC,
                    }[scenario],
                ),
            ),
        )
        sequences = count(1)

        async def sequence(task_id):
            # SQLite는 매 실행 새 Task ID이므로 (task_id, call_seq)가 중복되지 않는다.
            return next(sequences)

        async def audit(event):
            # 사전 예약 실패 감사의 공개 저장 API가 없다(B-SQL-04). 영속 감사로 간주하지 않는다.
            pass

        guard = GuardService(
            store,
            registry,
            catalog,
            transport,
            access,
            profile_id="synthetic",
            policy_version="v1",
            operator_id="local-demo-human",
            request_id_factory=lambda: f"request-{uuid4().hex}",
            approval_id_factory=lambda: f"approval-{uuid4().hex}",
            next_call_seq=sequence,
            failure_audit=audit,
            monotonic=time.monotonic,
        )
        runner = TaskRunner(
            state,
            model_client_factory=SyntheticModel,
            guard=guard,
            catalog=catalog,
            store=store,
            server_ids=("docs",),
            system_message="합성 자료 전용 mock 모델",
        )
        lifetime.callback(runner.close)
        runner.add_user_message("합성 문서를 읽고 합성 수신처에 전송")

        async def snapshot(task_id):
            if task_id != runner.task_id:
                raise ValueError("Task is unavailable")
            current = await store.load_task(task_id)
            pending = runner.pending_approval_id
            if pending is not None:
                approval = await store.get_approval(pending)
                if approval is not None and approval.approval_id != pending:
                    raise StoreError()
                if approval is None or approval.status is not ApprovalStatus.PENDING:
                    pending = None
            counts = read_counts()
            return TaskProjection(
                task_id,
                current.epoch,
                current.status,
                pending,
                host_dispatch=len(dispatches),
                receiver_effect=counts["receiver_effect"],
                model_mode="mock",
                can_advance=runner.can_advance,
                label=current.high_watermark,
                unclassified=current.unclassified,
            )

        async def close():
            await retained.aclose()

        retained = lifetime.pop_all()
        return BackendBindings(
            lambda task_id: runner,
            guard.human_control("local-demo-human"),
            close=close,
            snapshot=snapshot,
            initial_task_id=state.task_id,
            discard_queued=transport.discard_queued,
        )
