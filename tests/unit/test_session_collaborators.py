"""A112: the session runtime's collaborators on their own (I9, 10.4, STOP-01, B3.12).

The runtime tests cover how they are composed; these pin each one's rule.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc

import pytest

from guard_app.domain.contracts import TaskIdentity
from guard_app.domain.enums import TaskStatus
from guard_app.domain.models import TaskState
from guard_app.services.catalog import GuardSettings
from guard_app.services.guard import SystemControlError
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_cleanup import BackgroundWork, CleanupLimits, SessionCleanup
from guard_app.services.session_policy import TaskPolicy
from guard_app.services.session_preparation import ConfigurationReceipt, TaskResources
from guard_app.services.session_stops import StopChannel

CONFIGURATION = ConfigurationReceipt("a" * 64, "b" * 64, "c" * 64)


def test_admission_refusals_keep_their_order_and_never_lift():
    admission = SessionAdmission()
    assert admission.refusal() is None
    admission.require_restart("POLICY_APPLY_FAILED")
    assert admission.refusal() == "POLICY_APPLY_FAILED"
    admission.mark_cleanup_unconfirmed()
    assert admission.refusal() == "CODEX_CLEANUP_UNCONFIRMED"
    admission.close()
    assert admission.refusal() == "SESSION_UNAVAILABLE"

    async def check():
        admission.check()

    with pytest.raises(SessionRuntimeError, match="^SESSION_UNAVAILABLE$"):
        asyncio.run(check())
    assert admission.cleanup_unconfirmed
    assert admission.restart_code == "POLICY_APPLY_FAILED"


def test_admission_refuses_a_creator_that_ignored_its_cancellation():
    """A preparation callback that swallows cancellation must not let creation go on (I9)."""

    async def run():
        admission = SessionAdmission()
        entered = asyncio.Event()

        async def creator():
            entered.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Future()
            admission.check()

        task = asyncio.create_task(creator())
        await entered.wait()
        task.cancel()
        with pytest.raises(SessionRuntimeError, match="^SESSION_UNAVAILABLE$"):
            await task
        admission.check()  # not being cancelled: admitted

    asyncio.run(run())


def test_cleanup_limits_default_to_the_reviewed_bounds():
    """PR #35 A review, A103: exit confirmation and folder removal outlast the session bound."""
    limits = CleanupLimits()
    assert (limits.session_s, limits.confirm_exit_s, limits.task_folder_s) == (0.5, 12.0, 5.0)


def _cleanup(admission: SessionAdmission) -> SessionCleanup:
    return SessionCleanup(None, admission, CleanupLimits(), BackgroundWork(), lambda _: None)


@pytest.mark.parametrize(
    "receipt",
    [
        lambda handle: object(),
        lambda handle: TaskResources("task-9", CONFIGURATION, "token-2", object()),
        lambda handle: TaskResources("task-2", CONFIGURATION, "", object()),
        lambda handle: TaskResources("task-2", CONFIGURATION, "t" * 257, object()),
        lambda handle: TaskResources("task-2", CONFIGURATION, "token-2", None),
        lambda handle: TaskResources("task-2", CONFIGURATION, "token-1", object()),
        lambda handle: TaskResources("task-2", CONFIGURATION, "token-2", handle),
    ],
    ids=[
        "not-a-receipt",
        "other-task",
        "empty-token",
        "long-token",
        "no-handle",
        "shared-token",
        "shared-handle",
    ],
)
def test_cleanup_never_owns_a_mismatched_or_shared_receipt(receipt):
    admission = SessionAdmission()
    cleanup = _cleanup(admission)
    handle = object()
    cleanup.claim_resources("task-1", TaskResources("task-1", CONFIGURATION, "token-1", handle))
    with pytest.raises(SessionRuntimeError, match="^CODEX_CLEANUP_UNCONFIRMED$"):
        cleanup.claim_resources("task-2", receipt(handle))
    assert cleanup.pending == {"task-1"}  # nothing to erase on another Task's behalf
    assert admission.refusal() == "CODEX_CLEANUP_UNCONFIRMED"


class _Session:
    async def send(self, text, effort=None):
        return text

    async def interrupt(self):
        pass

    async def aclose(self):
        pass


def test_cleanup_refuses_a_shared_session_but_still_closes_an_incomplete_one():
    cleanup = _cleanup(SessionAdmission())
    session = _Session()
    cleanup.claim_session("task-1", session)
    with pytest.raises(SessionRuntimeError, match="^CODEX_SESSION_INVALID$"):
        cleanup.claim_session("task-2", session)
    with pytest.raises(SessionRuntimeError, match="^CODEX_SESSION_INVALID$"):
        cleanup.claim_session("task-3", object())
    assert cleanup.pending == {"task-1", "task-3"}  # the factory's object is still cleaned up


def test_background_work_consumes_failures_without_raising_them():
    async def run():
        reported = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: reported.append(1))
        work = BackgroundWork()

        async def fail():
            raise SystemControlError("private-detail")

        await asyncio.wait({work.start(fail())})
        gc.collect()
        return reported

    assert asyncio.run(run()) == []


class _RowStore:
    def __init__(self, status):
        self.state = TaskState(
            task_id="task-1", epoch=1, profile_id="p", policy_version="v1", status=status
        )
        self.writes = []

    async def load_task(self, task_id):
        return self.state

    async def set_task_status(self, task_id, status, **kwargs):
        self.writes.append(status)
        self.state = self.state.model_copy(update={"status": status})
        return self.state


@pytest.mark.parametrize(
    ("status", "writes"),
    [
        (TaskStatus.RUNNING, [TaskStatus.HALTED]),
        (TaskStatus.HALTED, []),
        (TaskStatus.UNKNOWN_OUTCOME, []),  # an unknown SEND outcome is never rewritten
        (TaskStatus.CLOSED, []),
    ],
)
def test_stop_channel_without_guard_halts_only_a_live_row(status, writes):
    async def run():
        store = _RowStore(status)
        channel = StopChannel(store, None, SessionAdmission(), BackgroundWork())
        settled = []
        await channel.stop(TaskIdentity(task_id="task-1", epoch=1), "CODEX_L3_VIOLATION", settled)
        return store.writes, settled

    assert asyncio.run(run()) == (writes, [])


def test_stop_channel_reports_a_failed_system_stop_as_unconfirmed():
    async def run():
        class FailingSystem:
            async def stop_task(self, identity, reason):
                raise SystemControlError("unconfirmed")

        store = _RowStore(TaskStatus.RUNNING)
        channel = StopChannel(store, FailingSystem(), SessionAdmission(), BackgroundWork())
        settled = asyncio.get_running_loop().create_future()
        identity = TaskIdentity(task_id="task-1", epoch=1)
        await channel.stop(identity, "CODEX_L3_VIOLATION", settled.set_result)
        return await asyncio.wait_for(settled, 1), store.writes

    assert asyncio.run(run()) == (True, [])  # Guard's to write; A never writes HALTED


def test_task_policy_has_no_placeholder_profile_or_change_without_guard():
    admission = SessionAdmission()
    missing = TaskPolicy(None, admission)
    assert missing.blocker() == "GUARD_PROFILE_UNAVAILABLE"
    with pytest.raises(SessionRuntimeError, match="^GUARD_PROFILE_UNAVAILABLE$"):
        missing.task_state("task-1")
    assert not missing.available

    async def no_stops(task_ids):
        raise AssertionError("nothing may stop")

    with pytest.raises(SessionRuntimeError, match="^POLICY_CHANGE_UNAVAILABLE$"):
        asyncio.run(missing.change_server_access("docs", (), ("read",), no_stops))
    loaded = TaskPolicy(GuardSettings(profile_id="p", policy_version="v1"), admission)
    assert loaded.blocker() is None
    state = loaded.task_state("task-1")
    assert (state.epoch, state.status, state.profile_id, state.policy_version) == (
        1,
        TaskStatus.RUNNING,
        "p",
        "v1",
    )
