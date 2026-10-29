"""B6.12: actual SQLite timeout configuration and approval expiry at commit boundary."""

import asyncio
from itertools import count

import pytest

from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_atomic_approval_store import _open, _snapshot, _store
from tests.unit.test_sqlite_decisions import LATER
from tests.unit.test_sqlite_lifecycle import approval


def test_default_sqlite_busy_timeout_is_1000_ms(tmp_path):
    store = SQLiteStore(tmp_path / "timeout.db")
    try:
        timeout = store._run_on_writer(
            lambda connection: connection.execute("PRAGMA busy_timeout").fetchone()[0]
        )
        assert timeout == 1000
    finally:
        store.close()
        assert asyncio.run(store.wait_closed(5))


def test_open_approval_expiring_during_write_rolls_back_every_row(tmp_path):
    path = tmp_path / "expiry.db"
    clock = [LATER]
    with _store(path, utc_now=lambda: clock[0]) as store:
        before = _snapshot(path)
        ids = count(1)

        def expire_during_audit():
            # Task update has already happened inside the transaction at this point.
            clock[0] = approval().expires_at_utc
            return f"late-audit-{next(ids)}"

        store._audit_id_factory = expire_during_audit
        with pytest.raises(StoreError) as error:
            _open(store)
        assert error.value.reason is Reason.APPROVAL_STALE
        assert _snapshot(path) == before
