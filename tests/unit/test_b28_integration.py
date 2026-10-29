"""A's use of PR #28: STOP-01 session stops and B3.10 policy changes (I6/I9/I10).

Real Guard, SQLiteStore and a scripted Codex app-server. Audit rows are read
back from SQLite to check that stops come from the system channel.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from guard_app.domain.enums import SystemStopReason, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.services.bridge import SessionOptions
from guard_app.services.guard import GuardService, PolicyChangeError, SystemControlError
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product
from guard_app.services.session_stops import stop_reason

from .fake_codex_app_server import THREAD_ID, FakeAppServer
from .test_codex_product_flow import TaskEvents, preparation, read_then_answer
from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs
from .test_session_runtime import StoppableSession, make_runtime


def test_session_stop_codes_map_to_the_agreed_stop_reasons(subtests):
    for case_index, (code, reason) in enumerate(
        [
            ("CODEX_L3_VIOLATION", SystemStopReason.L3_VIOLATION),
            ("CODEX_WATCHDOG_TIMEOUT", SystemStopReason.WATCHDOG_EXCEEDED),
            ("CODEX_AUTH_FAILED", SystemStopReason.AUTH_FAILED),
            ("CODEX_USAGE_LIMIT", SystemStopReason.USAGE_LIMIT),
            ("CODEX_TOOL_CALL_INVALID", SystemStopReason.PROTOCOL_FAILED),
            ("CODEX_SERVER_REQUEST_REFUSED", SystemStopReason.PROTOCOL_FAILED),
            ("CODEX_SESSION_FAILED", SystemStopReason.PROCESS_FAILED),
            ("CODEX_TURN_FAILED", SystemStopReason.PROCESS_FAILED),
            ("CODEX_TOOL_STOPPED", SystemStopReason.PROCESS_FAILED),
            ("SOMETHING_NEW", SystemStopReason.PROCESS_FAILED),
        ]
    ):
        with subtests.test(case=case_index):
            assert stop_reason(code) is reason


def audit_rows(db_path, task_id: str) -> list[tuple[str, str]]:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT event_type, reason_code FROM audit_events WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        ).fetchall()
    finally:
        connection.close()


def test_l3_stop_and_user_input_are_audited_on_the_system_channel(tmp_path) -> None:
    """STOP-01: the audit names a session stop reason, not a human cancel."""

    async def ask_then_answer(server: FakeAppServer, turn_id: str, _text: str) -> None:
        await server.request(
            "item/tool/requestUserInput",
            {"threadId": THREAD_ID, "turnId": turn_id, "itemId": "i1", "questions": []},
        )
        await server.agent(turn_id, "answered")
        server.complete(turn_id)

    db_path = tmp_path / "stop.db"
    servers: list[FakeAppServer] = []

    async def run():
        bindings = await bootstrap_product(
            str(db_path),
            preparation=preparation(servers, ask_then_answer),
            product=inputs(
                Peer([READ_DEFINITION]), Registry(tool_fingerprint(READ_DEFINITION)), document()
            ),
        )
        events = TaskEvents()
        try:
            assert bindings.task_events is not None
            bindings.task_events(events)
            assert bindings.create_session is not None
            assert bindings.snapshot is not None
            created = await bindings.create_session(SessionOptions())
            runner = bindings.runner_for_task(created.task_id)
            runner.add_user_message("go")
            assert (await asyncio.wait_for(runner.advance(), 5)).status == "complete"
            servers[0].item("turn-1", "commandExecution")  # L3 after the turn
            await events.reach(2)
            after = await bindings.snapshot(created.task_id)
            return created.task_id, after
        finally:
            assert bindings.close is not None
            await bindings.close()

    task_id, after = asyncio.run(run())
    assert after.status is TaskStatus.HALTED
    rows = audit_rows(db_path, task_id)
    assert ("SESSION_REQUEST_USER_INPUT", "OK") in rows
    assert ("SESSION_STOPPED", "SESSION_L3_VIOLATION") in rows
    assert all("CANCEL" not in event for event, _ in rows)


class FailingSystem:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    async def stop_task(self, identity, reason):
        self.calls.append((identity, reason))
        raise SystemControlError("unconfirmed")

    async def record_request_user_input(self, identity):
        pass


def test_failed_system_stop_is_never_shown_as_applied(tmp_path) -> None:
    """B: when stop_task fails Guard stays halted; the Task must not look stopped cleanly."""

    async def run():
        system = FailingSystem()
        runtime, sessions = make_runtime(
            tmp_path, session_type=StoppableSession, system_control=system
        )
        changed = asyncio.Event()
        seen = []

        def listener(task_id):
            seen.append(task_id)
            changed.set()

        try:
            runtime.bindings().task_events(listener)
            created = await runtime.create_session(SessionOptions())
            sessions[0].stop_handler("CODEX_L3_VIOLATION")
            for _ in range(200):
                if len(seen) >= 2:
                    break
                await asyncio.sleep(0.005)
            snapshot = await runtime.snapshot(created.task_id)
            return system.calls, snapshot, sessions[0]
        finally:
            await runtime.close()

    calls, snapshot, session = asyncio.run(run())
    [(identity, reason)] = calls
    assert identity.epoch == 1
    assert reason is SystemStopReason.L3_VIOLATION
    assert snapshot.connection_code == "SESSION_STOP_UNCONFIRMED"
    assert not snapshot.model_ready
    assert not snapshot.can_accept_message
    assert session.closed == 1


async def policy_product(
    tmp_path, servers, saved, *, save_result=True, script=read_then_answer, during_save=None
):
    async def saver(change) -> bool:
        saved.append(change.document.to_dict())
        if during_save is not None:
            await during_save()
        return save_result

    peer = Peer([READ_DEFINITION])
    bindings = await bootstrap_product(
        str(tmp_path / "policy.db"),
        preparation=preparation(servers, script),
        product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        profile_saver=saver,
    )
    return bindings, peer


def test_server_access_change_saves_then_applies_and_stops_old_sessions(tmp_path) -> None:
    """B3.10: prepare -> save -> apply. The old Task stops; a new Task gets the new rules."""

    servers: list[FakeAppServer] = []
    saved: list[dict[str, object]] = []

    async def run():
        bindings, peer = await policy_product(tmp_path, servers, saved)
        try:
            assert bindings.policy is not None
            assert bindings.policy.change_available
            assert bindings.policy_access is not None
            assert bindings.create_session is not None
            assert bindings.snapshot is not None
            before = bindings.policy
            old = await bindings.create_session(SessionOptions())
            view = await bindings.policy_access("docs", (), ("read",))
            old_after = await bindings.snapshot(old.task_id)
            fresh = await bindings.create_session(SessionOptions())
            runner = bindings.runner_for_task(fresh.task_id)
            runner.add_user_message("summarize the public document")
            update = await asyncio.wait_for(runner.advance(), 5)
            return before, view, old_after, update, peer
        finally:
            assert bindings.close is not None
            await bindings.close()

    before, view, old_after, update, peer = asyncio.run(run())
    [document_saved] = saved
    assert document_saved["access_rules"] == {"allow": [], "deny": [["docs", "read"]]}
    assert view.policy_version != before.policy_version
    assert view.deny == (("docs", "read"),)
    assert view.allow == ()
    assert old_after.status is not TaskStatus.RUNNING
    assert old_after.connection_code == "POLICY_CHANGED"
    assert not old_after.model_ready
    # The new Task runs under the new rules: the READ is denied, never dispatched.
    assert update.status == "complete"
    assert peer.calls == []
    assert servers[1].last_reply["result"]["success"] is False  # type: ignore[attr-defined]


def test_unsaved_or_invalid_access_change_applies_nothing(tmp_path, subtests):
    def check(save_result, allowed, denied, code, tmp_path):
        servers: list[FakeAppServer] = []
        saved: list[dict[str, object]] = []

        async def run():
            bindings, _peer = await policy_product(
                tmp_path, servers, saved, save_result=save_result
            )
            try:
                assert bindings.policy_access is not None
                assert bindings.create_session is not None
                assert bindings.snapshot is not None
                created = await bindings.create_session(SessionOptions())
                with pytest.raises(SessionRuntimeError) as refused:
                    await bindings.policy_access("docs", allowed, denied)
                after = await bindings.snapshot(created.task_id)
                return refused.value.code, after
            finally:
                assert bindings.close is not None
                await bindings.close()

        refused_code, after = asyncio.run(run())
        assert refused_code == code
        assert after.status is TaskStatus.RUNNING
        assert after.connection_code == "READY_CODEX"
        assert len(saved) == (1 if code == "POLICY_SAVE_FAILED" else 0)

    for case_index, (save_result, allowed, denied, code) in enumerate(
        [
            (False, (), ("read",), "POLICY_SAVE_FAILED"),
            (True, ("read",), ("read",), "POLICY_CHANGE_INVALID"),
            (True, ("missing",), (), "POLICY_CHANGE_INVALID"),
        ]
    ):
        with subtests.test(case=case_index):
            case_path = tmp_path / str(case_index)
            case_path.mkdir()
            check(save_result, allowed, denied, code, case_path)


def test_saved_but_unapplied_change_closes_guard_until_restart(tmp_path, monkeypatch, subtests):
    def check(failure, tmp_path, monkeypatch):
        """A104 (#33 B reply): the file holds the new rules but Guard does not.

        "refused": Guard rejects before applying and would keep its old rules.
        "failed": applying fails and Guard halts itself. Either way no new tool call
        runs, no session starts and nothing is saved again until a restart.
        """
        if failure == "refused":

            async def refuse(self, change, access_checker, *, task_source):
                raise PolicyChangeError("private-refusal")

            monkeypatch.setattr(GuardService, "apply_policy_change", refuse)
        else:

            async def fail(self, change, access_checker, task_source):
                raise StoreError(Reason.STATE_COMMIT_FAILED)

            monkeypatch.setattr(GuardService, "_apply_policy_change", fail)
        servers: list[FakeAppServer] = []
        saved: list[dict[str, object]] = []

        async def run():
            bindings, peer = await policy_product(tmp_path, servers, saved)
            try:
                assert bindings.policy_access is not None
                assert bindings.create_session is not None
                assert bindings.session_blocker is not None
                old = await bindings.create_session(SessionOptions())
                with pytest.raises(SessionRuntimeError) as refused:
                    await bindings.policy_access("docs", (), ("read",))
                blocker = bindings.session_blocker()
                with pytest.raises(SessionRuntimeError) as again:
                    await bindings.policy_access("docs", (), ("read",))
                with pytest.raises(SessionRuntimeError) as created:
                    await bindings.create_session(SessionOptions())
                runner = bindings.runner_for_task(old.task_id)
                runner.add_user_message("summarize the public document")
                update = await asyncio.wait_for(runner.advance(), 5)
                return (refused.value, again.value, created.value), blocker, update, peer
            finally:
                assert bindings.close is not None
                await bindings.close()

        errors, blocker, update, peer = asyncio.run(run())
        assert [error.code for error in errors] == ["POLICY_APPLY_FAILED"] * 3
        assert all("private" not in str(error) for error in errors)
        assert blocker == "POLICY_APPLY_FAILED"
        assert len(saved) == 1  # never saved a second time
        # The old session's READ is refused by the closed Guard, never dispatched.
        assert update.status == "complete"
        assert peer.calls == []
        assert servers[0].last_reply["result"]["success"] is False  # type: ignore[attr-defined]
        assert len(servers) == 1  # no new session was launched

    for case_index, failure in enumerate(["refused", "failed"]):
        with subtests.test(case=case_index), monkeypatch.context() as patch:
            case_path = tmp_path / str(case_index)
            case_path.mkdir()
            check(failure, case_path, patch)


class WatchedLock(asyncio.Lock):
    """Marks when someone has to wait for the lock (the policy change holds it)."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.contended.set()
        return await super().acquire()


def test_session_created_during_a_policy_change_takes_the_new_policy(tmp_path) -> None:
    """A105 (B3.12): a Task row is written under the policy lock with the rules in force.

    A session asked for while the change is saving gets its Codex process, then
    waits; its row is written after the change with the new policy_version, so
    the change does not stop it as an old-policy Task.
    """
    servers: list[FakeAppServer] = []
    saved: list[dict[str, object]] = []
    held: dict[str, object] = {}
    written_while_saving: list[bool] = []

    async def during_save() -> None:
        runtime = held["runtime"]
        held["creation"] = asyncio.ensure_future(runtime.create_session(SessionOptions()))
        held["saving"] = True
        waits = {
            asyncio.ensure_future(runtime._policy.lock.contended.wait()),
            asyncio.ensure_future(held["written"].wait()),
        }
        try:
            await asyncio.wait_for(asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED), 5)
        finally:
            for waiting in waits:
                waiting.cancel()
            held["saving"] = False

    async def run():
        bindings, _peer = await policy_product(tmp_path, servers, saved, during_save=during_save)
        try:
            assert bindings.create_session is not None
            assert bindings.policy_access is not None
            assert bindings.snapshot is not None
            runtime = bindings.create_session.__self__  # type: ignore[attr-defined]
            # The one internal seam left: only the lock shows the creation waits on it.
            runtime._policy.lock = WatchedLock()
            held.update(runtime=runtime, written=asyncio.Event(), saving=False)
            create_task = runtime.store.create_task

            async def recording_create(state, **kwargs):
                written_while_saving.append(held["saving"])
                held["written"].set()
                await create_task(state, **kwargs)

            runtime.store.create_task = recording_create
            view = await bindings.policy_access("docs", (), ("read",))
            created = await asyncio.wait_for(held["creation"], 5)
            row = await runtime.store.load_task(created.task_id)
            after = await bindings.snapshot(created.task_id)
            return view, row, after
        finally:
            assert bindings.close is not None
            await bindings.close()

    view, row, after = asyncio.run(run())
    assert written_while_saving == [False]
    assert row.policy_version == view.policy_version
    assert after.status is TaskStatus.RUNNING
    assert after.connection_code == "READY_CODEX"


def test_overlapping_server_access_changes_build_on_each_other(tmp_path) -> None:
    """A117 (B3.10): a change waiting for the policy lock starts from the document saved first.

    The bridge runs control commands on several workers, so two activation
    changes can overlap. One prepared from the document the other replaced
    would be lost as "unchanged" or refused by Guard as stale.
    """
    servers: list[FakeAppServer] = []
    saved: list[dict[str, object]] = []
    held: dict[str, object] = {}

    async def during_save() -> None:
        if "second" in held:
            return
        held["second"] = asyncio.ensure_future(held["access"]("docs", ("read",), ()))
        await asyncio.sleep(0)  # the second change runs up to the policy lock
        held["waited"] = not held["second"].done()

    async def run():
        bindings, _peer = await policy_product(tmp_path, servers, saved, during_save=during_save)
        try:
            assert bindings.policy_access is not None
            held["access"] = bindings.policy_access
            first = await bindings.policy_access("docs", (), ("read",))
            second = await asyncio.wait_for(held["second"], 5)
            return first, second
        finally:
            assert bindings.close is not None
            await bindings.close()

    first, second = asyncio.run(run())
    assert held["waited"]
    assert first.deny == (("docs", "read"),)
    assert [document["access_rules"] for document in saved] == [
        {"allow": [], "deny": [["docs", "read"]]},
        {"allow": [["docs", "read"]], "deny": []},
    ]
    assert second.allow == (("docs", "read"),)
    assert second.deny == ()
    assert second.policy_version != first.policy_version


def test_without_a_profile_saver_policy_change_is_unavailable(tmp_path) -> None:
    async def run():
        bindings = await bootstrap_product(
            str(tmp_path / "nosave.db"),
            preparation=preparation([], read_then_answer),
            product=inputs(
                Peer([READ_DEFINITION]), Registry(tool_fingerprint(READ_DEFINITION)), document()
            ),
        )
        try:
            return bindings.policy, bindings.policy_access
        finally:
            assert bindings.close is not None
            await bindings.close()

    policy, access = asyncio.run(run())
    assert policy is not None
    assert not policy.change_available
    assert access is None


def test_human_stop_with_guard_leaves_the_store_to_guard(tmp_path, subtests):
    def check(guard_stopped_first, tmp_path):
        """PR #29 B review 5: after the bridge's human cancel, A writes no HALTED itself.

        Nor before Guard's stop has landed (it may wait for the Task lock behind an
        in-flight submit): the row stays Guard's to write.
        """
        from guard_app.services.bridge import ControlAction

        db_path = tmp_path / "human.db"

        async def run():
            bindings = await bootstrap_product(
                str(db_path),
                preparation=preparation([], read_then_answer),
                product=inputs(
                    Peer([READ_DEFINITION]), Registry(tool_fingerprint(READ_DEFINITION)), document()
                ),
            )
            try:
                assert bindings.create_session is not None
                assert bindings.stop_session is not None
                assert bindings.human_control is not None
                assert bindings.snapshot is not None
                runtime = bindings.stop_session.__self__  # type: ignore[attr-defined]
                created = await bindings.create_session(SessionOptions())
                if guard_stopped_first:
                    # The bridge's order: Guard's human channel first, then the session.
                    await bindings.human_control.cancel(created.task_id)
                writes: list[str] = []
                set_task_status = runtime.store.set_task_status

                async def recording_status(task_id, *args, **kwargs):
                    writes.append(task_id)
                    return await set_task_status(task_id, *args, **kwargs)

                runtime.store.set_task_status = recording_status
                await bindings.stop_session(created.task_id, created.epoch, ControlAction.CANCEL)
                after = await bindings.snapshot(created.task_id)
                return created.task_id, after, writes
            finally:
                assert bindings.close is not None
                await bindings.close()

        task_id, after, writes = asyncio.run(run())
        assert writes == []
        assert after.status is (TaskStatus.HALTED if guard_stopped_first else TaskStatus.RUNNING)
        assert not after.can_accept_message
        assert all(event != "SESSION_STOPPED" for event, _ in audit_rows(db_path, task_id))

    for case_index, guard_stopped_first in enumerate([True, False]):
        with subtests.test(case=case_index):
            case_path = tmp_path / str(case_index)
            case_path.mkdir()
            check(guard_stopped_first, case_path)
