"""A-only injected sessions, mandatory L2 gate and Task concurrency (I7/I8/I9).

No actual Codex process/security-platform validation or T46 acceptance claim.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from guard_app.domain.contracts import Pending
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.bridge import ControlAction, SessionOptions
from guard_app.services.catalog import GuardSettings
from guard_app.services.guard_relay import ApprovalOutcomes
from guard_app.services.runner import CodexAuthenticationError, RunnerStateError
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)
from guard_app.services.session_runtime import SessionRuntime, SessionRuntimeError
from guard_app.storage.sqlite_store import SQLiteStore


class Session:
    def __init__(self):
        self.messages = []
        self.closed = 0
        self.interrupted = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.error = None

    async def send(self, text):
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


def make_runtime(tmp_path, *, l2=passed_l2, approvals=None, session_type=None):
    store = SQLiteStore(tmp_path / "sessions.db", audit_id_factory=lambda: "unit-audit")
    sessions = []

    async def factory(state, options, resources):
        assert state.status is TaskStatus.RUNNING
        assert options == SessionOptions()
        session = (session_type or Session)()
        sessions.append(session)
        return session

    runtime = SessionRuntime(
        store,
        session_factory=factory,
        preparation=synthetic_preparation(l2),
        close_store=store.close,
        guard_settings=GuardSettings(profile_id="unit-profile", policy_version="unit-policy-v1"),
        approvals=approvals,
    )
    return runtime, sessions


@pytest.mark.parametrize(
    ("l2", "code"), [(None, "CODEX_L2_UNAVAILABLE"), (lambda: failed_l2(), "CODEX_L2_FAILED")]
)
def test_missing_or_failed_l2_rejects_before_task_or_session(tmp_path, monkeypatch, l2, code):
    async def run():
        runtime, sessions = make_runtime(tmp_path, l2=l2)
        creates = []
        original = runtime.store.create_task

        async def tracked(*args, **kwargs):
            creates.append(True)
            return await original(*args, **kwargs)

        monkeypatch.setattr(runtime.store, "create_task", tracked)
        try:
            with pytest.raises(SessionRuntimeError, match=code):
                await runtime.create_session(SessionOptions())
            assert not creates
            assert not sessions
            assert not runtime._runners
        finally:
            await runtime.close()

    asyncio.run(run())


async def failed_l2():
    return False


def test_two_tasks_overlap_and_stop_is_task_local_with_late_response_discarded(tmp_path):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        try:
            first, second = await asyncio.gather(
                runtime.create_session(SessionOptions()),
                runtime.create_session(SessionOptions()),
            )
            one, two = (
                runtime.runner_for_task(first.task_id),
                runtime.runner_for_task(second.task_id),
            )
            one.add_user_message("first-private-text")
            two.add_user_message("second-private-text")
            run_one, run_two = (
                asyncio.create_task(one.advance()),
                asyncio.create_task(two.advance()),
            )
            await asyncio.wait_for(
                asyncio.gather(*(session.entered.wait() for session in sessions)), 1
            )
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
        finally:
            await runtime.close()
        assert all(session.closed == 1 for session in sessions)
        assert b"first-private-text" not in (tmp_path / "sessions.db").read_bytes()

    asyncio.run(run())


@pytest.mark.parametrize("error", [CodexAuthenticationError(), OSError("private-error")])
def test_auth_or_unclassified_failure_halts_only_own_task(tmp_path, error):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        try:
            first = await runtime.create_session(SessionOptions())
            second = await runtime.create_session(SessionOptions())
            one, two = (
                runtime.runner_for_task(first.task_id),
                runtime.runner_for_task(second.task_id),
            )
            sessions[0].error = error
            one.add_user_message("one")
            two.add_user_message("two")
            operations = [asyncio.create_task(runner.advance()) for runner in (one, two)]
            await asyncio.wait_for(
                asyncio.gather(*(session.entered.wait() for session in sessions)), 1
            )
            sessions[0].release.set()
            if isinstance(error, CodexAuthenticationError):
                assert (await operations[0]).status == "halted"
                assert (
                    await runtime.snapshot(first.task_id)
                ).connection_code == "CODEX_AUTH_FAILED"
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
        finally:
            await runtime.close()

    asyncio.run(run())


def test_l2_once_and_same_task_rejects_overlapping_input(tmp_path):
    async def run():
        checks = []

        async def check():
            checks.append(True)
            await asyncio.sleep(0)
            return True

        runtime, sessions = make_runtime(tmp_path, l2=check)
        try:
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
        finally:
            await runtime.close()

    asyncio.run(run())


def test_old_epoch_cannot_stop_fresh_task(tmp_path):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        try:
            state = await runtime.create_session(SessionOptions())
            with pytest.raises(SessionRuntimeError, match="SESSION_STATE_MISMATCH"):
                await runtime.stop_session(state.task_id, state.epoch + 1, ControlAction.CANCEL)
            assert (await runtime.snapshot(state.task_id)).status is TaskStatus.RUNNING
            assert sessions[0].closed == 0
        finally:
            await runtime.close()

    asyncio.run(run())


def test_create_failure_closes_unadopted_session(tmp_path, monkeypatch):
    async def run():
        runtime, sessions = make_runtime(tmp_path)

        async def fail(*args, **kwargs):
            raise StoreError()

        monkeypatch.setattr(runtime.store, "create_task", fail)
        try:
            with pytest.raises(StoreError):
                await runtime.create_session(SessionOptions())
            assert sessions[0].closed == 1
            assert not runtime._runners
            assert not runtime._sessions
        finally:
            await runtime.close()

    asyncio.run(run())


def test_close_during_factory_creation_reclaims_late_session(tmp_path):
    async def run():
        runtime, _ = make_runtime(tmp_path)
        entered = asyncio.Event()
        session = Session()

        async def factory(state, options, resources):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return session

        runtime._session_factory = factory
        creation = asyncio.create_task(runtime.create_session(SessionOptions()))
        await entered.wait()
        await runtime.close()
        with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
            await creation
        assert session.closed == 1
        assert not runtime._sessions
        assert not runtime._runners

    asyncio.run(run())


def test_blocked_close_is_bounded_and_other_task_cleanup_runs(tmp_path, monkeypatch):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
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
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        try:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await asyncio.wait_for(runtime.close(), 1)
            assert entered.is_set()
            assert sessions[0].interrupted == sessions[0].closed == 1
            assert sessions[1].interrupted == sessions[1].closed == 1
            assert first.task_id in runtime._sessions
            assert runtime._cleanup_unconfirmed
        finally:
            released.set()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_stubborn_factory_close_is_bounded_and_late_session_reclaimed(tmp_path, monkeypatch):
    async def run():
        runtime, _ = make_runtime(tmp_path)
        entered, released = asyncio.Event(), asyncio.Event()
        session = Session()

        async def factory(state, options, resources):
            entered.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                await released.wait()
            return session

        runtime._session_factory = factory
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        creation = asyncio.create_task(runtime.create_session(SessionOptions()))
        await entered.wait()
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await asyncio.wait_for(runtime.close(), 1)
        assert not creation.done()
        released.set()
        with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
            await creation
        assert session.closed == 1
        assert not runtime._creating
        assert runtime._close_store is None

    asyncio.run(run())


def test_failed_creation_cleanup_is_bounded(tmp_path, monkeypatch):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
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
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        try:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await asyncio.wait_for(runtime.create_session(SessionOptions()), 1)
            assert runtime._sessions
            assert runtime._resources
            assert not runtime._creating
        finally:
            released.set()
            await asyncio.sleep(0)
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await runtime.close()

    asyncio.run(run())


def test_task_cleanup_uncertainty_latches_without_stopping_other_task(tmp_path):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        confirmations = []

        async def confirm(resources):
            # The first Task's exit is unproven once; every later answer is yes.
            confirmations.append(resources.task_id)
            return resources.task_id != first.task_id or confirmations.count(first.task_id) > 1

        runtime._preparation = replace(runtime._preparation, confirm_exit=confirm)
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        assert runtime.creation_blocker() == "CODEX_CLEANUP_UNCONFIRMED"
        assert first.task_id in runtime._resources  # never erased without proof
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

    asyncio.run(run())


@pytest.mark.parametrize("exits_in_time", [True, False])
def test_confirm_exit_has_its_own_bound_longer_than_cleanup(tmp_path, monkeypatch, exits_in_time):
    """PR #35 A review: C2.12 close() may wait its grace period before it confirms (I9)."""

    async def run():
        runtime, _ = make_runtime(tmp_path)
        first = await runtime.create_session(SessionOptions())
        release = asyncio.Event()
        confirmations = []

        async def confirm(resources):
            confirmations.append(resources.task_id)
            if resources.task_id == first.task_id:
                await asyncio.sleep(0.1)  # longer than the cleanup bound below
                if not exits_in_time:
                    await release.wait()
            return True

        runtime._preparation = replace(runtime._preparation, confirm_exit=confirm)
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        monkeypatch.setattr(
            "guard_app.services.session_runtime.CONFIRM_EXIT_TIMEOUT_S",
            1.0 if exits_in_time else 0.2,
        )
        stopping = runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
        try:
            if exits_in_time:
                await asyncio.wait_for(stopping, 2)
                assert first.task_id not in runtime._resources
                assert runtime.creation_blocker() is None
                second = await runtime.create_session(SessionOptions())
                assert second.status is TaskStatus.RUNNING
            else:
                with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                    await asyncio.wait_for(stopping, 2)
                assert first.task_id in runtime._resources  # never erased without proof
                assert runtime.creation_blocker() == "CODEX_CLEANUP_UNCONFIRMED"
        finally:
            release.set()
            if exits_in_time:
                await runtime.close()
            else:
                with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                    await runtime.close()
        assert confirmations.count(first.task_id) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["blocked", "raises"])
def test_lost_interrupt_alone_leaves_cleanup_confirmed(tmp_path, monkeypatch, failure):
    """A88 QA: interrupt is a courtesy; close + confirm_exit + cleanup prove the stop (I9)."""

    async def run():
        runtime, sessions = make_runtime(tmp_path)
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        released = asyncio.Event()

        async def interrupt():
            sessions[0].interrupted += 1
            if failure == "raises":
                raise OSError("private-interrupt-error")
            await released.wait()

        sessions[0].interrupt = interrupt
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        try:
            await asyncio.wait_for(
                runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL), 1
            )
            assert sessions[0].interrupted == sessions[0].closed == 1
            assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
            assert first.task_id not in runtime._sessions
            assert first.task_id not in runtime._resources
            assert not runtime._cleanup_unconfirmed
            assert runtime.creation_blocker() is None
            assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
            third = await runtime.create_session(SessionOptions())
            assert third.status is TaskStatus.RUNNING
        finally:
            released.set()
            await runtime.close()
        assert not runtime._cleanup_unconfirmed

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_succeeds", [True, False])
def test_stop_during_failure_cleanup_adopts_its_result(tmp_path, monkeypatch, cleanup_succeeds):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        snapshot = await runtime.create_session(SessionOptions())
        session = sessions[0]
        closing = asyncio.Event()
        finish = asyncio.Event()

        async def slow_close():
            session.closed += 1
            closing.set()
            await finish.wait()

        session.aclose = slow_close
        if not cleanup_succeeds:
            monkeypatch.setattr(
                "guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.05
            )
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
            assert not runtime._cleanup_unconfirmed
        else:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await stopping
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await advancing
            finish.set()
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await runtime.close()
        assert session.closed == 1  # one cleanup, never a second run

    asyncio.run(run())


def test_creation_blocker_reports_known_refusals_in_creation_order(tmp_path):
    async def run():
        store = SQLiteStore(tmp_path / "blocker.db", audit_id_factory=lambda: "unit-audit")
        bare = SessionRuntime(store, close_store=store.close)
        assert bare.creation_blocker() == "CODEX_CONFIG_UNAVAILABLE"
        await bare.close()
        assert bare.creation_blocker() == "SESSION_UNAVAILABLE"
        runtime, _ = make_runtime(tmp_path)
        assert runtime.creation_blocker() is None
        runtime._session_factory = None
        assert runtime.creation_blocker() == "CODEX_SESSION_UNAVAILABLE"
        runtime._cleanup_unconfirmed = True
        assert runtime.creation_blocker() == "CODEX_CLEANUP_UNCONFIRMED"
        runtime._cleanup_unconfirmed = False
        failed, _ = make_runtime(tmp_path, l2=None)
        with pytest.raises(SessionRuntimeError, match="CODEX_L2_UNAVAILABLE"):
            await failed.create_session(SessionOptions())
        # A101: the first refusal's code stays the blocker and answers later tries.
        assert failed.creation_blocker() == "CODEX_L2_UNAVAILABLE"
        with pytest.raises(SessionRuntimeError, match="^CODEX_L2_UNAVAILABLE$"):
            await failed.create_session(SessionOptions())
        await failed.close()
        await runtime.close()

    asyncio.run(run())


def test_product_bootstrap_without_c_components_starts_and_refuses_sessions(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    async def run():
        bindings = await bootstrap_product(str(tmp_path / "product.db"))
        try:
            assert bindings.human_control is None
            assert bindings.create_session is not None
            assert bindings.session_blocker is not None
            assert bindings.session_blocker() == "CODEX_CONFIG_UNAVAILABLE"
            with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_UNAVAILABLE"):
                await bindings.create_session(SessionOptions())
        finally:
            assert bindings.close is not None
            await bindings.close()

    asyncio.run(run())
    assert (tmp_path / "product.db").exists()


def test_product_bootstrap_starts_and_keeps_a_startup_refusal_as_the_blocker(tmp_path):
    """A101: a refused app-wide preparation blocks sessions, not the app (10.4
    refuses Task starts). It runs once at start and is not retried."""
    from guard_app.services.session_runtime import bootstrap_product

    preparation = synthetic_preparation(l2=None)
    startups: list[None] = []

    async def startup() -> StartupReceipt:
        startups.append(None)
        return await preparation.prepare_startup()

    async def run():
        bindings = await bootstrap_product(
            str(tmp_path / "failed.db"), preparation=replace(preparation, prepare_startup=startup)
        )
        try:
            assert bindings.session_blocker is not None
            assert bindings.create_session is not None
            assert bindings.session_blocker() == "CODEX_L2_UNAVAILABLE"
            with pytest.raises(SessionRuntimeError, match="^CODEX_L2_UNAVAILABLE$"):
                await bindings.create_session(SessionOptions())
        finally:
            assert bindings.close is not None
            await bindings.close()

    asyncio.run(run())
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
    ids=["l2-missing", "callback-failed"],
)
def test_a_startup_refusal_is_reported_with_its_own_code(tmp_path, receipt, code):
    async def run():
        async def startup() -> StartupReceipt:
            if receipt is None:
                raise RuntimeError("raw-startup-detail")
            return receipt

        runtime, _ = make_runtime(tmp_path)
        runtime._preparation = replace(synthetic_preparation(), prepare_startup=startup)
        try:
            await runtime.prepare_startup()
            return runtime.creation_blocker()
        finally:
            await runtime.close()

    assert asyncio.run(run()) == code


def test_snapshot_carries_the_store_watermark_and_unclassified_flag(tmp_path):
    from guard_app.domain.enums import Label

    async def run():
        runtime, _ = make_runtime(tmp_path)
        try:
            created = await runtime.create_session(SessionOptions())
            assert created.label is Label.PUBLIC
            assert created.unclassified is False
        finally:
            await runtime.close()

    asyncio.run(run())


def test_new_task_row_uses_the_loaded_profile_not_a_placeholder(tmp_path):
    async def run():
        runtime, _ = make_runtime(tmp_path)
        try:
            created = await runtime.create_session(SessionOptions())
            state = await runtime.store.load_task(created.task_id)
            assert (state.profile_id, state.policy_version) == ("unit-profile", "unit-policy-v1")
        finally:
            await runtime.close()

    asyncio.run(run())


def test_missing_guard_profile_refuses_before_any_task_row(tmp_path):
    async def run():
        store = SQLiteStore(tmp_path / "noprofile.db", audit_id_factory=lambda: "unit-audit")
        sessions = []

        async def factory(state, options, resources):
            sessions.append(state)
            raise AssertionError("must not start")

        runtime = SessionRuntime(
            store,
            session_factory=factory,
            preparation=synthetic_preparation(),
            close_store=store.close,
        )
        try:
            assert runtime.creation_blocker() == "GUARD_PROFILE_UNAVAILABLE"
            with pytest.raises(SessionRuntimeError, match="GUARD_PROFILE_UNAVAILABLE"):
                await runtime.create_session(SessionOptions())
            assert sessions == []
        finally:
            await runtime.close()

    asyncio.run(run())


class StoppableSession(Session):
    def __init__(self):
        super().__init__()
        self.stop_handler = None

    def set_stop_handler(self, handler):
        self.stop_handler = handler


def test_session_stop_outside_send_halts_only_its_task_and_republishes(tmp_path):
    """L3 item after a finished turn: no send() raises, so the owner stops the Task (I9)."""

    async def run():
        runtime, sessions = make_runtime(tmp_path, session_type=StoppableSession)
        changed = asyncio.Event()
        events = []

        def listener(task_id):
            events.append(task_id)
            changed.set()

        try:
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
            assert not runtime._cleanup_unconfirmed
            assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
            assert sessions[1].closed == 0
        finally:
            await runtime.close()
        # After shutdown a late stop report is ignored.
        sessions[1].stop_handler("CODEX_L3_VIOLATION")
        await asyncio.sleep(0)
        assert events == [first.task_id]

    asyncio.run(run())


def test_snapshot_offers_the_waiting_approval_and_the_listener_sees_wait_start_and_end(
    tmp_path,
):
    """The approval offered is Guard's id for this trusted Task, not model text (I3)."""

    async def run():
        approvals = ApprovalOutcomes()
        runtime, _ = make_runtime(tmp_path, approvals=approvals)
        events = []
        try:
            runtime.bindings().task_events(events.append)
            first = await runtime.create_session(SessionOptions())
            second = await runtime.create_session(SessionOptions())
            assert (await runtime.snapshot(first.task_id)).approval_id is None
            pending = Pending(
                request_id="request-1",
                approval_id="approval-1",
                reasons=(Reason.INTERNAL_TO_EXTERNAL,),
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
        finally:
            await runtime.close()

    asyncio.run(run())


class FinalResult:
    def __init__(self):
        self.final = asyncio.get_running_loop().create_future()

    async def wait(self):
        return await asyncio.shield(self.final)


def test_cancelled_wait_clears_the_offered_approval_but_not_guards_result():
    """NOTIFY-01: the watchdog cancels only the wait; the projection stops offering it."""

    async def run():
        approvals = ApprovalOutcomes()
        pending = Pending(
            request_id="request-1",
            approval_id="approval-1",
            reasons=(Reason.INTERNAL_TO_EXTERNAL,),
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

    asyncio.run(run())


class StreamingSession(Session):
    def __init__(self):
        super().__init__()
        self.partial_handler = None

    def set_partial_handler(self, handler):
        self.partial_handler = handler


def test_streamed_pieces_carry_their_own_task_and_epoch_until_shutdown(tmp_path):
    """A90: the runtime, not the session or model, names the Task of a piece (I9)."""

    async def run():
        runtime, sessions = make_runtime(tmp_path, session_type=StreamingSession)
        pieces = []
        try:
            runtime.bindings().answer_partials(lambda *piece: pieces.append(piece))
            first = await runtime.create_session(SessionOptions())
            second = await runtime.create_session(SessionOptions())
            sessions[1].partial_handler("beta")
            sessions[0].partial_handler("alpha")
            assert pieces == [(second.task_id, 1, "beta"), (first.task_id, 1, "alpha")]
        finally:
            await runtime.close()
        sessions[0].partial_handler("after-close")
        assert len(pieces) == 2

    asyncio.run(run())
