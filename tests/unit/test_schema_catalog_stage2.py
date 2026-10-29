"""SCHEMA-01: Catalog의 worker 검증과 진단은 실행 허가를 우회하지 않는다."""

import asyncio
import re

import pytest

from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import ProposedCall
from guard_app.domain.reasons import Reason
from guard_app.domain.schema_jobs import (
    MAX_ARGUMENT_BYTES,
    SchemaJobResponse,
    SchemaJobRunnerError,
    SchemaRunnerFailure,
    SchemaToolIssueCode,
)
from guard_app.services.catalog import Catalog, CatalogConfigError, load_profile_settings
from guard_app.services.catalog_schema import (
    ArgumentSchema,
    SchemaPreparationCache,
    prepare_tool_schemas,
)
from guard_app.services.schema_jobs import decode_response, encode_request, run_schema_job
from tests.unit.test_catalog import _registration, _Registry, _state, _tool
from tests.unit.test_profile_loading import _document
from tests.unit.test_profile_loading import _Registry as _ProfileRegistry


class _Runner:
    def __init__(self, mode="ok", on_validate=None):
        self.mode = mode
        self.on_validate = on_validate
        self.requests = []

    async def run(self, request, *, deadline):
        assert deadline > asyncio.get_running_loop().time()
        self.requests.append(request)
        if request.kind == "VALIDATE":
            if self.on_validate:
                self.on_validate()
            if self.mode == "cancel":
                raise asyncio.CancelledError
            if self.mode in {"timeout", "unavailable", "too_large"}:
                raise SchemaJobRunnerError(SchemaRunnerFailure(self.mode.upper()))
            if self.mode == "unexpected":
                raise RuntimeError("PRIVATE_INPUT")
            if self.mode == "mismatch":
                return SchemaJobResponse(job_id="different", code="OK")
            if self.mode == "schema_invalid":
                return SchemaJobResponse(job_id=request.job_id, code="SCHEMA_INVALID")
        return decode_response(run_schema_job(encode_request(request)))

    async def aclose(self):
        return True


def _call(value="ok"):
    return ProposedCall(tool_ref="docs__search", arguments={"query": value}, model_call_id="m")


def _make():
    tool = _tool()
    registry = _Registry(_registration(tools=(tool,)))
    cache = SchemaPreparationCache()
    runner = _Runner()
    asyncio.run(prepare_tool_schemas(cache, runner, [tool]))
    catalog = Catalog(registry, profile_id="default", schema_cache=cache, schema_runner=runner)
    return catalog, cache, runner, registry, tool


def _aresolve(catalog, call=None):
    return asyncio.run(catalog.aresolve(_state(), call or _call(), request_id="r", call_seq=1))


def test_sync_resolution_on_injected_catalog_fails_closed_without_runner_call():
    catalog, _, runner, _, _ = _make()
    before = len(runner.requests)
    with pytest.raises(ResolutionError) as error:
        catalog.resolve(_state(), _call(), request_id="r", call_seq=1)
    assert error.value.reason is Reason.INTERNAL_ERROR
    assert len(runner.requests) == before


def test_legacy_sync_and_async_agree_and_injected_view_preserves_display_schema():
    catalog, _, runner, registry, tool = _make()
    legacy = Catalog(registry, profile_id="default")
    assert _aresolve(catalog) == legacy.resolve(_state(), _call(), request_id="r", call_seq=1)
    assert _aresolve(legacy) == legacy.resolve(_state(), _call(), request_id="r", call_seq=1)
    assert catalog.tool_views(("docs",)) == legacy.tool_views(("docs",))
    assert catalog.tool_views(("docs",))[0].input_schema == ArgumentSchema(tool.input_schema).schema
    assert runner.requests[-1].kind == "VALIDATE"


def test_each_validation_gets_a_distinct_job_id_from_shared_source():
    catalog, _, runner, _, _ = _make()
    _aresolve(catalog)
    _aresolve(catalog)
    jobs = [request for request in runner.requests if request.kind == "VALIDATE"]
    assert len(jobs) == 2
    assert jobs[0].job_id != jobs[1].job_id
    assert all(re.fullmatch(r"schema-[0-9a-f]{32}", job.job_id) for job in jobs)


@pytest.mark.parametrize(
    "mode", ["timeout", "unavailable", "too_large", "unexpected", "mismatch", "schema_invalid"]
)
def test_worker_validation_failure_is_fixed_internal_error_without_input(mode):
    catalog, _, runner, _, _ = _make()
    runner.mode = mode
    with pytest.raises(ResolutionError) as error:
        _aresolve(catalog, _call("PRIVATE_INPUT"))
    assert error.value.reason is Reason.INTERNAL_ERROR
    assert "PRIVATE_INPUT" not in str(error.value)
    assert "PRIVATE_INPUT" not in repr(error.value)


def test_worker_invalid_arguments_and_cancellation_are_distinct():
    catalog, _, runner, _, _ = _make()
    with pytest.raises(ResolutionError) as error:
        _aresolve(catalog, _call(1))
    assert error.value.reason is Reason.INVALID_ARGUMENTS
    runner.mode = "cancel"
    with pytest.raises(asyncio.CancelledError):
        _aresolve(catalog)


def test_cancelled_aresolve_cancels_running_validation_and_propagates():
    # _make uses asyncio.run, so build it before starting the parent task's loop.
    catalog, _, runner, _, _ = _make()

    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def wait_for_cancel(request, *, deadline):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runner.run = wait_for_cancel
        task = asyncio.create_task(catalog.aresolve(_state(), _call(), request_id="r", call_seq=1))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("char", ["a", "é"])
def test_argument_size_is_jcs_utf8_boundary_before_worker(injected, char):
    catalog, _, runner, registry, _ = _make()
    if not injected:
        catalog = Catalog(registry, profile_id="default")
    overhead = len(b'{"query":""}')
    width = len(char.encode("utf-8"))
    value = char * ((MAX_ARGUMENT_BYTES - overhead) // width)
    value += "a" * (MAX_ARGUMENT_BYTES - overhead - len(value.encode("utf-8")))
    before = len(runner.requests)
    assert _aresolve(catalog, _call(value)).arguments["query"] == value
    expected = before + int(injected)
    assert len(runner.requests) == expected
    with pytest.raises(ResolutionError) as error:
        _aresolve(catalog, _call(value + "a"))
    assert error.value.reason is Reason.INVALID_ARGUMENTS
    assert len(runner.requests) == expected
    if not injected:
        with pytest.raises(ResolutionError) as error:
            catalog.resolve(_state(), _call(value + "a"), request_id="r", call_seq=1)
        assert error.value.reason is Reason.INVALID_ARGUMENTS


def test_unprepared_or_failed_tools_are_hidden_and_unsupported():
    ready = _tool(tool_name="search", fingerprint="a" * 64)
    failed = _tool(tool_name="failed", fingerprint="b" * 64)
    pending = _tool(tool_name="pending", fingerprint="c" * 64)
    registry = _Registry(_registration(tools=(ready, failed, pending)))
    cache, runner = SchemaPreparationCache(), _Runner()
    asyncio.run(prepare_tool_schemas(cache, runner, [ready]))
    cache._put(failed.fingerprint, SchemaToolIssueCode.SCHEMA_PREPARE_FAILED)
    catalog = Catalog(registry, profile_id="default", schema_cache=cache, schema_runner=runner)
    assert [item.tool_ref for item in catalog.tool_views(("docs",))] == ["docs__search"]
    for name in ("failed", "pending"):
        call = ProposedCall(tool_ref=f"docs__{name}", arguments={"query": "x"}, model_call_id="m")
        before = len(runner.requests)
        with pytest.raises(ResolutionError) as error:
            _aresolve(catalog, call)
        assert error.value.reason is Reason.TOOL_UNSUPPORTED
        assert len(runner.requests) == before


def test_registry_metadata_change_during_validation_is_refused():
    catalog, _, runner, registry, _ = _make()

    def change():
        current = registry.servers["docs"]
        boundary = current.boundary.model_copy(update={"boundary_id": "changed"})
        registry.servers["docs"] = current.model_copy(update={"boundary": boundary})

    runner.on_validate = change
    with pytest.raises(ResolutionError) as error:
        _aresolve(catalog)
    assert error.value.reason is Reason.INTERNAL_ERROR


def test_catalog_requires_pair_and_profile_can_load_with_cache_only():
    registry = _Registry(_registration())
    cache, runner = SchemaPreparationCache(), _Runner()
    with pytest.raises(CatalogConfigError):
        Catalog(registry, profile_id="default", schema_cache=cache)
    with pytest.raises(CatalogConfigError):
        Catalog(registry, profile_id="default", schema_runner=runner)
    profile = load_profile_settings(_document(), _ProfileRegistry(), schema_cache=cache)
    assert profile.tool_issue("docs", "read") is None
    assert profile.catalog.tool_views(("docs",))
    with pytest.raises(CatalogConfigError):
        load_profile_settings(_document(), _ProfileRegistry(), schema_runner=runner)


@pytest.mark.parametrize("code", list(SchemaToolIssueCode))
def test_profile_tool_issue_exposes_only_selected_registered_tool(code):
    registry, cache = _ProfileRegistry(), SchemaPreparationCache()
    profile = load_profile_settings(_document(), registry, schema_cache=cache)
    tool = registry.records["docs"].tools[0]
    cache._put(tool.fingerprint, code)
    issue = profile.tool_issue("docs", "read")
    assert issue is not None
    assert (issue.server_id, issue.tool_name, issue.code) == ("docs", "read", code)
    assert profile.tool_issue("docs", "missing") is None
    assert profile.tool_issue("missing", "read") is None
    assert profile.tool_issue("", "read") is None
    assert profile.tool_issue("docs", "") is None
    doc = _document()
    doc["profile"]["servers"] = ["remote"]
    doc["profile"]["sources"] = []
    hidden = load_profile_settings(doc, registry, schema_cache=cache)
    assert hidden.tool_issue("docs", "read") is None


def test_prepared_profile_tool_has_no_issue_and_runner_is_injected():
    registry, cache, runner = _ProfileRegistry(), SchemaPreparationCache(), _Runner()
    asyncio.run(prepare_tool_schemas(cache, runner, [registry.records["docs"].tools[0]]))
    profile = load_profile_settings(_document(), registry, schema_cache=cache, schema_runner=runner)
    assert profile.tool_issue("docs", "read") is None
    with pytest.raises(ResolutionError) as error:
        profile.catalog.resolve(_state(), _call(), request_id="r", call_seq=1)
    assert error.value.reason is Reason.INTERNAL_ERROR


def test_profile_tool_issue_remains_visible_when_server_has_url_or_settings_issue():
    registry, cache = _ProfileRegistry(), SchemaPreparationCache()
    code = SchemaToolIssueCode.SCHEMA_PREPARE_FAILED
    cache._put(registry.records["docs"].tools[0].fingerprint, code)
    document = _document()
    document["servers"][1]["url"] = "http://not-loopback.example/path"
    remote = load_profile_settings(document, registry, schema_cache=cache)
    assert remote.server_issue("remote").startswith("URL_")
    assert remote.tool_issue("remote", "send").code is code
    original = registry.records["docs"]
    registry.records["docs"] = original.model_copy(
        update={"boundary": registry.records["remote"].boundary}
    )
    mismatched = load_profile_settings(_document(), registry, schema_cache=cache)
    assert mismatched.server_issue("docs") == "REG_SETTINGS_MISMATCH"
    assert mismatched.tool_issue("docs", "read").code is code


def test_tool_issue_invalid_identifiers_match_server_issue_handling():
    profile = load_profile_settings(
        _document(), _ProfileRegistry(), schema_cache=SchemaPreparationCache()
    )
    for value in ("", "bad__id", "missing", None, 12):
        assert profile.server_issue(value) is None
        assert profile.tool_issue(value, "read") is None
        assert profile.tool_issue("docs", value) is None
    for value in ([], {}):
        with pytest.raises(TypeError):
            profile.server_issue(value)
        with pytest.raises(TypeError):
            profile.tool_issue(value, "read")
        with pytest.raises(TypeError):
            profile.tool_issue("docs", value)
