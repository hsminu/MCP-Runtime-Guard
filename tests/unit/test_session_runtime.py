"""A-only injected sessions, mandatory L2 gate and Task concurrency (I7/I8/I9).

No actual Codex process/security-platform validation or T46 acceptance claim.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from guard_app.domain.contracts import Pending
from guard_app.domain.enums import SystemStopReason, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.bridge import ControlAction, SessionOptions
from guard_app.services.catalog import GuardSettings
from guard_app.services.guard_relay import ApprovalOutcomes
from guard_app.services.runner import CodexAuthenticationError, RunnerStateError
from guard_app.services.session_cleanup import CleanupLimits
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)
from guard_app.services.session_runtime import SessionRuntime, SessionRuntimeError
from guard_app.storage.sqlite_store import SQLiteStore

from .product_folders import product_folders
from .test_product_assembly import closing_backend


class Session:
    def __init__(self):
        self.task_id = None
        self.messages = []
        self.closed = 0
        self.interrupted = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.error = None

    async def send(self, text, effort=None):
        self.messages.append(text)
        self.entered.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return f"answer:{text}"

    async def interrupt(self):
        self.interrupted += 1

    async def aclose(self):
        self.closed += 1


async def passed_l2():
    return True


def synthetic_preparation(l2=passed_l2):
    configuration = ConfigurationReceipt("a" * 64, "b" * 64, "c" * 64)

    async def startup():
        if l2 is None:
            return StartupReceipt(configuration, None, False)
        return StartupReceipt(configuration, configuration, await l2())

    async def verify(expected):
        return expected

    async def prepare(task_id, expected):
        return TaskResources(task_id, expected, task_id, object())

    async def confirm(resources):
        return True

    async def cleanup(resources):
        pass

    return SessionPreparation(startup, verify, prepare, confirm, cleanup)


class ObservedStore(SQLiteStore):
    """The product Store, telling a test when shutdown asks it to close."""

    on_close = None

    def close(self):
        super().close()
        if self.on_close is not None:
            self.on_close()


class UnconfirmedStore(ObservedStore):
    """A Store whose connection close is never confirmed in time (#58)."""

    async def wait_closed(self, timeout):  # noqa: ASYNC109 - the Store port's signature
        await super().wait_closed(timeout)
        return False


def make_runtime(
    tmp_path,
    *,
    l2=passed_l2,
    session_type=None,
    factory=None,
    preparation=None,
    on_store_close=None,
    **options,
):
    """A runtime over SQLite with synthetic C callbacks; `options` go to SessionRuntime."""
    store = ObservedStore(tmp_path / "sessions.db", audit_id_factory=lambda: "unit-audit")
    store.on_close = on_store_close
    sessions = []

    async def sessions_factory(state, options, resources):
        assert state.status is TaskStatus.RUNNING
        assert options == SessionOptions()
        session = (session_type or Session)()
        session.task_id = state.task_id
        sessions.append(session)
        return session

    runtime = SessionRuntime(
        store,
        session_factory=factory or sessions_factory,
        preparation=preparation or synthetic_preparation(l2),
        owned_store=store,
        guard_settings=GuardSettings(profile_id="unit-profile", policy_version="unit-policy-v1"),
        **options,
    )
    return runtime, sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("l2", "code"), [(None, "CODEX_L2_UNAVAILABLE"), (lambda: failed_l2(), "CODEX_L2_FAILED")]
)
async def test_missing_or_failed_l2_rejects_before_task_or_session(tmp_path, monkeypatch, l2, code):
    runtime, sessions = make_runtime(tmp_path, l2=l2)
    creates = []
    original = runtime.store.create_task

    async def tracked(*args, **kwargs):
        creates.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(runtime.store, "create_task", tracked)
    async with closing_backend(runtime):
        with pytest.raises(SessionRuntimeError, match=code):
            await runtime.create_session(SessionOptions())
        assert not creates
        assert not sessions
        assert not runtime.pending_cleanup


async def failed_l2():
    return False


@pytest.mark.asyncio
async def test_two_tasks_overlap_and_stop_is_task_local_with_late_response_discarded(tmp_path):
    runtime, sessions = make_runtime(tmp_path)
    async with closing_backend(runtime):
        first, second = await asyncio.gather(
            runtime.create_session(SessionOptions()), runtime.create_session(SessionOptions())
        )
        from guard_app.domain.enums import Label

        assert first.label is Label.PUBLIC
        assert first.unclassified is False
        stored = await runtime.store.load_task(first.task_id)
        assert (stored.profile_id, stored.policy_version) == ("unit-profile", "unit-policy-v1")
        with pytest.raises(SessionRuntimeError, match="SESSION_STATE_MISMATCH"):
            await runtime.stop_session(first.task_id, first.epoch + 1, ControlAction.CANCEL)
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.RUNNING
        assert sessions[0].closed == 0
        one, two = (runtime.runner_for_task(first.task_id), runtime.runner_for_task(second.task_id))
        one.add_user_message("first-private-text")
        two.add_user_message("second-private-text")
        run_one, run_two = (asyncio.create_task(one.advance()), asyncio.create_task(two.advance()))
        await asyncio.wait_for(asyncio.gather(*(session.entered.wait() for session in sessions)), 1)
        assert not run_one.done()
        assert not run_two.done()
        await runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        assert sessions[0].closed == sessions[0].interrupted == 1
        assert sessions[1].closed == 0
        for session in sessions:
            session.release.set()
        result_one, result_two = await asyncio.gather(run_one, run_two)
        assert result_one.status == "closed"
        assert result_one.assistant_text is None
        assert result_two.assistant_text == "answer:second-private-text"
        assert sessions[0].messages == ["first-private-text"]
        assert sessions[1].messages == ["second-private-text"]
        assert two.can_accept_message
        assert not one.can_accept_message
    assert all(session.closed == 1 for session in sessions)
    assert b"first-private-text" not in (tmp_path / "sessions.db").read_bytes()
    # A134: Windows refuses unlink while the SQLite connection is open.
    (tmp_path / "sessions.db").unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [CodexAuthenticationError(), OSError("private-error")])
async def test_auth_or_unclassified_failure_halts_only_own_task(tmp_path, error):
    runtime, sessions = make_runtime(tmp_path)
    async with closing_backend(runtime):
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        one, two = (runtime.runner_for_task(first.task_id), runtime.runner_for_task(second.task_id))
        sessions[0].error = error
        one.add_user_message("one")
        two.add_user_message("two")
        operations = [asyncio.create_task(runner.advance()) for runner in (one, two)]
        await asyncio.wait_for(asyncio.gather(*(session.entered.wait() for session in sessions)), 1)
        sessions[0].release.set()
        if isinstance(error, CodexAuthenticationError):
            assert (await operations[0]).status == "halted"
            assert (await runtime.snapshot(first.task_id)).connection_code == "CODEX_AUTH_FAILED"
        else:
            with pytest.raises(OSError, match="private-error"):
                await operations[0]
        assert sessions[0].closed == 1
        assert sessions[1].closed == 0
        assert not operations[1].done()
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        sessions[1].release.set()
        assert (await operations[1]).assistant_text == "answer:two"


@pytest.mark.asyncio
async def test_l2_once_and_same_task_rejects_overlapping_input(tmp_path):
    checks = []

    async def check():
        checks.append(True)
        await asyncio.sleep(0)
        return True

    runtime, sessions = make_runtime(tmp_path, l2=check)
    async with closing_backend(runtime):
        first, _ = await asyncio.gather(
            *(runtime.create_session(SessionOptions()) for _ in range(2))
        )
        assert checks == [True]
        runner = runtime.runner_for_task(first.task_id)
        runner.add_user_message("one")
        operation = asyncio.create_task(runner.advance())
        await sessions[0].entered.wait()
        with pytest.raises(RunnerStateError):
            runner.add_user_message("overlap")
        sessions[0].release.set()
        assert (await operation).status == "complete"


@pytest.mark.asyncio
async def test_create_failure_closes_unadopted_session(tmp_path, monkeypatch):
    runtime, sessions = make_runtime(tmp_path)

    async def fail(*args, **kwargs):
        raise StoreError()

    monkeypatch.setattr(runtime.store, "create_task", fail)
    async with closing_backend(runtime):
        with pytest.raises(StoreError):
            await runtime.create_session(SessionOptions())
        assert sessions[0].closed == 1
        with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
            runtime.runner_for_task(sessions[0].task_id)
        assert not runtime.pending_cleanup


@pytest.mark.asyncio
async def test_close_during_factory_creation_reclaims_late_session(tmp_path):
    entered = asyncio.Event()
    session = Session()

    async def factory(state, options, resources):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return session

    runtime, _ = make_runtime(tmp_path, factory=factory)
    creation = asyncio.create_task(runtime.create_session(SessionOptions()))
    await entered.wait()
    await runtime.close()
    with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
        await creation
    assert session.closed == 1
    assert not runtime.pending_cleanup


@pytest.mark.asyncio
async def test_blocked_close_is_bounded_and_other_task_cleanup_runs(tmp_path):
    runtime, sessions = make_runtime(tmp_path, cleanup_limits=CleanupLimits(session_s=0.01))
    first = await runtime.create_session(SessionOptions())
    await runtime.create_session(SessionOptions())
    entered = asyncio.Event()
    released = asyncio.Event()

    async def blocked():
        sessions[0].closed += 1
        entered.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            await released.wait()

    # The process close (not the interrupt courtesy) is what stays unproven.
    sessions[0].aclose = blocked
    try:
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await asyncio.wait_for(runtime.close(), 1)
        assert entered.is_set()
        assert sessions[0].interrupted == sessions[0].closed == 1
        assert sessions[1].interrupted == sessions[1].closed == 1
        assert runtime.pending_cleanup == {first.task_id}
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.close()  # the latch answers every later close
    finally:
        released.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stubborn_factory_close_is_bounded_and_late_session_reclaimed(tmp_path):
    entered, released = asyncio.Event(), asyncio.Event()
    session = Session()
    store_closes = []

    async def factory(state, options, resources):
        entered.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            await released.wait()
        return session

    runtime, _ = make_runtime(
        tmp_path,
        factory=factory,
        on_store_close=lambda: store_closes.append(True),
        cleanup_limits=CleanupLimits(session_s=0.01),
    )
    creation = asyncio.create_task(runtime.create_session(SessionOptions()))
    await entered.wait()
    with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
        await asyncio.wait_for(runtime.close(), 1)
    assert not creation.done()
    assert not store_closes  # the creator still owns its Task and needs the Store
    released.set()
    with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
        await creation
    assert session.closed == 1
    assert store_closes == [True]  # the last creator out closes it


@pytest.mark.asyncio
async def test_failed_creation_cleanup_is_bounded(tmp_path, monkeypatch):
    runtime, sessions = make_runtime(tmp_path, cleanup_limits=CleanupLimits(session_s=0.01))
    released = asyncio.Event()

    async def fail(*args, **kwargs):
        async def blocked_close():
            try:
                await released.wait()
            except asyncio.CancelledError:
                await released.wait()

        sessions[0].aclose = blocked_close
        raise StoreError()

    monkeypatch.setattr(runtime.store, "create_task", fail)
    try:
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await asyncio.wait_for(runtime.create_session(SessionOptions()), 1)
        assert runtime.pending_cleanup == {sessions[0].task_id}
    finally:
        released.set()
        await asyncio.sleep(0)
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.close()


@pytest.mark.asyncio
async def test_task_cleanup_uncertainty_latches_without_stopping_other_task(tmp_path):
    confirmations = []

    async def confirm(resources):
        # The first Task's exit is unproven once; every later answer is yes.
        confirmations.append(resources.task_id)
        return resources.task_id != first.task_id or confirmations.count(first.task_id) > 1

    runtime, sessions = make_runtime(
        tmp_path, preparation=replace(synthetic_preparation(), confirm_exit=confirm)
    )
    first = await runtime.create_session(SessionOptions())
    second = await runtime.create_session(SessionOptions())
    with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
        await runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
    assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
    assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
    assert runtime.creation_blocker() == "CODEX_CLEANUP_UNCONFIRMED"
    assert first.task_id in runtime.pending_cleanup  # never erased without proof
    runner = runtime.runner_for_task(second.task_id)
    runner.add_user_message("unaffected")
    sessions[1].release.set()
    assert (await runner.advance()).assistant_text == "answer:unaffected"
    # Even a later successful shutdown retry cannot claim the earlier
    # process cleanup was confirmed. Bridge's restart gate must see this.
    with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
        await runtime.close()
    assert confirmations.count(first.task_id) == 1  # never retried
    assert sessions[1].closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "exits_in_time"),
    [(stage, timely) for stage in ("confirm_exit", "cleanup_task") for timely in (True, False)],
)
async def test_resource_cleanup_stages_have_their_own_bounds(tmp_path, stage, exits_in_time):
    """A103, C2.12, I9: exit confirmation and folder removal have independent bounds."""

    release = asyncio.Event()
    confirmations = []

    async def confirm(resources):
        confirmations.append(resources.task_id)
        if resources.task_id == first.task_id:
            await asyncio.sleep(0.1)  # longer than the session bound
            if not exits_in_time:
                await release.wait()
        return True if stage == "confirm_exit" else None

    runtime, _ = make_runtime(
        tmp_path,
        preparation=replace(synthetic_preparation(), **{stage: confirm}),
        cleanup_limits=CleanupLimits(
            session_s=0.01,
            **{
                ("confirm_exit_s" if stage == "confirm_exit" else "task_folder_s"): 1.0
                if exits_in_time
                else 0.2
            },
        ),
    )
    first = await runtime.create_session(SessionOptions())
    stopping = runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
    try:
        if exits_in_time:
            await asyncio.wait_for(stopping, 2)
            assert first.task_id not in runtime.pending_cleanup
            assert runtime.creation_blocker() is None
            second = await runtime.create_session(SessionOptions())
            assert second.status is TaskStatus.RUNNING
        else:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await asyncio.wait_for(stopping, 2)
            assert first.task_id in runtime.pending_cleanup  # never erased without proof
            assert runtime.creation_blocker() == "CODEX_CLEANUP_UNCONFIRMED"
    finally:
        release.set()
        if exits_in_time:
            await runtime.close()
        else:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await runtime.close()
    assert confirmations.count(first.task_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked", "raises"])
async def test_lost_interrupt_alone_leaves_cleanup_confirmed(tmp_path, failure):
    """A88 QA: interrupt is a courtesy; close + confirm_exit + cleanup prove the stop (I9)."""

    runtime, sessions = make_runtime(tmp_path, cleanup_limits=CleanupLimits(session_s=0.01))
    first = await runtime.create_session(SessionOptions())
    second = await runtime.create_session(SessionOptions())
    released = asyncio.Event()

    async def interrupt():
        sessions[0].interrupted += 1
        if failure == "raises":
            raise OSError("private-interrupt-error")
        await released.wait()

    sessions[0].interrupt = interrupt
    try:
        await asyncio.wait_for(
            runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL), 1
        )
        assert sessions[0].interrupted == sessions[0].closed == 1
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert first.task_id not in runtime.pending_cleanup
        assert runtime.creation_blocker() is None
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        third = await runtime.create_session(SessionOptions())
        assert third.status is TaskStatus.RUNNING
    finally:
        released.set()
        await runtime.close()
    await runtime.close()  # a later close finds no unconfirmed cleanup either


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_succeeds", [True, False])
async def test_stop_during_failure_cleanup_adopts_its_result(tmp_path, cleanup_succeeds):
    limits = CleanupLimits() if cleanup_succeeds else CleanupLimits(session_s=0.05)
    runtime, sessions = make_runtime(tmp_path, cleanup_limits=limits)
    snapshot = await runtime.create_session(SessionOptions())
    session = sessions[0]
    closing = asyncio.Event()
    finish = asyncio.Event()

    async def slow_close():
        session.closed += 1
        closing.set()
        await finish.wait()

    session.aclose = slow_close
    runner = runtime.runner_for_task(snapshot.task_id)
    runner.add_user_message("hi")
    session.error = CodexAuthenticationError()
    advancing = asyncio.create_task(runner.advance())
    await session.entered.wait()
    session.release.set()
    await asyncio.wait_for(closing.wait(), 1)  # failure cleanup is now in flight
    stopping = asyncio.create_task(
        runtime.stop_session(snapshot.task_id, snapshot.epoch, ControlAction.CANCEL)
    )
    await asyncio.sleep(0)
    assert not stopping.done()  # waits instead of reporting unconfirmed at once
    if cleanup_succeeds:
        finish.set()
        await stopping
        await advancing
        await runtime.close()
        await runtime.close()  # no unconfirmed cleanup latched
    else:
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await stopping
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await advancing
        finish.set()
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.close()
    assert session.closed == 1  # one cleanup, never a second run


@pytest.mark.asyncio
async def test_creation_blocker_reports_known_refusals_in_creation_order(tmp_path):
    store = SQLiteStore(tmp_path / "blocker.db", audit_id_factory=lambda: "unit-audit")
    bare = SessionRuntime(store, owned_store=store)
    assert bare.creation_blocker() == "CODEX_CONFIG_UNAVAILABLE"
    await bare.close()
    assert bare.creation_blocker() == "SESSION_UNAVAILABLE"
    runtime, _ = make_runtime(tmp_path)
    assert runtime.creation_blocker() is None
    without_factory = SessionRuntime(
        runtime.store,
        preparation=synthetic_preparation(),
        guard_settings=GuardSettings(profile_id="unit-profile", policy_version="v1"),
    )
    assert without_factory.creation_blocker() == "CODEX_SESSION_UNAVAILABLE"
    await without_factory.close()
    failed, _ = make_runtime(tmp_path, l2=None)
    with pytest.raises(SessionRuntimeError, match="CODEX_L2_UNAVAILABLE"):
        await failed.create_session(SessionOptions())
    # A101: the first refusal's code stays the blocker and answers later tries.
    assert failed.creation_blocker() == "CODEX_L2_UNAVAILABLE"
    with pytest.raises(SessionRuntimeError, match="^CODEX_L2_UNAVAILABLE$"):
        await failed.create_session(SessionOptions())
    await failed.close()
    await runtime.close()


@pytest.mark.asyncio
async def test_product_bootstrap_without_c_components_starts_and_refuses_sessions(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    bindings = await bootstrap_product(product_folders(str(tmp_path / "product.db")))
    async with closing_backend(bindings):
        assert bindings.settings_status is None
        assert bindings.human_control is None
        assert bindings.create_session is not None
        assert bindings.session_blocker is not None
        assert bindings.session_blocker() == "CODEX_CONFIG_UNAVAILABLE"
        with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_UNAVAILABLE"):
            await bindings.create_session(SessionOptions())
    assert (tmp_path / "product.db").exists()


@pytest.mark.asyncio
async def test_product_bootstrap_starts_and_keeps_a_startup_refusal_as_the_blocker(tmp_path):
    """A101: a refused app-wide preparation blocks sessions, not the app (10.4
    refuses Task starts). It runs once at start and is not retried."""
    from guard_app.services.session_runtime import bootstrap_product

    preparation = synthetic_preparation(l2=None)
    startups: list[None] = []

    async def startup() -> StartupReceipt:
        startups.append(None)
        return await preparation.prepare_startup()

    bindings = await bootstrap_product(
        product_folders(str(tmp_path / "failed.db")),
        preparation=replace(preparation, prepare_startup=startup),
    )
    async with closing_backend(bindings):
        assert bindings.session_blocker is not None
        assert bindings.create_session is not None
        assert bindings.session_blocker() == "CODEX_L2_UNAVAILABLE"
        with pytest.raises(SessionRuntimeError, match="^CODEX_L2_UNAVAILABLE$"):
            await bindings.create_session(SessionOptions())
    assert len(startups) == 1


@pytest.mark.parametrize(
    ("receipt", "code"),
    [
        (
            StartupReceipt(ConfigurationReceipt("a" * 64, "b" * 64, "c" * 64), None, False),
            "CODEX_L2_UNAVAILABLE",
        ),
        (None, "CODEX_PREPARATION_FAILED"),
    ],
)
@pytest.mark.asyncio
async def test_a_startup_refusal_is_reported_with_its_own_code(tmp_path, receipt, code):

    async def startup() -> StartupReceipt:
        if receipt is None:
            raise RuntimeError("raw-startup-detail")
        return receipt

    runtime, _ = make_runtime(
        tmp_path, preparation=replace(synthetic_preparation(), prepare_startup=startup)
    )
    async with closing_backend(runtime):
        await runtime.prepare_startup()
        scenario_result = runtime.creation_blocker()
    assert scenario_result == code


@pytest.mark.asyncio
async def test_missing_guard_profile_refuses_before_any_task_row(tmp_path):
    store = SQLiteStore(tmp_path / "noprofile.db", audit_id_factory=lambda: "unit-audit")
    sessions = []

    async def factory(state, options, resources):
        sessions.append(state)
        raise AssertionError("must not start")

    runtime = SessionRuntime(
        store, session_factory=factory, preparation=synthetic_preparation(), owned_store=store
    )
    async with closing_backend(runtime):
        assert runtime.creation_blocker() == "GUARD_PROFILE_UNAVAILABLE"
        with pytest.raises(SessionRuntimeError, match="GUARD_PROFILE_UNAVAILABLE"):
            await runtime.create_session(SessionOptions())
        assert sessions == []


class StoppableSession(Session):
    def __init__(self):
        super().__init__()
        self.stop_handler = None

    def set_stop_handler(self, handler):
        self.stop_handler = handler


@pytest.mark.asyncio
async def test_session_stop_outside_send_halts_only_its_task_and_republishes(tmp_path):
    """L3 item after a finished turn: no send() raises, so the owner stops the Task (I9)."""

    runtime, sessions = make_runtime(tmp_path, session_type=StoppableSession)
    changed = asyncio.Event()
    events = []

    def listener(task_id):
        events.append(task_id)
        changed.set()

    async with closing_backend(runtime):
        runtime.bindings().task_events(listener)
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        assert callable(sessions[0].stop_handler)
        sessions[0].stop_handler("CODEX_L3_VIOLATION")
        await asyncio.wait_for(changed.wait(), 1)
        assert events == [first.task_id]
        stopped = await runtime.snapshot(first.task_id)
        assert stopped.status is TaskStatus.HALTED
        assert stopped.connection_code == "CODEX_L3_VIOLATION"
        assert not stopped.model_ready
        assert not stopped.can_accept_message
        assert sessions[0].interrupted == sessions[0].closed == 1
        assert runtime.creation_blocker() is None
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        assert sessions[1].closed == 0
    # After shutdown a late stop report is ignored.
    sessions[1].stop_handler("CODEX_L3_VIOLATION")
    await asyncio.sleep(0)
    assert events == [first.task_id]


@pytest.mark.asyncio
async def test_snapshot_offers_the_waiting_approval_and_the_listener_sees_wait_start_and_end(
    tmp_path,
):
    """The approval offered is Guard's id for this trusted Task, not model text (I3)."""

    approvals = ApprovalOutcomes()
    runtime, _ = make_runtime(tmp_path, approvals=approvals)
    events = []
    async with closing_backend(runtime):
        runtime.bindings().task_events(events.append)
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        assert (await runtime.snapshot(first.task_id)).approval_id is None
        pending = Pending(
            request_id="request-1", approval_id="approval-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,)
        )
        result = FinalResult()
        waiting = asyncio.ensure_future(approvals.wait(pending, first.task_id, result))
        await asyncio.sleep(0)
        assert events == [first.task_id]
        assert approvals.approval_for(first.task_id) == "approval-1"
        assert (await runtime.snapshot(first.task_id)).approval_id == "approval-1"
        assert (await runtime.snapshot(second.task_id)).approval_id is None
        result.final.set_result("guard-final")
        assert await asyncio.wait_for(waiting, 1) == "guard-final"
        assert events == [first.task_id, first.task_id]
        assert (await runtime.snapshot(first.task_id)).approval_id is None


class FinalResult:
    def __init__(self):
        self.final = asyncio.get_running_loop().create_future()

    async def wait(self):
        return await asyncio.shield(self.final)


@pytest.mark.asyncio
async def test_cancelled_wait_clears_the_offered_approval_but_not_guards_result():
    """NOTIFY-01: the watchdog cancels only the wait; the projection stops offering it."""

    approvals = ApprovalOutcomes()
    pending = Pending(
        request_id="request-1", approval_id="approval-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,)
    )
    result = FinalResult()
    waiting = asyncio.ensure_future(approvals.wait(pending, "task-1", result))
    await asyncio.sleep(0)
    assert approvals.approval_for("task-1") == "approval-1"
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not approvals.waiting("approval-1")
    assert approvals.approval_for("task-1") is None
    assert not result.final.cancelled()


class StreamingSession(Session):
    def __init__(self):
        super().__init__()
        self.partial_handler = None

    def set_partial_handler(self, handler):
        self.partial_handler = handler


@pytest.mark.asyncio
async def test_streamed_pieces_carry_their_own_task_and_epoch_until_shutdown(tmp_path):
    """A90: the runtime, not the session or model, names the Task of a piece (I9)."""

    runtime, sessions = make_runtime(tmp_path, session_type=StreamingSession)
    pieces = []
    async with closing_backend(runtime):
        runtime.bindings().answer_partials(lambda *piece: pieces.append(piece))
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        sessions[1].partial_handler("beta")
        sessions[0].partial_handler("alpha")
        assert pieces == [(second.task_id, 1, "beta"), (first.task_id, 1, "alpha")]
    sessions[0].partial_handler("after-close")
    assert len(pieces) == 2


class RecordingSystem:
    """Guard's system channel: a stop waits (for the Task lock) before its Store write."""

    def __init__(self, events):
        self.events = events
        self.store = None
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def stop_task(self, identity, reason):
        self.waiting.set()
        await self.release.wait()
        state = await self.store.load_task(identity.task_id)  # refused once the Store closed
        self.events.append(("stop-recorded", identity.task_id, reason))
        return state

    async def record_request_user_input(self, identity):
        pass


async def stop_one_task(runtime, sessions, system):
    created = await runtime.create_session(SessionOptions())
    runner = runtime.runner_for_task(created.task_id)
    sessions[0].error = CodexAuthenticationError()
    sessions[0].release.set()
    runner.add_user_message("one")
    assert (await runner.advance()).status == "halted"
    await asyncio.wait_for(system.waiting.wait(), 1)
    return created.task_id


@pytest.mark.asyncio
async def test_shutdown_lets_a_session_stop_reach_the_store_before_closing_it(tmp_path):
    """#58 B review: Guard records the stop in the background; closing the Store
    first refused that write (SystemControlError). Shutdown now waits for it."""

    events = []
    system = RecordingSystem(events)
    runtime, sessions = make_runtime(
        tmp_path, system_control=system, on_store_close=lambda: events.append(("store-closed",))
    )
    system.store = runtime.store
    task_id = await stop_one_task(runtime, sessions, system)
    closing = asyncio.create_task(runtime.close())
    await asyncio.sleep(0.05)
    assert events == []  # the Store is still open for the waiting stop
    system.release.set()
    await asyncio.wait_for(closing, 1)
    assert events == [("stop-recorded", task_id, SystemStopReason.AUTH_FAILED), ("store-closed",)]


@pytest.mark.asyncio
async def test_a_stop_that_does_not_end_in_time_is_reported_and_the_store_still_closes(tmp_path):

    events = []
    system = RecordingSystem(events)
    runtime, sessions = make_runtime(
        tmp_path,
        system_control=system,
        on_store_close=lambda: events.append(("store-closed",)),
        cleanup_limits=CleanupLimits(stop_record_s=0.05, session_s=0.05),
    )
    system.store = runtime.store
    await stop_one_task(runtime, sessions, system)
    for _ in range(2):  # a repeated close reports it again
        with pytest.raises(SessionRuntimeError, match="^SESSION_STOP_UNCONFIRMED$"):
            await runtime.close()
    assert events == [("store-closed",)]
    system.release.set()  # the late write is refused by the closed Store
    await asyncio.sleep(0.05)
    scenario_result = events
    assert scenario_result == [("store-closed",)]


@pytest.mark.asyncio
async def test_a_store_close_not_confirmed_in_time_ends_shutdown_unconfirmed(tmp_path):
    class Late(UnconfirmedStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.waits = []

        async def wait_closed(self, timeout):  # noqa: ASYNC109 - the Store port's signature
            self.waits.append(timeout)
            return await super().wait_closed(timeout)

    store = Late(tmp_path / "late.db", audit_id_factory=lambda: "unit-audit")
    runtime = SessionRuntime(store, owned_store=store)
    for _ in range(2):  # a repeated close reports it again
        with pytest.raises(SessionRuntimeError, match="^STORE_CLOSE_UNCONFIRMED$"):
            await runtime.close()
    scenario_result = store.waits
    assert scenario_result == [CleanupLimits().store_close_s]
