"""B6-05 proposals: authoritative snapshot queries without payload disclosure."""

import asyncio
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, Decision, Label, RequestStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import AccessResult, ProposedCall
from guard_app.domain.queries import ApprovalListLane, ApprovalListQuery, CallListQuery, ListSort
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_pending_notifications import _Clock
from tests.unit.test_store_recovery import RUN, _close, _open
from tests.unit.test_store_v3_migration import _v2_database

STAMP = "2026-10-28T00:00:00.000000+00:00"
LATER = "2026-10-28T00:01:00.000000+00:00"
EXPIRES = "2026-10-28T00:02:00.000000+00:00"
RAW_MARKER = "PRIVATE_ARGUMENT_AND_RESULT_MARKER"


def approval_query(lane=ApprovalListLane.PENDING, **changes):
    return ApprovalListQuery(lane=lane, sort=ListSort.OLDEST, offset=0, limit=100, **changes)


def call_query(**changes):
    values = {"sort": ListSort.OLDEST, "offset": 0, "limit": 100}
    values.update(changes)
    return CallListQuery(**values)


def _seed(connection):
    for task in ("task-1", "task-2"):
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task, 1, "profile", "policy", 1, 0, 0, "running", 0, 0, STAMP),
        )
    for request, task, seq, server, status, reasons in (
        ("a_req", "task-1", 1, "docs", "WAITING_APPROVAL", '["ACCESS_DENY"]'),
        ("abreq", "task-1", 2, "docs", "DENIED", '["ACCESS_ERROR"]'),
        ("third", "task-2", 1, "drive", "SUCCEEDED", '["OK"]'),
        ("noaudit", "task-2", 2, "drive", "DENIED", None),
    ):
        connection.execute(
            "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,"
            "destination_id,arguments_digest,decision,reasons,status,dispatch_reserved_at_utc,"
            "epoch,kind,row_revision,legacy_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request,
                task,
                seq,
                server,
                "tool",
                None,
                "a" * 64,
                1,
                reasons,
                status,
                None,
                1,
                "read",
                2,
                None,
            ),
        )
    for approval, request, status, created, decided in (
        ("a_approval", "a_req", "PENDING", STAMP, None),
        ("abapproval", "abreq", "REJECTED", STAMP, LATER),
        ("third-approval", "third", "EXPIRED", LATER, LATER),
    ):
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (approval, request, "b" * 64, "operator", status, created, EXPIRES, decided, None, 4),
        )
    events = (
        ("d1", "a_req", "task-1", "DECIDED", "ACCESS_DENY", None, STAMP),
        ("d2", "abreq", "task-1", "DECIDED", "ACCESS_ERROR", None, STAMP),
        ("r1", "abreq", "task-1", "APPROVAL_REJECTED", "ACCESS_DENY", None, STAMP),
        ("r2", "abreq", "task-1", "APPROVAL_REJECTED", "ACCESS_ERROR", None, LATER),
        ("d3", "third", "task-2", "DECIDED", "OK", None, LATER),
        ("p3", "third", "task-2", "APPROVAL_PENDING", "OK", None, LATER),
        ("c3", "third", "task-2", "CONSUMED", "OK", None, LATER),
        ("l3", "third", "task-2", "DISPATCH_RESERVED", "OK", None, LATER),
        ("o3", "third", "task-2", "OBSERVED", "OK", 1, LATER),
        ("o4", "third", "task-2", "OBSERVED", "OK", 1, LATER),
        ("s3", "third", "task-2", "SEND_COMPLETED", "OK", None, LATER),
        ("w3", "third", "task-2", "REQUEST_WITHHELD", "OK", None, LATER),
        ("x3", "third", "task-2", "APPROVAL_EXPIRED", "APPROVAL_STALE", None, LATER),
    )
    for event_id, request, task, kind, reason, label, at in events:
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            (event_id, request, task, kind, reason, label, RAW_MARKER, at),
        )


@pytest.fixture
def seeded(tmp_path):
    store = SQLiteStore(tmp_path / "queries.db")
    store._run_on_writer(_seed)
    try:
        yield store
    finally:
        store.close()
        assert store._writer.wait_closed_sync(5)


def test_approval_lanes_search_sort_pagination_and_latest_reason(seeded):
    async def scenario():
        pending = await seeded.list_approvals(approval_query())
        assert pending.total_count == 1
        assert [row.approval_id for row in pending.rows] == ["a_approval"]
        assert pending.rows[0].reason_code is None
        assert pending.rows[0].row_revision == 4
        history = await seeded.list_approvals(approval_query(ApprovalListLane.HISTORY))
        assert history.total_count == 2
        assert [row.approval_id for row in history.rows] == ["abapproval", "third-approval"]
        assert history.rows[0].reason_code is Reason.ACCESS_ERROR
        assert history.rows[1].reason_code is Reason.APPROVAL_STALE
        literal = await seeded.list_approvals(approval_query(search="a_"))
        assert [row.approval_id for row in literal.rows] == ["a_approval"]
        task_prefix = await seeded.list_approvals(
            approval_query(ApprovalListLane.HISTORY, search="task-2")
        )
        assert [row.approval_id for row in task_prefix.rows] == ["third-approval"]
        request_prefix = await seeded.list_approvals(
            approval_query(ApprovalListLane.HISTORY, search="abreq")
        )
        assert [row.approval_id for row in request_prefix.rows] == ["abapproval"]
        assert not (
            await seeded.list_approvals(approval_query(search="a_", server_id="drive"))
        ).rows
        newest = await seeded.list_approvals(
            ApprovalListQuery(
                lane=ApprovalListLane.HISTORY, sort=ListSort.NEWEST, offset=1, limit=1
            )
        )
        assert newest.total_count == 2
        assert [row.approval_id for row in newest.rows] == ["abapproval"]
        assert pending.as_of_utc.tzinfo is not None

    asyncio.run(scenario())


def test_call_list_detail_and_payload_redaction(seeded):
    async def scenario():
        calls = await seeded.list_calls(call_query())
        assert calls.total_count == 4
        assert [row.request_id for row in calls.rows] == ["a_req", "abreq", "third", "noaudit"]
        newest = await seeded.list_calls(call_query(sort=ListSort.NEWEST, limit=2))
        assert [row.request_id for row in newest.rows] == ["third", "abreq"]
        assert newest.total_count == 4
        next_page = await seeded.list_calls(call_query(sort=ListSort.NEWEST, offset=2, limit=2))
        assert [row.request_id for row in next_page.rows] == ["a_req", "noaudit"]
        task = await seeded.list_calls(call_query(task_id="task-1", sort=ListSort.NEWEST))
        assert [row.call_seq for row in task.rows] == [2, 1]
        filtered = await seeded.list_calls(call_query(server_id="drive"))
        assert filtered.total_count == 2
        assert calls.rows[0].decision is Decision.ASK
        assert calls.rows[0].reason_codes == (Reason.ACCESS_DENY,)
        assert calls.rows[0].approval_status is ApprovalStatus.PENDING
        detail = await seeded.get_call("third")
        assert detail is not None
        assert detail.observed_label is Label.INTERNAL
        assert [stage.stage_code for stage in detail.stages] == [
            "DISPATCHING",
            "DECIDED",
            "OBSERVED",
            "WAITING_APPROVAL",
            "COMMITTED",
            "WITHHELD",
        ]
        assert (await seeded.get_call("absent")) is None
        for value in (calls, newest, filtered, detail):
            assert RAW_MARKER not in repr(value)
            assert RAW_MARKER not in repr(value.model_dump())

    asyncio.run(scenario())


def test_invalid_stored_reason_fails_closed_without_original(seeded):
    seeded._run_on_writer(
        lambda connection: (
            connection.execute(
                "UPDATE requests SET reasons=? WHERE request_id=?", ('["PRIVATE_UNKNOWN"]', "a_req")
            ).rowcount
        )
    )

    async def scenario():
        for operation in (seeded.list_calls(call_query()), seeded.get_call("a_req")):
            with pytest.raises(StoreError) as error:
                await operation
            assert "PRIVATE_UNKNOWN" not in repr(error.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["task", "request"])
def test_broken_approval_relation_returns_fixed_error_without_stored_ids(seeded, missing):
    def corrupt(connection):
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            if missing == "task":
                connection.execute(
                    "UPDATE requests SET task_id=? WHERE request_id=?",
                    ("PRIVATE_MISSING_TASK", "abreq"),
                )
            else:
                connection.execute("DELETE FROM requests WHERE request_id=?", ("abreq",))
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    seeded._run_on_writer(corrupt)

    async def scenario():
        operations = [seeded.list_approvals(approval_query(ApprovalListLane.HISTORY))]
        if missing == "task":
            operations.extend((seeded.list_calls(call_query()), seeded.get_call("abreq")))
        for operation in operations:
            with pytest.raises(StoreError) as error:
                await operation
            assert error.value.reason is Reason.INTERNAL_ERROR
            assert "PRIVATE_MISSING_TASK" not in repr(error.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["unknown_reason", "null_reason", "wrong_task"])
def test_corrupt_terminal_approval_audit_fails_closed(seeded, damage):
    def corrupt(connection):
        if damage == "unknown_reason":
            return connection.execute(
                "UPDATE audit_events SET reason_code=? WHERE event_id='r2'", ("PRIVATE_BAD",)
            ).rowcount
        if damage == "null_reason":
            return connection.execute(
                "UPDATE audit_events SET reason_code=NULL WHERE event_id='r2'"
            ).rowcount
        return connection.execute(
            "UPDATE audit_events SET task_id=? WHERE event_id='r2'", ("task-2",)
        ).rowcount

    assert seeded._run_on_writer(corrupt) == 1

    async def scenario():
        with pytest.raises(StoreError) as error:
            await seeded.list_approvals(approval_query(ApprovalListLane.HISTORY))
        assert "PRIVATE_BAD" not in repr(error.value)

    asyncio.run(scenario())


def test_approval_timestamp_ties_use_approval_id_for_both_directions(seeded):
    def add(connection):
        connection.execute(
            "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,"
            "arguments_digest,status,epoch,kind,row_revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("same-time", "task-1", 3, "docs", "tool", "a" * 64, "DENIED", 1, "read", 0),
        )
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "aaapproval",
                "same-time",
                "b" * 64,
                "operator",
                "REJECTED",
                STAMP,
                EXPIRES,
                LATER,
                None,
                0,
            ),
        )

    seeded._run_on_writer(add)

    async def scenario():
        oldest = await seeded.list_approvals(approval_query(ApprovalListLane.HISTORY))
        assert [row.approval_id for row in oldest.rows] == [
            "aaapproval",
            "abapproval",
            "third-approval",
        ]
        newest = await seeded.list_approvals(
            ApprovalListQuery(
                lane=ApprovalListLane.HISTORY, sort=ListSort.NEWEST, offset=0, limit=100
            )
        )
        assert [row.approval_id for row in newest.rows] == [
            "third-approval",
            "abapproval",
            "aaapproval",
        ]

    asyncio.run(scenario())


def test_real_guard_result_is_projected_without_raw_result(tmp_path):
    async def scenario():
        harness = _Harness(source_label=Label.PUBLIC)
        read = harness.registry.read.model_copy(
            update={
                "input_schema": {"type": "object", "properties": {"private": {"type": "string"}}}
            }
        )
        harness.registry.record = harness.registry.record.model_copy(
            update={"tools": (read, harness.registry.send)}
        )
        identifiers = count(1)
        store = SQLiteStore(
            tmp_path / "guard.db", audit_id_factory=lambda: f"event-{next(identifiers)}"
        )
        guard = None
        try:
            await store.create_task(harness.store.states["task"], created_at_utc=harness.now)
            guard = GuardService(
                store,
                harness.registry,
                harness.catalog,
                harness.transport,
                harness.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(harness.ids)}",
                approval_id_factory=lambda: f"approval-{next(harness.ids)}",
                next_call_seq=store.allocate_call_seq,
                failure_audit=harness.failure_audit,
                utc_now=lambda: harness.now,
                monotonic=lambda: harness.monotonic,
            )
            raw_call = ProposedCall.model_validate(
                {
                    "tool_ref": "docs__read",
                    "arguments": {"private": RAW_MARKER},
                    "model_call_id": "model-call",
                }
            )
            result = await guard.submit("task", raw_call)
            assert isinstance(result, Released)
            harness.access.result = AccessResult(allowed=False, reason="", error=False)
            denied = await guard.submit("task", _call())
            assert isinstance(denied, Denied)
            page = await store.list_calls(call_query(task_id="task"))
            assert len(page.rows) == 2
            assert page.rows[0].status is RequestStatus.SUCCEEDED
            assert page.rows[1].reason_codes == (Reason.ACCESS_DENY,)
            detail = await store.get_call(page.rows[0].request_id)
            assert detail is not None
            assert [stage.stage_code for stage in detail.stages] == [
                "DECIDED",
                "DISPATCHING",
                "OBSERVED",
            ]
            assert "RAW_SECRET_MARKER" not in repr(detail.model_dump())
            assert RAW_MARKER not in repr(detail.model_dump())
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["approve", "reject", "expire"])
def test_real_guard_approval_lifecycle_queries(tmp_path, action):
    async def scenario():
        clock = _Clock()
        harness = _Harness(label=Label.INTERNAL)
        identifiers = count(1)
        store = SQLiteStore(
            tmp_path / "approval.db",
            utc_now=lambda: clock.utc,
            audit_id_factory=lambda: f"event-{next(identifiers)}",
        )
        guard = None
        try:
            await store.create_task(harness.store.states["task"], created_at_utc=clock.utc)
            guard = GuardService(
                store,
                harness.registry,
                harness.catalog,
                harness.transport,
                harness.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: "request",
                approval_id_factory=lambda: "approval",
                next_call_seq=store.allocate_call_seq,
                failure_audit=harness.failure_audit,
                utc_now=lambda: clock.utc,
                monotonic=lambda: clock.mono,
                approval_sleep=clock.sleep,
            )
            pending = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(pending, Pending)
            page = await store.list_approvals(approval_query())
            assert page.total_count == 1
            assert page.rows[0].approval_id == pending.approval_id
            assert page.rows[0].status is ApprovalStatus.PENDING
            assert page.rows[0].reason_code is None
            if action == "expire":
                clock.utc += timedelta(seconds=300)
            await getattr(guard.human_control("operator"), action)(pending.approval_id)
            assert (await store.list_approvals(approval_query())).total_count == 0
            history = await store.list_approvals(approval_query(ApprovalListLane.HISTORY))
            assert history.total_count == 1
            expected_status = {
                "approve": ApprovalStatus.CONSUMED,
                "reject": ApprovalStatus.REJECTED,
                "expire": ApprovalStatus.EXPIRED,
            }[action]
            assert history.rows[0].status is expected_status
            detail = await store.get_call(pending.request_id)
            assert detail is not None
            assert detail.row.approval_status is expected_status
            codes = {stage.stage_code for stage in detail.stages}
            assert {"DECIDED", "WAITING_APPROVAL"} <= codes
            if action == "approve":
                assert {"DISPATCHING", "COMMITTED"} <= codes
                assert len(harness.transport.calls) == 1
            else:
                assert "DISPATCHING" not in codes
                assert not harness.transport.calls
            assert RAW_MARKER not in repr(history.model_dump())
            assert "RAW_SECRET_MARKER" not in repr(detail.model_dump())
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_legacy_migration_query_preserves_unknown_kind_epoch_and_maps_reason(tmp_path):
    path = tmp_path / "legacy.db"
    _v2_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tasks SET created_at_utc=?", (STAMP,))
        connection.execute("UPDATE approvals SET created_at_utc=?,expires_at_utc=?", (STAMP, LATER))
        connection.execute("UPDATE audit_events SET at_utc=?", (STAMP,))

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            detail = await store.get_call("r-a")
            assert detail is not None
            assert detail.row.epoch is None
            assert detail.row.kind is None
            assert detail.row.reason_codes == (Reason.ACCESS_DENY,)
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_explicit_query_gate_transitions(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "gate.db", startup="explicit")

        async def rejected():
            for operation in (
                store.list_calls(call_query()),
                store.list_approvals(approval_query()),
                store.get_call("missing"),
            ):
                with pytest.raises(StoreError):
                    await operation

        async def permitted():
            assert (await store.list_calls(call_query())).total_count == 0
            assert (await store.list_approvals(approval_query())).total_count == 0
            assert await store.get_call("missing") is None

        try:
            await rejected()
            await store.prepare_schema(
                deadline=time.monotonic() + 20,
                begin_backup=lambda _: pytest.fail("empty DB needs no backup"),
            )
            await rejected()
            await store.recover(deadline=time.monotonic() + 20, run_id=RUN)
            await permitted()
            await store.activate_run()
            await permitted()
            store.halt_execution(Reason.INTERNAL_ERROR)
            await permitted()
        finally:
            await _close(store)
        await rejected()

    asyncio.run(scenario())


def test_page_and_count_share_read_snapshot_even_if_other_connection_writes(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.db"
    store = SQLiteStore(path)
    store._run_on_writer(_seed)
    ready, release = threading.Event(), threading.Event()
    original = store._transaction

    @contextmanager
    def paused_transaction(*, write, **kwargs):
        with original(write=write, **kwargs) as connection:
            if not write:
                connection.execute("SELECT count(*) FROM requests").fetchone()
                ready.set()
                assert release.wait(10)
            yield connection

    monkeypatch.setattr(store, "_transaction", paused_transaction)

    def add_row():
        assert ready.wait(10)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,"
                "arguments_digest,status,epoch,kind,row_revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("added", "task-2", 3, "drive", "tool", "a" * 64, "DENIED", 1, "read", 0),
            )
        release.set()

    async def scenario():
        writer = asyncio.create_task(asyncio.to_thread(add_row))
        page = await store.list_calls(call_query())
        await writer
        assert page.total_count == len(page.rows) == 4
        assert "added" not in [row.request_id for row in page.rows]

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        store.close()
        assert store._writer.wait_closed_sync(5)
