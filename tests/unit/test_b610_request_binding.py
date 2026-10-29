"""B6.10: persisted request identity remains bound through approval consumption."""

import asyncio

import pytest

from guard_app.domain.enums import ApprovalStatus
from guard_app.domain.reasons import Reason
from tests.unit.test_atomic_approval_store import _open, _snapshot, _store
from tests.unit.test_sqlite_decisions import LATER


@pytest.mark.parametrize(
    ("statement", "value"),
    [
        ("UPDATE requests SET epoch=? WHERE request_id=?", 4),
        ("UPDATE requests SET kind=? WHERE request_id=?", "send"),
        ("UPDATE requests SET row_revision=? WHERE request_id=?", 4),
        ("UPDATE requests SET legacy_source=? WHERE request_id=?", "v1"),
    ],
)
def test_consume_rejects_tampered_request_binding_without_writes(tmp_path, statement, value):
    path = tmp_path / "binding.db"
    with _store(path) as store:
        _open(store)
        asyncio.run(
            store.transition_approval(
                "approval-1",
                ApprovalStatus.APPROVED,
                expected_row_revision=0,
                approver_id="operator-1",
                now_utc=LATER,
                reason=Reason.OK,
            )
        )
        store._run_on_writer(lambda db: db.execute(statement, (value, "request-1")))
        before = _snapshot(path)
        consumed = asyncio.run(
            store.consume_and_reserve(
                approval_id="approval-1",
                snapshot_hash="b" * 64,
                request_id="request-1",
                task_id="task-1",
                expected_security_version=7,
                now_utc=LATER,
            )
        )
        assert consumed is False
        assert _snapshot(path) == before
