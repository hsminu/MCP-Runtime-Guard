"""SCHEMA-01: worker 검증 실패는 실제 저장소 예약 경계를 지킨다."""

import asyncio
from contextlib import asynccontextmanager
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, RequestStatus, TaskStatus, TrustTier
from guard_app.domain.models import Boundary
from guard_app.domain.reasons import Reason
from guard_app.domain.schema_jobs import SchemaJobRunnerError, SchemaRunnerFailure
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_schema import SchemaPreparationCache, prepare_tool_schemas
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import GuardService
from guard_app.services.schema_jobs import decode_response, encode_request, run_schema_job
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness


class _Runner:
    def __init__(self, *, timeout_validate):
        self.timeout_validate = timeout_validate
        self.validate_count = 0
        self.requests = []

    async def run(self, request, *, deadline):
        self.requests.append((request.kind, deadline))
        if request.kind == "VALIDATE":
            self.validate_count += 1
            if self.validate_count == self.timeout_validate:
                raise SchemaJobRunnerError(SchemaRunnerFailure.TIMEOUT)
        return decode_response(run_schema_job(encode_request(request)))

    async def aclose(self):
        return True


@asynccontextmanager
async def _real_guard(tmp_path, *, tool_name, timeout_validate, label=Label.PUBLIC):
    harness = _Harness(label=label)
    runner = _Runner(timeout_validate=timeout_validate)
    cache = SchemaPreparationCache()
    tool = harness.registry.read if tool_name == "read" else harness.registry.send
    await prepare_tool_schemas(cache, runner, (tool,))
    catalog = Catalog(
        harness.registry,
        profile_id="p",
        destinations=(
            Boundary(
                boundary_id="external",
                tier=TrustTier.GENERAL_EXTERNAL,
                max_label=Label.INTERNAL,
            ),
        ),
        source_rules=(
            SourceRule(server_id="docs", tool_name="read", prefix=(), label=Label.PUBLIC),
        ),
        schema_cache=cache,
        schema_runner=runner,
    )
    ids = count(1)
    store = SQLiteStore(
        tmp_path / "schema-guard.db",
        utc_now=lambda: harness.now,
        audit_id_factory=lambda: f"audit-{next(ids)}",
    )
    guard = None
    try:
        await store.create_task(harness.store.states["task"], created_at_utc=harness.now)
        calls = {"reserve": 0, "consume": 0}
        original_reserve = store.reserve_dispatch
        original_consume = store.consume_and_reserve

        async def reserve(*args, **kwargs):
            calls["reserve"] += 1
            return await original_reserve(*args, **kwargs)

        async def consume(*args, **kwargs):
            calls["consume"] += 1
            return await original_consume(*args, **kwargs)

        store.reserve_dispatch = reserve
        store.consume_and_reserve = consume
        guard = GuardService(
            store,
            harness.registry,
            catalog,
            harness.transport,
            harness.access,
            profile_id="p",
            policy_version="v",
            operator_id="operator",
            request_id_factory=lambda: "request",
            approval_id_factory=lambda: "approval",
            next_call_seq=store.allocate_call_seq,
            failure_audit=harness.failure_audit,
            utc_now=lambda: harness.now,
            monotonic=lambda: harness.monotonic,
        )
        yield guard, store, harness, runner, calls
    finally:
        if guard is not None:
            await guard.aclose()
        store.close()
        assert await store.wait_closed(5.0)


async def _request_rows(store):
    return await store._run_on_writer_async(
        lambda connection: tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT request_id,status,dispatch_reserved_at_utc "
                "FROM requests ORDER BY request_id"
            )
        )
    )


def test_schema_timeout_before_reservation_denies_without_request_or_transport(tmp_path):
    async def scenario():
        async with _real_guard(tmp_path, tool_name="read", timeout_validate=1) as (
            guard,
            store,
            harness,
            runner,
            calls,
        ):
            result = await guard.submit("task", _call("read"))
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert result.request_id is None
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert await _request_rows(store) == ()
            assert calls == {"reserve": 0, "consume": 0}
            assert harness.transport.calls == []
            assert runner.validate_count == 1

    asyncio.run(scenario())


def test_schema_timeout_in_approval_recheck_invalidates_and_halts_without_consumption(tmp_path):
    async def scenario():
        async with _real_guard(
            tmp_path, tool_name="send", timeout_validate=2, label=Label.INTERNAL
        ) as (guard, store, harness, runner, calls):
            pending = await guard.submit("task", _call("send"))
            assert isinstance(pending, Pending)
            result = await guard.human_control("operator").approve(pending.approval_id)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert (await store.get_approval("approval")).status is ApprovalStatus.INVALIDATED
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert await _request_rows(store) == (("request", RequestStatus.DENIED.value, None),)
            assert calls == {"reserve": 0, "consume": 0}
            assert harness.transport.calls == []
            assert runner.validate_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("tool_name", "result_type", "request_status", "task_status"),
    [
        ("read", Withheld, RequestStatus.WITHHELD, TaskStatus.HALTED),
        ("send", OutcomeUnknown, RequestStatus.UNKNOWN_OUTCOME, TaskStatus.UNKNOWN_OUTCOME),
    ],
)
def test_schema_timeout_after_reservation_contains_without_transport(
    tmp_path, tool_name, result_type, request_status, task_status
):
    async def scenario():
        async with _real_guard(tmp_path, tool_name=tool_name, timeout_validate=2) as (
            guard,
            store,
            harness,
            runner,
            calls,
        ):
            result = await guard.submit("task", _call(tool_name))
            assert isinstance(result, result_type)
            assert result.reasons == (Reason.POST_DISPATCH_ERROR,)
            assert (await store.load_task("task")).status is task_status
            rows = await _request_rows(store)
            assert len(rows) == 1
            assert rows[0][:2] == ("request", request_status.value)
            assert rows[0][2] is not None
            assert calls == {"reserve": 1, "consume": 0}
            assert harness.transport.calls == []
            assert runner.validate_count == 2

    asyncio.run(scenario())
