"""B6.21: scan results must match the current listing inside the write transaction."""

from itertools import count

import pytest

from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_registry_store import FP, NEW_FP, NOW, add, report, rows, run, tool

OTHER_FP = "c" * 64


@pytest.fixture
def store(tmp_path):
    ids = count()
    with SQLiteStore(tmp_path / "registry.db", audit_id_factory=lambda: f"audit-{next(ids)}") as db:
        yield db


def test_same_fingerprint_set_in_different_order_can_be_recorded(store):
    initial = add(store, tools=(tool(), tool("other", fingerprint=NEW_FP)))
    scan = report(fingerprint_set=(NEW_FP, FP), findings=())
    before = rows(store)
    entry = run(store.record_scan_report("matching-scan", scan, at_utc=NOW))
    assert entry.last_scan_id == "matching-scan"
    assert entry.registration.row_revision == initial.registration.row_revision + 1
    after = rows(store)
    assert len(after["scan_reports"]) == len(before["scan_reports"]) + 1
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1


@pytest.mark.parametrize(
    "fingerprints",
    [(FP, OTHER_FP), (FP, NEW_FP, OTHER_FP), (FP,), ()],
    ids=["one-changed", "extra", "one-missing", "all-missing"],
)
def test_nonmatching_fingerprint_set_is_rejected_without_writes(store, fingerprints):
    initial = add(store, tools=(tool(), tool("other", fingerprint=NEW_FP)))
    before = rows(store)
    with pytest.raises(StoreError) as error:
        run(
            store.record_scan_report(
                "stale-scan", report(fingerprint_set=fingerprints, findings=()), at_utc=NOW
            )
        )
    assert error.value.reason is Reason.TOOL_FINGERPRINT_CHANGED
    assert rows(store) == before
    assert run(store.load_registration("server-1")) == initial


def test_distinct_tools_with_same_fingerprint_use_set_semantics(store):
    add(store, tools=(tool(), tool("other")))
    entry = run(
        store.record_scan_report("duplicate-fingerprint", report(fingerprint_set=(FP,)), at_utc=NOW)
    )
    assert entry.last_scan_id == "duplicate-fingerprint"
