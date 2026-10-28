"""B6-06 저장 계약 제안 단위 검사(I9/I10, S03 저장 부분).

직접 SQL은 과거 DB·손상·쓰기 실패 주입에만 쓴다. C 캐시/승인 무효화 연결과
예약/소비 동시 경쟁의 wire 0은 검증하지 않는다. 기존 Store 의미는 유지한다.
"""

import asyncio
import json
import sqlite3
from collections.abc import Coroutine, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import Finding, RegisteredServer, RegistryEntry, ScanReport
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.ports import RegistryStore
from guard_app.security.digest import jcs_canonicalize
from guard_app.storage import SQLiteStore
from guard_app.storage.sqlite_store import (
    _MAX_REPORT_BYTES,
    _MAX_SCHEMA_BYTES,
    _SCHEMA_V1,
    APPLICATION_ID,
    SCHEMA_VERSION,
    _migrate_to_v3,
)
from tests.unit.test_sqlite_store import FaultConnection, assert_sanitized, inject_connection

NOW = datetime(2026, 10, 19, 10, tzinfo=UTC)
FP = "a" * 64
NEW_FP = "b" * 64
RAW = "TEST_SECRET_registry"
SELECT_ROWS = {
    "tasks": "SELECT * FROM tasks ORDER BY 1,2",
    "escalations": "SELECT * FROM escalations ORDER BY 1,2",
    "requests": "SELECT * FROM requests ORDER BY 1,2",
    "approvals": "SELECT * FROM approvals ORDER BY 1,2",
    "audit_events": "SELECT * FROM audit_events ORDER BY 1,2",
    "registrations": "SELECT * FROM registrations ORDER BY 1,2",
    "registration_tools": "SELECT * FROM registration_tools ORDER BY 1,2",
    "scan_reports": "SELECT * FROM scan_reports ORDER BY 1,2",
}


def run[T](operation: Coroutine[object, object, T]) -> T:
    return asyncio.run(operation)


def tool(name: str = "read", **updates: object) -> ToolSpec:
    values: dict[str, object] = {
        "server_id": "server-1",
        "tool_name": name,
        "kind": ToolKind.READ,
        "input_schema": {
            "type": "object",
            "description": RAW + "_schema",
            "minimum": 0.1,
            "maximum": 1e20,
        },
        "destination_arg": None,
        "resource_arg": "path",
        "fingerprint": FP,
    }
    values.update(updates)
    return ToolSpec.model_validate(values)


def registration(**updates: object) -> RegisteredServer:
    values: dict[str, object] = {
        "server_id": "server-1",
        "status": RegStatus.PENDING,
        "boundary": Boundary(
            boundary_id="distinct-boundary", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
        ),
        "tools": (tool(),),
    }
    values.update(updates)
    return RegisteredServer.model_validate(values)


def report(**updates: object) -> ScanReport:
    values: dict[str, object] = {
        "server_id": "server-1",
        "fingerprint_set": (FP,),
        "analyzers_required": ("LLM", "YARA"),
        "analyzers_completed": (),
        "analyzers_failed": ("LLM",),
        "analyzers_skipped": ("YARA",),
        "max_severity": "HIGH",
        "findings": (
            Finding(
                analyzer="LLM",
                code="warning",
                severity="HIGH",
                tool_fingerprint=FP,
                threat_summary=RAW + "_finding",
            ),
        ),
        "scanned_at_utc": NOW,
        "scanner_version": "scanner-1",
        "threat_summary": RAW + "_summary",
    }
    values.update(updates)
    return ScanReport.model_validate(values)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    sequence = iter(range(1000))
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as instance:
        yield instance


def add(store: RegistryStore, **updates: object) -> RegistryEntry:
    return run(store.register_server(registration(**updates), at_utc=NOW))


def activate(store: RegistryStore, entry: RegistryEntry, **updates: object) -> RegistryEntry:
    values = {
        "expected_row_revision": entry.registration.row_revision,
        "tool_fingerprints": {t.tool_name: t.fingerprint for t in entry.registration.tools},
        "reviewed_by": "operator-1",
        "at_utc": NOW,
    }
    values.update(updates)
    return run(store.activate_server(entry.registration.server_id, **values))


def rows(store: SQLiteStore) -> dict[str, list[tuple[object, ...]]]:
    return store._run_on_writer(
        lambda connection: {
            table: [tuple(row) for row in connection.execute(SELECT_ROWS[table])]
            for table in ("registrations", "registration_tools", "scan_reports", "audit_events")
        }
    )


def assert_failure(operation: Coroutine[object, object, object], reason: Reason) -> None:
    with pytest.raises(StoreError) as error:
        run(operation)
    assert error.value.reason is reason
    assert_sanitized(error.value)


def test_empty_queries_and_registration_roundtrip_preserve_all_tool_metadata(
    store: SQLiteStore,
) -> None:
    assert run(store.list_registrations()) == ()
    assert run(store.load_registration("missing")) is None
    original = registration(tools=(tool("send", kind=ToolKind.SEND, destination_arg="to"), tool()))
    entry = run(store.register_server(original, at_utc=NOW))
    assert entry.registration == original.model_copy(update={"row_revision": 1})
    assert entry.last_scan is entry.last_scan_id is entry.reviewed_scan_id is None
    assert entry.reviewed_by is None
    assert entry.updated_at_utc == NOW
    assert run(store.load_registration("server-1")) == entry
    assert run(store.list_registrations()) == (entry,)
    schema_json = store._run_on_writer(
        lambda connection: connection.execute(
            "SELECT input_schema_json FROM registration_tools ORDER BY tool_position"
        ).fetchall()
    )
    assert [r[0] for r in schema_json] == [
        jcs_canonicalize(t.input_schema.to_dict()).decode() for t in original.tools
    ]
    assert (
        store._run_on_writer(
            lambda connection: connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        == []
    )


def test_pending_scan_warning_and_incomplete_scan_can_be_explicitly_activated(
    store: SQLiteStore,
) -> None:
    entry = add(store)
    entry = run(store.record_scan_report("scan-1", report(), at_utc=NOW))
    assert entry.registration.status is RegStatus.PENDING
    assert entry.registration.row_revision == 2
    assert entry.last_scan is not None
    assert entry.last_scan.report == report()
    assert entry.last_scan.stored_at_utc == NOW
    entry = activate(store, entry, reviewed_scan_id="scan-1")
    assert entry.registration.status is RegStatus.ACTIVE
    assert entry.registration.row_revision == 3
    assert entry.reviewed_by == "operator-1"
    assert entry.reviewed_scan_id == "scan-1"
    assert entry.last_scan is not None
    assert entry.last_scan.report.analyzers_failed == ("LLM",)
    assert entry.last_scan.report.analyzers_skipped == ("YARA",)
    assert entry.last_scan.report.max_severity == "HIGH"


def test_activation_without_scan_is_allowed_but_not_automatic(store: SQLiteStore) -> None:
    entry = add(store)
    active = activate(store, entry)
    assert active.registration.status is RegStatus.ACTIVE
    assert active.last_scan_id is active.reviewed_scan_id is None


@pytest.mark.parametrize("initial", list(RegStatus))
@pytest.mark.parametrize("operation", ["activate", "block", "review", "description"])
def test_explicit_state_transition_matrix(
    store: SQLiteStore, initial: RegStatus, operation: str
) -> None:
    entry = add(store)
    if initial is RegStatus.ACTIVE:
        entry = activate(store, entry)
    elif initial is RegStatus.BLOCKED:
        entry = run(store.block_server("server-1", expected_row_revision=1, at_utc=NOW))
    revision = entry.registration.row_revision
    before = rows(store)
    match operation:
        case "activate":
            coroutine = store.activate_server(
                "server-1",
                expected_row_revision=revision,
                tool_fingerprints={"read": FP},
                reviewed_by="operator",
                at_utc=NOW,
            )
            allowed, target = initial is RegStatus.PENDING, RegStatus.ACTIVE
        case "block":
            coroutine = store.block_server("server-1", expected_row_revision=revision, at_utc=NOW)
            allowed, target = initial is not RegStatus.BLOCKED, RegStatus.BLOCKED
        case "review":
            coroutine = store.request_review("server-1", expected_row_revision=revision, at_utc=NOW)
            allowed, target = initial is not RegStatus.PENDING, RegStatus.PENDING
        case _:
            coroutine = store.save_reviewed_descriptions(
                "server-1", {"read": "검토한 설명"}, expected_row_revision=revision, at_utc=NOW
            )
            allowed, target = initial is RegStatus.PENDING, RegStatus.PENDING
    if allowed:
        result = run(coroutine)
        assert result.registration.status is target
        assert result.registration.row_revision == revision + 1
        assert len(rows(store)["audit_events"]) == len(before["audit_events"]) + 1
    else:
        assert_failure(coroutine, Reason.REG_NOT_ACTIVE)
        assert rows(store) == before


@pytest.mark.parametrize("operation", ["activate", "block", "review", "description"])
def test_revision_conflicts_rollback_every_cas_write(store: SQLiteStore, operation: str) -> None:
    add(store)
    before = rows(store)
    match operation:
        case "activate":
            coroutine = store.activate_server(
                "server-1",
                expected_row_revision=0,
                tool_fingerprints={"read": FP},
                reviewed_by="operator",
                at_utc=NOW,
            )
        case "block":
            coroutine = store.block_server("server-1", expected_row_revision=0, at_utc=NOW)
        case "review":
            coroutine = store.request_review("server-1", expected_row_revision=0, at_utc=NOW)
        case _:
            coroutine = store.save_reviewed_descriptions(
                "server-1", {"read": "설명"}, expected_row_revision=0, at_utc=NOW
            )
    assert_failure(coroutine, Reason.APPROVAL_RACE)
    assert rows(store) == before


def test_duplicate_registration_or_scan_id_never_overwrites(store: SQLiteStore) -> None:
    add(store)
    before = rows(store)
    assert_failure(store.register_server(registration(), at_utc=NOW), Reason.APPROVAL_RACE)
    assert rows(store) == before
    run(store.record_scan_report("scan-1", report(), at_utc=NOW))
    before = rows(store)
    assert_failure(
        store.record_scan_report("scan-1", report(fingerprint_set=(NEW_FP,)), at_utc=NOW),
        Reason.APPROVAL_RACE,
    )
    assert rows(store) == before


@pytest.mark.parametrize("operation", ["scan", "activate", "change", "block", "review", "desc"])
def test_unknown_server_writes_fail_without_inserting(store: SQLiteStore, operation: str) -> None:
    match operation:
        case "scan":
            coroutine = store.record_scan_report("scan-1", report(), at_utc=NOW)
        case "activate":
            coroutine = store.activate_server(
                "server-1",
                expected_row_revision=0,
                tool_fingerprints={},
                reviewed_by="operator",
                at_utc=NOW,
            )
        case "change":
            coroutine = store.record_fingerprint_change("server-1", (tool(),), at_utc=NOW)
        case "block":
            coroutine = store.block_server("server-1", expected_row_revision=0, at_utc=NOW)
        case "review":
            coroutine = store.request_review("server-1", expected_row_revision=0, at_utc=NOW)
        case _:
            coroutine = store.save_reviewed_descriptions(
                "server-1", {}, expected_row_revision=0, at_utc=NOW
            )
    before = rows(store)
    assert_failure(coroutine, Reason.STATE_MISSING)
    assert rows(store) == before


@pytest.mark.parametrize("identifier", ["", "a__b", "a_", "a\n", "a\u200b", "a\ud800", 1, None])
def test_all_registry_server_queries_reuse_server_id_rules(
    store: SQLiteStore, identifier: object
) -> None:
    assert_failure(store.load_registration(identifier), Reason.INVALID_ARGUMENTS)
    assert_failure(
        store.record_fingerprint_change(identifier, (), at_utc=NOW), Reason.INVALID_ARGUMENTS
    )


def test_registration_revalidates_server_and_nested_fingerprint(store: SQLiteStore) -> None:
    original = registration()
    for identifier in ("a__b", "a_", "a\x00"):
        invalid = RegisteredServer.model_construct(
            **(original.model_dump() | {"server_id": identifier, "tools": ()})
        )
        assert_failure(store.register_server(invalid, at_utc=NOW), Reason.INVALID_ARGUMENTS)
    for fingerprint in ("", "A" * 64, "a" * 63, "g" * 64, "a" * 64 + "\n"):
        invalid_tool = ToolSpec.model_construct(
            **(tool().model_dump() | {"fingerprint": fingerprint})
        )
        invalid = RegisteredServer.model_construct(
            **(original.model_dump() | {"tools": (invalid_tool,)})
        )
        assert_failure(store.register_server(invalid, at_utc=NOW), Reason.INVALID_ARGUMENTS)
        assert_failure(
            store.record_fingerprint_change("server-1", (invalid_tool,), at_utc=NOW),
            Reason.INVALID_ARGUMENTS,
        )
    assert rows(store)["registrations"] == []


@pytest.mark.parametrize(
    "updates",
    [
        {"status": RegStatus.ACTIVE},
        {"status": RegStatus.BLOCKED},
        {"row_revision": 1},
        {"tools": (tool(reviewed_description="아직 검토하지 않은 원본"),)},
    ],
)
def test_new_registration_requires_unreviewed_initial_pending_state(
    store: SQLiteStore, updates: dict[str, object]
) -> None:
    assert_failure(
        store.register_server(registration(**updates), at_utc=NOW), Reason.INVALID_ARGUMENTS
    )


@pytest.mark.parametrize(
    "fingerprints",
    [
        {},
        {"read": NEW_FP},
        {"extra": FP},
        {"read": FP, "extra": FP},
    ],
)
def test_activation_requires_exact_tool_name_to_fingerprint_map(
    store: SQLiteStore, fingerprints: dict[str, str]
) -> None:
    add(store)
    before = rows(store)
    assert_failure(
        store.activate_server(
            "server-1",
            expected_row_revision=1,
            tool_fingerprints=fingerprints,
            reviewed_by="operator",
            at_utc=NOW,
        ),
        Reason.TOOL_FINGERPRINT_CHANGED,
    )
    assert rows(store) == before


@pytest.mark.parametrize("fingerprint", ["A" * 64, "g" * 64, "a" * 63, 1, None])
def test_activation_rejects_invalid_fingerprint_format(
    store: SQLiteStore, fingerprint: object
) -> None:
    add(store)
    assert_failure(
        store.activate_server(
            "server-1",
            expected_row_revision=1,
            tool_fingerprints={"read": fingerprint},
            reviewed_by="operator",
            at_utc=NOW,
        ),
        Reason.INVALID_ARGUMENTS,
    )


def test_selected_scan_must_exist_belong_to_server_and_match_current_set(
    store: SQLiteStore,
) -> None:
    entry = add(store)
    for scan_id, reason in (("absent", Reason.STATE_MISSING),):
        with pytest.raises(StoreError) as error:
            activate(store, entry, reviewed_scan_id=scan_id)
        assert error.value.reason is reason
        assert_sanitized(error.value)
    entry = run(store.record_scan_report("old", report(fingerprint_set=(NEW_FP,)), at_utc=NOW))
    before = rows(store)
    with pytest.raises(StoreError) as error:
        activate(store, entry, reviewed_scan_id="old")
    assert error.value.reason is Reason.TOOL_FINGERPRINT_CHANGED
    assert rows(store) == before
    other = registration(server_id="server-2", tools=(tool(server_id="server-2"),))
    run(store.register_server(other, at_utc=NOW))
    run(store.record_scan_report("other", report(server_id="server-2"), at_utc=NOW))
    with pytest.raises(StoreError) as error:
        activate(store, entry, reviewed_scan_id="other")
    assert error.value.reason is Reason.TOOL_FINGERPRINT_CHANGED


def test_reviewed_scan_and_last_scan_are_distinct_and_scan_cannot_activate_or_block(
    store: SQLiteStore,
) -> None:
    add(store)
    run(store.record_scan_report("reviewed", report(), at_utc=NOW))
    entry = run(store.record_scan_report("newer", report(), at_utc=NOW))
    active = activate(store, entry, reviewed_scan_id="reviewed")
    assert active.last_scan_id == "newer"
    assert active.reviewed_scan_id == "reviewed"
    active = run(store.record_scan_report("latest", report(), at_utc=NOW))
    assert active.registration.status is RegStatus.ACTIVE
    assert active.reviewed_scan_id == "reviewed"
    blocked = run(
        store.block_server(
            "server-1", expected_row_revision=active.registration.row_revision, at_utc=NOW
        )
    )
    blocked = run(store.record_scan_report("blocked", report(), at_utc=NOW))
    assert blocked.registration.status is RegStatus.BLOCKED
    assert blocked.reviewed_by is blocked.reviewed_scan_id is None


@pytest.mark.parametrize("initial", list(RegStatus))
def test_changed_definitions_clear_only_changed_reviewed_descriptions_and_keep_blocked(
    store: SQLiteStore, initial: RegStatus
) -> None:
    add(store, tools=(tool(), tool("stable"), tool("deleted")))
    entry = run(
        store.save_reviewed_descriptions(
            "server-1",
            {name: RAW + "_review" for name in ("read", "stable", "deleted")},
            expected_row_revision=1,
            at_utc=NOW,
        )
    )
    entry = run(store.record_scan_report("scan-1", report(), at_utc=NOW))
    if initial is RegStatus.ACTIVE:
        entry = activate(store, entry, reviewed_scan_id="scan-1")
    elif initial is RegStatus.BLOCKED:
        entry = run(
            store.block_server(
                "server-1", expected_row_revision=entry.registration.row_revision, at_utc=NOW
            )
        )
    observed = (
        tool("stable", reviewed_description="원본으로 대체 금지"),
        tool(fingerprint=NEW_FP, reviewed_description="이 설명도 버림"),
        tool("added", reviewed_description="새 도구 원본"),
    )
    changed = run(store.record_fingerprint_change("server-1", observed, at_utc=NOW))
    result = changed.entry
    assert changed.changed_tools == ("added", "deleted", "read")
    assert result.registration.status is (
        RegStatus.BLOCKED if initial is RegStatus.BLOCKED else RegStatus.PENDING
    )
    assert result.registration.row_revision == entry.registration.row_revision + 1
    assert [t.tool_name for t in result.registration.tools] == ["stable", "read", "added"]
    assert [t.reviewed_description for t in result.registration.tools] == [RAW + "_review", "", ""]
    assert result.last_scan is result.reviewed_scan_id is result.reviewed_by is None
    assert len(rows(store)["scan_reports"]) == 1  # 과거 검사 보관, 현재 포인터만 폐기.
    with pytest.raises(StoreError) as error:
        activate(store, entry)  # 관측에는 revision 조건이 없고 옛 화면의 CAS는 실패.
    assert error.value.reason is Reason.APPROVAL_RACE


@pytest.mark.parametrize(
    "update",
    [
        {"input_schema": {"type": "object"}},
        {"kind": ToolKind.SEND},
        {"destination_arg": "to"},
        {"resource_arg": None},
    ],
)
def test_metadata_changes_are_conservatively_changed_even_if_supplied_digest_same(
    store: SQLiteStore, update: dict[str, object]
) -> None:
    entry = activate(store, add(store))
    changed = run(store.record_fingerprint_change("server-1", (tool(**update),), at_utc=NOW))
    assert changed.changed_tools == ("read",)
    assert changed.entry.registration.status is RegStatus.PENDING
    assert changed.entry.registration.row_revision == entry.registration.row_revision + 1


def test_identical_observation_and_reordering_preserve_active_review_and_scan(
    store: SQLiteStore,
) -> None:
    add(store, tools=(tool(), tool("second")))
    run(
        store.save_reviewed_descriptions(
            "server-1", {"read": "검토 완료"}, expected_row_revision=1, at_utc=NOW
        )
    )
    entry = run(store.record_scan_report("scan", report(), at_utc=NOW))
    active = activate(store, entry, reviewed_scan_id="scan")
    observed = (tool("second"), tool(reviewed_description="원본은 무시"))
    before = rows(store)
    changes = store._run_on_writer(lambda connection: connection.total_changes)
    changed = run(store.record_fingerprint_change("server-1", observed, at_utc=NOW))
    assert changed.changed_tools == ()
    assert changed.entry == active
    assert rows(store) == before
    assert store._run_on_writer(lambda connection: connection.total_changes) == changes
    assert changed.entry.registration.tools[0].reviewed_description == "검토 완료"


@pytest.mark.parametrize("status", list(RegStatus))
@pytest.mark.parametrize("empty", [False, True])
def test_identical_observation_never_writes_in_any_registration_state(
    store: SQLiteStore, status: RegStatus, empty: bool
) -> None:
    entry = add(store, tools=() if empty else (tool(),))
    if status is RegStatus.ACTIVE:
        entry = activate(store, entry)
    elif status is RegStatus.BLOCKED:
        entry = run(store.block_server("server-1", expected_row_revision=1, at_utc=NOW))
    before = rows(store)
    changes = store._run_on_writer(lambda connection: connection.total_changes)
    # 무변경 관측은 감사 ID도 소비하지 않는다. 쓰려 하면 기존 ID와 충돌한다.
    store._audit_id_factory = lambda: "audit-0"
    for _ in range(2):
        changed = run(
            store.record_fingerprint_change(
                "server-1", entry.registration.tools, at_utc=NOW + timedelta(seconds=1)
            )
        )
        assert changed.changed_tools == ()
        assert changed.entry == entry
        assert rows(store) == before
        assert store._run_on_writer(lambda connection: connection.total_changes) == changes


def test_empty_tool_set_supports_delete_all_and_explicit_activation(store: SQLiteStore) -> None:
    activate(store, add(store))
    changed = run(store.record_fingerprint_change("server-1", (), at_utc=NOW))
    assert changed.changed_tools == ("read",)
    assert changed.entry.registration.tools == ()
    assert activate(store, changed.entry).registration.status is RegStatus.ACTIVE


def test_reviewed_description_partial_update_validates_names_size_and_never_activates(
    store: SQLiteStore,
) -> None:
    add(store, tools=(tool(), tool("second")))
    for descriptions in ({"missing": "설명"}, {"read": "x" * 2001}, {"read": 1}):
        before = rows(store)
        assert_failure(
            store.save_reviewed_descriptions(
                "server-1", descriptions, expected_row_revision=1, at_utc=NOW
            ),
            Reason.INVALID_ARGUMENTS,
        )
        assert rows(store) == before
    entry = run(
        store.save_reviewed_descriptions(
            "server-1", {"read": "검토 완료"}, expected_row_revision=1, at_utc=NOW
        )
    )
    assert entry.registration.status is RegStatus.PENDING
    assert [t.reviewed_description for t in entry.registration.tools] == ["검토 완료", ""]
    assert entry.reviewed_by is None


@pytest.mark.parametrize(
    "stage", ["register", "scan", "activate", "change", "block", "review", "desc"]
)
def test_audit_failure_rolls_back_registration_tools_scan_and_revision(
    store: SQLiteStore, stage: str
) -> None:
    entry = add(store)
    if stage == "review":
        entry = activate(store, entry)
    before = rows(store)
    store._audit_id_factory = lambda: "audit-0"  # 기존 ID 충돌: SQL 감사 실패.
    match stage:
        case "register":
            coroutine = store.register_server(
                registration(server_id="second", tools=()), at_utc=NOW
            )
        case "scan":
            coroutine = store.record_scan_report("scan", report(), at_utc=NOW)
        case "activate":
            coroutine = store.activate_server(
                "server-1",
                expected_row_revision=1,
                tool_fingerprints={"read": FP},
                reviewed_by="operator",
                at_utc=NOW,
            )
        case "change":
            coroutine = store.record_fingerprint_change(
                "server-1", (tool(fingerprint=NEW_FP),), at_utc=NOW
            )
        case "block":
            coroutine = store.block_server("server-1", expected_row_revision=1, at_utc=NOW)
        case "review":
            coroutine = store.request_review(
                "server-1", expected_row_revision=entry.registration.row_revision, at_utc=NOW
            )
        case _:
            coroutine = store.save_reviewed_descriptions(
                "server-1", {"read": RAW + "_review"}, expected_row_revision=1, at_utc=NOW
            )
    assert_failure(coroutine, Reason.INTERNAL_ERROR)
    assert rows(store) == before
    assert not store._run_on_writer(lambda connection: connection.in_transaction)


def test_report_summary_stays_only_in_report_json_and_reprs_and_audit_are_redacted(
    store: SQLiteStore, caplog: pytest.LogCaptureFixture
) -> None:
    add(store)
    entry = run(
        store.save_reviewed_descriptions(
            "server-1", {"read": RAW + "_review"}, expected_row_revision=1, at_utc=NOW
        )
    )
    entry = run(store.record_scan_report("scan", report(), at_utc=NOW))
    active = activate(store, entry, reviewed_scan_id="scan")
    changed = run(store.record_fingerprint_change("server-1", (tool(),), at_utc=NOW))
    for value in (active, active.registration, active.last_scan, report(), changed, tool()):
        assert RAW not in repr(value)
    data = rows(store)
    assert RAW + "_summary" in data["scan_reports"][0][2]
    assert RAW + "_finding" in data["scan_reports"][0][2]
    assert RAW + "_review" in data["registration_tools"][0][-1]
    for table in ("registrations", "registration_tools", "audit_events"):
        assert RAW + "_summary" not in repr(data[table])
        assert RAW + "_finding" not in repr(data[table])
    assert RAW not in repr(data["audit_events"])
    assert RAW not in caplog.text
    events = data["audit_events"]
    assert len(events) == 4
    assert {json.loads(row[6])["row_revision"] for row in events} == set(range(1, 5))
    assert {row[3] for row in events} == {
        "REGISTRY_REGISTERED",
        "REGISTRY_DESCRIPTIONS_REVIEWED",
        "REGISTRY_SCAN_RECORDED",
        "REGISTRY_ACTIVATED",
    }
    assert all(row[1] is row[2] is None for row in events)
    assert all(
        set(json.loads(row[6]))
        == {"server_id", "row_revision", "scan_id", "tool_count", "changed_count"}
        for row in events
    )


def test_legacy_sdk_description_is_rejected_without_persistence(store: SQLiteStore) -> None:
    add(store)
    raw = report(findings=(Finding(analyzer="LLM", code="raw", severity="HIGH", description=RAW),))
    before = rows(store)
    assert_failure(store.record_scan_report("scan", raw, at_utc=NOW), Reason.INVALID_ARGUMENTS)
    assert rows(store) == before


def test_json_size_limits_are_utf8_bytes_and_atomic(store: SQLiteStore) -> None:
    oversized = registration(tools=(tool(input_schema={"text": "가" * (_MAX_SCHEMA_BYTES // 3)}),))
    assert_failure(store.register_server(oversized, at_utc=NOW), Reason.INVALID_ARGUMENTS)
    many = tuple(
        tool(f"tool-{index}", input_schema={"x": "x" * (_MAX_SCHEMA_BYTES - 16)})
        for index in range(33)
    )
    assert_failure(
        store.register_server(registration(tools=many), at_utc=NOW), Reason.INVALID_ARGUMENTS
    )
    add(store)
    before = rows(store)
    assert_failure(
        store.record_scan_report(
            "scan", report(scanner_version="v" * _MAX_REPORT_BYTES), at_utc=NOW
        ),
        Reason.INVALID_ARGUMENTS,
    )
    assert rows(store) == before


@pytest.mark.parametrize("number", [1.0, -0.0, 1e20, 1e21, 5e-324, 1.7976931348623157e308])
def test_jcs_binary64_schema_roundtrip_keeps_canonical_bytes(
    store: SQLiteStore, number: float
) -> None:
    entry = add(store, tools=(tool(input_schema={"maximum": number}),))
    loaded = run(store.load_registration("server-1"))
    assert loaded == entry
    assert loaded is not None
    assert jcs_canonicalize(loaded.registration.tools[0].input_schema.to_dict()) == (
        jcs_canonicalize({"maximum": number})
    )


def test_unsafe_integer_schema_is_rejected_without_recomputing_fingerprints(
    store: SQLiteStore,
) -> None:
    assert_failure(
        store.register_server(
            registration(tools=(tool(input_schema={"maximum": 2**53}),)), at_utc=NOW
        ),
        Reason.INVALID_ARGUMENTS,
    )
    # supplied FP is deliberately unrelated to the schema: C owns the computation.
    assert add(store, tools=(tool(input_schema={}),)).registration.tools[0].fingerprint == FP


@pytest.mark.parametrize(
    "at", [NOW.replace(tzinfo=None), NOW.astimezone(UTC).replace(tzinfo=None), "now"]
)
def test_registry_rejects_non_utc_timestamps(store: SQLiteStore, at: object) -> None:
    assert_failure(store.register_server(registration(), at_utc=at), Reason.INVALID_ARGUMENTS)


@pytest.mark.parametrize("revision", [-1, True, "1", 1 << 63, None])
def test_registry_rejects_invalid_revision(store: SQLiteStore, revision: object) -> None:
    add(store)
    assert_failure(
        store.block_server("server-1", expected_row_revision=revision, at_utc=NOW),
        Reason.INVALID_ARGUMENTS,
    )


def test_nonzero_utc_offset_is_rejected(store: SQLiteStore) -> None:
    assert_failure(
        store.register_server(registration(), at_utc=NOW.astimezone(timezone(timedelta(hours=9)))),
        Reason.INVALID_ARGUMENTS,
    )


@pytest.mark.parametrize("stage", ["register", "change", "scan", "activate"])
def test_lost_registry_commit_ack_returns_no_copy_and_closes_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    inject_connection(monkeypatch)
    path = tmp_path / "guard.db"
    sequence = iter(range(100))
    with SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(sequence)}") as instance:
        if stage != "register":
            add(instance)

        def fail_commit(connection: sqlite3.Connection) -> None:
            assert isinstance(connection, FaultConnection)
            connection.fail_commit = True  # 실제 COMMIT 뒤 ACK만 실패.

        instance._run_on_writer(fail_commit)
        match stage:
            case "register":
                coroutine = instance.register_server(registration(), at_utc=NOW)
            case "change":
                coroutine = instance.record_fingerprint_change(
                    "server-1", (tool(fingerprint=NEW_FP),), at_utc=NOW
                )
            case "scan":
                coroutine = instance.record_scan_report("scan", report(), at_utc=NOW)
            case _:
                coroutine = instance.activate_server(
                    "server-1",
                    expected_row_revision=1,
                    tool_fingerprints={"read": FP},
                    reviewed_by="operator",
                    at_utc=NOW,
                )
        assert_failure(coroutine, Reason.INTERNAL_ERROR)
        assert instance._connection is None
    with SQLiteStore(path) as instance:
        entry = run(instance.load_registration("server-1"))
        assert entry is not None
        assert entry.registration.row_revision == (1 if stage == "register" else 2)
        assert len(rows(instance)["audit_events"]) == entry.registration.row_revision


def test_restart_rebuilds_full_copies_without_restoring_execution_authority(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    sequence = iter(range(100))
    with SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(sequence)}") as instance:
        add(instance, server_id="z-last", tools=())
        add(instance)
        entry = run(instance.record_scan_report("scan", report(), at_utc=NOW))
        activate(instance, entry, reviewed_scan_id="scan")
        expected = run(instance.list_registrations())
        assert [e.registration.server_id for e in expected] == ["server-1", "z-last"]
        facts = rows(instance)
    with SQLiteStore(path) as instance:
        assert run(instance.list_registrations()) == expected
        assert rows(instance) == facts
        contexts = instance._test_context_snapshot()
        assert all(not context for context in contexts)
        assert instance._inspect_startup().schema_version == SCHEMA_VERSION == 3


def test_new_wrappers_are_frozen_and_validate_utc_and_scan_ownership(store: SQLiteStore) -> None:
    entry = add(store)
    with pytest.raises(ValidationError):
        entry.updated_at_utc = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        entry.model_copy(update={"updated_at_utc": NOW.replace(tzinfo=None)})
    entry = run(store.record_scan_report("scan", report(), at_utc=NOW))
    with pytest.raises(ValidationError):
        entry.model_copy(update={"registration": registration(server_id="other", tools=())})


def legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for _, statement in _SCHEMA_V1:
            connection.execute(statement)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("task", 7, "profile", "policy", 1, 0, 0, "waiting_approval", 3, 4, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO escalations VALUES (?,?,?,?,?)", ("task", 2, "source", 0, 1)
        )
        connection.execute(
            "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "req",
                "task",
                3,
                "server",
                "read",
                None,
                FP,
                1,
                '["APPROVAL_REQUIRED"]',
                "WAITING_APPROVAL",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "approval",
                "req",
                FP,
                "operator",
                "PENDING",
                NOW.isoformat(),
                (NOW + timedelta(minutes=1)).isoformat(),
                None,
                None,
                0,
            ),
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            ("event", "req", "task", "DECIDED", None, None, None, NOW.isoformat()),
        )


def legacy_facts(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as connection:
        schema = connection.execute("SELECT name,sql FROM sqlite_schema ORDER BY name").fetchall()
        tables = [connection.execute(SELECT_ROWS[name]).fetchall() for name, _ in _SCHEMA_V1]
        return (schema, tables, connection.execute("PRAGMA user_version").fetchone())


def test_v1_empty_registry_migration_preserves_all_other_table_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = legacy_facts(path)
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _migrate_to_v3(connection, 1)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with SQLiteStore(path) as instance:
        assert (
            instance._run_on_writer(
                lambda connection: connection.execute("PRAGMA user_version").fetchone()[0]
            )
            == SCHEMA_VERSION
            == 3
        )
        assert run(instance.list_registrations()) == ()
        assert (
            instance._run_on_writer(
                lambda connection: connection.execute(
                    "SELECT count(*) FROM registration_tools"
                ).fetchone()[0]
            )
            == 0
        )
        assert (
            instance._run_on_writer(
                lambda connection: connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            == []
        )
    after = legacy_facts(path)
    for index in (0, 1, 3, 4, 5, 6):
        assert after[1][index] == before[1][index]
    assert [row[:11] for row in after[1][2]] == before[1][2]
    assert [row[11:] for row in after[1][2]] == [(None, None, 0, "v1")]
    with sqlite3.connect(path) as connection:
        actual = dict(connection.execute("SELECT name,sql FROM sqlite_schema"))
        assert all(
            actual[name] == statement
            for index, (name, statement) in enumerate(_SCHEMA_V1[:5])
            if index != 2
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO registrations VALUES ('legacy','active','internal',2,'{}',NULL,NULL,0)",
        "INSERT INTO scan_reports VALUES ('scan','legacy','{}','2026-10-19')",
        "ALTER TABLE registrations ADD COLUMN extra TEXT",
        "ALTER TABLE scan_reports ADD COLUMN extra TEXT",
        "CREATE INDEX unexpected ON registrations(status)",
        "PRAGMA application_id=0",
    ],
)
def test_nonempty_or_modified_v1_is_rejected_without_any_database_change(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
    before = legacy_facts(path)
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(StoreError) as error:
            _migrate_to_v3(connection, 1)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert_sanitized(error.value)
    assert legacy_facts(path) == before


def test_v1_migration_failure_rolls_back_table_drops_and_version(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = legacy_facts(path)
    original_connect = sqlite3.connect

    class MigrationFailure(sqlite3.Connection):
        def execute(self, statement: str, parameters: object = ()) -> sqlite3.Cursor:
            if statement.startswith("CREATE TABLE registration_tools"):
                raise sqlite3.OperationalError(RAW)
            return super().execute(statement, parameters)

    with original_connect(path, isolation_level=None, factory=MigrationFailure) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(StoreError) as error:
            _migrate_to_v3(connection, 1)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert_sanitized(error.value)
    assert legacy_facts(path) == before
    with pytest.raises(StoreError):
        SQLiteStore(path)


def test_scan_server_foreign_key_rejects_orphan_rows(store: SQLiteStore) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store._run_on_writer(
            lambda connection: connection.execute(
                "INSERT INTO scan_reports VALUES (?,?,?,?,?)",
                ("scan", "absent", "{}", NOW.isoformat(), NOW.isoformat()),
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE registration_tools SET input_schema_json='{} '",
        'UPDATE registration_tools SET input_schema_json=\'{"x":1,"x":2}\'',
        "UPDATE registration_tools SET tool_position=2",
        "UPDATE registrations SET updated_at_utc='invalid TEST_SECRET_time'",
        "UPDATE scan_reports SET report_json='{\"TEST_SECRET_raw\":NaN}'",
    ],
)
def test_corrupt_persisted_registry_is_fixed_error_without_cache_copy(
    store: SQLiteStore, mutation: str
) -> None:
    add(store)
    run(store.record_scan_report("scan", report(), at_utc=NOW))

    def corrupt(connection: sqlite3.Connection) -> None:
        connection.execute(mutation)

    store._run_on_writer(corrupt)
    before = rows(store)
    assert_failure(store.load_registration("server-1"), Reason.INTERNAL_ERROR)
    assert rows(store) == before
