"""B2.9: I2/I3/I6/I7/I9, T04/T18; synthetic stubs, no wire proof."""

import asyncio
import gc
import inspect
import threading
import warnings
from types import SimpleNamespace

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, Label, RegStatus, TaskStatus
from guard_app.domain.models import AccessResult
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import PendingResultError
from guard_app.services.guard import GuardConfigError, GuardService, PolicyChangeError
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_policy_change import _PolicyHarness


class CountingChecker:
    def __init__(self, produce):
        self.produce = produce
        self.calls = []

    def check_access(self, req, ctx):
        self.calls.append((req, ctx, asyncio.current_task(), threading.get_ident()))
        return self.produce()


class AsyncChecker:
    async def check_access(self, req, ctx):
        return AccessResult(allowed=True, reason="", error=False)


def _install(harness, produce):
    checker = CountingChecker(produce)
    # Constructor acceptance is tested separately. This injection lets each case
    # inspect the precise return or exception from the evaluation boundary.
    harness.guard._access_checker = checker
    return checker


def _build_guard(harness, checker):
    return GuardService(
        harness.store,
        harness.registry,
        harness.catalog,
        harness.transport,
        checker,
        profile_id="p",
        policy_version="v",
        operator_id="operator",
        request_id_factory=lambda: f"request-{next(harness.ids)}",
        approval_id_factory=lambda: f"approval-{next(harness.ids)}",
        next_call_seq=harness.sequence,
        failure_audit=harness.failure_audit,
        utc_now=lambda: harness.now,
        monotonic=lambda: harness.monotonic,
    )


@pytest.mark.parametrize(
    ("label", "tool", "access", "outcome_type", "reason", "dispatch_count"),
    [
        (
            Label.PUBLIC,
            "read",
            AccessResult(allowed=True, reason="allow", error=False),
            Released,
            Reason.OK,
            1,
        ),
        (
            Label.PUBLIC,
            "read",
            AccessResult(allowed=False, reason="deny", error=False),
            Denied,
            Reason.AGT_DENY,
            0,
        ),
        (
            Label.PUBLIC,
            "read",
            AccessResult(allowed=False, reason="error", error=True),
            Denied,
            Reason.AGT_ERROR,
            0,
        ),
        (
            Label.INTERNAL,
            "send",
            AccessResult(allowed=True, reason="allow", error=False),
            Pending,
            Reason.INTERNAL_TO_EXTERNAL,
            0,
        ),
        (
            Label.INTERNAL,
            "send",
            AccessResult(allowed=False, reason="deny", error=False),
            Denied,
            Reason.AGT_DENY,
            0,
        ),
    ],
)
def test_sync_access_composes_with_guard_without_weakening_ask(
    label, tool, access, outcome_type, reason, dispatch_count
):
    async def scenario():
        h = _Harness(label=label, source_label=Label.PUBLIC)
        expected_task, expected_thread = asyncio.current_task(), threading.get_ident()
        checker = _install(h, lambda: access)
        recorded = []
        original_record = h.store.record_decision

        async def record_decision(req, decision, **kwargs):
            recorded.append(decision)
            return await original_record(req, decision, **kwargs)

        h.store.record_decision = record_decision
        result = await h.guard.submit("task", _call(tool))
        assert isinstance(result, outcome_type)
        assert len(recorded) == 1
        assert recorded[0].reasons == (reason,)
        if not isinstance(result, Released):
            assert result.reasons == (reason,)
        assert len(checker.calls) == 1
        request, context, observed_task, observed_thread = checker.calls[0]
        assert request.task_id == context.state.task_id == "task"
        assert request.epoch == context.state.epoch == 1
        assert context.operator_id == "operator"
        assert observed_task is expected_task
        assert observed_thread == expected_thread
        assert len(h.transport.calls) == dispatch_count
        if isinstance(result, Pending):
            assert h.store.consume_count == 0
        await h.guard.aclose()

    asyncio.run(scenario())


def test_preliminary_deny_never_calls_access_or_transport():
    async def scenario():
        h = _Harness(label=Label.CONFIDENTIAL)
        checker = _install(h, lambda: pytest.fail("preliminary DENY called checker"))
        result = await h.guard.submit("task", _call("send"))
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.LABEL_EXCEEDS_BOUNDARY,)
        assert checker.calls == []
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_one_call_for_submit_and_one_for_approval_recheck():
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        checker = _install(h, lambda: AccessResult(allowed=True, reason="", error=False))
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        outcome = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(outcome, Released)
        assert len(checker.calls) == 2
        assert len(h.transport.calls) == 1
        assert checker.calls[0][1].state.status is TaskStatus.RUNNING
        assert checker.calls[1][1].state.status is TaskStatus.WAITING_APPROVAL
        assert h.store.consume_count == 1
        await h.guard.aclose()

    asyncio.run(scenario())


def test_checker_exception_is_denied_and_audit_keeps_only_exception_type(caplog):
    async def scenario():
        h = _Harness()

        def fail():
            raise ValueError("PRIVATE_CHECKER_ERROR")

        checker = _install(h, fail)
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        assert len(h.audit) == 1
        event = h.audit[0]
        assert event.exception_type == "ValueError"
        assert event.phase == "before_dispatch"
        assert event.reason is Reason.INTERNAL_ERROR
        assert "PRIVATE_CHECKER_ERROR" not in repr(event) + repr(outcome) + caplog.text
        await h.guard.aclose()

    asyncio.run(scenario())


def test_checker_cancel_is_contained_once_as_denied_without_message(caplog):
    async def scenario():
        h = _Harness()

        def fail():
            raise asyncio.CancelledError("PRIVATE_CHECKER_CANCELLATION")

        checker = _install(h, fail)
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert "PRIVATE_CHECKER_CANCELLATION" not in repr(outcome)
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        assert not any(entry[0] == "reserve" for entry in h.store.trace)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert len(h.audit) == 1
        assert h.audit[0].exception_type == "CancelledError"
        assert h.audit[0].phase == "before_dispatch"
        assert h.audit[0].reason is Reason.INTERNAL_ERROR
        assert "PRIVATE_CHECKER_CANCELLATION" not in repr(h.audit) + caplog.text
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


def test_checker_cancel_during_approval_recheck_denies_and_notifies_once(monkeypatch, caplog):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        checker = _install(h, lambda: AccessResult(allowed=True, reason="", error=False))
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        completions = []
        original_complete = handle._complete

        def count_completion(value):
            accepted = original_complete(value)
            if accepted:
                completions.append(value)
            return accepted

        monkeypatch.setattr(handle, "_complete", count_completion)

        def fail():
            raise asyncio.CancelledError("PRIVATE_APPROVAL_CANCELLATION")

        checker.produce = fail
        outcome = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert "PRIVATE_APPROVAL_CANCELLATION" not in repr(outcome)
        final = await asyncio.wait_for(handle.wait(), 1)
        assert isinstance(final.outcome, Denied)
        assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert final.cause == "failed"
        assert len(completions) == 1
        with pytest.raises(PendingResultError):
            await handle.wait()
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.store.approvals[pending.approval_id].status is ApprovalStatus.INVALIDATED
        assert h.store.consume_count == 0
        assert not any(entry[0] == "reserve" for entry in h.store.trace)
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        assert len(checker.calls) == 2
        assert h.transport.calls == []
        assert len(h.audit) == 1
        assert h.audit[0].exception_type == "CancelledError"
        assert h.audit[0].phase == "before_dispatch"
        assert h.audit[0].reason is Reason.INTERNAL_ERROR
        assert "PRIVATE_APPROVAL_CANCELLATION" not in repr(h.audit) + caplog.text
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


def test_checker_cancel_with_failed_audit_halts_globally_and_denies():
    async def scenario():
        h = _Harness()
        h.audit_error = RuntimeError("PRIVATE_AUDIT_FAILURE")

        def fail():
            raise asyncio.CancelledError("PRIVATE_CHECKER_CANCELLATION")

        _install(h, fail)
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert "PRIVATE_" not in repr(outcome)
        assert h.guard._global_halt
        assert h.transport.calls == []
        assert h.store.states["task"].status is TaskStatus.HALTED
        await h.guard.aclose()

    asyncio.run(scenario())


def test_checker_cancel_with_failed_approval_invalidation_halts_globally(monkeypatch):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        checker = _install(h, lambda: AccessResult(allowed=True, reason="", error=False))
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)

        def fail_checker():
            raise asyncio.CancelledError("PRIVATE_CHECKER_CANCELLATION")

        async def fail_invalidation(*args, **kwargs):
            raise RuntimeError("PRIVATE_INVALIDATION_FAILURE")

        checker.produce = fail_checker
        with monkeypatch.context() as patch:
            patch.setattr(h.guard._approvals, "invalidate_task", fail_invalidation)
            outcome = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert "PRIVATE_" not in repr(outcome)
        assert h.guard._global_halt
        assert h.transport.calls == []
        assert h.store.consume_count == 0
        assert not h.guard._task_lock("task").locked()
        final = await asyncio.wait_for(handle.wait(), 1)
        assert isinstance(final.outcome, Denied)
        await h.guard.aclose()

    asyncio.run(scenario())


class AccessResultSubclass(AccessResult):
    pass


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"allowed": True, "error": False},
        True,
        "ALLOW",
        AccessResultSubclass(allowed=True, reason="", error=False),
    ],
)
def test_non_exact_access_result_is_error_deny(invalid):
    async def scenario():
        h = _Harness()
        checker = _install(h, lambda: invalid)
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.AGT_ERROR,)
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_coroutine_return_is_closed_and_never_warns():
    async def scenario():
        h = _Harness()
        created = []

        async def accidental_coroutine():
            return AccessResult(allowed=True, reason="", error=False)

        def return_coroutine():
            coroutine = accidental_coroutine()
            created.append(coroutine)
            return coroutine

        checker = _install(h, return_coroutine)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = await h.guard.submit("task", _call())
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.AGT_ERROR,)
            assert len(checker.calls) == 1
            assert h.transport.calls == []
            assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED
            created.clear()
            gc.collect()
        await h.guard.aclose()

    asyncio.run(scenario())


def test_cancel_during_coroutine_close_is_contained_as_denied():
    async def scenario():
        h = _Harness()
        created = []

        async def invalid_coroutine():
            try:
                await asyncio.sleep(0)
            finally:
                raise asyncio.CancelledError("PRIVATE_CLOSE_CANCELLATION")

        def return_started_coroutine():
            coroutine = invalid_coroutine()
            coroutine.send(None)
            created.append(coroutine)
            return coroutine

        checker = _install(h, return_started_coroutine)
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert "PRIVATE_CLOSE_CANCELLATION" not in repr(outcome)
        assert inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        assert not any(entry[0] == "reserve" for entry in h.store.trace)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert len(h.audit) == 1
        assert h.audit[0].exception_type == "CancelledError"
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("checker", [AsyncChecker(), SimpleNamespace(check_access=None), object()])
def test_guard_constructor_rejects_async_or_missing_checker(checker):
    h = _Harness()
    with pytest.raises(GuardConfigError):
        _build_guard(h, checker)
    assert h.store.trace == []
    assert h.transport.calls == []
    assert h.audit == []


@pytest.mark.parametrize("checker", [AsyncChecker(), SimpleNamespace(check_access=None), object()])
def test_policy_change_rejects_async_or_missing_checker_without_side_effects(checker):
    async def scenario():
        h = _PolicyHarness()
        change = h.prepare()
        before = (
            h.guard._catalog,
            h.guard._access_checker,
            h.guard._policy_version,
            h.store.states.copy(),
            h.store.trace.copy(),
        )
        with pytest.raises(PolicyChangeError):
            await h.guard.apply_policy_change(change, checker, task_source=h.source)
        assert before == (
            h.guard._catalog,
            h.guard._access_checker,
            h.guard._policy_version,
            h.store.states,
            h.store.trace,
        )
        assert h.source_calls == 0
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("exception", [asyncio.CancelledError, KeyboardInterrupt])
def test_direct_evaluation_does_not_swallow_base_exceptions(exception):
    async def scenario():
        h = _Harness()
        state = h.store.states["task"]
        req = h.catalog.resolve(state, _call(), request_id="r", call_seq=1)

        def fail():
            raise exception("PRIVATE_BASE_EXCEPTION")

        checker = _install(h, fail)
        with pytest.raises(exception):
            await h.guard._evaluate_request(req, state, phase="submit")
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_checker_other_base_exceptions_propagate_from_submit(exception, caplog):
    async def scenario():
        h = _Harness()

        def fail():
            raise exception("PRIVATE_BASE_EXCEPTION")

        checker = _install(h, fail)
        with pytest.raises(exception):
            await h.guard.submit("task", _call())
        assert len(checker.calls) == 1
        assert h.transport.calls == []
        assert not any(entry[0] == "reserve" for entry in h.store.trace)
        assert "PRIVATE_BASE_EXCEPTION" not in repr(h.audit) + caplog.text
        await h.guard.aclose()

    asyncio.run(scenario())


def test_sync_access_uses_current_task_and_never_starts_thread_or_task(monkeypatch):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        checker = _install(h, lambda: AccessResult(allowed=True, reason="", error=False))
        current, thread_id = asyncio.current_task(), threading.get_ident()
        created = []
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        original_start = threading.Thread.start

        def count_task(*args, **kwargs):
            created.append("asyncio_task")
            return original_create_task(*args, **kwargs)

        def count_thread(thread):
            created.append("thread")
            return original_start(thread)

        with monkeypatch.context() as patch:
            patch.setattr(loop, "create_task", count_task)
            patch.setattr(threading.Thread, "start", count_thread)
            outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Released)
        assert len(checker.calls) == 1
        assert checker.calls[0][2:] == (current, thread_id)
        assert created == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["submit", "approval_recheck"])
@pytest.mark.parametrize(
    ("change", "submit_reason", "approval_reason"),
    [
        ("missing", Reason.TOOL_FINGERPRINT_CHANGED, Reason.TOOL_FINGERPRINT_CHANGED),
        ("changed", Reason.TOOL_FINGERPRINT_CHANGED, Reason.APPROVAL_STALE),
        ("blocked", Reason.REG_NOT_ACTIVE, Reason.APPROVAL_STALE),
        ("pending", Reason.REG_NOT_ACTIVE, Reason.APPROVAL_STALE),
    ],
)
def test_synchronous_access_rechecks_registry_before_reservation(
    phase, change, submit_reason, approval_reason
):
    """Change the queried Registry inside check_access; no scheduling gap is needed."""

    async def scenario():
        label = Label.PUBLIC if phase == "submit" else Label.INTERNAL
        h = _Harness(label=label, source_label=Label.PUBLIC)
        calls = []
        reservations = []
        original_reserve = h.store.reserve_dispatch
        original_consume = h.store.consume_and_reserve

        async def reserve(*args, **kwargs):
            reservations.append("reserve_dispatch")
            return await original_reserve(*args, **kwargs)

        async def consume(*args, **kwargs):
            reservations.append("consume_and_reserve")
            return await original_consume(*args, **kwargs)

        h.store.reserve_dispatch = reserve
        h.store.consume_and_reserve = consume
        pending = None
        handle = None
        if phase == "approval_recheck":
            pending = await h.guard.submit("task", _call("send"))
            assert isinstance(pending, Pending)
            handle = h.guard.pending_result(pending.request_id)
            assert h.store.states["task"].status is TaskStatus.WAITING_APPROVAL

        expected_task = asyncio.current_task()
        expected_thread = threading.get_ident()

        class MutatingChecker:
            def check_access(self, req, ctx):
                calls.append(
                    (
                        req.tool.tool_name,
                        ctx.state.status,
                        asyncio.current_task(),
                        threading.get_ident(),
                    )
                )
                if change == "missing":
                    h.registry.current_fingerprint = None
                elif change == "changed":
                    h.registry.current_fingerprint = "b" * 64
                else:
                    status = RegStatus.BLOCKED if change == "blocked" else RegStatus.PENDING
                    h.registry.record = h.registry.record.model_copy(update={"status": status})
                return AccessResult(allowed=True, reason="ACCESS_RULE_ALLOW", error=False)

        h.guard._access_checker = MutatingChecker()
        if pending is None:
            outcome = await h.guard.submit("task", _call("read"))
        else:
            outcome = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(outcome, Denied)
        assert outcome.reasons == (submit_reason if pending is None else approval_reason,)
        assert len(calls) == 1
        assert calls[0][0] == ("read" if pending is None else "send")
        assert calls[0][1] is (
            TaskStatus.RUNNING if pending is None else TaskStatus.WAITING_APPROVAL
        )
        assert calls[0][2] is expected_task
        assert calls[0][3] == expected_thread
        assert reservations == []
        assert h.store.consume_count == 0
        assert h.transport.calls == []
        assert not any(event[0] == "reserve" for event in h.store.trace)
        if pending is not None:
            assert h.store.approvals[pending.approval_id].status is ApprovalStatus.INVALIDATED
            assert h.store.states["task"].status is TaskStatus.HALTED
            assert handle is not None
            final = await asyncio.wait_for(handle.wait(), timeout=1)
            assert isinstance(final.outcome, Denied)
            assert final.outcome.reasons == (approval_reason,)
        await h.guard.aclose()

    asyncio.run(scenario())
