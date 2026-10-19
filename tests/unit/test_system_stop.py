"""STOP-01·T20/T41/T42의 B 단위 검증(I2/I5/I6/I8/I9). A/C 인수는 별도다."""

import asyncio
import json
from datetime import timedelta
from itertools import count

import pytest
from pydantic import ValidationError

from guard_app.core.model_messages import ModelMessageKind, fixed_model_message
from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, TaskIdentity, Withheld
from guard_app.domain.enums import (
    ApprovalStatus,
    Label,
    RequestStatus,
    SessionEvent,
    SystemStopReason,
    TaskStatus,
    TrustTier,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import PendingResultError
from guard_app.services.guard import GuardConfigError, SystemControl, SystemControlError
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import NOW, _call, _Harness
from tests.unit.test_pending_notifications import _single_confirmation
from tests.unit.test_policy_change import _PolicyHarness

IDENTITY = TaskIdentity(task_id="task", epoch=1)
STOP = SystemStopReason.WATCHDOG_EXCEEDED


class _Audit:
    def __init__(self):
        self.events = []
        self.error = None

    async def record_session_event(self, identity, event, *, at_utc, reason=None):
        if self.error is not None:
            raise self.error
        self.events.append((identity, event, at_utc, reason))


async def _turns():
    for _ in range(12):
        await asyncio.sleep(0)


def test_task_identity_and_capability_require_trusted_construction():
    for fields in (
        {"task_id": "", "epoch": 1},
        {"task_id": "task", "epoch": True},
        {"task_id": "task", "epoch": -1},
        {"task_id": "task", "epoch": "1"},
        {"task_id": "task", "epoch": 1, "question": "PRIVATE_QUESTION"},
    ):
        with pytest.raises(ValidationError):
            TaskIdentity.model_validate(fields)
    with pytest.raises(ValidationError):
        IDENTITY.epoch = 2
    h = _Harness()
    audit = _Audit()
    with pytest.raises(GuardConfigError):
        SystemControl(h.guard, audit, object())
    with pytest.raises(GuardConfigError):
        h.guard.system_control(object())
    human = h.guard.human_control("operator")
    system = h.guard.system_control(audit)
    for name in ("system_control", "stop_task", "record_request_user_input"):
        assert not hasattr(human, name)
    for name in ("approve", "review", "cancel", "emergency_halt", "human_control"):
        assert not hasattr(system, name)
    assert "PRIVATE_QUESTION" not in repr(system)


@pytest.mark.parametrize("reason", list(SystemStopReason))
def test_system_reasons_have_explicit_private_model_messages(reason):
    code = Reason(reason.value)
    assert fixed_model_message(ModelMessageKind.DENIED, code) == (
        False,
        "이 요청은 정책에 의해 차단되었습니다",
    )
    assert fixed_model_message(ModelMessageKind.STOPPED, code) == (False, "작업이 정지되었습니다")


@pytest.mark.parametrize("reason", list(SystemStopReason))
def test_each_stop_reason_commits_state_and_audit_and_leaves_other_task_alone(reason):
    async def scenario():
        h, audit = _Harness(), _Audit()
        other = h.store.states["task"].model_copy(update={"task_id": "other"})
        h.store.states["other"] = other
        before = h.store.states["task"]
        state = await h.guard.system_control(audit).stop_task(IDENTITY, reason)
        assert state == h.store.states["task"]
        assert state.status is TaskStatus.HALTED
        assert state.high_watermark == before.high_watermark
        assert state.policy_version == before.policy_version
        assert audit.events == [(IDENTITY, SessionEvent.STOPPED, NOW, reason)]
        assert h.store.states["other"] == other
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert h.transport.calls == []
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [STOP.value, Reason.SESSION_WATCHDOG_EXCEEDED, True, None, {}])
def test_model_strings_and_wrong_enum_do_not_stop_anything(reason):
    async def scenario():
        h, audit = _Harness(), _Audit()
        before = h.store.states["task"]
        with pytest.raises(SystemControlError):
            await h.guard.system_control(audit).stop_task(IDENTITY, reason)
        assert h.store.states["task"] == before
        assert not h.guard._system_stop_requests
        assert not audit.events
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "identity",
    [
        {"task_id": "task", "epoch": 1},
        "task",
        TaskIdentity.model_construct(task_id="task", epoch=True),
        TaskIdentity.model_construct(task_id="\ud800", epoch=1),
    ],
)
def test_unvalidated_identity_is_rejected_without_mutation(identity):
    async def scenario():
        h, audit = _Harness(), _Audit()
        with pytest.raises(SystemControlError):
            await h.guard.system_control(audit).stop_task(identity, STOP)
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert not audit.events
        await h.guard.aclose()

    asyncio.run(scenario())


def test_pending_stop_and_repeat_notify_exactly_once(monkeypatch):
    async def scenario():
        h, audit = _Harness(label=Label.INTERNAL), _Audit()
        system = h.guard.system_control(audit)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        confirmations = _single_confirmation(monkeypatch, handle)
        stopped = await system.stop_task(IDENTITY, STOP)
        repeated = await system.stop_task(IDENTITY, STOP)
        assert repeated == stopped
        final = await handle.wait()
        assert final.cause == "invalidated"
        assert final.outcome.reasons == (Reason(STOP.value),)
        assert h.store.approvals[pending.approval_id].status is ApprovalStatus.CANCELLED
        assert len(confirmations) == 1
        assert len(audit.events) == 2
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        assert isinstance(
            await h.guard.human_control("operator").approve(pending.approval_id), Denied
        )
        assert h.transport.calls == []
        with pytest.raises(PendingResultError):
            await handle.wait()
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["read", "send"])
@pytest.mark.parametrize("approved", [False, True])
def test_stop_during_transport_withholds_read_and_preserves_unknown_send(kind, approved):
    async def scenario():
        h = _Harness(label=Label.INTERNAL if approved else Label.PUBLIC)
        if approved and kind == "read":
            h.registry.record = h.registry.record.model_copy(
                update={
                    "boundary": Boundary(
                        boundary_id="docs",
                        tier=TrustTier.GENERAL_EXTERNAL,
                        max_label=Label.INTERNAL,
                    )
                }
            )
        audit = _Audit()
        entered, release = asyncio.Event(), asyncio.Event()

        async def transport(req):
            h.transport.calls.append(req)
            entered.set()
            await release.wait()
            return h.transport.raw

        h.transport.call = transport
        handle = None
        if approved:
            pending = await h.guard.submit("task", _call(kind))
            assert isinstance(pending, Pending)
            handle = h.guard.pending_result(pending.request_id)
            running = asyncio.create_task(
                h.guard.human_control("operator").approve(pending.approval_id)
            )
        else:
            running = asyncio.create_task(h.guard.submit("task", _call(kind)))
        await entered.wait()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        queued = asyncio.create_task(h.guard.submit("task", _call("send")))
        assert not stopped.done()
        release.set()
        outcome, state = await running, await stopped
        assert isinstance(outcome, OutcomeUnknown if kind == "send" else Withheld)
        assert "RAW_SECRET_MARKER" not in repr(outcome)
        assert outcome.reasons == (Reason(STOP.value),)
        assert state.status is (TaskStatus.UNKNOWN_OUTCOME if kind == "send" else TaskStatus.HALTED)
        assert state == h.store.states["task"]
        assert h.store.statuses[outcome.request_id] is (
            RequestStatus.UNKNOWN_OUTCOME if kind == "send" else RequestStatus.WITHHELD
        )
        assert len(h.transport.calls) == 1
        assert isinstance(await queued, Denied)
        if handle is not None:
            final = await handle.wait()
            assert final.cause == "executed"
            assert final.outcome == outcome
        assert await h.guard.system_control(audit).stop_task(IDENTITY, STOP) == state
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("approval_recheck", [False, True])
def test_stop_interrupts_access_before_lock_and_no_dispatch(approval_recheck):
    async def scenario():
        h, audit = _Harness(label=Label.INTERNAL if approval_recheck else Label.PUBLIC), _Audit()
        entered = asyncio.Event()

        async def blocked_access(req, context):
            entered.set()
            await asyncio.Event().wait()

        handle = None
        if approval_recheck:
            pending = await h.guard.submit("task", _call("send"))
            assert isinstance(pending, Pending)
            handle = h.guard.pending_result(pending.request_id)
        h.access.check_access = blocked_access
        running = asyncio.create_task(
            h.guard.human_control("operator").approve(pending.approval_id)
            if approval_recheck
            else h.guard.submit("task", _call())
        )
        await entered.wait()
        state = await h.guard.system_control(audit).stop_task(IDENTITY, STOP)
        assert isinstance(await running, Denied)
        assert state.status is TaskStatus.HALTED
        assert not h.transport.calls
        if handle is not None:
            final = await handle.wait()
            assert final.cause == "invalidated"
            assert final.outcome.reasons == (Reason(STOP.value),)
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


def test_request_user_input_is_fact_only_and_does_not_stop_task():
    async def scenario():
        h, audit = _Harness(), _Audit()
        state = h.store.states["task"]
        system = h.guard.system_control(audit)
        await system.record_request_user_input(IDENTITY)
        assert h.store.states["task"] == state
        assert audit.events == [(IDENTITY, SessionEvent.REQUEST_USER_INPUT, NOW, None)]
        assert not h.guard._system_stop_requests
        with pytest.raises(TypeError):
            await system.record_request_user_input(IDENTITY, question="PRIVATE_QUESTION")
        assert len(audit.events) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["status", "transition", "audit", "load", "clock"])
def test_stop_commit_failure_has_no_success_ack_and_pending_final_is_failed(failure):
    async def scenario():
        h, audit = _Harness(label=Label.INTERNAL), _Audit()
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        if failure == "audit":
            audit.error = RuntimeError("PRIVATE_QUESTION")
        elif failure == "clock":
            h.now = NOW.replace(tzinfo=None)
        else:
            h.store.fail_at.add(failure)
        with pytest.raises(SystemControlError) as error:
            await h.guard.system_control(audit).stop_task(IDENTITY, STOP)
        assert "PRIVATE_QUESTION" not in str(error.value)
        assert h.guard._global_halt
        assert audit.events == []
        final = await handle.wait()
        assert final.cause == "failed"
        assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert not h.transport.calls
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        h.store.fail_at.clear()
        h.now = NOW
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", [TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED]
)
def test_already_terminal_stop_returns_committed_state_without_overwriting(status):
    async def scenario():
        h, audit = _Harness(), _Audit()
        state = h.store.states["task"].model_copy(update={"status": status})
        h.store.states["task"] = state
        assert await h.guard.system_control(audit).stop_task(IDENTITY, STOP) == state
        assert h.store.states["task"] == state
        assert audit.events == [(IDENTITY, SessionEvent.STOPPED, NOW, STOP)]
        assert h.store.trace == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_stale_or_missing_session_does_not_stop_other_life_or_task(missing):
    async def scenario():
        h, audit = _Harness(), _Audit()
        identity = TaskIdentity(task_id="missing" if missing else "task", epoch=0)
        before = dict(h.store.states)
        with pytest.raises(SystemControlError):
            await h.guard.system_control(audit).stop_task(identity, STOP)
        assert h.store.states == before
        assert not h.guard._global_halt
        assert not audit.events
        assert not h.guard._system_stop_requests
        await h.guard.aclose()

    asyncio.run(scenario())


def test_wrong_status_ack_is_a_failure(monkeypatch):
    async def scenario():
        h, audit = _Harness(), _Audit()
        before = h.store.states["task"]

        async def false_ack(*args, **kwargs):
            return before

        monkeypatch.setattr(h.store, "set_task_status", false_ack)
        with pytest.raises(SystemControlError):
            await h.guard.system_control(audit).stop_task(IDENTITY, STOP)
        assert h.guard._global_halt
        assert audit.events == []
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        await h.guard.aclose()

    asyncio.run(scenario())


def test_missing_task_with_pending_context_is_integrity_failure():
    async def scenario():
        h, audit = _Harness(label=Label.INTERNAL), _Audit()
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        del h.store.states["task"]
        with pytest.raises(SystemControlError):
            await h.guard.system_control(audit).stop_task(IDENTITY, STOP)
        assert h.guard._global_halt
        assert (await handle.wait()).cause == "failed"
        assert not h.transport.calls
        assert not audit.events
        await h.guard.aclose()

    asyncio.run(scenario())


def test_stop_while_approval_is_being_saved_notifies_the_new_pending_once(monkeypatch):
    async def scenario():
        h, audit = _Harness(label=Label.INTERNAL), _Audit()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.create_approval

        async def save(approval):
            entered.set()
            await release.wait()
            await original(approval)

        monkeypatch.setattr(h.store, "create_approval", save)
        running = asyncio.create_task(h.guard.submit("task", _call("send")))
        await entered.wait()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        release.set()
        pending = await running
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        assert (await stopped).status is TaskStatus.HALTED
        assert (await handle.wait()).cause == "invalidated"
        assert not h.transport.calls
        assert not h.guard._approvals._memory_snapshots
        await h.guard.aclose()

    asyncio.run(scenario())


def test_cancelled_stop_waiting_for_lock_is_a_fixed_failure_with_dispatch_blocked():
    async def scenario():
        h, audit = _Harness(), _Audit()
        lock = h.guard._task_lock("task")
        await lock.acquire()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        stopped.cancel()
        with pytest.raises(SystemControlError):
            await stopped
        lock.release()
        assert h.guard._global_halt
        assert isinstance(await h.guard.submit("task", _call("send")), Denied)
        assert not h.transport.calls
        assert not audit.events
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["read", "send"])
def test_stop_during_success_commit_hides_body_and_retains_known_result(kind):
    async def scenario():
        h, audit = _Harness(), _Audit()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.commit_observation if kind == "read" else h.store.commit_send_outcome

        async def commit(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        if kind == "read":
            h.store.commit_observation = commit
        else:
            h.store.commit_send_outcome = commit
        running = asyncio.create_task(h.guard.submit("task", _call(kind)))
        await entered.wait()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        release.set()
        outcome, state = await running, await stopped
        assert isinstance(outcome, Withheld)
        assert state.status is TaskStatus.HALTED
        assert h.store.statuses[outcome.request_id] is RequestStatus.SUCCEEDED
        assert "RAW_SECRET_MARKER" not in repr(outcome)
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


def test_failed_send_containment_is_not_overwritten_with_successful_halted_stop():
    async def scenario():
        h, audit = _Harness(), _Audit()
        entered, release = asyncio.Event(), asyncio.Event()

        async def transport(req):
            h.transport.calls.append(req)
            entered.set()
            await release.wait()
            return h.transport.raw

        h.transport.call = transport
        h.store.fail_at.add("failure")
        running = asyncio.create_task(h.guard.submit("task", _call("send")))
        await entered.wait()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        release.set()
        assert isinstance(await running, OutcomeUnknown)
        with pytest.raises(SystemControlError):
            await stopped
        assert h.guard._global_halt
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert h.store.statuses["request-1"] is RequestStatus.DISPATCHING
        assert audit.events == []
        assert len(h.transport.calls) == 1
        assert isinstance(await h.guard.submit("task", _call("send")), Denied)
        h.store.fail_at.clear()
        await h.guard.aclose()

    asyncio.run(scenario())


def test_old_epoch_stop_during_current_access_does_not_interrupt_current_life():
    async def scenario():
        h, audit = _Harness(), _Audit()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.access.check_access

        async def access(req, context):
            entered.set()
            await release.wait()
            return await original(req, context)

        h.access.check_access = access
        running = asyncio.create_task(h.guard.submit("task", _call()))
        await entered.wait()
        stopped = asyncio.create_task(
            h.guard.system_control(audit).stop_task(TaskIdentity(task_id="task", epoch=0), STOP)
        )
        await _turns()
        assert not running.done()
        release.set()
        assert (await running).kind == "released"
        with pytest.raises(SystemControlError):
            await stopped
        assert not h.guard._global_halt
        assert audit.events == []
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


def test_stop_waiting_for_transport_does_not_block_other_task_dispatch():
    async def scenario():
        h, audit = _Harness(), _Audit()
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})
        entered, release = asyncio.Event(), asyncio.Event()

        async def transport(req):
            h.transport.calls.append(req)
            if req.task_id == "task":
                entered.set()
                await release.wait()
            return h.transport.raw

        h.transport.call = transport
        running = asyncio.create_task(h.guard.submit("task", _call("send")))
        await entered.wait()
        stopped = asyncio.create_task(h.guard.system_control(audit).stop_task(IDENTITY, STOP))
        await _turns()
        assert (await h.guard.submit("other", _call())).kind == "released"
        assert h.store.states["other"].status is TaskStatus.RUNNING
        release.set()
        assert isinstance(await running, OutcomeUnknown)
        assert (await stopped).status is TaskStatus.UNKNOWN_OUTCOME
        assert [req.task_id for req in h.transport.calls] == ["task", "other"]
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [False, True])
def test_sqlite_stop_and_fact_audits_are_durable_without_question_or_arguments(tmp_path, pending):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL if pending else Label.PUBLIC)
        ids = count(1)
        with SQLiteStore(
            tmp_path / "session.db", utc_now=lambda: NOW, audit_id_factory=lambda: f"a-{next(ids)}"
        ) as store:
            await store.create_task(h.store.states["task"], created_at_utc=NOW)
            h.guard = h.build_guard(store)
            system = h.guard.system_control(store)
            if pending:
                value = await h.guard.submit("task", _call("send"))
                assert isinstance(value, Pending)
                handle = h.guard.pending_result(value.request_id)
            await system.record_request_user_input(IDENTITY)
            stopped = await system.stop_task(IDENTITY, STOP)
            assert stopped == await store.load_task("task")
            rows = (
                store._owned_connection()
                .execute(
                    "SELECT event_type,reason_code,detail,at_utc FROM audit_events "
                    "WHERE event_type IN ('SESSION_REQUEST_USER_INPUT','SESSION_STOPPED','HALTED') "
                    "ORDER BY rowid"
                )
                .fetchall()
            )
            assert [row[0] for row in rows] == [
                SessionEvent.REQUEST_USER_INPUT.value,
                "HALTED",
                SessionEvent.STOPPED.value,
            ]
            assert [row[1] for row in rows] == [Reason.OK.value, STOP.value, STOP.value]
            assert json.loads(rows[0][2]) == {"epoch": 1}
            assert json.loads(rows[2][2]) == {"epoch": 1}
            assert "PRIVATE_QUESTION" not in repr([tuple(row) for row in rows])
            if pending:
                assert (await handle.wait()).cause == "invalidated"
            await h.guard.aclose()

    asyncio.run(scenario())


def test_sqlite_session_audit_failure_does_not_ack_stop(tmp_path):
    async def scenario():
        h, ids = _PolicyHarness(), count(1)
        with SQLiteStore(
            tmp_path / "failure.db", utc_now=lambda: NOW, audit_id_factory=lambda: f"a-{next(ids)}"
        ) as store:
            await store.create_task(h.store.states["task"], created_at_utc=NOW)
            h.guard = h.build_guard(store)
            store._owned_connection().execute(
                "CREATE TRIGGER fail_session BEFORE INSERT ON audit_events "
                "WHEN NEW.event_type='SESSION_STOPPED' "
                "BEGIN SELECT RAISE(ABORT, 'PRIVATE_QUESTION'); END"
            )
            with pytest.raises(SystemControlError) as error:
                await h.guard.system_control(store).stop_task(IDENTITY, STOP)
            assert "PRIVATE_QUESTION" not in str(error.value)
            assert h.guard._global_halt
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert (
                store._owned_connection()
                .execute("SELECT COUNT(*) FROM audit_events WHERE event_type='SESSION_STOPPED'")
                .fetchone()[0]
                == 0
            )
            await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (SessionEvent.STOPPED, None),
        (SessionEvent.STOPPED, STOP.value),
        (SessionEvent.REQUEST_USER_INPUT, STOP),
        ("PRIVATE_QUESTION", None),
    ],
)
def test_sqlite_rejects_invalid_session_audit_envelopes(tmp_path, event, reason):
    async def scenario():
        ids = count(1)
        with SQLiteStore(
            tmp_path / "invalid.db", audit_id_factory=lambda: f"a-{next(ids)}"
        ) as store:
            await store.create_task(_Harness().store.states["task"], created_at_utc=NOW)
            with pytest.raises(StoreError) as error:
                await store.record_session_event(IDENTITY, event, at_utc=NOW, reason=reason)
            assert error.value.reason is Reason.INVALID_ARGUMENTS

    asyncio.run(scenario())


def test_fact_audit_error_is_not_success_or_question_disclosure():
    async def scenario():
        h, audit = _Harness(), _Audit()
        audit.error = ValueError("PRIVATE_QUESTION")
        with pytest.raises(SystemControlError) as error:
            await h.guard.system_control(audit).record_request_user_input(IDENTITY)
        assert "PRIVATE_QUESTION" not in str(error.value)
        assert h.guard._global_halt
        assert audit.events == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_session_audit_requires_utc_without_extending_source_clock(tmp_path):
    async def scenario():
        ids = count(1)
        with SQLiteStore(tmp_path / "utc.db", audit_id_factory=lambda: f"a-{next(ids)}") as store:
            await store.create_task(_Harness().store.states["task"], created_at_utc=NOW)
            with pytest.raises(StoreError) as error:
                await store.record_session_event(
                    IDENTITY, SessionEvent.REQUEST_USER_INPUT, at_utc=NOW.replace(tzinfo=None)
                )
            assert error.value.reason is Reason.INTERNAL_ERROR
        assert NOW.utcoffset() == timedelta(0)

    asyncio.run(scenario())
