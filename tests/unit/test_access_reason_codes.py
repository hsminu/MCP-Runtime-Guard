"""AGT-04: persisted reason compatibility and fail-closed recovery."""

import asyncio
import json
import sqlite3
import time
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import Denied
from guard_app.domain.enums import Label, StorePhase, StoreStartFailure
from guard_app.domain.errors import StoreError, StorePrepareError
from guard_app.domain.models import AccessResult
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from guard_app.storage import sqlite_store as store_module
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_store_recovery import RUN, _close, _open, _rows, _snapshot
from tests.unit.test_store_v3_migration import _v2_database

STAMP = "2026-10-28T00:00:00.000000+00:00"
EXPIRES = "2026-10-28T00:01:00.000000+00:00"


def _legacy_v2(path, *, reasons='["AGT_DENY"]'):
    _v2_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tasks SET created_at_utc=?", (STAMP,))
        connection.execute(
            "UPDATE approvals SET created_at_utc=?, expires_at_utc=?", (STAMP, EXPIRES)
        )
        connection.execute("UPDATE requests SET reasons=? WHERE request_id='r-a'", (reasons,))


def test_unknown_legacy_request_reason_fails_recovery_without_changes(tmp_path):
    path = tmp_path / "legacy-unknown.db"
    _legacy_v2(path, reasons='["FOO"]')

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before = await _snapshot(store)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert error.value.code is StoreStartFailure.STORE_INTEGRITY_FAILED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("value", "expected"),
    [("AGT_DENY", Reason.ACCESS_DENY), ("AGT_ERROR", Reason.ACCESS_ERROR)],
)
def test_legacy_stored_reason_maps_to_access_code(value, expected):
    assert store_module._stored_reason(value) is expected


@pytest.mark.parametrize("reason", list(Reason))
def test_all_current_stored_reason_values_round_trip(reason):
    assert store_module._stored_reason(reason.value) is reason


@pytest.mark.parametrize("value", ["FOO", "", "agt_deny", " AGT_DENY", None, 1, True])
def test_invalid_stored_reason_is_sanitized(value):
    with pytest.raises(StoreError) as error:
        store_module._stored_reason(value)
    if isinstance(value, str) and value:
        assert value not in repr(error.value)


@pytest.mark.parametrize(
    ("checker_error", "expected"),
    [(False, Reason.ACCESS_DENY), (True, Reason.ACCESS_ERROR)],
)
def test_real_guard_new_access_reasons_are_stored_without_legacy_codes(
    tmp_path, checker_error, expected
):
    async def scenario():
        harness = _Harness(source_label=Label.PUBLIC)
        harness.access.result = AccessResult(allowed=False, reason="", error=checker_error)
        ids = count(1)
        store = SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(ids)}")
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
            result = await guard.submit("task", _call())
            assert isinstance(result, Denied)
            assert result.reasons == (expected,)
            assert not harness.transport.calls
            assert await _rows(store, "SELECT reasons FROM requests") == [
                (json.dumps([expected.value]),)
            ]
            assert await _rows(
                store, "SELECT reason_code FROM audit_events WHERE event_type='DECIDED'"
            ) == [(expected.value,)]

            def text_cells(connection):
                names = connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                cells = []
                for (name,) in names:
                    quoted = name.replace('"', '""')
                    for row in connection.execute(f'SELECT * FROM "{quoted}"'):  # noqa: S608
                        cells.extend(value for value in row if isinstance(value, str))
                return cells

            assert all("AGT_" not in cell for cell in await store._run_on_writer_async(text_cells))
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_v2_legacy_rows_are_validated_but_never_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    _legacy_v2(path)
    decoded = []
    original = store_module._stored_reason

    def capture(value):
        reason = original(value)
        decoded.append((value, reason))
        return reason

    monkeypatch.setattr(store_module, "_stored_reason", capture)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert ("AGT_DENY", Reason.ACCESS_DENY) in decoded
            assert await _rows(store, "SELECT reasons FROM requests ORDER BY request_id") == [
                ('["AGT_DENY"]',),
                ('["AGT_DENY"]',),
            ]
            assert await _rows(
                store, "SELECT reason_code FROM audit_events WHERE event_id='audit'"
            ) == [("AGT_DENY",)]
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("reason", "label", "unclassified"),
    [(Reason.OK, 1, 0), (Reason.RESULT_UNCLASSIFIED, None, 1)],
)
def test_observed_audit_reason_uses_the_same_storage_decoder(
    tmp_path, monkeypatch, reason, label, unclassified
):
    path = tmp_path / "observed.db"
    _legacy_v2(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE tasks SET unclassified=? WHERE task_id='task-a'", (unclassified,)
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            ("observed", "r-a", "task-a", "OBSERVED", reason.value, label, None, STAMP),
        )
    decoded = []
    original = store_module._stored_reason

    def capture(value):
        result = original(value)
        decoded.append((value, result))
        return result

    monkeypatch.setattr(store_module, "_stored_reason", capture)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert (reason.value, reason) in decoded
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_unknown_observed_audit_reason_fails_without_changes(tmp_path):
    path = tmp_path / "observed-unknown.db"
    _legacy_v2(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            ("observed", "r-a", "task-a", "OBSERVED", "PRIVATE_FOO", 1, None, STAMP),
        )

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before = await _snapshot(store)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert error.value.code is StoreStartFailure.STORE_INTEGRITY_FAILED
            assert "PRIVATE_FOO" not in repr(error.value)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_legacy_reason_strings_exist_only_in_the_storage_mapping():
    root = Path(__file__).resolve().parents[2] / "src" / "guard_app"
    expected = root / "storage" / "sqlite_store.py"
    for old in ('"AGT_DENY"', '"AGT_ERROR"'):
        locations = [
            (path, path.read_text(encoding="utf-8").count(old)) for path in root.rglob("*.py")
        ]
        assert [(path, count) for path, count in locations if count] == [(expected, 1)]
