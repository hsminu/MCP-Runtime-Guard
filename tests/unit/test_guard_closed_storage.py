"""Guard close reason is readable even when a concurrent failure audit persists it."""

import asyncio
import time
from datetime import UTC, datetime

import pytest

from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.guard import FailureEvent, durable_failure_audit
from guard_app.storage.sqlite_store import _stored_reason
from tests.unit.test_store_queries import _seed, call_query
from tests.unit.test_store_recovery import RUN, _close, _open


def test_close_reason_can_be_persisted_by_failure_audit_and_read_by_queries(tmp_path):
    async def scenario():
        store = await _open(tmp_path / "close.db", tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 20, run_id=RUN)
            await store.activate_run()
            store._run_on_writer(_seed)
            store.halt_execution(Reason.GUARD_CLOSED)

            with pytest.raises(StoreError) as exc:
                await store.allocate_call_seq("task-1")
            assert exc.value.reason is Reason.GUARD_CLOSED
            await durable_failure_audit(store)(
                FailureEvent(
                    "task-1",
                    None,
                    "before_dispatch",
                    exc.value.reason,
                    "StoreError",
                    at_utc=datetime.now(UTC),
                )
            )

            def verify_and_seed_query(connection):
                row = connection.execute(
                    "SELECT reason_code FROM audit_events "
                    "WHERE event_type='FAILURE_BEFORE_DISPATCH'"
                ).fetchone()
                assert row[0] == "GUARD_CLOSED"
                assert _stored_reason(row[0]) is Reason.GUARD_CLOSED
                # Synthetic request reason exercises the public projection's decoder.
                connection.execute(
                    "UPDATE requests SET reasons=? WHERE request_id=?",
                    ('["GUARD_CLOSED"]', "a_req"),
                )

            store._run_on_writer(verify_and_seed_query)
            page = await store.list_calls(call_query(search="a_req"))
            assert len(page.rows) == 1
            assert page.rows[0].reason_codes == (Reason.GUARD_CLOSED,)
        finally:
            await _close(store)

    asyncio.run(scenario())
