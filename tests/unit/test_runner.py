"""A21/A22: Guard-only dispatch, release boundary, and Task isolation."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime, timedelta

import pytest
from examples.legacy_model_client import (
    MAX_MODEL_CONVERSATION_UTF8_BYTES,
    ModelFormatError,
    ProviderNeutralModelClient,
)
from examples.legacy_runner import RunnerStateError, TaskRunner

from guard_app.domain.contracts import (
    ApprovalRecord,
    Denied,
    ModelTurn,
    OutcomeUnknown,
    Pending,
    Released,
    ReleasedResult,
    ToolView,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService

from .test_task_tool_calls import bounded_test


def _state(task_id: str = "task-1", *, status: TaskStatus = TaskStatus.RUNNING) -> TaskState:
    return TaskState(
        task_id=task_id, epoch=1, profile_id="profile-1", policy_version="policy-1", status=status
    )


def _call(number: int = 1) -> ProposedCall:
    return ProposedCall(
        tool_ref="alpha__read",
        arguments=Arguments({"path": f"item-{number}"}),
        model_call_id=f"call-{number}",
    )


def _view() -> ToolView:
    return ToolView(
        tool_ref="alpha__read",
        input_schema=FrozenJsonObject(
            {"type": "object", "properties": {"path": {"type": "string"}}}
        ),
        description="",
    )


class FakeStore:
    def __init__(self, state: TaskState) -> None:
        self.state = state

    async def load_task(self, task_id: str) -> TaskState:
        assert task_id == self.state.task_id
        return self.state


class FakeCatalog:
    def tool_views(self, server_ids: tuple[str, ...]) -> tuple[ToolView, ...]:
        assert server_ids == ("alpha",)
        return (_view(),)


class FakeModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = deque(turns)
        self.seen = []

    async def step(self, conversation, tools):
        self.seen.append((conversation, tools))
        return self.turns.popleft()


class FakeGuard:
    def __init__(self, *outcomes) -> None:
        self.outcomes = deque(outcomes)
        self.calls = []

    async def submit(self, task_id: str, call: ProposedCall, *, expected_epoch):
        assert expected_epoch == 1
        self.calls.append((task_id, call))
        return self.outcomes.popleft()


def _runner(state: TaskState, model, guard, store=None) -> TaskRunner:
    return TaskRunner(
        state,
        model_client_factory=lambda: model,
        guard=guard,
        catalog=FakeCatalog(),
        store=store or FakeStore(state),
        server_ids=("alpha",),
        system_message="trusted system",
    )


def started_runner(model, guard, *, state=None, store=None, message="read"):
    runner = _runner(state or _state(), model, guard, store)
    runner.add_user_message(message)
    return runner


def scenario(*outcomes, calls=1, answer=None, state=None, store=None, message="read"):
    turns = [ModelTurn(calls=tuple(_call(index + 1) for index in range(calls)))]
    if answer is not None:
        turns.append(ModelTurn(text=answer))
    model, guard = FakeModel(*turns), FakeGuard(*outcomes)
    runner = started_runner(model, guard, state=state, store=store, message=message)
    return runner, model, guard


@bounded_test
async def test_released_content_reaches_next_model_turn_only_after_guard() -> None:
    marker = "synthetic-private-marker"
    runner, model, guard = scenario(_released(marker, label=Label.INTERNAL), answer="done")

    first = await runner.advance()
    second = await runner.advance()
    assert first.status == "continue"
    assert second.status == "complete"
    assert second.assistant_text == "done"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]
    next_conversation = model.seen[1][0]
    assert marker in next_conversation[-1].content
    assert "INTERNAL" not in next_conversation[-1].content
    assert next_conversation[-1].role == "tool"


@bounded_test
async def test_denied_stops_remaining_calls_and_uses_fixed_text() -> None:
    runner, model, guard = scenario(
        Denied(request_id="request-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,)),
        calls=2,
        answer="later",
        message="first user request",
    )

    update = await runner.advance()
    with pytest.raises(RunnerStateError):
        await runner.advance()
    runner.add_user_message("new user request")
    await runner.advance()
    assert update.status == "denied"
    assert len(guard.calls) == 1
    assert model.seen[1][0][-3].content == "이 요청은 실행되지 않았습니다."
    assert model.seen[1][0][-2].content == "이 호출은 실행되지 않았습니다."
    assert "INTERNAL_TO_EXTERNAL" not in model.seen[1][0][-3].content


@bounded_test
async def test_guard_policy_mismatch_halt_is_returned_without_new_user_turn() -> None:
    """T29: the real Guard halts before returning Denied on policy change."""
    state = _state()

    class HaltingStore(FakeStore):
        async def set_task_status(
            self,
            task_id: str,
            status: TaskStatus,
            *,
            expected_row_revision: int,
            at_utc: datetime,
            reason: Reason,
        ) -> TaskState:
            assert task_id == state.task_id
            assert status is TaskStatus.HALTED
            assert reason is Reason.POLICY_CHANGED
            assert expected_row_revision == self.state.row_revision
            assert at_utc.tzinfo is not None
            self.state = self.state.model_copy(
                update={"status": status, "row_revision": expected_row_revision + 1}
            )
            return self.state

    async def unused_next_call_seq(task_id: str) -> int:
        raise AssertionError("policy mismatch must deny before resolving the call")

    async def unused_failure_audit(event: object) -> None:
        raise AssertionError("policy mismatch is an expected denial")

    class UnusedAccessChecker:
        def check_access(self, req: object, ctx: object) -> None:
            raise AssertionError("policy mismatch must deny before access evaluation")

    store = HaltingStore(state)
    guard = GuardService(
        store,
        object(),
        FakeCatalog(),
        object(),
        UnusedAccessChecker(),
        profile_id=state.profile_id,
        policy_version="policy-2",
        operator_id="operator-1",
        request_id_factory=lambda: "request-1",
        approval_id_factory=lambda: "approval-1",
        next_call_seq=unused_next_call_seq,
        failure_audit=unused_failure_audit,
        monotonic=time.monotonic,
    )
    model = FakeModel(ModelTurn(calls=(_call(),)))
    runner = started_runner(model, guard, state=state, store=store)

    update = await runner.advance()
    assert store.state.status is TaskStatus.HALTED
    assert update.status == "halted"
    assert len(model.seen) == 1
    pytest.raises(RunnerStateError, runner.add_user_message, "another request")
    with pytest.raises(RunnerStateError):
        await runner.advance()


@bounded_test
async def test_denied_after_concurrent_task_change_rejects_new_input(subtests):

    async def check(replacement, expected):
        """T20/T29: a control or policy change can land while Guard is busy."""
        entered = asyncio.Event()
        release = asyncio.Event()
        state = _state()
        store = FakeStore(state)

        class DelayedDeniedGuard:
            calls = 0

            async def submit(
                self, task_id: str, call: ProposedCall, *, expected_epoch: int
            ) -> Denied:
                assert expected_epoch == state.epoch
                self.calls += 1
                entered.set()
                await release.wait()
                return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))

        guard = DelayedDeniedGuard()
        model = FakeModel(ModelTurn(calls=(_call(1), _call(2))))
        runner = started_runner(model, guard, state=state, store=store, message="read both")

        running = asyncio.create_task(runner.advance())
        await entered.wait()
        store.state = state.model_copy(update=replacement)
        release.set()
        update = await running
        pytest.raises(RunnerStateError, runner.add_user_message, "new input after cancellation")
        with pytest.raises(RunnerStateError):
            await runner.advance()
        assert update.status == expected
        assert guard.calls == 1
        assert len(model.seen) == 1

    for replacement, expected in [
        ({"status": TaskStatus.HALTED}, "halted"),
        ({"status": TaskStatus.UNKNOWN_OUTCOME}, "outcome_unknown"),
        ({"status": TaskStatus.CLOSED}, "stopped"),
        ({"epoch": 2}, "stopped"),
        ({"policy_version": "policy-2"}, "stopped"),
    ]:
        with subtests.test(replacement=replacement, expected=expected):
            await check(replacement, expected)


@bounded_test
async def test_pending_requires_matching_backend_result_and_resumes_remaining_calls() -> None:
    runner, model, guard = scenario(
        _pending(), _released("second", request_id="request-2"), calls=2, message="read both"
    )

    pending = await runner.advance()
    with pytest.raises(RunnerStateError):
        await runner.advance()
    with pytest.raises(RunnerStateError):
        await runner.resume_approval("task-1", 2, "approval-1", _released("first"))
    resumed = await runner.resume_approval("task-1", 1, "approval-1", _released("first"))
    assert pending.status == "pending" and pending.approval_id == "approval-1"
    assert resumed.status == "continue"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1", "call-2"]
    assert len(model.seen) == 1


@bounded_test
async def test_premature_expiry_race_keeps_approval_pending_until_actual_release() -> None:
    """B can return APPROVAL_RACE while approval and Task are still waiting."""
    state = _state()
    store = FakeStore(state)
    runner, model, guard = scenario(
        _pending(),
        _released("second", request_id="request-2"),
        calls=2,
        answer="done",
        state=state,
        store=store,
        message="read both",
    )
    assert (await runner.advance()).status == "pending"
    store.state = state.model_copy(update={"status": TaskStatus.WAITING_APPROVAL})
    race = await runner.resume_approval(
        "task-1", 1, "approval-1", Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,))
    )
    pytest.raises(RunnerStateError, runner.add_user_message, "try to bypass approval")
    with pytest.raises(RunnerStateError):
        await runner.advance()
    assert len(guard.calls) == 1
    store.state = state
    released = await runner.resume_approval("task-1", 1, "approval-1", _released("first"))
    assert (await runner.advance()).status == "complete"
    assert race.status == "pending" and race.approval_id == "approval-1"
    assert released.status == "continue"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1", "call-2"]
    resumed_messages = model.seen[1][0]
    assert [message.content for message in resumed_messages[-2:]] == [
        '{"text":"first","structured_content":null,"is_error":false}',
        '{"text":"second","structured_content":null,"is_error":false}',
    ]


@bounded_test
async def test_approval_race_does_not_hide_later_confirmed_rejection() -> None:
    state = _state()
    store = FakeStore(state)
    runner, model, guard = scenario(_pending(), answer="later", state=state, store=store)
    assert (await runner.advance()).status == "pending"
    store.state = state.model_copy(update={"status": TaskStatus.WAITING_APPROVAL})
    race = await runner.resume_approval(
        "task-1", 1, "approval-1", Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,))
    )
    store.state = state
    rejected = await runner.resume_approval(
        "task-1", 1, "approval-1", Denied(request_id="request-1", reasons=(Reason.OK,))
    )
    runner.add_user_message("new request")
    assert (await runner.advance()).status == "complete"
    assert race.status == "pending"
    assert rejected.status == "denied"
    assert len(guard.calls) == 1
    assert model.seen[1][0][-2].content == "이 요청은 실행되지 않았습니다."


@bounded_test
async def test_approval_denial_after_policy_halt_does_not_offer_new_turn() -> None:
    state = _state()
    store = FakeStore(state)
    runner, model, guard = scenario(_pending(), state=state, store=store)
    assert (await runner.advance()).status == "pending"
    store.state = state.model_copy(update={"status": TaskStatus.HALTED})
    halted = await runner.resume_approval(
        "task-1", 1, "approval-1", Denied(request_id="request-1", reasons=(Reason.POLICY_CHANGED,))
    )
    pytest.raises(RunnerStateError, runner.add_user_message, "new request")
    assert halted.status == "halted"
    assert (len(model.seen), len(guard.calls)) == (1, 1)


@bounded_test
async def test_approval_race_after_task_stop_is_not_reported_pending(subtests):

    for replacement, expected in [
        ({"status": TaskStatus.HALTED}, "halted"),
        ({"status": TaskStatus.UNKNOWN_OUTCOME}, "outcome_unknown"),
        ({"epoch": 2}, "stopped"),
    ]:
        with subtests.test(replacement=replacement, expected=expected):
            """T20: APPROVAL_RACE cannot hide a concurrent cancel or stale Task."""
            state = _state()
            store = FakeStore(state)
            guard = FakeGuard(_pending())
            model = FakeModel(ModelTurn(calls=(_call(),)))
            runner = started_runner(model, guard, state=state, store=store)
            assert (await runner.advance()).status == "pending"
            store.state = state.model_copy(update=replacement)
            update = await runner.resume_approval(
                state.task_id,
                state.epoch,
                "approval-1",
                Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,)),
            )
            pytest.raises(RunnerStateError, runner.add_user_message, "new input")
            with pytest.raises(RunnerStateError):
                await runner.advance()
            assert update.status == expected
            assert (len(model.seen), len(guard.calls)) == (1, 1)


@bounded_test
async def test_nonreleased_result_never_enters_model_context(subtests):

    async def check(outcome, store_status, expected):
        store = FakeStore(_state())

        class CommittingGuard(FakeGuard):
            async def submit(self, task_id, call, *, expected_epoch):
                store.state = _state(status=store_status)
                return await super().submit(task_id, call, expected_epoch=expected_epoch)

        model = FakeModel(ModelTurn(calls=(_call(1), _call(2))))
        guard = CommittingGuard(outcome)
        runner = started_runner(model, guard, store=store)

        update = await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.advance()
        assert update.status == expected
        assert (len(guard.calls), len(model.seen)) == (1, 1)

    for outcome, store_status, expected in [
        (
            Withheld(request_id="request-1", reasons=(Reason.STATE_COMMIT_FAILED,)),
            TaskStatus.HALTED,
            "halted",
        ),
        (
            OutcomeUnknown(request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,)),
            TaskStatus.UNKNOWN_OUTCOME,
            "outcome_unknown",
        ),
        (
            # A SEND uncertainty stops even while Store still says RUNNING.
            OutcomeUnknown(request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,)),
            TaskStatus.RUNNING,
            "outcome_unknown",
        ),
    ]:
        with subtests.test(outcome=outcome, store_status=store_status, expected=expected):
            await check(outcome, store_status, expected)


@bounded_test
async def test_halted_task_does_not_call_model() -> None:
    initial = _state()
    store = FakeStore(_state(status=TaskStatus.HALTED))
    model = FakeModel(ModelTurn(text="must not run"))
    runner = started_runner(model, FakeGuard(), state=initial, store=store)
    update = await runner.advance()
    assert update.status == "halted"
    assert model.seen == []


@bounded_test
async def test_new_task_has_no_prior_conversation_and_stale_approval_is_rejected() -> None:
    marker = "synthetic-private-marker"
    first_model = FakeModel(ModelTurn(calls=(_call(),)))
    first = _runner(
        _state("task-1"), first_model, FakeGuard(_released(marker, label=Label.CONFIDENTIAL))
    )
    first.add_user_message("read")
    await first.advance()
    first.close()

    second_model = FakeModel(ModelTurn(text="fresh"))
    second = _runner(_state("task-2"), second_model, FakeGuard())
    second.add_user_message("fresh input")

    with pytest.raises(RunnerStateError):
        await second.resume_approval(
            "task-1",
            1,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.APPROVAL_STALE,)),
        )
    assert (await second.advance()).status == "complete"
    assert marker not in tuple(message.content for message in second_model.seen[0][0])


@bounded_test
async def test_task_construction_calls_model_factory_for_each_task() -> None:
    created = []

    def factory():
        model = FakeModel(ModelTurn(text="done"))
        created.append(model)
        return model

    for task_id in ("task-1", "task-2"):
        state = _state(task_id)
        runner = TaskRunner(
            state,
            model_client_factory=factory,
            guard=FakeGuard(),
            catalog=FakeCatalog(),
            store=FakeStore(state),
            server_ids=("alpha",),
            system_message="trusted system",
        )
        runner.add_user_message(task_id)
        assert (await runner.advance()).status == "complete"
    assert len(created) == 2 and created[0] is not created[1]
    second_contents = tuple(message.content for message in created[1].seen[0][0])
    assert "task-1" not in second_contents
    assert "task-2" in second_contents


@bounded_test
async def test_close_during_reserved_send_keeps_unknown_outcome() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedGuard:
        calls = 0

        async def submit(self, task_id, call, *, expected_epoch):
            self.calls += 1
            entered.set()
            await release.wait()
            return OutcomeUnknown(request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,))

    guard = DelayedGuard()
    runner = started_runner(FakeModel(ModelTurn(calls=(_call(),))), guard, message="send")

    running = asyncio.create_task(runner.advance())
    await entered.wait()
    runner.close()
    release.set()
    update = await running
    assert guard.calls == 1
    assert update.status == "outcome_unknown"


@bounded_test
async def test_close_while_model_pending_reports_local_closed_without_dispatch() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedModel:
        async def step(self, conversation, tools):
            entered.set()
            await release.wait()
            return ModelTurn(calls=(_call(),))

    guard = FakeGuard()
    runner = started_runner(DelayedModel(), guard)

    running = asyncio.create_task(runner.advance())
    await entered.wait()
    runner.close()
    release.set()
    assert (await running).status == "closed"
    assert guard.calls == []


@bounded_test
async def test_invalid_model_output_stops_before_guard() -> None:

    class InvalidModel:
        async def step(self, conversation, tools):
            raise ModelFormatError()

    guard = FakeGuard()
    runner = started_runner(InvalidModel(), guard)
    assert (await runner.advance()).status == "model_error"
    assert guard.calls == []


@bounded_test
async def test_model_deadline_releases_runner_and_never_submits_late_call(subtests):

    async def check(late_failure):
        release = asyncio.Event()
        cancelled = asyncio.Event()
        finished = asyncio.Event()

        async def complete(conversation, tools):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                try:
                    await release.wait()
                    if late_failure:
                        raise OSError("synthetic-private-marker")
                    return {
                        "text": None,
                        "calls": [
                            {
                                "tool_ref": "alpha__read",
                                "arguments": {"path": "late"},
                                "model_call_id": "call-late",
                            }
                        ],
                    }
                finally:
                    finished.set()

        guard = FakeGuard()
        model = ProviderNeutralModelClient(complete, timeout_s=0.01)
        runner = started_runner(model, guard)

        running = asyncio.create_task(runner.advance())
        done, _ = await asyncio.wait({running}, timeout=1)
        try:
            assert done == {running}
            assert (await running).status == "model_error"
            with pytest.raises(RunnerStateError):
                await runner.advance()
            await asyncio.wait_for(cancelled.wait(), timeout=1)
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)
        assert guard.calls == []
        pytest.raises(RunnerStateError, runner.add_user_message, "retry")

    for late_failure in [False, True]:
        with subtests.test(late_failure=late_failure):
            await check(late_failure)


@bounded_test
async def test_oversized_user_input_stops_before_model_or_guard() -> None:
    model = FakeModel(ModelTurn(text="must not run"))
    guard = FakeGuard()
    runner = _runner(_state(), model, guard)
    runner.add_user_message("한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1))
    assert (await runner.advance()).status == "model_error"
    assert model.seen == []
    assert guard.calls == []
    pytest.raises(RunnerStateError, runner.add_user_message, "retry")


@bounded_test
async def test_oversized_released_result_cannot_leave_for_next_model_turn() -> None:
    model = FakeModel(ModelTurn(calls=(_call(),)), ModelTurn(text="must not run"))
    guard = FakeGuard(
        _released("한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1), label=Label.INTERNAL)
    )
    runner = started_runner(model, guard)
    assert (await runner.advance()).status == "continue"
    assert (await runner.advance()).status == "model_error"
    assert len(model.seen) == 1
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


@bounded_test
async def test_oversized_approved_result_preserves_pending_then_stops_egress() -> None:
    runner, model, guard = scenario(_pending(), answer="must not run")

    pending = await runner.advance()
    with pytest.raises(RunnerStateError):
        await runner.advance()
    resumed = await runner.resume_approval(
        "task-1",
        1,
        "approval-1",
        _released("한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1), label=Label.INTERNAL),
    )
    failed = await runner.advance()
    assert pending.status == "pending"
    assert resumed.status == "continue"
    assert failed.status == "model_error"
    assert len(model.seen) == 1
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


@bounded_test
async def test_policy_change_during_model_step_discards_late_calls() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedModel:
        async def step(self, conversation, tools):
            entered.set()
            await release.wait()
            return ModelTurn(calls=(_call(),))

    state = _state()
    store = FakeStore(state)
    guard = FakeGuard()
    runner = started_runner(DelayedModel(), guard, state=state, store=store)

    running = asyncio.create_task(runner.advance())
    await entered.wait()
    store.state = state.model_copy(update={"policy_version": "policy-2"})
    release.set()
    assert (await running).status == "stopped"
    assert guard.calls == []


@bounded_test
async def test_epoch_change_between_calls_stops_before_second_guard_submission() -> None:
    state = _state()
    store = FakeStore(state)

    class EpochChangingGuard:
        def __init__(self) -> None:
            self.calls = []

        async def submit(self, task_id: str, call: ProposedCall, *, expected_epoch):
            self.calls.append((task_id, call))
            store.state = state.model_copy(update={"epoch": 2})
            return _released("first")

    guard = EpochChangingGuard()
    runner = started_runner(
        FakeModel(ModelTurn(calls=(_call(1), _call(2)))),
        guard,
        state=state,
        store=store,
        message="read both",
    )
    assert (await runner.advance()).status == "stopped"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


@bounded_test
async def test_close_during_approval_state_lookup_discards_late_release() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedStore(FakeStore):
        delay = False

        async def load_task(self, task_id):
            if self.delay:
                entered.set()
                await release.wait()
            return await super().load_task(task_id)

    state = _state()
    store = DelayedStore(state)
    guard = FakeGuard(_pending())
    runner = started_runner(FakeModel(ModelTurn(calls=(_call(),))), guard, state=state, store=store)
    assert (await runner.advance()).status == "pending"
    store.delay = True
    continuing = asyncio.create_task(
        runner.resume_approval("task-1", 1, "approval-1", _released("late result"))
    )
    await entered.wait()
    runner.close()
    release.set()
    assert (await continuing).status == "closed"
    assert len(guard.calls) == 1


def _pending(request_id: str = "request-1", approval_id: str = "approval-1") -> Pending:
    return Pending(
        request_id=request_id, approval_id=approval_id, reasons=(Reason.INTERNAL_TO_EXTERNAL,)
    )


def _released(text, *, request_id="request-1", label=Label.PUBLIC):
    return Released(request_id=request_id, result=ReleasedResult(text=text), label=label)


class ApprovalStore(FakeStore):
    """FakeStore plus the approval row B committed for this approval."""

    def __init__(self, state: TaskState, status: ApprovalStatus | None) -> None:
        super().__init__(state)
        self.status = status
        self.approval_reads = 0

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        self.approval_reads += 1
        if self.status is None:
            raise StoreError(Reason.STATE_MISSING)
        now = datetime(2026, 10, 13, tzinfo=UTC)
        consumed = self.status is ApprovalStatus.CONSUMED
        return ApprovalRecord(
            approval_id=approval_id,
            request_id="request-1",
            snapshot_hash="0" * 64,
            approver_id="operator-1",
            status=self.status,
            created_at_utc=now,
            expires_at_utc=now + timedelta(minutes=5),
            decided_at_utc=now if consumed else None,
            consumed_at_utc=now if consumed else None,
        )


@bounded_test
async def test_guard_containment_deny_stops_runner_while_store_running(subtests):

    for reason in [
        Reason.INTERNAL_ERROR,
        Reason.STATE_COMMIT_FAILED,
        Reason.STATE_MISSING,
        # A StoreError reason Guard also uses with its global halt.
        Reason.APPROVAL_RACE,
    ]:
        with subtests.test(reason=reason):
            """Q04/I1: an in-memory global halt is not a policy deny to retry around."""
            runner, model, guard = scenario(
                Denied(request_id=None, reasons=(reason,)), calls=2, answer="never"
            )

            update = await runner.advance()
            assert update.status == "guard_error"
            pytest.raises(RunnerStateError, runner.add_user_message, "new input after global halt")
            with pytest.raises(RunnerStateError):
                await runner.advance()
            assert not runner.can_advance
            assert (len(model.seen), len(guard.calls)) == (1, 1)


@bounded_test
async def test_pre_decision_resolution_deny_still_allows_new_turn() -> None:
    """Resolution denials of the model's own call remain ordinary denies."""
    runner, model, guard = scenario(
        Denied(request_id=None, reasons=(Reason.INVALID_ARGUMENTS,)), answer="later"
    )

    first = await runner.advance()
    runner.add_user_message("try again")
    first, second = first, await runner.advance()
    assert (first.status, second.status) == ("denied", "complete")


@bounded_test
async def test_stop_latch_deny_before_store_halt_rejects_new_input() -> None:
    """R16/T20: cancel can deny a submit before its Store halt commits."""
    runner, model, guard = scenario(
        Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,)), calls=2
    )

    update = await runner.advance()
    assert update.status == "stopped"
    pytest.raises(RunnerStateError, runner.add_user_message, "new input while cancel lands")
    assert len(model.seen) == 1


@bounded_test
async def test_withheld_read_can_continue_directly_or_after_approval(subtests):
    """Q05/I7: a failed READ without data leaves the Task RUNNING."""
    for approved in (False, True):
        with subtests.test(approved=approved):
            model = FakeModel(ModelTurn(calls=(_call(1), _call(2))), ModelTurn(text="next"))
            withheld = Withheld(request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,))
            guard = FakeGuard(_pending() if approved else withheld)
            runner = started_runner(model, guard)
            first = await runner.advance()
            if approved:
                assert first.status == "pending"
                first = await runner.resume_approval("task-1", 1, "approval-1", withheld)
            else:
                assert not runner.can_advance
                with pytest.raises(RunnerStateError):
                    await runner.advance()
            runner.add_user_message("continue")
            second = await runner.advance()
            assert (first.status, second.status) == ("withheld", "complete")
            assert len(guard.calls) == 1
            messages = [m for m in model.seen[1][0] if m.role == "tool"]
            assert [m.content for m in messages] == [
                "이 호출 결과는 공개되지 않았습니다.",
                "이 호출은 실행되지 않았습니다.",
            ]
            assert all("POST_DISPATCH_ERROR" not in m.content for m in messages)


@bounded_test
async def test_approval_race_after_resume_to_running_follows_approval_record(subtests):

    for approval_status, expected in [
        (ApprovalStatus.INVALIDATED, "denied"),
        (ApprovalStatus.CANCELLED, "denied"),
        (ApprovalStatus.CONSUMED, "pending"),
        (None, "stopped"),
    ]:
        with subtests.test(approval_status=approval_status, expected=expected):
            """Q08: B resumes the Task to RUNNING and settles the approval."""
            state = _state()
            store = ApprovalStore(state, approval_status)
            runner, model, guard = scenario(
                _pending(), calls=2, answer="later", state=state, store=store
            )
            assert (await runner.advance()).status == "pending"
            update = await runner.resume_approval(
                "task-1",
                1,
                "approval-1",
                Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,)),
            )
            assert update.status == expected
            assert store.approval_reads == 1
            if expected == "denied":
                runner.add_user_message("new request")
                assert (await runner.advance()).status == "complete"
                assert model.seen[1][0][-3].content == "이 요청은 실행되지 않았습니다."
                assert model.seen[1][0][-2].content == "이 호출은 실행되지 않았습니다."
            else:
                pytest.raises(RunnerStateError, runner.add_user_message, "new request")
            assert len(guard.calls) == 1


@bounded_test
async def test_approval_failure_without_request_id_is_terminal(subtests):

    for reason, store_status, expected in [
        (Reason.INTERNAL_ERROR, TaskStatus.HALTED, "guard_error"),
        (Reason.INTERNAL_ERROR, TaskStatus.RUNNING, "guard_error"),
        (Reason.APPROVAL_RACE, TaskStatus.RUNNING, "guard_error"),
        (Reason.TASK_NOT_RUNNING, TaskStatus.HALTED, "halted"),
    ]:
        with subtests.test(reason=reason, store_status=store_status, expected=expected):
            """Q09: approval-path containment has no request ID but ends this approval."""
            state = _state()
            store = FakeStore(state)
            runner, model, guard = scenario(_pending(), calls=2, state=state, store=store)
            assert (await runner.advance()).status == "pending"
            store.state = state.model_copy(update={"status": store_status})
            update = await runner.resume_approval(
                "task-1", 1, "approval-1", Denied(request_id=None, reasons=(reason,))
            )
            with pytest.raises(RunnerStateError):
                await runner.resume_approval(
                    "task-1", 1, "approval-1", Denied(request_id=None, reasons=(reason,))
                )
            assert update.status == expected
            assert not runner.can_advance
            pytest.raises(RunnerStateError, runner.add_user_message, "new input")
            assert (len(model.seen), len(guard.calls)) == (1, 1)


@bounded_test
async def test_resume_still_rejects_other_request_result() -> None:
    runner, model, guard = scenario(_pending(), answer="later")
    assert (await runner.advance()).status == "pending"
    with pytest.raises(RunnerStateError):
        await runner.resume_approval(
            "task-1", 1, "approval-1", Denied(request_id="request-other", reasons=(Reason.OK,))
        )
    # A mismatched result neither consumes nor stops the pending approval.
    _run_result = await runner.resume_approval(
        "task-1", 1, "approval-1", Denied(request_id="request-1", reasons=(Reason.OK,))
    )
    assert _run_result.status == "denied"


@bounded_test
async def test_unclassified_model_failure_stops_runner(subtests):

    async def check(failure):
        """R07: an exception escaping the model client leaves no runnable loop."""

        class RaisingModel:
            calls = 0

            async def step(self, conversation, tools):
                self.calls += 1
                raise failure("synthetic-private-marker")

        model = RaisingModel()
        guard = FakeGuard()
        runner = started_runner(model, guard)

        with pytest.raises(failure):
            await runner.advance()
        assert not runner.can_advance
        with pytest.raises(RunnerStateError):
            await runner.advance()
        assert model.calls == 1
        assert guard.calls == []

    for failure in [ValueError, OverflowError, RecursionError]:
        with subtests.test(failure=failure):
            await check(failure)
