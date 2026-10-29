"""SCHEMA-01: 준비 결과의 실행별 캐시와 runner 실패 경계."""

import asyncio
import inspect

import pytest

from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.schema_jobs import (
    MAX_JOB_ELEMENTS,
    MAX_REGISTERED_SCHEMA_BYTES,
    PREPARE_DEADLINE_S,
    SchemaJobResponse,
    SchemaJobRunnerError,
    SchemaRunnerFailure,
    SchemaToolIssueCode,
    _jcs_size,
)
from guard_app.ports import SchemaJobRunner
from guard_app.services import catalog_schema
from guard_app.services.catalog_schema import SchemaPreparationCache, prepare_tool_schemas
from guard_app.services.schema_jobs import decode_response, encode_request, run_schema_job
from tests.unit.test_catalog import _tool


class _Runner:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.deadlines = []

    async def run(self, request, *, deadline):
        self.requests.append(request)
        self.deadlines.append(deadline)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "invalid":
            return SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID")
        if outcome == "mismatch":
            return SchemaJobResponse(job_id="wrong-id", code="SCHEMA_INVALID")
        if outcome == "bad-shape":
            return SchemaJobResponse(job_id=request.job_id, code="OK")
        return decode_response(run_schema_job(encode_request(request)))

    async def aclose(self):
        return True


def test_runner_protocol_exposes_async_close_and_fixed_failures():
    assert inspect.iscoroutinefunction(SchemaJobRunner.aclose)
    assert asyncio.run(_Runner().aclose()) is True
    for failure in SchemaRunnerFailure:
        error = SchemaJobRunnerError(failure)
        assert error.failure is failure
        assert str(error) == failure.value
        assert "private-schema" not in repr(error)


def test_prepare_caches_success_by_fingerprint_and_version():
    async def scenario():
        cache, runner = SchemaPreparationCache(), _Runner()
        first = _tool(input_schema={"type": "object", "description": "private-schema"})
        changed = _tool(fingerprint="b" * 64)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await prepare_tool_schemas(cache, runner, (first, first, changed))
        assert len(runner.requests) == 2
        assert all(
            start < deadline <= loop.time() + PREPARE_DEADLINE_S for deadline in runner.deadlines
        )
        assert len({request.job_id for request in runner.requests}) == 2
        assert all(request.job_id.startswith("schema-") for request in runner.requests)
        assert all(request.kind == "PREPARE" for request in runner.requests)
        assert isinstance(cache.get(first.fingerprint), FrozenJsonObject)
        assert isinstance(cache.get(changed.fingerprint), FrozenJsonObject)
        await prepare_tool_schemas(cache, runner, (first, changed))
        assert len(runner.requests) == 2
        assert "private-schema" not in repr(cache)
        assert "private-schema" not in repr(runner.requests[0])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "issue"),
    [
        ("invalid", SchemaToolIssueCode.SCHEMA_PREPARE_FAILED),
        (
            SchemaJobRunnerError(SchemaRunnerFailure.TIMEOUT),
            SchemaToolIssueCode.SCHEMA_PREPARE_TIMEOUT,
        ),
        (
            SchemaJobRunnerError(SchemaRunnerFailure.TOO_LARGE),
            SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE,
        ),
        (
            SchemaJobRunnerError(SchemaRunnerFailure.UNAVAILABLE),
            SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE,
        ),
        (RuntimeError("private-exception"), SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE),
        ("mismatch", SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE),
        ("bad-shape", SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE),
    ],
)
def test_prepare_failure_is_cached_and_new_cache_retries(outcome, issue):
    async def scenario():
        tool = _tool(input_schema={"type": "object", "description": "private-schema"})
        cache, runner = SchemaPreparationCache(), _Runner(outcome)
        await prepare_tool_schemas(cache, runner, (tool,))
        assert cache.get(tool.fingerprint) is issue
        await prepare_tool_schemas(cache, runner, (tool,))
        assert len(runner.requests) == 1
        assert "private-schema" not in repr(cache)
        assert "private-exception" not in repr(cache)
        fresh, succeeding = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(fresh, succeeding, (tool,))
        assert len(succeeding.requests) == 1
        assert isinstance(fresh.get(tool.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_invalid_and_oversized_registered_schema_are_distinct_and_never_retried():
    async def scenario():
        too_large = _tool(
            input_schema={"type": "object", "description": "x" * MAX_REGISTERED_SCHEMA_BYTES}
        )
        invalid = _tool(fingerprint="b" * 64, input_schema={"type": "array"})
        cache, runner = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(cache, runner, (too_large, invalid))
        assert cache.get(too_large.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE
        assert cache.get(invalid.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
        assert len(runner.requests) == 1
        await prepare_tool_schemas(cache, runner, (too_large, invalid))
        assert len(runner.requests) == 1

    asyncio.run(scenario())


def test_preparation_schema_size_boundary_is_jcs_bytes():
    async def scenario():
        empty = {"type": "object", "description": ""}
        description_bytes = MAX_REGISTERED_SCHEMA_BYTES - _jcs_size(empty)
        exact = _tool(input_schema={**empty, "description": "x" * description_bytes})
        excess = _tool(
            fingerprint="b" * 64,
            input_schema={**empty, "description": "x" * (description_bytes + 1)},
        )
        assert _jcs_size(exact.input_schema) == MAX_REGISTERED_SCHEMA_BYTES
        assert _jcs_size(excess.input_schema) == MAX_REGISTERED_SCHEMA_BYTES + 1
        cache, runner = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(cache, runner, (exact, excess))
        assert isinstance(cache.get(exact.fingerprint), FrozenJsonObject)
        assert cache.get(excess.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE
        assert len(runner.requests) == 1

    asyncio.run(scenario())


def test_request_element_overflow_is_too_large_without_stopping_other_tools():
    async def scenario():
        schema = {"type": "object", "examples": [0] * (MAX_JOB_ELEMENTS + 1)}
        assert _jcs_size(schema) < MAX_REGISTERED_SCHEMA_BYTES
        oversized = _tool(input_schema=schema)
        healthy = _tool(fingerprint="b" * 64)
        cache, runner = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(cache, runner, (oversized, healthy))
        assert cache.get(oversized.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE
        assert isinstance(cache.get(healthy.fingerprint), FrozenJsonObject)
        assert len(runner.requests) == 1
        await prepare_tool_schemas(cache, runner, (oversized, healthy))
        assert len(runner.requests) == 1

    asyncio.run(scenario())


def test_request_depth_overflow_is_too_large_without_stopping_other_tools():
    def schema_with_depth(depth):
        value = 0
        for _ in range(depth):
            value = [value]
        return {"type": "object", "examples": value}

    async def scenario():
        at_limit = _tool(input_schema=schema_with_depth(62))
        oversized = _tool(fingerprint="b" * 64, input_schema=schema_with_depth(63))
        healthy = _tool(fingerprint="c" * 64)
        cache, runner = SchemaPreparationCache(), _Runner("invalid")
        await prepare_tool_schemas(cache, runner, (at_limit, oversized, healthy))
        assert cache.get(at_limit.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
        assert cache.get(oversized.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_TOO_LARGE
        assert isinstance(cache.get(healthy.fingerprint), FrozenJsonObject)
        assert len(runner.requests) == 2

    asyncio.run(scenario())


def test_request_unsafe_integer_is_failed_without_stopping_other_tools():
    async def scenario():
        invalid = _tool(input_schema={"type": "object", "examples": [2**53]})
        healthy = _tool(fingerprint="b" * 64)
        cache, runner = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(cache, runner, (invalid, healthy))
        assert cache.get(invalid.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
        assert isinstance(cache.get(healthy.fingerprint), FrozenJsonObject)
        assert len(runner.requests) == 1

    asyncio.run(scenario())


def test_failed_schema_is_retried_after_its_tool_fingerprint_changes():
    async def scenario():
        original = _tool(input_schema={"type": "array"})
        changed = _tool(fingerprint="b" * 64, input_schema={"type": "object"})
        cache, runner = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(cache, runner, (original,))
        assert cache.get(original.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
        await prepare_tool_schemas(cache, runner, (changed,))
        assert len(runner.requests) == 2
        assert isinstance(cache.get(changed.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_prepare_request_construction_failure_is_fixed_and_never_calls_runner(monkeypatch):
    def invalid_request(**_kwargs):
        raise ValueError("private-request")

    async def scenario():
        cache, runner, tool = SchemaPreparationCache(), _Runner(), _tool()
        monkeypatch.setattr(catalog_schema, "SchemaJobRequest", invalid_request)
        await prepare_tool_schemas(cache, runner, (tool,))
        assert cache.get(tool.fingerprint) is SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
        assert runner.requests == []
        assert "private-request" not in repr(cache)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [SchemaRunnerFailure.TIMEOUT, SchemaRunnerFailure.TOO_LARGE, SchemaRunnerFailure.UNAVAILABLE],
)
def test_only_unavailable_short_circuits_later_unprepared_tools(failure):
    async def scenario():
        first, second, third = _tool(), _tool(fingerprint="b" * 64), _tool(fingerprint="c" * 64)
        cache, runner = SchemaPreparationCache(), _Runner(SchemaJobRunnerError(failure))
        await prepare_tool_schemas(cache, runner, (first, second, third))
        if failure is SchemaRunnerFailure.UNAVAILABLE:
            assert len(runner.requests) == 1
            assert cache.get(second.fingerprint) is SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE
            assert cache.get(third.fingerprint) is SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE
        else:
            assert len(runner.requests) == 3
            assert isinstance(cache.get(second.fingerprint), FrozenJsonObject)
            assert isinstance(cache.get(third.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_unavailable_also_blocks_later_prepare_calls_until_a_new_cache():
    async def scenario():
        first, second = _tool(), _tool(fingerprint="b" * 64)
        cache = SchemaPreparationCache()
        runner = _Runner(SchemaJobRunnerError(SchemaRunnerFailure.UNAVAILABLE))
        await prepare_tool_schemas(cache, runner, (first,))
        await prepare_tool_schemas(cache, runner, (second,))
        assert len(runner.requests) == 1
        assert cache.get(second.fingerprint) is SchemaToolIssueCode.SCHEMA_WORKER_UNAVAILABLE
        fresh, succeeding = SchemaPreparationCache(), _Runner()
        await prepare_tool_schemas(fresh, succeeding, (second,))
        assert len(succeeding.requests) == 1
        assert isinstance(fresh.get(second.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_cancel_propagates_and_releases_cache_lock_for_a_later_attempt():
    async def scenario():
        cache, runner, tool = SchemaPreparationCache(), _Runner(asyncio.CancelledError()), _tool()
        with pytest.raises(asyncio.CancelledError):
            await prepare_tool_schemas(cache, runner, (tool,))
        assert cache.get(tool.fingerprint) is None
        await prepare_tool_schemas(cache, runner, (tool,))
        assert len(runner.requests) == 2
        assert isinstance(cache.get(tool.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_concurrent_preparation_does_not_duplicate_runner_requests():
    class YieldingRunner(_Runner):
        async def run(self, request, *, deadline):
            await asyncio.sleep(0)
            return await super().run(request, deadline=deadline)

    async def scenario():
        cache, runner, tool = SchemaPreparationCache(), YieldingRunner(), _tool()
        await asyncio.gather(
            prepare_tool_schemas(cache, runner, (tool,)),
            prepare_tool_schemas(cache, runner, (tool,)),
        )
        assert len(runner.requests) == 1
        assert isinstance(cache.get(tool.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())


def test_outer_cancellation_cancels_runner_and_leaves_cache_available():
    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()

        class WaitingRunner(_Runner):
            async def run(self, request, *, deadline):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        cache, tool = SchemaPreparationCache(), _tool()
        task = asyncio.create_task(prepare_tool_schemas(cache, WaitingRunner(), (tool,)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert cache.get(tool.fingerprint) is None
        succeeding = _Runner()
        await prepare_tool_schemas(cache, succeeding, (tool,))
        assert isinstance(cache.get(tool.fingerprint), FrozenJsonObject)

    asyncio.run(scenario())
