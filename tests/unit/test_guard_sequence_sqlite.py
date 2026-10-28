"""Guard와 실제 영속 Task 순번의 통합 경계(SEQ-01·02)."""

import asyncio
from itertools import count

from guard_app.domain.contracts import Released
from guard_app.domain.enums import Label
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness


def test_guard_uses_sqlite_allocator_and_records_request_epoch_and_kind(tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        audit_ids = count(1)
        store = SQLiteStore(
            tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(audit_ids)}"
        )
        guard = None
        try:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(h.ids)}",
                approval_id_factory=lambda: f"approval-{next(h.ids)}",
                next_call_seq=store.allocate_call_seq,
                failure_audit=h.failure_audit,
                utc_now=lambda: h.now,
                monotonic=lambda: h.monotonic,
            )
            result = await guard.submit("task", _call())
            assert isinstance(result, Released)
            assert len(h.transport.calls) == 1
            row = await store._run_on_writer_async(
                lambda connection: tuple(
                    connection.execute(
                        "SELECT call_seq,epoch,kind,legacy_source FROM requests"
                    ).fetchone()
                )
            )
            assert row == (1, 1, "read", None)
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())
