"""Source-checkout-only composition: real stdio MCP, explicit B unit doubles.

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
from guard_app.adapters.mcp_transport import (
    McpTransport,
    StdioServerParameters,
    stdio_stream_factory,
)
from guard_app.domain.contracts import ModelTurn
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, TrustTier
from guard_app.domain.models import Boundary, ProposedCall, TaskState
from guard_app.services.bridge import BackendBindings, TaskProjection
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import GuardService
from guard_app.services.runner import TaskRunner
from tests.unit.test_guard import _Registry, _Store


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


async def bootstrap(scenario="ask") -> BackendBindings:
    async with AsyncExitStack() as lifetime:
        temporary = lifetime.enter_context(TemporaryDirectory(prefix="guard-a-spike-"))
        counter_path = Path(temporary) / "counts.json"
        counter_path.write_text('{"receiver_calls":0,"receiver_effect":0}', encoding="utf-8")
        # Deliberate discard: never inherit the host's stderr.
        sink = lifetime.enter_context(open(os.devnull, "w", encoding="utf-8"))  # noqa: ASYNC230, SIM115 - ExitStack owns backend null-device lifetime
        dispatches = []
        server_path = Path(__file__).with_name("stdio_spike_server.py").resolve()
        transport = McpTransport(
            {
                "docs": stdio_stream_factory(
                    StdioServerParameters(
                        command=sys.executable, args=[str(server_path), str(counter_path)], env={}
                    ),
                    errlog=sink,
                )
            },
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
            RuleBasedMockAccessChecker({("docs", "read"): True, ("docs", "send"): True}),
            profile_id="synthetic",
            policy_version="v1",
            operator_id="local-demo-human",
            request_id_factory=lambda: f"request-{next(ids)}",
            approval_id_factory=lambda: f"approval-{next(ids)}",
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
            counts = json.loads(counter_path.read_text(encoding="utf-8"))
            return TaskProjection(
                task_id,
                current.epoch,
                current.status,
                pending,
                host_dispatch=len(dispatches),
                receiver_effect=counts["receiver_effect"],
                agt_mode="mock",
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
        )
