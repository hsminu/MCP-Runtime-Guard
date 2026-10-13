"""B6 현행 승인/응답 Store 연산의 파일 DB 단위 검사.

I4/I5/I6/I8/I9, T07/T11/T19, S03의 저장소 단위 부분이다. 직접 SQL/장애 주입은
B 구현의 rollback과 결속을 검사하며 C 독립 계약·중단 복구·wire 인수를 대신하지 않는다.
"""

import asyncio
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import ApprovalRecord, Released, Withheld
from guard_app.domain.enums import (
    ApprovalStatus,
    Decision,
    Label,
    RequestStatus,
    TaskStatus,
    ToolKind,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.models import ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.response_gate import ResponseGate
from guard_app.storage import SQLiteStore
from tests.unit.test_response_gate import _raw, _Registry
from tests.unit.test_sqlite_decisions import (
    LATER,
    NOW,
    assert_sanitized,
    audit_failure,
    change,
    decision,
    record,
    request,
    reserve,
    task,
)
from tests.unit.test_sqlite_store import FaultConnection, inject_connection


@dataclass
class Clock:
    now: datetime = LATER

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[tuple[SQLiteStore, Clock]]:
    clock = Clock()
    identifiers = count(1)
    with SQLiteStore(
        tmp_path / "guard.db",
        audit_id_factory=lambda: f"event-{next(identifiers)}",
        utc_now=clock,
    ) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        yield instance, clock


def approval(**updates: object) -> ApprovalRecord:
    values: dict[str, object] = {
        "approval_id": "approval-1",
        "request_id": "request-1",
        "snapshot_hash": "b" * 64,
        "approver_id": "operator-1",
        "status": ApprovalStatus.PENDING,
        "created_at_utc": NOW,
        "expires_at_utc": NOW + timedelta(seconds=30),
    }
    values.update(updates)
    return ApprovalRecord.model_validate(values)


def full_snapshot(store: SQLiteStore) -> dict[str, list[tuple[object, ...]]]:
    connection = store._owned_connection()
    queries = {
        "tasks": "SELECT * FROM tasks ORDER BY 1",
        "escalations": "SELECT * FROM escalations ORDER BY 1,2",
        "requests": "SELECT * FROM requests ORDER BY 1",
        "approvals": "SELECT * FROM approvals ORDER BY 1",
        "audit_events": "SELECT * FROM audit_events ORDER BY 1",
    }
    return {
        table: [tuple(row) for row in connection.execute(sql)] for table, sql in queries.items()
    }


def pending(store: SQLiteStore, req: ResolvedRequest | None = None) -> ApprovalRecord:
    record(store, req, decision(decision=Decision.ASK, reasons=(Reason.INTERNAL_TO_EXTERNAL,)))
    change(store, TaskStatus.WAITING_APPROVAL)
    value = approval()
    asyncio.run(store.create_approval(value))
    return value


def transition(store: SQLiteStore, target: ApprovalStatus, **updates: object) -> ApprovalRecord:
    value = asyncio.run(store.get_approval("approval-1"))
    assert value is not None
    arguments: dict[str, object] = {
        "expected_row_revision": value.row_revision,
        "approver_id": value.approver_id,
        "now_utc": LATER,
        "reason": Reason.OK,
    }
    arguments.update(updates)
    return asyncio.run(store.transition_approval("approval-1", target, **arguments))


def consume(store: SQLiteStore, **updates: object) -> bool:
    arguments: dict[str, object] = {
        "approval_id": "approval-1",
        "snapshot_hash": "b" * 64,
        "request_id": "request-1",
        "task_id": "task-1",
        "expected_security_version": 7,
        "now_utc": LATER,
    }
    arguments.update(updates)
    return asyncio.run(store.consume_and_reserve(**arguments))


def load(store: SQLiteStore) -> TaskState:
    return asyncio.run(store.load_task("task-1"))


def dispatched(store: SQLiteStore, kind: ToolKind = ToolKind.READ) -> ResolvedRequest:
    req = request()
    if kind is not ToolKind.READ:
        req = req.model_copy(update={"tool": req.tool.model_copy(update={"kind": kind})})
    record(store, req)
    reserve(store)
    return req


def observe(store: SQLiteStore, req: ResolvedRequest, label: Label | None) -> TaskState:
    return asyncio.run(store.commit_observation(req, label, at_utc=LATER))


def send(store: SQLiteStore, req: ResolvedRequest) -> None:
    asyncio.run(store.commit_send_outcome(req, at_utc=LATER))


def failure(
    store: SQLiteStore,
    req: ResolvedRequest,
    request_status: RequestStatus = RequestStatus.WITHHELD,
    task_status: TaskStatus = TaskStatus.HALTED,
    **updates: object,
) -> TaskState:
    arguments: dict[str, object] = {
        "request_status": request_status,
        "task_status": task_status,
        "reason": Reason.INTERNAL_ERROR,
        "at_utc": LATER,
        "exception_type": "DispatchError",
    }
    arguments.update(updates)
    return asyncio.run(store.commit_request_failure(req, **arguments))


def request_status(store: SQLiteStore) -> str:
    return store._owned_connection().execute("SELECT status FROM requests").fetchone()[0]


def execute(store: SQLiteStore, operation: str, req: ResolvedRequest | None = None) -> object:
    if operation == "create":
        return asyncio.run(store.create_approval(approval()))
    if operation == "approve":
        return asyncio.run(
            store.transition_approval(
                "approval-1",
                ApprovalStatus.APPROVED,
                expected_row_revision=0,
                approver_id="operator-1",
                now_utc=LATER,
                reason=Reason.OK,
            )
        )
    if operation == "consume":
        return consume(store)
    req = request() if req is None else req
    if operation == "read":
        return observe(store, req, Label.INTERNAL)
    if operation == "send":
        return send(store, req)
    if operation == "failure":
        return failure(store, req)
    raise AssertionError("unknown test operation")


def result_commit(store: SQLiteStore, req: ResolvedRequest) -> object:
    return execute(store, "read" if req.tool.kind is ToolKind.READ else "send", req)


def test_pending_get_and_approved_consume_commit_all_rows(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    value = pending(store)
    assert asyncio.run(store.get_approval("approval-1")) == value
    assert request_status(store) == RequestStatus.WAITING_APPROVAL.value
    approved = transition(store, ApprovalStatus.APPROVED)
    assert approved.row_revision == 1
    assert approved.decided_at_utc == LATER
    assert consume(store) is True
    consumed = asyncio.run(store.get_approval("approval-1"))
    assert consumed == approved.model_copy(
        update={"status": ApprovalStatus.CONSUMED, "consumed_at_utc": LATER, "row_revision": 2}
    )
    assert load(store) == task(row_revision=13)
    assert request_status(store) == RequestStatus.DISPATCHING.value
    assert store._owned_connection().execute(
        "SELECT dispatch_reserved_at_utc FROM requests"
    ).fetchone()[0] == LATER.isoformat(timespec="microseconds")
    assert (
        store._owned_connection()
        .execute("SELECT count(*) FROM audit_events WHERE event_type='CONSUMED'")
        .fetchone()[0]
        == 1
    )
    before = full_snapshot(store)
    assert consume(store) is False
    assert full_snapshot(store) == before
    assert observe(store, request(), Label.INTERNAL) == task(
        row_revision=14,
        high_watermark=Label.INTERNAL,
        last_read_label=Label.INTERNAL,
        security_version=8,
        escalations=(
            {
                "at_seq": 1,
                "source_id": "resource-1",
                "from_label": Label.PUBLIC,
                "to_label": Label.INTERNAL,
            },
        ),
    )


def test_ask_send_can_commit_without_taint_merge(runtime: tuple[SQLiteStore, Clock]) -> None:
    store, _ = runtime
    req = request()
    req = req.model_copy(update={"tool": req.tool.model_copy(update={"kind": ToolKind.SEND})})
    pending(store, req)
    transition(store, ApprovalStatus.APPROVED)
    assert consume(store) is True
    original = load(store)
    send(store, req)
    assert load(store) == original
    assert request_status(store) == RequestStatus.SUCCEEDED.value


@pytest.mark.parametrize("source", list(ApprovalStatus))
@pytest.mark.parametrize("target", list(ApprovalStatus))
def test_approval_transition_matrix(
    runtime: tuple[SQLiteStore, Clock], source: ApprovalStatus, target: ApprovalStatus
) -> None:
    store, _ = runtime
    pending(store)
    if source in (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED, ApprovalStatus.INVALIDATED):
        transition(store, ApprovalStatus.APPROVED)
    if source is ApprovalStatus.CONSUMED:
        assert consume(store) is True
    elif source not in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
        transition(store, source)
    original = full_snapshot(store)
    allowed = {
        ApprovalStatus.PENDING: {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        },
        ApprovalStatus.APPROVED: {ApprovalStatus.EXPIRED, ApprovalStatus.INVALIDATED},
    }
    if target not in allowed.get(source, set()):
        with pytest.raises(StoreError) as error:
            transition(store, target)
        assert_sanitized(error.value)
        assert full_snapshot(store) == original
    else:
        result = transition(store, target)
        assert result.status is target
        assert load(store).status is TaskStatus.WAITING_APPROVAL
        assert request_status(store) == (
            RequestStatus.WAITING_APPROVAL.value
            if target is ApprovalStatus.APPROVED
            else RequestStatus.DENIED.value
        )
        if target is not ApprovalStatus.APPROVED:
            assert consume(store) is False


@pytest.mark.parametrize(
    "updates",
    [
        {"expected_row_revision": 1},
        {"expected_row_revision": True},
        {"expected_row_revision": -1},
        {"approver_id": "another-operator"},
        {"now_utc": NOW.replace(tzinfo=None)},
        {"now_utc": NOW.astimezone(timezone(timedelta(hours=9)))},
        {"reason": "TEST_SECRET_reason"},
    ],
)
def test_transition_rejects_invalid_cas_and_actor(
    runtime: tuple[SQLiteStore, Clock], updates: dict[str, object]
) -> None:
    store, clock = runtime
    pending(store)
    if "now_utc" in updates:
        clock.now = updates["now_utc"]
    original = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        transition(store, ApprovalStatus.APPROVED, **updates)
    assert_sanitized(error.value)
    assert full_snapshot(store) == original


@pytest.mark.parametrize("changed", ["epoch", "row_revision", "security_version", "status"])
def test_approval_cannot_approve_changed_task(
    runtime: tuple[SQLiteStore, Clock], changed: str
) -> None:
    store, _ = runtime
    pending(store)
    queries = {
        "epoch": "UPDATE tasks SET epoch=4",
        "row_revision": "UPDATE tasks SET row_revision=13",
        "security_version": "UPDATE tasks SET security_version=8",
        "status": "UPDATE tasks SET status='halted'",
    }
    store._owned_connection().execute(queries[changed])
    original = full_snapshot(store)
    with pytest.raises(StoreError):
        transition(store, ApprovalStatus.APPROVED)
    assert full_snapshot(store) == original


@pytest.mark.parametrize(
    "updates",
    [
        {"approval_id": "missing"},
        {"snapshot_hash": "c" * 64},
        {"request_id": "missing"},
        {"task_id": "missing"},
        {"expected_security_version": 8},
        {"now_utc": NOW},
        {"now_utc": NOW + timedelta(seconds=30)},
    ],
)
def test_consume_relation_conflicts_are_false_without_partial_writes(
    runtime: tuple[SQLiteStore, Clock], updates: dict[str, object]
) -> None:
    store, clock = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    if updates.get("now_utc") == NOW:
        clock.now = NOW
    before = full_snapshot(store)
    assert consume(store, **updates) is False
    assert full_snapshot(store) == before


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE requests SET task_id='other-task'",
        "UPDATE requests SET call_seq=2",
        "UPDATE requests SET arguments_digest='" + "c" * 64 + "'",
        "UPDATE requests SET decision=0",
        "UPDATE requests SET status='DENIED'",
        "UPDATE tasks SET epoch=4",
        "UPDATE tasks SET security_version=8",
        "UPDATE tasks SET row_revision=13",
        "UPDATE approvals SET row_revision=2",
        "UPDATE approvals SET approver_id='other-operator'",
    ],
)
def test_consume_rechecks_persistent_relations(
    runtime: tuple[SQLiteStore, Clock], query: str
) -> None:
    store, _ = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    asyncio.run(store.create_task(task(task_id="other-task"), created_at_utc=NOW))
    store._owned_connection().execute(query)
    before = full_snapshot(store)
    assert consume(store) is False
    assert full_snapshot(store) == before


@pytest.mark.parametrize(
    "updates",
    [
        {"snapshot_hash": "invalid"},
        {"task_id": ""},
        {"expected_security_version": True},
        {"expected_security_version": -1},
        {"expected_security_version": 1 << 63},
        {"now_utc": LATER.replace(tzinfo=None)},
    ],
)
def test_consume_invalid_input_is_sanitized_error(
    runtime: tuple[SQLiteStore, Clock], updates: dict[str, object]
) -> None:
    store, _ = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        consume(store, **updates)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("operation", ["create", "approve", "consume"])
@pytest.mark.parametrize("late", [False, True])
def test_expiry_at_transaction_entry_or_just_before_commit(
    runtime: tuple[SQLiteStore, Clock], operation: str, late: bool
) -> None:
    store, clock = runtime
    if operation == "create":
        record(store, result=decision(decision=Decision.ASK))
        change(store, TaskStatus.WAITING_APPROVAL)
    else:
        pending(store)
        if operation == "consume":
            transition(store, ApprovalStatus.APPROVED)
    before = full_snapshot(store)
    expiry = NOW + timedelta(seconds=30)
    if late:

        def delayed_audit_id() -> str:
            clock.now = expiry
            return "late-event"

        store._audit_id_factory = delayed_audit_id
    else:
        clock.now = expiry
    if operation == "consume":
        assert consume(store) is False
    else:
        with pytest.raises(StoreError):
            execute(store, "create" if operation == "create" else "approve")
    assert full_snapshot(store) == before


@pytest.mark.parametrize("table", ["approvals", "requests", "tasks", "audit_events"])
def test_consume_database_failure_rolls_back_all_four_writes(
    runtime: tuple[SQLiteStore, Clock], table: str
) -> None:
    store, _ = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    operation = "INSERT" if table == "audit_events" else "UPDATE"
    store._owned_connection().execute(
        f"""CREATE TRIGGER fail_write BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'TEST_SECRET_write'); END"""
    )
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        consume(store)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before
    store._owned_connection().execute("DROP TRIGGER fail_write")
    assert consume(store) is True


@pytest.mark.parametrize("table", ["approvals", "requests", "tasks"])
def test_consume_zero_rows_is_false_and_rolls_back_prior_changes(
    runtime: tuple[SQLiteStore, Clock], table: str
) -> None:
    store, _ = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    store._owned_connection().execute(
        f"""CREATE TRIGGER ignore_write BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(IGNORE); END"""
    )
    before = full_snapshot(store)
    assert consume(store) is False
    assert full_snapshot(store) == before


def test_create_approval_audit_failure_keeps_old_api_waiting_gap(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    record(store, result=decision(decision=Decision.ASK))
    change(store, TaskStatus.WAITING_APPROVAL)
    audit_failure(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        asyncio.run(store.create_approval(approval()))
    assert_sanitized(error.value)
    assert full_snapshot(store) == before
    assert load(store).status is TaskStatus.WAITING_APPROVAL
    assert request_status(store) == RequestStatus.DECIDED.value
    assert asyncio.run(store.get_approval("approval-1")) is None


@pytest.mark.parametrize("target", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_transition_audit_failure_preserves_approval_request_and_task(
    runtime: tuple[SQLiteStore, Clock], target: ApprovalStatus
) -> None:
    store, _ = runtime
    pending(store)
    audit_failure(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        transition(store, target)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_only_ask_and_exact_waiting_state_can_create_approval(
    runtime: tuple[SQLiteStore, Clock], kind: ToolKind
) -> None:
    store, _ = runtime
    dispatched(store, kind)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        asyncio.run(store.create_approval(approval()))
    assert full_snapshot(store) == before


def test_ask_without_waiting_and_duplicate_approval_are_rejected(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    record(store, result=decision(decision=Decision.ASK))
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        asyncio.run(store.create_approval(approval()))
    assert full_snapshot(store) == before
    change(store, TaskStatus.WAITING_APPROVAL)
    asyncio.run(store.create_approval(approval()))
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        asyncio.run(store.create_approval(approval(approval_id="another-approval")))
    assert full_snapshot(store) == before


@pytest.mark.parametrize("identifier", ["", None, 12, "\ud800"])
def test_get_approval_validates_identifier(
    runtime: tuple[SQLiteStore, Clock], identifier: object
) -> None:
    store, _ = runtime
    with pytest.raises(StoreError) as error:
        asyncio.run(store.get_approval(identifier))
    assert_sanitized(error.value)


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE approvals SET created_at_utc='2026-10-13T12:34:56'",
        "UPDATE approvals SET expires_at_utc=created_at_utc",
        "UPDATE approvals SET status='APPROVED'",
        "UPDATE approvals SET consumed_at_utc=created_at_utc",
    ],
)
def test_get_approval_rejects_invalid_stored_metadata(
    runtime: tuple[SQLiteStore, Clock], query: str
) -> None:
    store, _ = runtime
    pending(store)
    store._owned_connection().execute(query)
    with pytest.raises(StoreError) as error:
        asyncio.run(store.get_approval("approval-1"))
    assert_sanitized(error.value)


def test_reopen_can_read_metadata_but_cannot_restore_approval_execution(
    runtime: tuple[SQLiteStore, Clock], tmp_path: Path
) -> None:
    store, clock = runtime
    pending(store)
    approved = transition(store, ApprovalStatus.APPROVED)
    store.close()
    with SQLiteStore(
        tmp_path / "guard.db", utc_now=clock, audit_id_factory=lambda: "new"
    ) as reopened:
        assert asyncio.run(reopened.get_approval("missing")) is None
        assert asyncio.run(reopened.get_approval("approval-1")) == approved
        before = full_snapshot(reopened)
        assert consume(reopened) is False
        with pytest.raises(StoreError):
            transition(reopened, ApprovalStatus.INVALIDATED)
        assert full_snapshot(reopened) == before


@pytest.mark.parametrize(
    ("high", "last", "observed", "security_increment", "revision_increment"),
    [
        (Label.PUBLIC, Label.PUBLIC, Label.PUBLIC, 0, 0),
        (Label.PUBLIC, Label.PUBLIC, Label.INTERNAL, 1, 1),
        (Label.PUBLIC, Label.PUBLIC, Label.CONFIDENTIAL, 1, 1),
        (Label.CONFIDENTIAL, Label.CONFIDENTIAL, Label.INTERNAL, 0, 1),
        (Label.CONFIDENTIAL, Label.INTERNAL, Label.PUBLIC, 0, 1),
        (Label.CONFIDENTIAL, Label.PUBLIC, Label.CONFIDENTIAL, 0, 1),
        (Label.CONFIDENTIAL, Label.INTERNAL, None, 1, 1),
    ],
)
def test_read_commits_monotonic_labels_history_and_request_atomically(
    runtime: tuple[SQLiteStore, Clock],
    high: Label,
    last: Label,
    observed: Label | None,
    security_increment: int,
    revision_increment: int,
) -> None:
    store, _ = runtime
    store._owned_connection().execute(
        "UPDATE tasks SET high_watermark=?,last_read_label=?", (int(high), int(last))
    )
    req = dispatched(store)
    result = observe(store, req, observed)
    assert result == load(store)
    assert result.high_watermark == max(high, observed if observed is not None else high)
    assert result.last_read_label is (observed if observed is not None else last)
    assert result.security_version == 7 + security_increment
    assert result.row_revision == 11 + revision_increment
    assert result.status is (TaskStatus.HALTED if observed is None else TaskStatus.RUNNING)
    assert result.unclassified == (observed is None)
    assert len(result.escalations) == int(observed is not None and observed > high)
    if result.escalations:
        assert result.escalations[0].source_id == "resource-1"
        assert result.escalations[0].at_seq == 1
    assert request_status(store) == (
        RequestStatus.WITHHELD.value if observed is None else RequestStatus.SUCCEEDED.value
    )
    audit = (
        store._owned_connection()
        .execute("SELECT * FROM audit_events WHERE event_type='OBSERVED'")
        .fetchone()
    )
    assert audit["label"] == (None if observed is None else int(observed))
    assert audit["detail"] is None
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        observe(store, req, observed)
    assert full_snapshot(store) == before


def test_read_without_resource_uses_registered_tool_identity(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    req = request(resource_id=None)
    record(store, req)
    reserve(store)
    assert observe(store, req, Label.INTERNAL).escalations[0].source_id == "server-1/read-tool"


@pytest.mark.parametrize("target", ["tasks", "escalations", "requests", "audit_events"])
def test_observation_any_write_failure_rolls_back_and_does_not_consume_reservation(
    runtime: tuple[SQLiteStore, Clock], target: str
) -> None:
    store, _ = runtime
    req = dispatched(store)
    action = "INSERT" if target in ("escalations", "audit_events") else "UPDATE"
    store._owned_connection().execute(
        f"""CREATE TRIGGER fail_write BEFORE {action} ON {target}
        BEGIN SELECT RAISE(ABORT, 'TEST_SECRET_write'); END"""
    )
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        observe(store, req, Label.INTERNAL)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before
    store._owned_connection().execute("DROP TRIGGER fail_write")
    assert observe(store, req, Label.INTERNAL).high_watermark is Label.INTERNAL


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
@pytest.mark.parametrize("field", ["epoch", "arguments", "fingerprint", "resource", "boundary"])
def test_response_rejects_request_context_changes(
    runtime: tuple[SQLiteStore, Clock], kind: ToolKind, field: str
) -> None:
    store, _ = runtime
    req = dispatched(store, kind)
    changes = {
        "epoch": {"epoch": 4},
        "arguments": {"arguments": {"different": "TEST_SECRET_changed"}},
        "fingerprint": {"tool": req.tool.model_copy(update={"fingerprint": "c" * 64})},
        "resource": {"resource_id": "another-resource"},
        "boundary": {
            "server_boundary": req.server_boundary.model_copy(update={"boundary_id": "x"})
        },
    }
    changed = req.model_copy(update=changes[field])
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        result_commit(store, changed)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("operation", ["read", "send", "failure"])
def test_unreserved_response_cannot_be_committed(
    runtime: tuple[SQLiteStore, Clock], operation: str
) -> None:
    store, _ = runtime
    record(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        execute(store, operation)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_response_wrong_kind_and_changed_task_cannot_commit(
    runtime: tuple[SQLiteStore, Clock], kind: ToolKind
) -> None:
    store, _ = runtime
    req = dispatched(store, kind)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        execute(store, "read" if kind is ToolKind.SEND else "send", req)
    assert full_snapshot(store) == before
    change(store, TaskStatus.HALTED)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        result_commit(store, req)
    assert full_snapshot(store) == before


def test_send_success_preserves_all_task_fields_and_rejects_duplicate(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    store._owned_connection().execute("UPDATE tasks SET high_watermark=2,last_read_label=1")
    req = dispatched(store, ToolKind.SEND)
    original = load(store)
    send(store, req)
    assert load(store) == original
    assert request_status(store) == RequestStatus.SUCCEEDED.value
    assert store._owned_connection().execute("SELECT count(*) FROM escalations").fetchone()[0] == 0
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        send(store, req)
    assert full_snapshot(store) == before


def test_send_audit_failure_rolls_back_success(runtime: tuple[SQLiteStore, Clock]) -> None:
    store, _ = runtime
    req = dispatched(store, ToolKind.SEND)
    audit_failure(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        send(store, req)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize(
    ("kind", "req_status", "target", "increment"),
    [
        (ToolKind.READ, RequestStatus.FAILED, TaskStatus.RUNNING, 0),
        (ToolKind.READ, RequestStatus.WITHHELD, TaskStatus.HALTED, 1),
        (ToolKind.SEND, RequestStatus.UNKNOWN_OUTCOME, TaskStatus.UNKNOWN_OUTCOME, 1),
    ],
)
def test_post_dispatch_failure_commits_valid_pair_without_lowering_taint(
    runtime: tuple[SQLiteStore, Clock],
    kind: ToolKind,
    req_status: RequestStatus,
    target: TaskStatus,
    increment: int,
) -> None:
    store, _ = runtime
    store._owned_connection().execute("UPDATE tasks SET high_watermark=2,last_read_label=1")
    req = dispatched(store, kind)
    original = load(store)
    result = failure(store, req, req_status, target)
    assert result == original.model_copy(
        update={
            "status": target,
            "row_revision": 11 + increment,
            "security_version": 7 + increment,
        }
    )
    assert result == load(store)
    assert request_status(store) == req_status.value
    assert (
        store._owned_connection()
        .execute(
            "SELECT detail FROM audit_events WHERE event_type=?", ("REQUEST_" + req_status.name,)
        )
        .fetchone()[0]
        == '{"exception_type":"DispatchError"}'
    )
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        failure(store, req, req_status, target)
    assert full_snapshot(store) == before


@pytest.mark.parametrize(
    ("kind", "req_status", "target"),
    [
        (kind, req_status, target)
        for kind in (ToolKind.READ, ToolKind.SEND)
        for req_status in (
            RequestStatus.FAILED,
            RequestStatus.WITHHELD,
            RequestStatus.UNKNOWN_OUTCOME,
        )
        for target in (TaskStatus.RUNNING, TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME)
        if (kind, req_status, target)
        not in {
            (ToolKind.READ, RequestStatus.FAILED, TaskStatus.RUNNING),
            (ToolKind.READ, RequestStatus.WITHHELD, TaskStatus.HALTED),
            (ToolKind.SEND, RequestStatus.UNKNOWN_OUTCOME, TaskStatus.UNKNOWN_OUTCOME),
        }
    ],
)
def test_invalid_failure_pairs_are_rejected(
    runtime: tuple[SQLiteStore, Clock],
    kind: ToolKind,
    req_status: RequestStatus,
    target: TaskStatus,
) -> None:
    store, _ = runtime
    req = dispatched(store, kind)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        failure(store, req, req_status, target)
    assert full_snapshot(store) == before


@pytest.mark.parametrize(
    "status", [TaskStatus.WAITING_APPROVAL, TaskStatus.HALTED, TaskStatus.CLOSED]
)
def test_known_read_failure_cannot_resume_changed_or_terminal_task(
    runtime: tuple[SQLiteStore, Clock], status: TaskStatus
) -> None:
    store, _ = runtime
    req = dispatched(store)
    change(store, status)
    before = full_snapshot(store)
    with pytest.raises(StoreError):
        failure(store, req, RequestStatus.FAILED, TaskStatus.RUNNING)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_failure_can_finish_request_when_task_already_has_matching_terminal_status(
    runtime: tuple[SQLiteStore, Clock], kind: ToolKind
) -> None:
    store, _ = runtime
    req = dispatched(store, kind)
    target = TaskStatus.HALTED if kind is ToolKind.READ else TaskStatus.UNKNOWN_OUTCOME
    req_status = RequestStatus.WITHHELD if kind is ToolKind.READ else RequestStatus.UNKNOWN_OUTCOME
    original = change(store, target)
    assert failure(store, req, req_status, target) == original
    assert load(store) == original


@pytest.mark.parametrize("exception_type", ["TEST_SECRET raw body", "Name\nSecret", "", "한글", 1])
def test_failure_rejects_free_form_exception_input(
    runtime: tuple[SQLiteStore, Clock], exception_type: object
) -> None:
    store, _ = runtime
    req = dispatched(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        failure(store, req, exception_type=exception_type)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


def test_failure_audit_error_rolls_back_task_and_request(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    req = dispatched(store)
    audit_failure(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        failure(store, req)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_reopen_does_not_restore_response_commit_authority(
    runtime: tuple[SQLiteStore, Clock], tmp_path: Path, kind: ToolKind
) -> None:
    store, _ = runtime
    req = dispatched(store, kind)
    store.close()
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "new") as reopened:
        before = full_snapshot(reopened)
        with pytest.raises(StoreError):
            result_commit(reopened, req)
        assert full_snapshot(reopened) == before


@pytest.mark.parametrize("operation", ["create", "approve", "consume", "read", "send", "failure"])
def test_lost_commit_ack_discards_all_authority_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    inject_connection(monkeypatch)
    identifiers = count(1)
    clock = Clock()
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"event-{next(identifiers)}", utc_now=clock
    ) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        req = request()
        if operation == "create":
            record(store, result=decision(decision=Decision.ASK))
            change(store, TaskStatus.WAITING_APPROVAL)
        elif operation in ("approve", "consume"):
            pending(store)
            if operation == "consume":
                transition(store, ApprovalStatus.APPROVED)
        else:
            req = dispatched(store, ToolKind.SEND if operation == "send" else ToolKind.READ)
        connection = store._owned_connection()
        assert isinstance(connection, FaultConnection)
        connection.fail_commit = True
        with pytest.raises(StoreError) as error:
            execute(store, operation, req)
        assert_sanitized(error.value)
        assert store._connection is None
        assert not store._allowed_decisions
        assert not store._request_contexts
        assert not store._approval_contexts
        assert not store._dispatch_contexts
    # ACK failure here occurred after actual COMMIT. The durable event appears once.
    with SQLiteStore(tmp_path / "guard.db") as reopened:
        event = {
            "create": "APPROVAL_PENDING",
            "approve": "APPROVAL_APPROVED",
            "consume": "CONSUMED",
            "read": "OBSERVED",
            "send": "SEND_COMPLETED",
            "failure": "REQUEST_WITHHELD",
        }[operation]
        assert (
            reopened._owned_connection()
            .execute("SELECT count(*) FROM audit_events WHERE event_type=?", (event,))
            .fetchone()[0]
            == 1
        )


def test_all_lifecycle_metadata_and_audits_omit_raw_arguments(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    pending(store)
    transition(store, ApprovalStatus.APPROVED)
    assert consume(store) is True
    failure(store, request())
    assert "TEST_SECRET" not in repr(full_snapshot(store))
    assert {
        row[0] for row in store._owned_connection().execute("SELECT detail FROM audit_events")
    } == {
        None,
        '{"exception_type":"DispatchError"}',
    }


def test_non_callable_clock_is_rejected_without_db_creation(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    with pytest.raises(StoreError):
        SQLiteStore(path, utc_now=1)
    assert not path.exists()


@pytest.mark.parametrize("operation", ["create", "approve", "consume"])
def test_clock_regression_during_approval_write_does_not_grant_execution(
    runtime: tuple[SQLiteStore, Clock], operation: str
) -> None:
    store, clock = runtime
    if operation == "create":
        record(store, result=decision(decision=Decision.ASK))
        change(store, TaskStatus.WAITING_APPROVAL)
    else:
        pending(store)
        if operation == "consume":
            transition(store, ApprovalStatus.APPROVED)
    clock.now = NOW + timedelta(seconds=10)
    before = full_snapshot(store)

    def regressing_audit_id() -> str:
        clock.now = NOW - timedelta(seconds=1)
        return "clock-regressed-event"

    store._audit_id_factory = regressing_audit_id
    if operation == "consume":
        assert consume(store) is False
    else:
        with pytest.raises(StoreError):
            execute(store, operation)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("label", [False, True, 0, 1, 2, "INTERNAL", "TEST_SECRET"])
def test_read_label_requires_enum_and_does_not_write_on_invalid_input(
    runtime: tuple[SQLiteStore, Clock], label: object
) -> None:
    store, _ = runtime
    req = dispatched(store)
    before = full_snapshot(store)
    with pytest.raises(StoreError) as error:
        observe(store, req, label)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


def test_multiple_reads_accumulate_history_and_never_lower_high_watermark(
    runtime: tuple[SQLiteStore, Clock],
) -> None:
    store, _ = runtime
    req = dispatched(store)
    observe(store, req, Label.INTERNAL)
    second = request(request_id="request-2", call_seq=2)
    record(store, second, decision(security_version=8))
    reserve(store, "request-2")
    lower = observe(store, second, Label.PUBLIC)
    assert lower.high_watermark is Label.INTERNAL
    assert lower.last_read_label is Label.PUBLIC
    assert lower.security_version == 8
    third = request(request_id="request-3", call_seq=3)
    record(store, third, decision(security_version=8))
    reserve(store, "request-3")
    higher = observe(store, third, Label.CONFIDENTIAL)
    assert higher.high_watermark is Label.CONFIDENTIAL
    assert higher.security_version == 9
    assert higher.row_revision == 14
    assert tuple(item.at_seq for item in higher.escalations) == (1, 3)
    assert higher.escalations[-1].from_label is Label.INTERNAL
    assert higher == load(store)


@pytest.mark.parametrize("operation", ["create", "approve", "consume", "read", "send", "failure"])
def test_missing_audit_id_provider_rolls_back_each_lifecycle_operation(
    runtime: tuple[SQLiteStore, Clock], operation: str
) -> None:
    store, _ = runtime
    req = request()
    if operation == "create":
        record(store, result=decision(decision=Decision.ASK))
        change(store, TaskStatus.WAITING_APPROVAL)
    elif operation in ("approve", "consume"):
        pending(store)
        if operation == "consume":
            transition(store, ApprovalStatus.APPROVED)
    else:
        req = dispatched(store, ToolKind.SEND if operation == "send" else ToolKind.READ)
    before = full_snapshot(store)
    store._audit_id_factory = None
    with pytest.raises(StoreError) as error:
        execute(store, operation, req)
    assert_sanitized(error.value)
    assert full_snapshot(store) == before


@pytest.mark.parametrize("result", ["success", "unclassified", "rollback", "ack"])
def test_real_sqlite_read_commit_controls_b_gate_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: str
) -> None:
    """B 공개 경계 단위 확인. MCP/GUI/모델/wire 연결 검증이 아니다."""
    if result == "ack":
        inject_connection(monkeypatch)
    identifiers = count(1)
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"event-{next(identifiers)}"
    ) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        req = dispatched(store)
        expected = load(store)
        rules = (
            ()
            if result == "unclassified"
            else (
                SourceRule(
                    server_id="server-1", tool_name="read-tool", prefix=(), label=Label.INTERNAL
                ),
            )
        )
        halts: list[bool] = []
        gate = ResponseGate(
            store,
            Catalog(_Registry(), profile_id="default", source_rules=rules),
            utc_now=lambda: LATER,
            global_halt=lambda: halts.append(True),
        )
        if result == "rollback":
            audit_failure(store)
        elif result == "ack":
            connection = store._owned_connection()
            assert isinstance(connection, FaultConnection)
            connection.fail_commit = True
        raw = _raw("TEST_SECRET_result_body")
        if result in ("rollback", "ack"):
            with pytest.raises(StoreError) as error:
                asyncio.run(gate.handle(req, raw, expected_state=expected))
            # 기존 gate의 중첩 실패는 고정 StoreError를 context로 가질 수 있다.
            assert error.value.args == (error.value.reason.value,)
            assert "TEST_SECRET" not in "".join(traceback.format_exception(error.value))
            assert halts == [True]
        else:
            outcome = asyncio.run(gate.handle(req, raw, expected_state=expected))
            assert not halts
            if result == "success":
                assert isinstance(outcome, Released)
                assert outcome.result.text == "TEST_SECRET_result_body"
                assert request_status(store) == RequestStatus.SUCCEEDED.value
                assert load(store).high_watermark is Label.INTERNAL
            else:
                assert isinstance(outcome, Withheld)
                assert "TEST_SECRET_result_body" not in repr(outcome)
                assert load(store).status is TaskStatus.HALTED
            assert "TEST_SECRET" not in repr(full_snapshot(store))
