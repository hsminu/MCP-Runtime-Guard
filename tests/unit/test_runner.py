"""A21/A22: Guard-only dispatch, release boundary, and Task isolation."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime

import pytest

from guard_app.adapters.model_client import (
    MAX_MODEL_CONVERSATION_UTF8_BYTES,
    ModelFormatError,
    ProviderNeutralModelClient,
)
from guard_app.domain.contracts import (
    Denied,
    ModelTurn,
    OutcomeUnknown,
    Pending,
    Released,
    ReleasedResult,
    ToolView,
    Withheld,
)
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.services.runner import RunnerStateError, TaskRunner


def _state(
    task_id: str = "task-1", *, status: TaskStatus = TaskStatus.RUNNING
) -> TaskState:
    return TaskState(
        task_id=task_id,
        epoch=1,
        profile_id="profile-1",
        policy_version="policy-1",
        status=status,
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


def test_released_content_reaches_next_model_turn_only_after_guard() -> None:
    marker = "synthetic-private-marker"
    model = FakeModel(ModelTurn(calls=(_call(),)), ModelTurn(text="done"))
    guard = FakeGuard(
        Released(
            request_id="request-1",
            result=ReleasedResult(text=marker),
            label=Label.INTERNAL,
        )
    )
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read")

    async def run():
        first = await runner.advance()
        second = await runner.advance()
        return first, second

    first, second = asyncio.run(run())
    assert first.status == "continue"
    assert second.status == "complete"
    assert second.assistant_text == "done"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]
    next_conversation = model.seen[1][0]
    assert marker in next_conversation[-1].content
    assert "INTERNAL" not in next_conversation[-1].content
    assert next_conversation[-1].role == "tool"


def test_denied_stops_remaining_calls_and_uses_fixed_text() -> None:
    model = FakeModel(ModelTurn(calls=(_call(1), _call(2))), ModelTurn(text="later"))
    guard = FakeGuard(
        Denied(request_id="request-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,))
    )
    runner = _runner(_state(), model, guard)
    runner.add_user_message("first user request")

    async def run():
        update = await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.advance()
        runner.add_user_message("new user request")
        await runner.advance()
        return update

    update = asyncio.run(run())
    assert update.status == "denied"
    assert len(guard.calls) == 1
    assert model.seen[1][0][-3].content == "이 요청은 실행되지 않았습니다."
    assert model.seen[1][0][-2].content == "이 호출은 실행되지 않았습니다."
    assert "INTERNAL_TO_EXTERNAL" not in model.seen[1][0][-3].content


def test_guard_policy_mismatch_halt_is_returned_without_new_user_turn() -> None:
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

    store = HaltingStore(state)
    guard = GuardService(
        store,
        object(),
        FakeCatalog(),
        object(),
        object(),
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
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read")

    update = asyncio.run(runner.advance())

    assert store.state.status is TaskStatus.HALTED
    assert update.status == "halted"
    assert len(model.seen) == 1
    with pytest.raises(RunnerStateError):
        runner.add_user_message("another request")
    with pytest.raises(RunnerStateError):
        asyncio.run(runner.advance())


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ({"status": TaskStatus.HALTED}, "halted"),
        ({"status": TaskStatus.UNKNOWN_OUTCOME}, "outcome_unknown"),
        ({"status": TaskStatus.CLOSED}, "stopped"),
        ({"epoch": 2}, "stopped"),
        ({"policy_version": "policy-2"}, "stopped"),
    ],
)
def test_denied_after_concurrent_task_change_rejects_new_input(
    replacement: dict[str, object], expected: str
) -> None:
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
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read both")

    async def run():
        running = asyncio.create_task(runner.advance())
        await entered.wait()
        store.state = state.model_copy(update=replacement)
        release.set()
        update = await running
        with pytest.raises(RunnerStateError):
            runner.add_user_message("new input after cancellation")
        with pytest.raises(RunnerStateError):
            await runner.advance()
        return update

    assert asyncio.run(run()).status == expected
    assert guard.calls == 1
    assert len(model.seen) == 1


def test_pending_requires_matching_backend_result_and_resumes_remaining_calls() -> None:
    model = FakeModel(ModelTurn(calls=(_call(1), _call(2))))
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        ),
        Released(
            request_id="request-2",
            result=ReleasedResult(text="second"),
            label=Label.PUBLIC,
        ),
    )
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read both")

    async def run():
        pending = await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.resume_approval(
                "task-1",
                2,
                "approval-1",
                Released(
                    request_id="request-1",
                    result=ReleasedResult(text="first"),
                    label=Label.PUBLIC,
                ),
            )
        resumed = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Released(
                request_id="request-1",
                result=ReleasedResult(text="first"),
                label=Label.PUBLIC,
            ),
        )
        return pending, resumed

    pending, resumed = asyncio.run(run())
    assert pending.status == "pending" and pending.approval_id == "approval-1"
    assert resumed.status == "continue"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1", "call-2"]
    assert len(model.seen) == 1


def test_premature_expiry_race_keeps_approval_pending_until_actual_release() -> None:
    """B can return APPROVAL_RACE while approval and Task are still waiting."""
    state = _state()
    store = FakeStore(state)
    model = FakeModel(ModelTurn(calls=(_call(1), _call(2))), ModelTurn(text="done"))
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        ),
        Released(
            request_id="request-2",
            result=ReleasedResult(text="second"),
            label=Label.PUBLIC,
        ),
    )
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read both")

    async def run():
        assert (await runner.advance()).status == "pending"
        store.state = state.model_copy(update={"status": TaskStatus.WAITING_APPROVAL})
        race = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,)),
        )
        with pytest.raises(RunnerStateError):
            runner.add_user_message("try to bypass approval")
        with pytest.raises(RunnerStateError):
            await runner.advance()
        assert len(guard.calls) == 1
        store.state = state
        released = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Released(
                request_id="request-1",
                result=ReleasedResult(text="first"),
                label=Label.PUBLIC,
            ),
        )
        assert (await runner.advance()).status == "complete"
        return race, released

    race, released = asyncio.run(run())
    assert race.status == "pending" and race.approval_id == "approval-1"
    assert released.status == "continue"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1", "call-2"]
    resumed_messages = model.seen[1][0]
    assert [message.content for message in resumed_messages[-2:]] == [
        '{"text":"first","structured_content":null,"is_error":false}',
        '{"text":"second","structured_content":null,"is_error":false}',
    ]


def test_approval_race_does_not_hide_later_confirmed_rejection() -> None:
    state = _state()
    store = FakeStore(state)
    model = FakeModel(ModelTurn(calls=(_call(),)), ModelTurn(text="later"))
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        )
    )
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read")

    async def run():
        assert (await runner.advance()).status == "pending"
        store.state = state.model_copy(update={"status": TaskStatus.WAITING_APPROVAL})
        race = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,)),
        )
        store.state = state
        rejected = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.OK,)),
        )
        runner.add_user_message("new request")
        assert (await runner.advance()).status == "complete"
        return race, rejected

    race, rejected = asyncio.run(run())
    assert race.status == "pending"
    assert rejected.status == "denied"
    assert len(guard.calls) == 1
    assert model.seen[1][0][-2].content == "이 요청은 실행되지 않았습니다."


def test_approval_denial_after_policy_halt_does_not_offer_new_turn() -> None:
    state = _state()
    store = FakeStore(state)
    model = FakeModel(ModelTurn(calls=(_call(),)))
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        )
    )
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read")

    async def run():
        assert (await runner.advance()).status == "pending"
        store.state = state.model_copy(update={"status": TaskStatus.HALTED})
        halted = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.POLICY_CHANGED,)),
        )
        with pytest.raises(RunnerStateError):
            runner.add_user_message("new request")
        return halted

    assert asyncio.run(run()).status == "halted"
    assert len(model.seen) == 1
    assert len(guard.calls) == 1


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ({"status": TaskStatus.HALTED}, "halted"),
        ({"status": TaskStatus.UNKNOWN_OUTCOME}, "outcome_unknown"),
        ({"epoch": 2}, "stopped"),
    ],
)
def test_approval_race_after_task_stop_is_not_reported_pending(
    replacement: dict[str, object], expected: str
) -> None:
    """T20: APPROVAL_RACE cannot hide a concurrent cancel or stale Task."""
    state = _state()
    store = FakeStore(state)
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        )
    )
    model = FakeModel(ModelTurn(calls=(_call(),)))
    runner = _runner(state, model, guard, store)
    runner.add_user_message("read")

    async def run():
        assert (await runner.advance()).status == "pending"
        store.state = state.model_copy(update=replacement)
        update = await runner.resume_approval(
            state.task_id,
            state.epoch,
            "approval-1",
            Denied(request_id="request-1", reasons=(Reason.APPROVAL_RACE,)),
        )
        with pytest.raises(RunnerStateError):
            runner.add_user_message("new input")
        with pytest.raises(RunnerStateError):
            await runner.advance()
        return update

    assert asyncio.run(run()).status == expected
    assert len(model.seen) == 1
    assert len(guard.calls) == 1


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (
            Withheld(request_id="request-1", reasons=(Reason.STATE_COMMIT_FAILED,)),
            "halted",
        ),
        (
            OutcomeUnknown(
                request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,)
            ),
            "outcome_unknown",
        ),
    ],
)
def test_nonreleased_result_never_enters_model_context(outcome, expected) -> None:
    model = FakeModel(ModelTurn(calls=(_call(1), _call(2))))
    guard = FakeGuard(outcome)
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read")

    async def run():
        update = await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.advance()
        return update

    update = asyncio.run(run())
    assert update.status == expected
    assert len(guard.calls) == 1
    assert len(model.seen) == 1


def test_halted_task_does_not_call_model() -> None:
    initial = _state()
    store = FakeStore(_state(status=TaskStatus.HALTED))
    model = FakeModel(ModelTurn(text="must not run"))
    runner = _runner(initial, model, FakeGuard(), store)
    runner.add_user_message("read")
    update = asyncio.run(runner.advance())
    assert update.status == "halted"
    assert model.seen == []


def test_new_task_has_no_prior_conversation_and_stale_approval_is_rejected() -> None:
    marker = "synthetic-private-marker"
    first_model = FakeModel(ModelTurn(calls=(_call(),)))
    first = _runner(
        _state("task-1"),
        first_model,
        FakeGuard(
            Released(
                request_id="request-1",
                result=ReleasedResult(text=marker),
                label=Label.CONFIDENTIAL,
            )
        ),
    )
    first.add_user_message("read")
    asyncio.run(first.advance())
    first.close()

    second_model = FakeModel(ModelTurn(text="fresh"))
    second = _runner(_state("task-2"), second_model, FakeGuard())
    second.add_user_message("fresh input")

    async def run():
        with pytest.raises(RunnerStateError):
            await second.resume_approval(
                "task-1",
                1,
                "approval-1",
                Denied(request_id="request-1", reasons=(Reason.APPROVAL_STALE,)),
            )
        return await second.advance()

    assert asyncio.run(run()).status == "complete"
    assert marker not in tuple(message.content for message in second_model.seen[0][0])


def test_task_construction_calls_model_factory_for_each_task() -> None:
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
        assert asyncio.run(runner.advance()).status == "complete"
    assert len(created) == 2 and created[0] is not created[1]
    second_contents = tuple(message.content for message in created[1].seen[0][0])
    assert "task-1" not in second_contents
    assert "task-2" in second_contents


def test_close_during_reserved_send_keeps_unknown_outcome() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedGuard:
        calls = 0

        async def submit(self, task_id, call, *, expected_epoch):
            self.calls += 1
            entered.set()
            await release.wait()
            return OutcomeUnknown(
                request_id="request-1", reasons=(Reason.POST_DISPATCH_ERROR,)
            )

    guard = DelayedGuard()
    runner = _runner(_state(), FakeModel(ModelTurn(calls=(_call(),))), guard)
    runner.add_user_message("send")

    async def run():
        running = asyncio.create_task(runner.advance())
        await entered.wait()
        runner.close()
        release.set()
        return await running

    update = asyncio.run(run())
    assert guard.calls == 1
    assert update.status == "outcome_unknown"


def test_close_while_model_pending_reports_local_closed_without_dispatch() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedModel:
        async def step(self, conversation, tools):
            entered.set()
            await release.wait()
            return ModelTurn(calls=(_call(),))

    guard = FakeGuard()
    runner = _runner(_state(), DelayedModel(), guard)
    runner.add_user_message("read")

    async def run():
        running = asyncio.create_task(runner.advance())
        await entered.wait()
        runner.close()
        release.set()
        return await running

    assert asyncio.run(run()).status == "closed"
    assert guard.calls == []


def test_invalid_model_output_stops_before_guard() -> None:
    class InvalidModel:
        async def step(self, conversation, tools):
            raise ModelFormatError()

    guard = FakeGuard()
    runner = _runner(_state(), InvalidModel(), guard)
    runner.add_user_message("read")
    assert asyncio.run(runner.advance()).status == "model_error"
    assert guard.calls == []


@pytest.mark.parametrize("late_failure", [False, True])
def test_model_deadline_releases_runner_and_never_submits_late_call(
    late_failure: bool,
) -> None:
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
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read")

    async def run():
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

    asyncio.run(run())
    assert guard.calls == []
    with pytest.raises(RunnerStateError):
        runner.add_user_message("retry")


def test_oversized_user_input_stops_before_model_or_guard() -> None:
    model = FakeModel(ModelTurn(text="must not run"))
    guard = FakeGuard()
    runner = _runner(_state(), model, guard)
    runner.add_user_message("한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1))

    assert asyncio.run(runner.advance()).status == "model_error"
    assert model.seen == []
    assert guard.calls == []
    with pytest.raises(RunnerStateError):
        runner.add_user_message("retry")


def test_oversized_released_result_cannot_leave_for_next_model_turn() -> None:
    model = FakeModel(ModelTurn(calls=(_call(),)), ModelTurn(text="must not run"))
    guard = FakeGuard(
        Released(
            request_id="request-1",
            result=ReleasedResult(
                text="한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1)
            ),
            label=Label.INTERNAL,
        )
    )
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read")

    async def run():
        assert (await runner.advance()).status == "continue"
        return await runner.advance()

    assert asyncio.run(run()).status == "model_error"
    assert len(model.seen) == 1
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


def test_oversized_approved_result_preserves_pending_then_stops_egress() -> None:
    model = FakeModel(ModelTurn(calls=(_call(),)), ModelTurn(text="must not run"))
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        )
    )
    runner = _runner(_state(), model, guard)
    runner.add_user_message("read")

    async def run():
        pending = await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.advance()
        resumed = await runner.resume_approval(
            "task-1",
            1,
            "approval-1",
            Released(
                request_id="request-1",
                result=ReleasedResult(
                    text="한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3 + 1)
                ),
                label=Label.INTERNAL,
            ),
        )
        failed = await runner.advance()
        return pending, resumed, failed

    pending, resumed, failed = asyncio.run(run())
    assert pending.status == "pending"
    assert resumed.status == "continue"
    assert failed.status == "model_error"
    assert len(model.seen) == 1
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


def test_policy_change_during_model_step_discards_late_calls() -> None:
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
    runner = _runner(state, DelayedModel(), guard, store)
    runner.add_user_message("read")

    async def run():
        running = asyncio.create_task(runner.advance())
        await entered.wait()
        store.state = state.model_copy(update={"policy_version": "policy-2"})
        release.set()
        return await running

    assert asyncio.run(run()).status == "stopped"
    assert guard.calls == []


def test_epoch_change_between_calls_stops_before_second_guard_submission() -> None:
    state = _state()
    store = FakeStore(state)

    class EpochChangingGuard:
        def __init__(self) -> None:
            self.calls = []

        async def submit(self, task_id: str, call: ProposedCall, *, expected_epoch):
            self.calls.append((task_id, call))
            store.state = state.model_copy(update={"epoch": 2})
            return Released(
                request_id="request-1",
                result=ReleasedResult(text="first"),
                label=Label.PUBLIC,
            )

    guard = EpochChangingGuard()
    runner = _runner(
        state, FakeModel(ModelTurn(calls=(_call(1), _call(2)))), guard, store
    )
    runner.add_user_message("read both")

    assert asyncio.run(runner.advance()).status == "stopped"
    assert [call.model_call_id for _, call in guard.calls] == ["call-1"]


def test_close_during_approval_state_lookup_discards_late_release() -> None:
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
    guard = FakeGuard(
        Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        )
    )
    runner = _runner(state, FakeModel(ModelTurn(calls=(_call(),))), guard, store)
    runner.add_user_message("read")

    async def run():
        assert (await runner.advance()).status == "pending"
        store.delay = True
        continuing = asyncio.create_task(
            runner.resume_approval(
                "task-1",
                1,
                "approval-1",
                Released(
                    request_id="request-1",
                    result=ReleasedResult(text="late result"),
                    label=Label.PUBLIC,
                ),
            )
        )
        await entered.wait()
        runner.close()
        release.set()
        return await continuing

    assert asyncio.run(run()).status == "closed"
    assert len(guard.calls) == 1
