"""A-only injected sessions, mandatory L2 gate and Task concurrency (I7/I8/I9).

No actual Codex process/security-platform validation or T46 acceptance claim.
"""

from __future__ import annotations

import asyncio

import pytest

from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.services.bridge import ControlAction, SessionOptions
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


def make_runtime(tmp_path, *, l2=passed_l2):
    store = SQLiteStore(tmp_path / "sessions.db", audit_id_factory=lambda: "unit-audit")
    sessions = []

    async def factory(state, options, resources):
        assert state.status is TaskStatus.RUNNING
        assert options == SessionOptions()
        session = Session()
        sessions.append(session)
        return session

    runtime = SessionRuntime(
        store,
        session_factory=factory,
        preparation=synthetic_preparation(l2),
        close_store=store.close,
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


def test_blocked_interrupt_is_bounded_and_other_task_cleanup_runs(tmp_path, monkeypatch):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        first = await runtime.create_session(SessionOptions())
        await runtime.create_session(SessionOptions())
        entered = asyncio.Event()
        released = asyncio.Event()

        async def blocked():
            entered.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                await released.wait()

        sessions[0].interrupt = blocked
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        try:
            with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
                await asyncio.wait_for(runtime.close(), 1)
            assert entered.is_set()
            assert sessions[0].closed == 1
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


def test_task_cleanup_uncertainty_latches_without_stopping_other_task(tmp_path, monkeypatch):
    async def run():
        runtime, sessions = make_runtime(tmp_path)
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        released = asyncio.Event()

        async def blocked():
            await released.wait()

        sessions[0].interrupt = blocked
        monkeypatch.setattr("guard_app.services.session_runtime.SESSION_CLEANUP_TIMEOUT_S", 0.01)
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
        assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        runner = runtime.runner_for_task(second.task_id)
        runner.add_user_message("unaffected")
        sessions[1].release.set()
        assert (await runner.advance()).assistant_text == "answer:unaffected"
        released.set()
        # Even a later successful shutdown retry cannot claim the earlier
        # process cleanup was confirmed. Bridge's restart gate must see this.
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await runtime.close()
        assert sessions[1].closed == 1

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
