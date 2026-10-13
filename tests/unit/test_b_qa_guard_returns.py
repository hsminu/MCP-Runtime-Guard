"""B-QA-08: I4/I5/I6/I9·T06/T19/T36의 Store 반환 상태 보존 단위 회귀.

메모리 대역의 Guard dispatch 계수만 측정한다. 실제 SQLite 원자성·MCP wire·
수신 부작용과 C 독립 인수는 이 검사로 증명하지 않는다.
"""

import asyncio

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, RawResult, Released, Withheld
from guard_app.domain.enums import Label, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary, Escalation, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_response_gate import _gate, _GateStore, _raw, _request


def _tainted_state(state: TaskState) -> TaskState:
    return state.model_copy(
        update={
            "high_watermark": Label.INTERNAL,
            "security_version": 5,
            "row_revision": 8,
            "escalations": (
                Escalation(
                    at_seq=0,
                    source_id="docs/earlier",
                    from_label=Label.PUBLIC,
                    to_label=Label.INTERNAL,
                ),
            ),
        }
    )


def _harness() -> _Harness:
    h = _Harness()
    h.store.states["task"] = _tainted_state(h.store.states["task"])
    # SEND도 승인을 거치지 않는 내부 목적지를 사용해 예약 후 오류를 고립한다.
    h.catalog = Catalog(
        h.registry,
        profile_id="p",
        destinations=(
            Boundary(
                boundary_id="external",
                tier=TrustTier.INTERNAL,
                max_label=Label.CONFIDENTIAL,
            ),
        ),
        source_rules=(
            SourceRule(server_id="docs", tool_name="read", prefix=(), label=Label.CONFIDENTIAL),
        ),
    )
    h.guard._catalog = h.catalog
    h.guard._gate._catalog = h.catalog
    return h


def _wrong_state(state: TaskState, field: str) -> TaskState:
    if field == "history_prefix":
        update = {
            "escalations": (state.escalations[0].model_copy(update={"source_id": "docs/other"}),)
        }
    else:
        update = {
            "profile": {"profile_id": "other-profile"},
            "policy": {"policy_version": "other-policy"},
            "high": {"high_watermark": Label.PUBLIC},
            "security": {"security_version": state.security_version - 1},
            "revision": {"row_revision": state.row_revision - 1},
            "lost_history": {"escalations": ()},
            "task": {"task_id": "unrelated"},
            "epoch": {"epoch": state.epoch + 1},
            "status": {"status": TaskStatus.RUNNING},
            "unclassified": {"unclassified": False},
        }[field]
    return state.model_copy(update=update)


_INVALID_CONTEXTS = (
    "profile",
    "policy",
    "high",
    "security",
    "revision",
    "lost_history",
    "history_prefix",
    "task",
    "epoch",
    "status",
)


@pytest.mark.parametrize("name", ["read", "send"])
@pytest.mark.parametrize("field", _INVALID_CONTEXTS)
def test_transport_failure_bad_store_return_blocks_all_later_dispatch_t36(name, field):
    async def scenario():
        h = _harness()
        original = h.store.commit_request_failure
        h.transport.error = RuntimeError("EXCEPTION_SECRET_MARKER")

        async def invalid_return(req, **kwargs):
            state = await original(req, **kwargs)
            return _wrong_state(state, field)

        h.store.commit_request_failure = invalid_return
        outcome = await h.guard.submit("task", _call(name))

        assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
        assert outcome.reasons == (Reason.POST_DISPATCH_ERROR,)
        assert h.guard._global_halt
        h.store.states["healthy"] = TaskState(
            task_id="healthy",
            epoch=1,
            profile_id="p",
            policy_version="v",
            status=TaskStatus.RUNNING,
        )
        h.transport.error = None
        later = await h.guard.submit("healthy", _call())
        assert isinstance(later, Denied)
        assert later.reasons == (Reason.INTERNAL_ERROR,)
        assert len(h.transport.calls) == 1
        assert h.transport.calls[0].task_id == "task"
        assert len(h.access.calls) == 1
        assert "EXCEPTION_SECRET_MARKER" not in repr(outcome) + repr(later) + repr(h.audit)
        assert "RAW_SECRET_MARKER" not in repr(outcome) + repr(later) + repr(h.audit)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_containment_preserves_existing_unclassified_flag_t19(name):
    async def scenario():
        h = _harness()
        expected = h.store.states["task"].model_copy(update={"unclassified": True})
        req = h.catalog.resolve(expected, _call(name), request_id="reserved", call_seq=1)
        h.store.states["task"] = expected
        original = h.store.commit_request_failure

        async def invalid_return(req, **kwargs):
            return _wrong_state(await original(req, **kwargs), "unclassified")

        h.store.commit_request_failure = invalid_return
        with pytest.raises(StoreError) as raised:
            await h.guard._contain_post_dispatch(
                req, RuntimeError("EXCEPTION_SECRET_MARKER"), expected_state=expected
            )
        assert raised.value.reason is Reason.STATE_COMMIT_FAILED
        assert "EXCEPTION_SECRET_MARKER" not in repr(raised.value) + repr(h.audit)
        assert not h.transport.calls

    asyncio.run(scenario())


def _raised_taint(state: TaskState) -> TaskState:
    return state.model_copy(
        update={
            "high_watermark": Label.CONFIDENTIAL,
            "security_version": state.security_version + 1,
            "row_revision": state.row_revision + 1,
            "escalations": state.escalations
            + (
                Escalation(
                    at_seq=1,
                    source_id="docs/later",
                    from_label=state.high_watermark,
                    to_label=Label.CONFIDENTIAL,
                ),
            ),
        }
    )


@pytest.mark.parametrize("name", ["read", "send"])
@pytest.mark.parametrize("advance", [False, True])
def test_containment_accepts_preserved_or_advanced_security_state_t36(name, advance):
    async def scenario():
        h = _harness()
        original = h.store.commit_request_failure
        h.transport.error = RuntimeError("EXCEPTION_SECRET_MARKER")

        async def valid_return(req, **kwargs):
            state = await original(req, **kwargs)
            if advance:
                state = _raised_taint(state)
                h.store.states[req.task_id] = state
            return state

        h.store.commit_request_failure = valid_return
        outcome = await h.guard.submit("task", _call(name))
        assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
        assert outcome.reasons == (Reason.POST_DISPATCH_ERROR,)
        assert not h.guard._global_halt
        h.store.states["healthy"] = TaskState(
            task_id="healthy",
            epoch=1,
            profile_id="p",
            policy_version="v",
            status=TaskStatus.RUNNING,
        )
        h.transport.error = None
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        assert [req.task_id for req in h.transport.calls] == ["task", "healthy"]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
@pytest.mark.parametrize("field", [*_INVALID_CONTEXTS, "unclassified"])
def test_gate_failure_bad_store_return_cannot_claim_containment_t06_t19(kind, field):
    async def scenario():
        class _WrongFailure(_GateStore):
            async def commit_request_failure(self, req, **fields):
                state = await super().commit_request_failure(req, **fields)
                return _wrong_state(state, field)

        store = _WrongFailure()
        store.state = _tainted_state(store.state).model_copy(update={"unclassified": True})
        expected = store.state
        halts = []
        raw = _raw(content=[{"type": "image", "data": "RAW_SECRET_MARKER"}])
        with pytest.raises(StoreError) as raised:
            await _gate(store, halts=halts).handle(
                _request(kind=kind), raw, expected_state=expected
            )
        assert raised.value.reason is Reason.STATE_COMMIT_FAILED
        assert halts == [True]
        assert "RAW_SECRET_MARKER" not in repr(raised.value) + repr(store.events)

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["lost_history", "history_prefix", "revision"])
def test_read_observation_must_preserve_prior_history_and_revision_t06(field):
    async def scenario():
        class _WrongObservation(_GateStore):
            async def commit_observation(self, req, label, **kwargs):
                state = await super().commit_observation(req, label, **kwargs)
                return _wrong_state(state, field)

        store = _WrongObservation()
        store.state = _tainted_state(store.state)
        expected = store.state
        result = await _gate(store, label=Label.CONFIDENTIAL).handle(
            _request(), _raw("RAW_SECRET_MARKER"), expected_state=expected
        )
        assert isinstance(result, Withheld)
        assert result.reasons == (Reason.STATE_COMMIT_FAILED,)
        assert "RAW_SECRET_MARKER" not in repr(result) + repr(store.events)
        assert store.state.status is TaskStatus.HALTED

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
@pytest.mark.parametrize("advance", [False, True])
def test_gate_failure_accepts_conservative_taint_with_history_prefix_t06(kind, advance):
    async def scenario():
        class _ConservativeFailure(_GateStore):
            async def commit_request_failure(self, req, **fields):
                state = await super().commit_request_failure(req, **fields)
                self.state = _raised_taint(state) if advance else state
                return self.state

        store = _ConservativeFailure()
        store.state = _tainted_state(store.state).model_copy(update={"unclassified": True})
        expected = store.state
        halts = []
        outcome = await _gate(store, halts=halts).handle(
            _request(kind=kind),
            _raw(content=[{"type": "image", "data": "RAW_SECRET_MARKER"}]),
            expected_state=expected,
        )
        assert isinstance(outcome, Withheld if kind is ToolKind.READ else OutcomeUnknown)
        assert outcome.reasons == (Reason.UNSUPPORTED_CONTENT,)
        assert not halts
        assert store.state.unclassified
        assert store.state.escalations[: len(expected.escalations)] == expected.escalations
        assert "RAW_SECRET_MARKER" not in repr(outcome) + repr(store.events)

    asyncio.run(scenario())


def test_read_observation_allows_appended_escalation_and_classified_release_t06():
    async def scenario():
        store = _GateStore()
        store.state = _tainted_state(store.state)
        expected = store.state
        result = await _gate(store, label=Label.CONFIDENTIAL).handle(
            _request(), _raw(), expected_state=expected
        )
        assert isinstance(result, Released)
        assert result.label is Label.CONFIDENTIAL
        assert store.state.high_watermark is Label.CONFIDENTIAL
        assert store.state.security_version == expected.security_version + 1
        assert store.state.escalations[: len(expected.escalations)] == expected.escalations
        assert len(store.state.escalations) == len(expected.escalations) + 1

    asyncio.run(scenario())


def test_no_data_read_failure_does_not_lower_last_read_mode_label_t06():
    async def scenario():
        class WrongFailure(_GateStore):
            async def commit_request_failure(self, req, **fields):
                state = await super().commit_request_failure(req, **fields)
                self.state = state.model_copy(update={"last_read_label": Label.PUBLIC})
                return self.state

        store = WrongFailure()
        store.state = _tainted_state(store.state).model_copy(
            update={"last_read_label": Label.INTERNAL}
        )
        expected = store.state
        outcome = await _gate(store).handle(
            _request(),
            RawResult(failed=True, failure_reason=Reason.POST_DISPATCH_ERROR),
            expected_state=expected,
        )
        assert isinstance(outcome, Withheld)
        assert outcome.reasons == (Reason.STATE_COMMIT_FAILED,)
        assert store.state.status is TaskStatus.HALTED
        assert not hasattr(outcome, "result")

    asyncio.run(scenario())


def test_send_failure_cannot_change_last_read_mode_label_t06():
    async def scenario():
        class WrongFailure(_GateStore):
            async def commit_request_failure(self, req, **fields):
                state = await super().commit_request_failure(req, **fields)
                return state.model_copy(update={"last_read_label": Label.PUBLIC})

        store = WrongFailure()
        store.state = _tainted_state(store.state).model_copy(
            update={"last_read_label": Label.INTERNAL}
        )
        halts = []
        with pytest.raises(StoreError) as raised:
            await _gate(store, halts=halts).handle(
                _request(kind=ToolKind.SEND),
                RawResult(failed=True, failure_reason=Reason.POST_DISPATCH_ERROR),
                expected_state=store.state,
            )
        assert raised.value.reason is Reason.STATE_COMMIT_FAILED
        assert halts == [True]

    asyncio.run(scenario())


def test_transport_send_failure_last_label_mismatch_globally_halts_t36():
    async def scenario():
        h = _harness()
        h.store.states["task"] = h.store.states["task"].model_copy(
            update={"last_read_label": Label.INTERNAL}
        )
        original = h.store.commit_request_failure
        h.transport.error = RuntimeError("EXCEPTION_SECRET_MARKER")

        async def invalid_return(req, **fields):
            returned = await original(req, **fields)
            return returned.model_copy(update={"last_read_label": Label.PUBLIC})

        h.store.commit_request_failure = invalid_return
        outcome = await h.guard.submit("task", _call("send"))
        assert isinstance(outcome, OutcomeUnknown)
        assert h.guard._global_halt
        assert len(h.transport.calls) == 1
        assert "EXCEPTION_SECRET_MARKER" not in repr(outcome)

    asyncio.run(scenario())
