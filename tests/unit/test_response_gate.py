"""I4/I5/I6/I8 및 T06/T19/T33/T35/T36/T38의 gate 단위 의미.

메모리 Store 대역으로 공개 순서·호출 포트만 확인한다. SQLite 원자성,
MCP wire·부작용 계수, 실제 로그·UI·모델 유출 차단의 C 검증을 대체하지 않는다.
"""

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest

from guard_app.core.taint import merge
from guard_app.domain.contracts import (
    OutcomeUnknown,
    RawResult,
    RegisteredServer,
    Released,
    Withheld,
)
from guard_app.domain.enums import Label, RegStatus, RequestStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.response_gate import ResponseGate

NOW = datetime(2026, 10, 11, 13, tzinfo=UTC)


class _Registry:
    def registration(self, server_id: str) -> RegisteredServer | None:
        return None

    def status(self, server_id: str) -> RegStatus | None:
        return None

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return None


class _GateStore:
    """게이트가 사용하는 세 커밋의 단위 대역이며 실제 Store 구현이 아니다."""

    def __init__(self) -> None:
        self.state = TaskState(
            task_id="task-1",
            epoch=2,
            profile_id="default",
            policy_version="policy-1",
            status=TaskStatus.RUNNING,
        )
        self.events: list[tuple[str, object]] = []
        self.commit_error: BaseException | None = None
        self.failure_errors: list[BaseException] = []

    async def commit_observation(
        self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
    ) -> TaskState:
        self.events.append(("observation", label))
        assert at_utc is NOW
        if self.commit_error is not None:
            raise self.commit_error
        self.state = merge(self.state, label, "docs/search", req.call_seq)
        return self.state

    async def commit_send_outcome(self, req: ResolvedRequest, *, at_utc: datetime) -> None:
        self.events.append(("send", req.request_id))
        assert at_utc is NOW
        if self.commit_error is not None:
            raise self.commit_error

    async def commit_request_failure(self, req: ResolvedRequest, **fields: object) -> TaskState:
        self.events.append(("failure", fields))
        assert fields["at_utc"] is NOW
        if self.failure_errors:
            raise self.failure_errors.pop(0)
        self.state = self.state.model_copy(update={"status": fields["task_status"]})
        return self.state


def _request(*, kind: ToolKind = ToolKind.READ, resource: bool = False) -> ResolvedRequest:
    boundary = Boundary(boundary_id="docs", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL)
    return ResolvedRequest(
        request_id="request-1",
        task_id="task-1",
        epoch=2,
        call_seq=7,
        tool=ToolSpec(
            server_id="docs",
            tool_name="search",
            kind=kind,
            input_schema=FrozenJsonObject({"type": "object"}),
            destination_arg="destination_id" if kind is ToolKind.SEND else None,
            resource_arg="path" if resource else None,
            fingerprint="a" * 64,
        ),
        arguments=Arguments({}),
        resource_id="secret/file.md" if resource else None,
        server_boundary=boundary,
        destination=boundary if kind is ToolKind.SEND else None,
    )


def _raw(text: str = "answer", **updates: object) -> RawResult:
    fields: dict[str, object] = {"content": [{"type": "text", "text": text}], "isError": False}
    fields.update(updates)
    return RawResult(payload=FrozenJsonObject(fields))


def _gate(
    store: _GateStore,
    *,
    label: Label | None = Label.INTERNAL,
    prefix: tuple[str, ...] = (),
    max_result_bytes: int = 262144,
    halts: list[bool] | None = None,
) -> ResponseGate:
    rules = (
        ()
        if label is None
        else (SourceRule(server_id="docs", tool_name="search", prefix=prefix, label=label),)
    )
    return ResponseGate(
        cast("Store", store),
        Catalog(_Registry(), profile_id="default", source_rules=rules),
        utc_now=lambda: NOW,
        global_halt=lambda: halts.append(True) if halts is not None else None,
        max_result_bytes=max_result_bytes,
    )


def _failure_fields(store: _GateStore) -> dict[str, object]:
    assert store.events[-1][0] == "failure"
    return cast("dict[str, object]", store.events[-1][1])


def test_read_releases_only_after_observation_commit() -> None:
    store = _GateStore()
    result = asyncio.run(_gate(store).handle(_request(), _raw()))
    assert isinstance(result, Released)
    assert result.label is Label.INTERNAL
    assert result.result.text == "answer"
    assert store.events == [("observation", Label.INTERNAL)]
    assert store.state.high_watermark is Label.INTERNAL


def test_release_waits_for_async_commit_completion() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        complete = asyncio.Event()

        class _SlowStore(_GateStore):
            async def commit_observation(
                self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
            ) -> TaskState:
                entered.set()
                await complete.wait()
                return await super().commit_observation(req, label, at_utc=at_utc)

        store = _SlowStore()
        task = asyncio.create_task(_gate(store).handle(_request(), _raw()))
        await entered.wait()
        assert not task.done()
        complete.set()
        result = await task
        assert isinstance(result, Released)
        assert store.state.high_watermark is Label.INTERNAL

    asyncio.run(exercise())


def test_unknown_read_is_committed_and_withheld() -> None:
    store = _GateStore()
    result = asyncio.run(_gate(store, label=None).handle(_request(), _raw("SECRET_BODY")))
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.RESULT_UNCLASSIFIED,)
    assert store.events == [("observation", None)]
    assert store.state.unclassified
    assert store.state.status is TaskStatus.HALTED
    assert "SECRET_BODY" not in repr(result)


def test_output_label_cannot_override_configured_read_label() -> None:
    store = _GateStore()
    raw = _raw(structuredContent={"label": "PUBLIC", "high_watermark": 0})
    result = asyncio.run(_gate(store, label=Label.CONFIDENTIAL).handle(_request(), raw))
    assert isinstance(result, Released)
    assert result.label is Label.CONFIDENTIAL
    assert store.state.high_watermark is Label.CONFIDENTIAL


@pytest.mark.parametrize("actual", [None, "other/file.md", 1, False, [], {}])
def test_resource_read_requires_exact_reported_resource_id(actual: object) -> None:
    store = _GateStore()
    raw = _raw(structuredContent={"resource_id": actual})
    result = asyncio.run(_gate(store, prefix=("secret",)).handle(_request(resource=True), raw))
    assert isinstance(result, Withheld)
    assert store.events == [("observation", None)]


def test_resource_read_matches_expected_id_and_ignores_output_label() -> None:
    store = _GateStore()
    raw = _raw(structuredContent={"resource_id": "secret/file.md", "label": "PUBLIC"})
    result = asyncio.run(
        _gate(store, label=Label.CONFIDENTIAL, prefix=("secret",)).handle(
            _request(resource=True), raw
        )
    )
    assert isinstance(result, Released)
    assert result.label is Label.CONFIDENTIAL


@pytest.mark.parametrize("structured", [None, {}, {"body": "SECRET_BODY"}])
def test_error_read_with_received_data_is_classified(structured: object) -> None:
    store = _GateStore()
    raw = _raw("ERROR_WITH_BODY", isError=True, structuredContent=structured)
    result = asyncio.run(_gate(store).handle(_request(), raw))
    assert isinstance(result, Released)
    assert result.result.is_error
    assert result.label is Label.INTERNAL
    assert store.events == [("observation", Label.INTERNAL)]


def test_structured_only_error_is_classified_even_when_object_is_empty() -> None:
    store = _GateStore()
    raw = _raw("", isError=True, content=[], structuredContent={})
    result = asyncio.run(_gate(store).handle(_request(), raw))
    assert isinstance(result, Released)
    assert store.events == [("observation", Label.INTERNAL)]


@pytest.mark.parametrize("content", [[], [{"type": "text", "text": ""}]])
def test_empty_error_read_does_not_classify_or_halt(content: object) -> None:
    store = _GateStore()
    result = asyncio.run(
        _gate(store, label=None).handle(_request(), _raw(isError=True, content=content))
    )
    assert isinstance(result, Withheld)
    fields = _failure_fields(store)
    assert fields["request_status"] is RequestStatus.FAILED
    assert fields["task_status"] is TaskStatus.RUNNING
    assert not store.state.unclassified


@pytest.mark.parametrize("sent", [False, True])
def test_known_no_data_read_failure_preserves_grade_and_running(sent: bool) -> None:
    store = _GateStore()
    raw = RawResult(failed=True, sent=sent, failure_reason=Reason.POST_DISPATCH_ERROR)
    result = asyncio.run(_gate(store).handle(_request(), raw))
    assert isinstance(result, Withheld)
    assert _failure_fields(store)["task_status"] is TaskStatus.RUNNING
    assert store.state.high_watermark is Label.PUBLIC
    assert not store.state.unclassified


def test_successful_send_does_not_merge_or_classify() -> None:
    store = _GateStore()
    before = store.state
    result = asyncio.run(_gate(store, label=None).handle(_request(kind=ToolKind.SEND), _raw()))
    assert isinstance(result, Released)
    assert result.label is None
    assert store.events == [("send", "request-1")]
    assert store.state == before


@pytest.mark.parametrize("sent", [False, True])
def test_send_transport_failure_is_unknown_and_not_retried(sent: bool) -> None:
    store = _GateStore()
    raw = RawResult(failed=True, sent=sent, failure_reason=Reason.POST_DISPATCH_ERROR)
    result = asyncio.run(_gate(store).handle(_request(kind=ToolKind.SEND), raw))
    assert isinstance(result, OutcomeUnknown)
    assert _failure_fields(store)["request_status"] is RequestStatus.UNKNOWN_OUTCOME
    assert store.state.status is TaskStatus.UNKNOWN_OUTCOME
    assert len(store.events) == 1


def test_send_error_response_is_unknown_without_observation_merge() -> None:
    store = _GateStore()
    result = asyncio.run(_gate(store).handle(_request(kind=ToolKind.SEND), _raw(isError=True)))
    assert isinstance(result, OutcomeUnknown)
    assert _failure_fields(store)["task_status"] is TaskStatus.UNKNOWN_OUTCOME
    assert store.state.high_watermark is Label.PUBLIC


def test_unsupported_shapes_are_never_released(subtests) -> None:
    case_index = 0
    for kind in (ToolKind.READ, ToolKind.SEND):
        for updates in [
            {"content": None},
            {"content": {}},
            {"content": ["body"]},
            {"content": [{"type": "text", "text": 1}]},
            {"content": [{"type": "text"}]},
            {"content": [{"type": "image", "data": "secret"}]},
            {"content": [{"type": "audio", "data": "secret"}]},
            {"content": [{"type": "resource", "resource": {"text": "secret"}}]},
            {"content": [{"type": "resource_link", "uri": "https://example.com"}]},
            {"content": [{"type": "unknown", "text": "secret"}]},
            {"structuredContent": []},
            {"structuredContent": "secret"},
            {"isError": 0},
            {"isError": "false"},
            {"isError": None},
        ]:
            with subtests.test(case=case_index):
                store = _GateStore()
                result = asyncio.run(_gate(store).handle(_request(kind=kind), _raw(**updates)))
                assert isinstance(result, OutcomeUnknown if kind is ToolKind.SEND else Withheld)
                assert result.reasons == (Reason.UNSUPPORTED_CONTENT,)
                assert _failure_fields(store)["task_status"] is (
                    TaskStatus.UNKNOWN_OUTCOME if kind is ToolKind.SEND else TaskStatus.HALTED
                )
            case_index += 1


@pytest.mark.parametrize(("limit", "allowed"), [(2, False), (3, True)])
def test_size_limit_counts_utf8_bytes(limit: int, allowed: bool) -> None:
    store = _GateStore()
    result = asyncio.run(_gate(store, max_result_bytes=limit).handle(_request(), _raw("가")))
    assert isinstance(result, Released if allowed else Withheld)


@pytest.mark.parametrize(("limit", "allowed"), [(13, False), (14, True)])
def test_text_and_structured_content_share_one_size_limit(limit: int, allowed: bool) -> None:
    store = _GateStore()
    # UTF-8 '가' 3 bytes + compact '{"x":"나"}' 11 bytes = 14 bytes.
    result = asyncio.run(
        _gate(store, max_result_bytes=limit).handle(
            _request(), _raw("가", structuredContent={"x": "나"})
        )
    )
    assert isinstance(result, Released if allowed else Withheld)


def test_multiple_text_blocks_share_limit_and_preserve_transferred_bytes() -> None:
    store = _GateStore()
    raw = _raw(content=[{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}])
    result = asyncio.run(_gate(store, max_result_bytes=4).handle(_request(), raw))
    assert isinstance(result, Released)
    assert result.result.text == "abcd"


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_commit_failure_withholds_and_persists_containment(kind: ToolKind) -> None:
    store = _GateStore()
    store.commit_error = StoreError()
    result = asyncio.run(_gate(store).handle(_request(kind=kind), _raw("SECRET_BODY")))
    assert isinstance(result, OutcomeUnknown if kind is ToolKind.SEND else Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)
    assert _failure_fields(store)["exception_type"] == "StoreError"
    assert "SECRET_BODY" not in repr(result)


def test_containment_store_failure_halts_globally_and_propagates() -> None:
    store = _GateStore()
    store.commit_error = StoreError()
    store.failure_errors = [StoreError()]
    halts: list[bool] = []
    with pytest.raises(StoreError):
        asyncio.run(_gate(store, halts=halts).handle(_request(), _raw()))
    assert halts == [True]


@pytest.mark.parametrize("exception", [ValueError, asyncio.CancelledError])
def test_unexpected_commit_error_goes_to_after_dispatch(exception: type[BaseException]) -> None:
    store = _GateStore()
    store.commit_error = exception("SECRET_ERROR")
    with pytest.raises(exception):
        asyncio.run(_gate(store).handle(_request(), _raw()))
    assert store.events == [("observation", Label.INTERNAL)]


@pytest.mark.parametrize("limit", [0, -1, True, 1.0])
def test_size_limit_configuration_rejects_nonpositive_or_noninteger(limit: object) -> None:
    with pytest.raises(ValueError, match="양의 정수"):
        _gate(_GateStore(), max_result_bytes=cast("int", limit))


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": "other-task"},
        {"epoch": 3},
        {"high_watermark": Label.PUBLIC},
        {"last_read_label": Label.PUBLIC},
        {"status": TaskStatus.HALTED},
        {"unclassified": True},
    ],
)
def test_read_commit_must_return_matching_classified_state(updates: dict[str, object]) -> None:
    class _WrongObservation(_GateStore):
        async def commit_observation(
            self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
        ) -> TaskState:
            state = await super().commit_observation(req, label, at_utc=at_utc)
            return state.model_copy(update=updates)

    store = _WrongObservation()
    result = asyncio.run(_gate(store).handle(_request(), _raw("SECRET_BODY")))
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)
    assert store.state.status is TaskStatus.HALTED


@pytest.mark.parametrize("updates", [{"status": TaskStatus.RUNNING}, {"unclassified": False}])
def test_unclassified_commit_requires_halted_unclassified_state(updates: dict[str, object]) -> None:
    class _WrongObservation(_GateStore):
        async def commit_observation(
            self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
        ) -> TaskState:
            state = await super().commit_observation(req, label, at_utc=at_utc)
            return state.model_copy(update=updates)

    store = _WrongObservation()
    result = asyncio.run(_gate(store, label=None).handle(_request(), _raw()))
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)


def test_invalid_raw_envelope_cannot_claim_no_data_and_running() -> None:
    store = _GateStore()
    raw = RawResult.model_construct(
        failed=True,
        sent=True,
        payload=FrozenJsonObject({"content": [{"type": "text", "text": "SECRET_BODY"}]}),
        failure_reason=Reason.POST_DISPATCH_ERROR,
    )
    result = asyncio.run(_gate(store).handle(_request(), raw))
    assert isinstance(result, Withheld)
    assert _failure_fields(store)["task_status"] is TaskStatus.HALTED


@pytest.mark.parametrize(
    "updates",
    [
        {"profile_id": "other-profile"},
        {"policy_version": "other-policy"},
        {"security_version": 0},
        {"high_watermark": Label.INTERNAL, "last_read_label": Label.PUBLIC},
    ],
)
def test_observation_must_preserve_dispatch_security_context(updates: dict[str, object]) -> None:
    class _WrongObservation(_GateStore):
        async def commit_observation(
            self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
        ) -> TaskState:
            state = await super().commit_observation(req, label, at_utc=at_utc)
            return state.model_copy(update=updates)

    store = _WrongObservation()
    store.state = store.state.model_copy(
        update={"high_watermark": Label.CONFIDENTIAL, "security_version": 4}
    )
    expected = store.state
    result = asyncio.run(
        _gate(store, label=Label.PUBLIC).handle(_request(), _raw(), expected_state=expected)
    )
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)


def test_changed_observation_requires_security_version_increase() -> None:
    class _UnversionedObservation(_GateStore):
        async def commit_observation(
            self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
        ) -> TaskState:
            state = await super().commit_observation(req, label, at_utc=at_utc)
            return state.model_copy(update={"security_version": 0})

    store = _UnversionedObservation()
    result = asyncio.run(_gate(store).handle(_request(), _raw(), expected_state=store.state))
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)


def test_invalid_failure_commit_state_cannot_report_contained_send() -> None:
    class _WrongFailure(_GateStore):
        async def commit_request_failure(self, req: ResolvedRequest, **fields: object) -> TaskState:
            await super().commit_request_failure(req, **fields)
            return self.state.model_copy(update={"status": TaskStatus.RUNNING})

    store = _WrongFailure()
    halts: list[bool] = []
    with pytest.raises(StoreError):
        asyncio.run(
            _gate(store, halts=halts).handle(_request(kind=ToolKind.SEND), _raw(isError=True))
        )
    assert halts == [True]


def test_constructed_unvalidated_observation_state_is_not_a_commit_proof() -> None:
    class _UnvalidatedObservation(_GateStore):
        async def commit_observation(
            self, req: ResolvedRequest, label: Label | None, *, at_utc: datetime
        ) -> TaskState:
            state = await super().commit_observation(req, label, at_utc=at_utc)
            values = state.model_dump(mode="python")
            values["high_watermark"] = 1  # StrictLabel enum 검증을 우회한 DTO.
            return TaskState.model_construct(**values)

    store = _UnvalidatedObservation()
    result = asyncio.run(_gate(store).handle(_request(), _raw()))
    assert isinstance(result, Withheld)
    assert result.reasons == (Reason.STATE_COMMIT_FAILED,)


def test_expected_state_allows_normal_taint_and_unclassified_commits() -> None:
    for label in (Label.PUBLIC, Label.INTERNAL, None):
        store = _GateStore()
        result = asyncio.run(
            _gate(store, label=label).handle(_request(), _raw(), expected_state=store.state)
        )
        assert isinstance(result, Withheld if label is None else Released)
        if isinstance(result, Withheld):
            assert result.reasons == (Reason.RESULT_UNCLASSIFIED,)
