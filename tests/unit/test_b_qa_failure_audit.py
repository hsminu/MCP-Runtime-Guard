"""B-QA-07: I5/I6/I8/I9·T07/T36의 예약 후 실패 저장 순서 단위 회귀.

B SQLite와 Guard를 같은 스레드에서 연결해 추가 감사 대기/실패/취소 전에
Task·요청·내구성 감사 커밋이 끝났는지 검사한다. transport는 메모리 대역이며,
여기서 측정하는 call 수는 Guard dispatch다. MCP wire·수신 부작용·C 인수는 별도다.
"""

import asyncio
from collections.abc import Iterator
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Withheld
from guard_app.domain.enums import RequestStatus, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_b_qa_guard_returns import _harness, _wrong_state
from tests.unit.test_guard import _call, _Harness


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[tuple[_Harness, SQLiteStore, GuardService]]:
    h = _harness()
    identifiers = count(1)
    with SQLiteStore(
        tmp_path / "guard.db",
        audit_id_factory=lambda: f"event-{next(identifiers)}",
        utc_now=lambda: h.now,
    ) as store:
        asyncio.run(store.create_task(h.store.states["task"], created_at_utc=h.now))
        guard = GuardService(
            store,
            h.registry,
            h.catalog,
            h.transport,
            h.access,
            profile_id="p",
            policy_version="v",
            operator_id="operator",
            request_id_factory=lambda: f"request-{next(h.ids)}",
            approval_id_factory=lambda: f"approval-{next(h.ids)}",
            next_call_seq=h.sequence,
            failure_audit=h.failure_audit,
            utc_now=lambda: h.now,
            monotonic=lambda: h.monotonic,
        )
        h.transport.error = RuntimeError("EXCEPTION_SECRET_MARKER")
        yield h, store, guard


async def _assert_committed(runtime, name):
    h, store, _ = runtime
    state = await store.load_task("task")
    expected = TaskStatus.HALTED if name == "read" else TaskStatus.UNKNOWN_OUTCOME
    status = RequestStatus.WITHHELD if name == "read" else RequestStatus.UNKNOWN_OUTCOME
    assert state.status is expected
    assert state.high_watermark == h.store.states["task"].high_watermark
    assert state.escalations == h.store.states["task"].escalations
    assert not state.unclassified
    connection = store._owned_connection()
    assert connection.execute("SELECT status FROM requests").fetchall()[0][0] == status.value
    events = connection.execute(
        "SELECT * FROM audit_events WHERE event_type=?", ("REQUEST_" + status.name,)
    ).fetchall()
    assert len(events) == 1
    assert "EXCEPTION_SECRET_MARKER" not in repr(tuple(events[0]))
    assert "RAW_SECRET_MARKER" not in repr(tuple(events[0]))


def _assert_outcome(outcome, name):
    assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
    assert outcome.reasons == (Reason.POST_DISPATCH_ERROR,)
    assert "EXCEPTION_SECRET_MARKER" not in repr(outcome)


@pytest.mark.parametrize("name", ["read", "send"])
def test_extra_audit_waits_after_failure_state_and_durable_audit_commit_t36(runtime, name):
    async def scenario():
        h, _, guard = runtime
        entered, release = asyncio.Event(), asyncio.Event()
        events = []

        async def delayed_audit(event):
            events.append(event)
            entered.set()
            await release.wait()

        guard._failure_audit = delayed_audit
        work = asyncio.create_task(guard.submit("task", _call(name)))
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            assert not work.done()
            await _assert_committed(runtime, name)
        finally:
            release.set()
            outcome = await work
        _assert_outcome(outcome, name)
        assert not guard._global_halt
        assert len(events) == 1
        assert events[0].phase == "after_dispatch"
        assert events[0].exception_type == "RuntimeError"
        assert "EXCEPTION_SECRET_MARKER" not in repr(events)
        assert isinstance(await guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_extra_audit_error_keeps_committed_failure_and_halts_globally_t36(runtime, name):
    async def scenario():
        h, _, guard = runtime
        events = []

        async def failed_audit(event):
            events.append(event)
            raise RuntimeError("AUDIT_SECRET_MARKER")

        guard._failure_audit = failed_audit
        outcome = await guard.submit("task", _call(name))
        _assert_outcome(outcome, name)
        await _assert_committed(runtime, name)
        assert guard._global_halt
        assert len(events) == 1
        assert "AUDIT_SECRET_MARKER" not in repr(outcome) + repr(events)
        assert isinstance(await guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_cancelled_extra_audit_keeps_committed_failure_without_retry_t36(runtime, name):
    async def scenario():
        h, _, guard = runtime
        entered, release = asyncio.Event(), asyncio.Event()
        events = []

        async def blocked_audit(event):
            events.append(event)
            entered.set()
            await release.wait()

        guard._failure_audit = blocked_audit
        work = asyncio.create_task(guard.submit("task", _call(name)))
        await asyncio.wait_for(entered.wait(), timeout=1)
        work.cancel()
        outcome = await work
        _assert_outcome(outcome, name)
        await _assert_committed(runtime, name)
        assert guard._global_halt
        assert len(events) == 1
        assert isinstance(await guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
@pytest.mark.parametrize(
    "field", ["task", "epoch", "policy", "high", "security", "revision", "history_prefix", "status"]
)
def test_unverified_failure_return_skips_extra_audit_and_halts_t36(
    runtime, monkeypatch, name, field
):
    async def scenario():
        h, store, guard = runtime
        original = store.commit_request_failure
        commits = []

        async def invalid_return(req, **fields):
            commits.append(req.request_id)
            returned = _wrong_state(await original(req, **fields), field)
            if field in ("security", "revision"):
                attribute = "security_version" if field == "security" else "row_revision"
                returned = returned.model_copy(
                    update={attribute: getattr(h.store.states["task"], attribute) - 1}
                )
            return returned

        monkeypatch.setattr(store, "commit_request_failure", invalid_return)
        outcome = await guard.submit("task", _call(name))
        _assert_outcome(outcome, name)
        await _assert_committed(runtime, name)
        assert guard._global_halt
        assert commits == ["request-1"]
        assert h.audit == []
        assert isinstance(await guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_failure_transaction_error_skips_extra_audit_and_halts_t36(runtime, name):
    async def scenario():
        h, store, guard = runtime
        connection = store._owned_connection()
        connection.execute(
            """CREATE TEMP TRIGGER reject_failure_audit BEFORE INSERT ON audit_events
            WHEN NEW.event_type IN ('REQUEST_WITHHELD','REQUEST_UNKNOWN_OUTCOME')
            BEGIN SELECT RAISE(ABORT,'AUDIT_SECRET_MARKER'); END"""
        )
        outcome = await guard.submit("task", _call(name))
        _assert_outcome(outcome, name)
        assert guard._global_halt
        assert h.audit == []
        assert (await store.load_task("task")).status is TaskStatus.RUNNING
        assert connection.execute("SELECT status FROM requests").fetchone()[0] == "DISPATCHING"
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 2
        assert isinstance(await guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())
