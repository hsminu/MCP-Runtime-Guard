"""B6.20: settings edits preserve checked definitions and revoke activation."""

import asyncio
import json
from itertools import count

import pytest

from guard_app.domain.enums import RegStatus, StorePhase, ToolKind
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_registry_store import NOW, activate, add, registration, report, rows, run, tool
from tests.unit.test_store_recovery import _close, _rows
from tests.unit.test_store_run_activation import _recovered


@pytest.fixture
def store(tmp_path):
    ids = count()
    with SQLiteStore(tmp_path / "registry.db", audit_id_factory=lambda: f"audit-{next(ids)}") as db:
        yield db


def _reviewed_pair(store):
    entry = add(store, tools=(tool(), tool("stable")))
    entry = run(
        store.save_reviewed_descriptions(
            "server-1",
            {"read": "검토한 읽기", "stable": "유지할 설명"},
            expected_row_revision=entry.registration.row_revision,
            at_utc=NOW,
        )
    )
    entry = run(store.record_scan_report("scan-1", report(), at_utc=NOW))
    return activate(store, entry, reviewed_scan_id="scan-1")


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": ToolKind.SEND},
        {"destination_arg": "target"},
        {"resource_arg": "resource"},
    ],
)
def test_active_changed_settings_clear_only_changed_review_and_preserve_scan(store, updates):
    active = _reviewed_pair(store)
    previous = active.registration.tools
    changed = previous[0].model_copy(update={**updates, "reviewed_description": "UNTRUSTED"})
    stable = previous[1].model_copy(update={"reviewed_description": "UNTRUSTED"})
    before = rows(store)
    result = run(
        store.record_settings_change(
            "server-1",
            (stable, changed),
            expected_row_revision=active.registration.row_revision,
            at_utc=NOW,
        )
    )
    entry = result.entry
    assert result.changed_tools == ("read",)
    assert entry.registration.status is RegStatus.PENDING
    assert entry.registration.row_revision == active.registration.row_revision + 1
    assert [item.tool_name for item in entry.registration.tools] == ["read", "stable"]
    assert entry.registration.tools[0].reviewed_description == ""
    assert entry.registration.tools[1].reviewed_description == "유지할 설명"
    assert entry.last_scan_id == "scan-1"
    assert entry.reviewed_by is entry.reviewed_scan_id is None
    after = rows(store)
    assert len(after["audit_events"]) == len(before["audit_events"]) + 1
    event = store._run_on_writer(
        lambda connection: connection.execute(
            "SELECT event_type,reason_code,detail FROM audit_events "
            "WHERE event_type='REGISTRY_SETTINGS_CHANGED'"
        ).fetchone()
    )
    assert event[0] == "REGISTRY_SETTINGS_CHANGED"
    assert event[1] == Reason.REG_NOT_ACTIVE.value
    assert json.loads(event[2])["changed_count"] == 1
    assert "UNTRUSTED" not in repr(after["audit_events"])


def test_blocked_status_is_preserved_and_multiple_changes_are_sorted(store):
    entry = add(store, tools=(tool(), tool("stable")))
    blocked = run(
        store.block_server(
            "server-1", expected_row_revision=entry.registration.row_revision, at_utc=NOW
        )
    )
    changed = tuple(
        item.model_copy(update={"resource_arg": "new"}) for item in blocked.registration.tools
    )
    result = run(
        store.record_settings_change(
            "server-1", changed, expected_row_revision=blocked.registration.row_revision, at_utc=NOW
        )
    )
    assert result.entry.registration.status is RegStatus.BLOCKED
    assert result.changed_tools == ("read", "stable")
    assert result.entry.registration.row_revision == blocked.registration.row_revision + 1


def test_noop_keeps_rows_and_ignores_supplied_review_descriptions_after_cas(store):
    active = _reviewed_pair(store)
    before = rows(store)
    inputs = tuple(
        item.model_copy(update={"reviewed_description": "UNTRUSTED"})
        for item in reversed(active.registration.tools)
    )
    result = run(
        store.record_settings_change(
            "server-1", inputs, expected_row_revision=active.registration.row_revision, at_utc=NOW
        )
    )
    assert result.entry == active
    assert result.changed_tools == ()
    assert rows(store) == before
    with pytest.raises(StoreError) as error:
        run(
            store.record_settings_change(
                "server-1",
                inputs,
                expected_row_revision=active.registration.row_revision - 1,
                at_utc=NOW,
            )
        )
    assert error.value.reason is Reason.APPROVAL_RACE
    assert rows(store) == before


@pytest.mark.parametrize("case", ["server_id", "schema", "fingerprint", "extra", "missing"])
def test_definition_or_tool_set_changes_are_rejected_without_writes(store, case):
    entry = add(store, tools=(tool(), tool("stable")))
    existing = entry.registration.tools
    if case == "server_id":
        supplied = (existing[0].model_copy(update={"server_id": "server-2"}), existing[1])
    elif case == "schema":
        supplied = (
            existing[0].model_copy(update={"input_schema": {"type": "object", "title": "changed"}}),
            existing[1],
        )
    elif case == "fingerprint":
        supplied = (existing[0].model_copy(update={"fingerprint": "b" * 64}), existing[1])
    elif case == "extra":
        supplied = (*existing, tool("extra"))
    else:
        supplied = existing[:1]
    before = rows(store)
    with pytest.raises(StoreError) as error:
        run(
            store.record_settings_change(
                "server-1",
                supplied,
                expected_row_revision=entry.registration.row_revision,
                at_utc=NOW,
            )
        )
    assert error.value.reason is Reason.TOOL_FINGERPRINT_CHANGED
    assert rows(store) == before


def test_stale_revision_is_checked_before_valid_foreign_tool_definition(store):
    entry = add(store)
    foreign = entry.registration.tools[0].model_copy(update={"server_id": "server-2"})
    before = rows(store)
    with pytest.raises(StoreError) as error:
        run(
            store.record_settings_change(
                "server-1",
                (foreign,),
                expected_row_revision=entry.registration.row_revision - 1,
                at_utc=NOW,
            )
        )
    assert error.value.reason is Reason.APPROVAL_RACE
    assert rows(store) == before


def test_changed_registration_survives_restart(tmp_path):
    path = tmp_path / "restart.db"
    ids = count()
    with SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(ids)}") as first:
        active = _reviewed_pair(first)
        changed = active.registration.tools[0].model_copy(update={"resource_arg": "location"})
        expected = run(
            first.record_settings_change(
                "server-1",
                (changed, active.registration.tools[1]),
                expected_row_revision=active.registration.row_revision,
                at_utc=NOW,
            )
        ).entry
    with SQLiteStore(path) as second:
        assert run(second.load_registration("server-1")) == expected


def test_settings_change_is_permitted_in_recovered_phase(tmp_path):
    async def scenario():
        store = await _recovered(tmp_path / "recovered.db", tmp_path)
        try:
            assert store.execution_state().phase is StorePhase.RECOVERED
            entry = await store.register_server(registration(), at_utc=NOW)
            item = entry.registration.tools[0].model_copy(update={"resource_arg": "new"})
            result = await store.record_settings_change(
                "server-1",
                (item,),
                expected_row_revision=entry.registration.row_revision,
                at_utc=NOW,
            )
            assert result.changed_tools == ("read",)
            assert result.entry.registration.status is RegStatus.PENDING
            assert result.entry.registration.row_revision == entry.registration.row_revision + 1
            assert not store.execution_state().execution_enabled
            events = await _rows(store, "SELECT event_type FROM audit_events ORDER BY event_id")
            assert ("REGISTRY_SETTINGS_CHANGED",) in events
        finally:
            await _close(store)

    asyncio.run(scenario())
