"""The single approval view shares the list projection and its snapshot."""

import asyncio
import sqlite3
import threading

import pytest

from guard_app.domain.contracts import ApprovalRecord
from guard_app.domain.enums import ApprovalStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.queries import ApprovalListLane
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_store_queries import LATER, RAW_MARKER, _seed, approval_query


@pytest.fixture
def seeded_store(tmp_path):
    store = SQLiteStore(tmp_path / "approval-row.db")
    store._run_on_writer(_seed)
    try:
        yield store
    finally:
        store.close()
        assert store._writer.wait_closed_sync(5)


@pytest.mark.parametrize("status", list(ApprovalStatus))
def test_single_row_equals_list_projection_for_every_approval_status(seeded_store, status):
    def change(connection):
        decided = None if status is ApprovalStatus.PENDING else LATER
        consumed = LATER if status is ApprovalStatus.CONSUMED else None
        connection.execute(
            "UPDATE approvals SET status=?,decided_at_utc=?,consumed_at_utc=? "
            "WHERE approval_id='a_approval'",
            (status.value, decided, consumed),
        )
        if status is not ApprovalStatus.PENDING:
            connection.execute(
                "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                (
                    "status-event",
                    "a_req",
                    "task-1",
                    f"APPROVAL_{status.value}",
                    "ACCESS_DENY",
                    None,
                    RAW_MARKER,
                    LATER,
                ),
            )

    seeded_store._run_on_writer(change)

    async def check():
        lane = (
            ApprovalListLane.PENDING
            if status is ApprovalStatus.PENDING
            else ApprovalListLane.HISTORY
        )
        page = await seeded_store.list_approvals(approval_query(lane, search="a_approval"))
        row = await seeded_store.get_approval_row("a_approval")
        assert row is not None
        assert page.rows == (row,)
        assert row.status is status
        assert row.row_revision == 4
        assert row.reason_code is (None if status is ApprovalStatus.PENDING else Reason.ACCESS_DENY)
        assert RAW_MARKER not in repr(row)
        assert RAW_MARKER not in repr(row.model_dump())
        assert "snapshot_hash" not in type(row).model_fields
        assert "approver_id" not in type(row).model_fields
        assert isinstance(await seeded_store.get_approval("a_approval"), ApprovalRecord)

    asyncio.run(check())


def test_absent_and_invalid_id_match_call_lookup_behavior(seeded_store):
    async def check():
        assert await seeded_store.get_approval_row("absent") is None
        with pytest.raises(StoreError, match="INTERNAL_ERROR") as approval_error:
            await seeded_store.get_approval_row("")
        with pytest.raises(StoreError, match="INTERNAL_ERROR") as call_error:
            await seeded_store.get_call("")
        assert approval_error.value.reason is call_error.value.reason is Reason.INTERNAL_ERROR

    asyncio.run(check())


def test_single_approval_row_uses_one_snapshot_during_external_write(
    seeded_store, tmp_path, monkeypatch
):
    ready, release = threading.Event(), threading.Event()
    original = seeded_store._query_approval_locked

    def paused(connection, approval_id):
        ready.set()
        assert release.wait(10)
        return original(connection, approval_id)

    monkeypatch.setattr(seeded_store, "_query_approval_locked", paused)

    def update():
        assert ready.wait(10)
        try:
            with sqlite3.connect(tmp_path / "approval-row.db") as connection:
                connection.execute(
                    "UPDATE approvals SET status='REJECTED',decided_at_utc=?,row_revision=5 "
                    "WHERE approval_id='a_approval'",
                    (LATER,),
                )
                connection.execute(
                    "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                    (
                        "new-event",
                        "a_req",
                        "task-1",
                        "APPROVAL_REJECTED",
                        "ACCESS_DENY",
                        None,
                        RAW_MARKER,
                        LATER,
                    ),
                )
        finally:
            release.set()

    async def check():
        writer = asyncio.create_task(asyncio.to_thread(update))
        row = await seeded_store.get_approval_row("a_approval")
        await writer
        assert row is not None
        assert (row.status, row.row_revision, row.reason_code) == (ApprovalStatus.PENDING, 4, None)

    try:
        asyncio.run(check())
    finally:
        release.set()
