"""B6 상태 전이·판정 감사·ALLOW 예약의 실제 파일 DB 단위 검사.

I4/I6/I9·T07/T11/T19·S03의 저장소 단위 부분이다. 직접 SQL은 저장 결과
관찰과 오염/감사 실패 주입용이며 C의 독립 인수·wire 검증을 대신하지 않는다.
v1에 요청 epoch가 없어 재시작한 Store는 과거 ALLOW의 예약을 거부해야 한다.
"""

import asyncio
import json
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.enums import Decision, Label, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary, PolicyDecision, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.security.digest import json_digest
from guard_app.storage import SQLiteStore

NOW = datetime(2026, 10, 13, 12, 34, 56, 123456, tzinfo=UTC)
LATER = NOW + timedelta(seconds=1)
TRANSITIONS = {
    (TaskStatus.READY, TaskStatus.RUNNING),
    (TaskStatus.READY, TaskStatus.HALTED),
    (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL),
    (TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING),
    (TaskStatus.RUNNING, TaskStatus.HALTED),
    (TaskStatus.WAITING_APPROVAL, TaskStatus.HALTED),
    (TaskStatus.RUNNING, TaskStatus.UNKNOWN_OUTCOME),
    (TaskStatus.RUNNING, TaskStatus.CLOSED),
    (TaskStatus.HALTED, TaskStatus.CLOSED),
    (TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED),
}


def task(**updates: object) -> TaskState:
    values: dict[str, object] = {
        "task_id": "task-1",
        "epoch": 3,
        "profile_id": "default",
        "policy_version": "v1",
        "status": TaskStatus.RUNNING,
        "security_version": 7,
        "row_revision": 11,
    }
    values.update(updates)
    return TaskState.model_validate(values)


def request(**updates: object) -> ResolvedRequest:
    values: dict[str, object] = {
        "request_id": "request-1",
        "task_id": "task-1",
        "epoch": 3,
        "call_seq": 1,
        "tool": ToolSpec(
            server_id="server-1",
            tool_name="read-tool",
            kind=ToolKind.READ,
            input_schema={"type": "object"},
            destination_arg=None,
            resource_arg=None,
            fingerprint="a" * 64,
        ),
        "arguments": {"body": "TEST_SECRET_request_body", "nested": ["TEST_SECRET_token"]},
        "resource_id": "resource-1",
        "server_boundary": Boundary(
            boundary_id="server-boundary",
            tier=TrustTier.INTERNAL,
            max_label=Label.CONFIDENTIAL,
        ),
        "destination": None,
    }
    values.update(updates)
    return ResolvedRequest.model_validate(values)


def decision(**updates: object) -> PolicyDecision:
    values: dict[str, object] = {
        "decision": Decision.ALLOW,
        "reasons": (Reason.OK,),
        "verdicts": (),
        "policy_version": "v1",
        "security_version": 7,
    }
    values.update(updates)
    return PolicyDecision.model_validate(values)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    identifiers = count(1)
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"event-{next(identifiers)}"
    ) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        yield instance


def change(
    store: SQLiteStore,
    status: TaskStatus,
    *,
    task_id: str = "task-1",
    revision: int = 11,
    at: datetime = NOW,
    reason: Reason = Reason.OK,
) -> TaskState:
    return asyncio.run(
        store.set_task_status(
            task_id, status, expected_row_revision=revision, at_utc=at, reason=reason
        )
    )


def record(
    store: SQLiteStore,
    req: ResolvedRequest | None = None,
    result: PolicyDecision | None = None,
    *,
    digest: str | None = None,
    at: datetime = NOW,
) -> None:
    req = request() if req is None else req
    asyncio.run(
        store.record_decision(
            req,
            decision() if result is None else result,
            arguments_digest=json_digest(req.arguments) if digest is None else digest,
            at_utc=at,
        )
    )


def reserve(store: SQLiteStore, request_id: str = "request-1", at: datetime = LATER) -> None:
    asyncio.run(store.reserve_dispatch(request_id, now_utc=at))


def snapshot(store: SQLiteStore) -> dict[str, list[tuple[object, ...]]]:
    connection = store._owned_connection()
    queries = {
        "tasks": "SELECT * FROM tasks ORDER BY 1",
        "escalations": "SELECT * FROM escalations ORDER BY 1",
        "requests": "SELECT * FROM requests ORDER BY 1",
        "audit_events": "SELECT * FROM audit_events ORDER BY 1",
    }
    return {
        table: [tuple(row) for row in connection.execute(query)] for table, query in queries.items()
    }


def assert_sanitized(error: StoreError) -> None:
    assert error.args == (error.reason.value,)
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert "TEST_SECRET" not in rendered
    assert "sqlite3.IntegrityError:" not in rendered
    assert "sqlite3.OperationalError:" not in rendered


def audit_failure(store: SQLiteStore) -> None:
    store._owned_connection().execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events
        BEGIN SELECT RAISE(ABORT, 'TEST_SECRET_audit'); END""")


@pytest.mark.parametrize(("before", "after"), sorted(TRANSITIONS))
def test_allowed_transition_preserves_security_state_and_has_atomic_audit(
    tmp_path: Path, before: TaskStatus, after: TaskStatus
) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        original = task(
            status=before, high_watermark=Label.CONFIDENTIAL, last_read_label=Label.INTERNAL
        )
        asyncio.run(instance.create_task(original, created_at_utc=NOW))
        result = change(instance, after, reason=Reason.TASK_NOT_RUNNING)
        increment = int(after in {TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED})
        assert result == original.model_copy(
            update={"status": after, "row_revision": 12, "security_version": 7 + increment}
        )
        assert asyncio.run(instance.load_task("task-1")) == result
        rows = instance._owned_connection().execute("SELECT * FROM audit_events").fetchall()
        assert len(rows) == 1
        assert rows[0]["event_id"] == "event-1"
        assert rows[0]["task_id"] == "task-1"
        assert (
            rows[0]["event_type"]
            == {
                TaskStatus.RUNNING: "TASK_RUNNING",
                TaskStatus.WAITING_APPROVAL: "TASK_WAITING_APPROVAL",
                TaskStatus.HALTED: "HALTED",
                TaskStatus.UNKNOWN_OUTCOME: "TASK_UNKNOWN_OUTCOME",
                TaskStatus.CLOSED: "TASK_CLOSED",
            }[after]
        )
        assert rows[0]["reason_code"] == Reason.TASK_NOT_RUNNING.value
        assert rows[0]["at_utc"] == NOW.isoformat(timespec="microseconds")
        assert rows[0]["detail"] is None
        assert rows[0]["label"] is None
        assert not instance._owned_connection().in_transaction


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (before, after)
        for before in TaskStatus
        for after in TaskStatus
        if before != after and (before, after) not in TRANSITIONS
    ],
)
def test_forbidden_transition_cannot_change_or_audit_task(
    tmp_path: Path, before: TaskStatus, after: TaskStatus
) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(status=before), created_at_utc=NOW))
        original = snapshot(instance)
        with pytest.raises(StoreError) as error:
            change(instance, after)
        assert_sanitized(error.value)
        assert snapshot(instance) == original


@pytest.mark.parametrize("status", list(TaskStatus))
def test_same_status_is_revision_checked_noop(tmp_path: Path, status: TaskStatus) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        original = task(status=status)
        asyncio.run(instance.create_task(original, created_at_utc=NOW))
        assert change(instance, status) == original
        assert snapshot(instance)["audit_events"] == []
        with pytest.raises(StoreError):
            change(instance, status, revision=10)
        assert asyncio.run(instance.load_task("task-1")) == original


@pytest.mark.parametrize("after", [TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL])
def test_unclassified_task_cannot_enter_execution_state(tmp_path: Path, after: TaskStatus) -> None:
    before = TaskStatus.READY if after is TaskStatus.RUNNING else TaskStatus.RUNNING
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        original = task(status=before, unclassified=True)
        asyncio.run(instance.create_task(original, created_at_utc=NOW))
        saved = snapshot(instance)
        with pytest.raises(StoreError):
            change(instance, after)
        assert snapshot(instance) == saved


@pytest.mark.parametrize("revision", [10, 12, -1, True, "11", 1 << 63])
def test_status_revision_conflict_or_bad_type_changes_nothing(
    store: SQLiteStore, revision: int
) -> None:
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        change(store, TaskStatus.HALTED, revision=revision)
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    ("status", "reason"),
    [("halted", Reason.OK), (TaskStatus.HALTED, "OK"), (1, Reason.OK), (TaskStatus.HALTED, None)],
)
def test_status_and_reason_require_enum_values(
    store: SQLiteStore, status: TaskStatus, reason: Reason
) -> None:
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        change(store, status, reason=reason)
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize("field", ["row_revision", "security_version"])
def test_status_counter_overflow_rolls_back(tmp_path: Path, field: str) -> None:
    original = task(**{field: (1 << 63) - 1})
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(original, created_at_utc=NOW))
        before = snapshot(instance)
        with pytest.raises(StoreError) as error:
            change(instance, TaskStatus.HALTED, revision=original.row_revision)
        assert_sanitized(error.value)
        assert snapshot(instance) == before


def test_status_change_audit_abort_rolls_back_and_can_retry(store: SQLiteStore) -> None:
    before = snapshot(store)
    audit_failure(store)
    with pytest.raises(StoreError) as error:
        change(store, TaskStatus.HALTED)
    assert_sanitized(error.value)
    assert snapshot(store) == before
    assert not store._owned_connection().in_transaction
    store._owned_connection().execute("DROP TRIGGER fail_audit")
    assert change(store, TaskStatus.HALTED).status is TaskStatus.HALTED


@pytest.mark.parametrize("choice", list(Decision))
def test_decision_records_only_metadata_and_audit(store: SQLiteStore, choice: Decision) -> None:
    req = request()
    result = decision(decision=choice, reasons=(Reason.OK, Reason.AGT_DENY))
    record(store, req, result)
    connection = store._owned_connection()
    row = connection.execute("SELECT * FROM requests").fetchone()
    assert row["request_id"] == req.request_id
    assert row["task_id"] == req.task_id
    assert row["call_seq"] == req.call_seq
    assert row["server_id"] == req.tool.server_id
    assert row["tool_name"] == req.tool.tool_name
    assert row["arguments_digest"] == json_digest(req.arguments)
    assert row["decision"] == int(choice)
    assert json.loads(row["reasons"]) == ["OK", "AGT_DENY"]
    assert row["status"] == ("DENIED" if choice is Decision.DENY else "DECIDED")
    assert row["dispatch_reserved_at_utc"] is None
    audit = connection.execute("SELECT * FROM audit_events").fetchone()
    assert audit["event_type"] == "DECIDED"
    assert audit["request_id"] == req.request_id
    assert audit["task_id"] == req.task_id
    assert audit["detail"] is None
    assert audit["at_utc"] == NOW.isoformat(timespec="microseconds")
    assert "TEST_SECRET" not in repr(snapshot(store))
    assert asyncio.run(store.load_task("task-1")) == task()


@pytest.mark.parametrize(
    ("req_updates", "decision_updates"),
    [
        ({"epoch": 2}, {}),
        ({"epoch": 4}, {}),
        ({}, {"security_version": 6}),
        ({}, {"security_version": 8}),
        ({}, {"policy_version": "v2"}),
    ],
)
def test_decision_context_must_match_current_task(
    store: SQLiteStore, req_updates: dict[str, object], decision_updates: dict[str, object]
) -> None:
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        record(store, request(**req_updates), decision(**decision_updates))
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "status", [status for status in TaskStatus if status is not TaskStatus.RUNNING]
)
def test_decision_cannot_record_on_nonrunning_task(tmp_path: Path, status: TaskStatus) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(status=status), created_at_utc=NOW))
        before = snapshot(instance)
        with pytest.raises(StoreError):
            record(instance)
        assert snapshot(instance) == before


def test_decision_cannot_record_on_unclassified_task(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(unclassified=True), created_at_utc=NOW))
        with pytest.raises(StoreError):
            record(instance)
        assert snapshot(instance)["requests"] == []
        assert snapshot(instance)["audit_events"] == []


@pytest.mark.parametrize("digest", ["", "a" * 64, "F" * 64, "0" * 63, "TEST_SECRET", True])
def test_decision_digest_must_be_exact_canonical_arguments_hash(
    store: SQLiteStore, digest: str
) -> None:
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        record(store, digest=digest)
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        ("request", "request_id", ""),
        ("request", "task_id", True),
        ("request", "epoch", True),
        ("request", "epoch", 1 << 63),
        ("request", "call_seq", True),
        ("request", "call_seq", 1 << 63),
        ("request", "resource_id", "\ud800"),
        ("decision", "decision", True),
        ("decision", "decision", 0),
        ("decision", "reasons", ("TEST_SECRET",)),
        ("decision", "reasons", ()),
        ("decision", "security_version", True),
        ("decision", "security_version", 1 << 63),
        ("decision", "policy_version", "\ud800"),
    ],
)
def test_constructed_decision_or_request_cannot_bypass_validation(
    store: SQLiteStore, model: str, field: str, value: object
) -> None:
    req, result = request(), decision()
    if model == "request":
        fields = req.model_dump(mode="python")
        fields[field] = value
        req = ResolvedRequest.model_construct(**fields)
    else:
        fields = result.model_dump(mode="python")
        fields[field] = value
        result = PolicyDecision.model_construct(**fields)
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        record(store, req, result, digest=json_digest(request().arguments))
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize("duplicate", ["id", "sequence"])
def test_duplicate_request_or_task_sequence_does_not_overwrite(
    store: SQLiteStore, duplicate: str
) -> None:
    record(store)
    before = snapshot(store)
    req = request(call_seq=2) if duplicate == "id" else request(request_id="request-2")
    with pytest.raises(StoreError) as error:
        record(store, req)
    assert_sanitized(error.value)
    assert snapshot(store) == before
    reserve(store)
    assert (
        store._owned_connection().execute("SELECT status FROM requests").fetchone()[0]
        == "DISPATCHING"
    )


def test_sequence_is_scoped_to_task_and_other_task_is_unchanged(store: SQLiteStore) -> None:
    other = task(task_id="other", high_watermark=Label.CONFIDENTIAL)
    asyncio.run(store.create_task(other, created_at_utc=NOW))
    record(store)
    record(store, request(request_id="other-request", task_id="other"))
    reserve(store)
    assert asyncio.run(store.load_task("other")) == other
    rows = (
        store._owned_connection()
        .execute("SELECT request_id,status FROM requests ORDER BY request_id")
        .fetchall()
    )
    assert [tuple(row) for row in rows] == [
        ("other-request", "DECIDED"),
        ("request-1", "DISPATCHING"),
    ]


def test_decision_audit_failure_rolls_back_request_and_can_retry(store: SQLiteStore) -> None:
    before = snapshot(store)
    audit_failure(store)
    with pytest.raises(StoreError) as error:
        record(store)
    assert_sanitized(error.value)
    assert snapshot(store) == before
    store._owned_connection().execute("DROP TRIGGER fail_audit")
    record(store)
    reserve(store)


def test_reservation_is_atomic_single_use_and_does_not_change_task(store: SQLiteStore) -> None:
    record(store)
    reserve(store)
    connection = store._owned_connection()
    row = connection.execute("SELECT * FROM requests").fetchone()
    assert row["status"] == "DISPATCHING"
    assert row["dispatch_reserved_at_utc"] == LATER.isoformat(timespec="microseconds")
    events = connection.execute(
        "SELECT event_type,request_id,task_id,detail FROM audit_events ORDER BY event_id"
    ).fetchall()
    assert [tuple(event) for event in events] == [
        ("DECIDED", "request-1", "task-1", None),
        ("DISPATCH_RESERVED", "request-1", "task-1", None),
    ]
    assert asyncio.run(store.load_task("task-1")) == task()
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        reserve(store)
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize("choice", [Decision.ASK, Decision.DENY])
def test_ask_and_deny_cannot_use_allow_reservation(store: SQLiteStore, choice: Decision) -> None:
    record(store, result=decision(decision=choice))
    before = snapshot(store)
    with pytest.raises(StoreError):
        reserve(store)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    "status",
    [
        "WAITING_APPROVAL",
        "DISPATCHING",
        "SUCCEEDED",
        "WITHHELD",
        "FAILED",
        "UNKNOWN_OUTCOME",
        "DENIED",
    ],
)
def test_nondecided_requests_cannot_reserve(store: SQLiteStore, status: str) -> None:
    record(store)
    store._owned_connection().execute("UPDATE requests SET status=?", (status,))
    before = snapshot(store)
    with pytest.raises(StoreError):
        reserve(store)
    assert snapshot(store) == before


def test_reservation_audit_abort_rolls_back_and_can_retry(store: SQLiteStore) -> None:
    record(store)
    before = snapshot(store)
    audit_failure(store)
    with pytest.raises(StoreError) as error:
        reserve(store)
    assert_sanitized(error.value)
    assert snapshot(store) == before
    assert not store._owned_connection().in_transaction
    store._owned_connection().execute("DROP TRIGGER fail_audit")
    reserve(store)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epoch", 4),
        ("profile_id", "other-profile"),
        ("policy_version", "v2"),
        ("high_watermark", 1),
        ("last_read_label", 1),
        ("unclassified", 1),
        ("status", "halted"),
        ("security_version", 8),
        ("row_revision", 12),
    ],
)
def test_any_task_context_change_blocks_old_allow(
    store: SQLiteStore, field: str, value: object
) -> None:
    record(store)
    queries = {
        "epoch": "UPDATE tasks SET epoch=? WHERE task_id=?",
        "profile_id": "UPDATE tasks SET profile_id=? WHERE task_id=?",
        "policy_version": "UPDATE tasks SET policy_version=? WHERE task_id=?",
        "high_watermark": "UPDATE tasks SET high_watermark=? WHERE task_id=?",
        "last_read_label": "UPDATE tasks SET last_read_label=? WHERE task_id=?",
        "unclassified": "UPDATE tasks SET unclassified=? WHERE task_id=?",
        "status": "UPDATE tasks SET status=? WHERE task_id=?",
        "security_version": "UPDATE tasks SET security_version=? WHERE task_id=?",
        "row_revision": "UPDATE tasks SET row_revision=? WHERE task_id=?",
    }
    store._owned_connection().execute(queries[field], (value, "task-1"))
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        reserve(store)
    assert_sanitized(error.value)
    assert snapshot(store) == before


def test_state_cycle_cannot_resurrect_prior_allow(store: SQLiteStore) -> None:
    record(store)
    change(store, TaskStatus.WAITING_APPROVAL)
    change(store, TaskStatus.RUNNING, revision=12)
    assert asyncio.run(store.load_task("task-1")).row_revision == 13
    before = snapshot(store)
    with pytest.raises(StoreError):
        reserve(store)
    assert snapshot(store) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "other"),
        ("call_seq", 2),
        ("server_id", "other-server"),
        ("tool_name", "other-tool"),
        ("destination_id", "other-destination"),
        ("arguments_digest", "b" * 64),
        ("decision", 1),
        ("reasons", '["AGT_DENY"]'),
        ("dispatch_reserved_at_utc", NOW.isoformat(timespec="microseconds")),
    ],
)
def test_request_metadata_corruption_cannot_borrow_reservation_context(
    store: SQLiteStore, field: str, value: object
) -> None:
    asyncio.run(store.create_task(task(task_id="other"), created_at_utc=NOW))
    record(store)
    queries = {
        "task_id": "UPDATE requests SET task_id=? WHERE request_id=?",
        "call_seq": "UPDATE requests SET call_seq=? WHERE request_id=?",
        "server_id": "UPDATE requests SET server_id=? WHERE request_id=?",
        "tool_name": "UPDATE requests SET tool_name=? WHERE request_id=?",
        "destination_id": "UPDATE requests SET destination_id=? WHERE request_id=?",
        "arguments_digest": "UPDATE requests SET arguments_digest=? WHERE request_id=?",
        "decision": "UPDATE requests SET decision=? WHERE request_id=?",
        "reasons": "UPDATE requests SET reasons=? WHERE request_id=?",
        "dispatch_reserved_at_utc": (
            "UPDATE requests SET dispatch_reserved_at_utc=? WHERE request_id=?"
        ),
    }
    store._owned_connection().execute(queries[field], (value, "request-1"))
    before = snapshot(store)
    with pytest.raises(StoreError) as error:
        reserve(store)
    assert_sanitized(error.value)
    assert snapshot(store) == before


def test_reopened_store_cannot_reserve_old_allow_without_epoch_evidence(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    with SQLiteStore(path, audit_id_factory=lambda: "event-old") as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance)
        original = snapshot(instance)
    with SQLiteStore(path, audit_id_factory=lambda: "event-new") as instance:
        with pytest.raises(StoreError) as error:
            reserve(instance)
        assert_sanitized(error.value)
        assert snapshot(instance) == original


@pytest.mark.parametrize("operation", ["status", "decision", "reserve"])
def test_missing_audit_factory_never_enables_writes(tmp_path: Path, operation: str) -> None:
    path = tmp_path / "guard.db"
    if operation == "reserve":
        with SQLiteStore(path, audit_id_factory=lambda: "event-old") as instance:
            asyncio.run(instance.create_task(task(), created_at_utc=NOW))
            record(instance)
    with SQLiteStore(path) as instance:
        if operation != "reserve":
            asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        before = snapshot(instance)
        action = {
            "status": lambda: change(instance, TaskStatus.HALTED),
            "decision": lambda: record(instance),
            "reserve": lambda: reserve(instance),
        }[operation]
        with pytest.raises(StoreError) as error:
            action()
        assert_sanitized(error.value)
        assert snapshot(instance) == before
        assert asyncio.run(instance.load_task("task-1")) == task()


@pytest.mark.parametrize("identifier", ["", True, 1, None, "\ud800"])
def test_invalid_audit_id_rolls_back_decision(tmp_path: Path, identifier: str) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: identifier) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        before = snapshot(instance)
        with pytest.raises(StoreError) as error:
            record(instance)
        assert_sanitized(error.value)
        assert snapshot(instance) == before


def test_audit_id_factory_value_error_is_sanitized(tmp_path: Path) -> None:
    def bad_identifier() -> str:
        raise ValueError("TEST_SECRET_factory")

    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=bad_identifier) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        with pytest.raises(StoreError) as error:
            record(instance)
        assert_sanitized(error.value)
        assert snapshot(instance)["requests"] == []


@pytest.mark.parametrize("operation", ["status", "decision", "reserve"])
def test_audit_id_collision_rolls_back_entire_operation(tmp_path: Path, operation: str) -> None:
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "same-event") as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance)
        before = snapshot(instance)
        action = {
            "status": lambda: change(instance, TaskStatus.HALTED),
            "decision": lambda: record(instance, request(request_id="request-2", call_seq=2)),
            "reserve": lambda: reserve(instance),
        }[operation]
        with pytest.raises(StoreError) as error:
            action()
        assert_sanitized(error.value)
        assert snapshot(instance) == before


@pytest.mark.parametrize("operation", ["status", "decision", "reserve"])
@pytest.mark.parametrize(
    "at",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9))), "TEST_SECRET", True],
)
def test_write_dates_require_real_utc_datetime(
    store: SQLiteStore, operation: str, at: datetime
) -> None:
    if operation == "reserve":
        record(store)
    before = snapshot(store)
    action = {
        "status": lambda: change(store, TaskStatus.HALTED, at=at),
        "decision": lambda: record(store, at=at),
        "reserve": lambda: reserve(store, at=at),
    }[operation]
    with pytest.raises(StoreError) as error:
        action()
    assert_sanitized(error.value)
    assert snapshot(store) == before


@pytest.mark.parametrize("operation", ["status", "decision", "reserve"])
def test_missing_record_is_an_error_without_default_creation(
    store: SQLiteStore, operation: str
) -> None:
    before = snapshot(store)
    action = {
        "status": lambda: change(store, TaskStatus.HALTED, task_id="missing"),
        "decision": lambda: record(store, request(task_id="missing")),
        "reserve": lambda: reserve(store, request_id="missing"),
    }[operation]
    with pytest.raises(StoreError) as error:
        action()
    assert_sanitized(error.value)
    assert snapshot(store) == before


def test_sql_metacharacters_are_bound_and_secret_arguments_never_persist(
    store: SQLiteStore,
) -> None:
    identifier = "x'); DROP TABLE tasks; --"
    original = task(task_id=identifier)
    asyncio.run(store.create_task(original, created_at_utc=NOW))
    req = request(request_id=identifier, task_id=identifier)
    record(store, req)
    reserve(store, request_id=identifier)
    assert asyncio.run(store.load_task(identifier)) == original
    assert asyncio.run(store.load_task("task-1")) == task()
    assert "TEST_SECRET" not in repr(snapshot(store))
    assert store._owned_connection().execute("SELECT count(*) FROM tasks").fetchone()[0] == 2
