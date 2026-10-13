"""Source-checkout-only composition: real stdio/HTTP MCP, explicit B unit doubles.

No production Store/Registry or external model is implied by this spike.
Every dependency is constructed on the backend loop. The fixed server is the
only launch target; the UI accepts no command, environment, or real document.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import AsyncExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from guard_app.adapters.agt import RuleBasedMockAccessChecker
from guard_app.adapters.agt_real import AgentGovernanceAccessChecker
from guard_app.adapters.mcp_transport import (
    McpTransport,
    StdioServerParameters,
    http_stream_factory,
    stdio_stream_factory,
)
from guard_app.domain.contracts import ModelTurn
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import Boundary, ProposedCall, TaskState
from guard_app.services.bridge import BackendBindings, TaskProjection
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import GuardService
from guard_app.services.runner import TaskRunner
from tests.unit.test_guard import _Registry, _Store

from .http_spike_server import http_spike


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
    scenario="ask", *, agt_mode: str, agt_policy="allow-tools", mcp_transport="stdio"
) -> BackendBindings:
    if scenario not in {"ask", "allow", "deny"} or agt_mode not in {"real", "mock"}:
        raise ValueError("합성 실행 모드가 유효하지 않다")
    if agt_policy not in {"allow-tools", "deny-send", "deny-all"}:
        raise ValueError("합성 AGT 정책이 유효하지 않다")
    if mcp_transport not in {"stdio", "http"}:
        raise ValueError("합성 MCP 전송 방식이 유효하지 않다")
    async with AsyncExitStack() as lifetime:
        # Fixed, synthetic metadata rules; no arbitrary policy expressions/files.
        rules = {
            ("docs", "read", ToolKind.READ): agt_policy != "deny-all",
            ("docs", "send", ToolKind.SEND): agt_policy == "allow-tools",
        }
        access_timeout = 2.0
        if agt_mode == "real":
            access = AgentGovernanceAccessChecker(
                rules,
                rule_version=f"synthetic-v1-{agt_policy}",
                evaluation_timeout_s=access_timeout / 2,
            )
            lifetime.push_async_callback(access.close)
            await access.start()
        else:
            access = RuleBasedMockAccessChecker(
                {(server, tool): allowed for (server, tool, _kind), allowed in rules.items()}
            )
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
        state = TaskState(
            task_id="synthetic-task",
            epoch=1,
            profile_id="synthetic",
            policy_version="v1",
            status=TaskStatus.RUNNING,
        )
        store, registry = _Store(state), _Registry()
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
        ids, sequences = count(1), count(1)

        async def sequence(task_id):
            return next(sequences)

        async def audit(event):
            # Only fixed failure metadata; the real B6 audit remains unimplemented.
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
            request_id_factory=lambda: f"request-{next(ids)}",
            approval_id_factory=lambda: f"approval-{next(ids)}",
            next_call_seq=sequence,
            failure_audit=audit,
            monotonic=time.monotonic,
            access_timeout_seconds=access_timeout,
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
        runner.add_user_message("합성 문서를 읽고 합성 수신처에 전송")

        async def snapshot(task_id):
            current = await store.load_task(task_id)
            pending = next(
                (
                    a.approval_id
                    for a in store.approvals.values()
                    if a.status is ApprovalStatus.PENDING
                ),
                None,
            )
            counts = read_counts()
            return TaskProjection(
                task_id,
                current.epoch,
                current.status,
                pending,
                host_dispatch=len(dispatches),
                receiver_effect=counts["receiver_effect"],
                agt_mode=access.mode,
                model_mode="mock",
                can_advance=runner.can_advance,
            )

        async def close():
            runner.close()
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
