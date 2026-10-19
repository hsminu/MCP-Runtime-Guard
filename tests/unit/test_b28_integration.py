"""A's use of PR #28: STOP-01 session stops and B3.10 policy changes (I6/I9/I10).

Real Guard, SQLiteStore and a scripted Codex app-server. Audit rows are read
back from SQLite to check that stops come from the system channel.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from guard_app.domain.enums import SystemStopReason, TaskStatus
from guard_app.security.digest import tool_fingerprint
from guard_app.services.bridge import SessionOptions
from guard_app.services.guard import SystemControlError
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product, stop_reason

from .fake_codex_app_server import THREAD_ID, FakeAppServer
from .test_codex_product_flow import TaskEvents, preparation, read_then_answer
from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs
from .test_session_runtime import StoppableSession, make_runtime


@pytest.mark.parametrize(
    ("code", "reason"),
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
    ],
)
def test_session_stop_codes_map_to_the_agreed_stop_reasons(code, reason) -> None:
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
        runtime, sessions = make_runtime(tmp_path, session_type=StoppableSession)
        system = FailingSystem()
        runtime._system = system
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


async def policy_product(tmp_path, servers, saved, *, save_result=True, script=read_then_answer):
    async def saver(change) -> bool:
        saved.append(change.document.to_dict())
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


@pytest.mark.parametrize(
    ("save_result", "allowed", "denied", "code"),
    [
        (False, (), ("read",), "POLICY_SAVE_FAILED"),
        (True, ("read",), ("read",), "POLICY_CHANGE_INVALID"),
        (True, ("missing",), (), "POLICY_CHANGE_INVALID"),
    ],
)
def test_unsaved_or_invalid_access_change_applies_nothing(
    tmp_path, save_result, allowed, denied, code
) -> None:
    servers: list[FakeAppServer] = []
    saved: list[dict[str, object]] = []

    async def run():
        bindings, _peer = await policy_product(tmp_path, servers, saved, save_result=save_result)
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


def test_closing_the_assembly_ends_transport_then_closes_guard(tmp_path) -> None:
    """NOTIFY-01 shutdown order: transport first, then Guard.aclose (no new approvals)."""
    from uuid import uuid4

    from guard_app.services.product_assembly import assemble_guard
    from guard_app.storage.sqlite_store import SQLiteStore

    order: list[str] = []

    async def run():
        store = SQLiteStore(tmp_path / "close.db", audit_id_factory=lambda: f"audit-{uuid4().hex}")
        try:
            assembled = await assemble_guard(
                store,
                inputs(
                    Peer([READ_DEFINITION]), Registry(tool_fingerprint(READ_DEFINITION)), document()
                ),
            )
            close_transport, close_guard = assembled.transport.close, assembled.guard.aclose

            async def transport_closed():
                order.append("transport")
                await close_transport()

            async def guard_closed():
                order.append("guard")
                await close_guard()

            assembled.transport.close = transport_closed  # type: ignore[method-assign]
            assembled.guard.aclose = guard_closed  # type: ignore[method-assign]
            await assembled.aclose()
        finally:
            store.close()

    asyncio.run(run())
    assert order == ["transport", "guard"]


def test_human_stop_with_guard_leaves_the_store_to_guard(tmp_path) -> None:
    """PR #29 B review 5: after the bridge's human cancel, A writes no HALTED itself."""
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
            writes: list[str] = []

            async def no_direct_write(task_id):
                writes.append(task_id)

            runtime._halt_store_task = no_direct_write
            created = await bindings.create_session(SessionOptions())
            # The bridge's order: Guard's human channel first, then the session.
            await bindings.human_control.cancel(created.task_id)
            await bindings.stop_session(created.task_id, created.epoch, ControlAction.CANCEL)
            after = await bindings.snapshot(created.task_id)
            return created.task_id, after, writes
        finally:
            assert bindings.close is not None
            await bindings.close()

    task_id, after, writes = asyncio.run(run())
    assert writes == []
    assert after.status is TaskStatus.HALTED
    assert not after.can_accept_message
    assert all(event != "SESSION_STOPPED" for event, _ in audit_rows(db_path, task_id))
