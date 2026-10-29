"""B6.8 SEQ-02/REC-04: real SQLite failure-audit durability and I9."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, RawResult, Released, Withheld
from guard_app.domain.enums import Label, RequestStatus, TaskStatus
from guard_app.domain.errors import ResolutionError, StoreError
from guard_app.domain.models import FailureAuditRecord, ProposedCall, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import FailureEvent, GuardService, durable_failure_audit
from guard_app.storage import SQLiteStore
from tests.unit.c_store_kill import KILLED, TABLES
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_store_recovery import RUN, _close, _open

NOW = datetime(2026, 10, 30, 12, 34, 56, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
PATH_MARKER = "B68_PATH_MARKER_3a794921"
ARG_MARKER = "B68_ARG_MARKER_84d62413"
ERROR_MARKER = "B68_EXCEPTION_SECRET_a71c9f82"
VISIBLE_RESOURCE = "public/B68-visible-resource-72e43a"
BOOT = (
    "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
    "from tests.unit.test_b68_failure_audit_durability import _kill_child; "
    "_kill_child(sys.argv[3:])"
)


@asynccontextmanager
async def _runtime(path, *, resolver=None):
    h = _Harness(label=Label.PUBLIC, source_label=Label.INTERNAL)
    if resolver is not None:
        tool = ToolSpec.model_validate(
            {
                **h.registry.read.model_dump(mode="python"),
                "resource_arg": "path",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "payload": {"type": "string"}},
                    "required": ["path", "payload"],
                    "additionalProperties": False,
                },
            }
        )
        h.registry.read = tool
        h.registry.record = h.registry.record.model_copy(update={"tools": (tool, h.registry.send)})
        h.catalog = Catalog(
            h.registry,
            profile_id="p",
            resource_resolver=resolver,
            source_rules=(
                SourceRule(
                    server_id="docs", tool_name="read", prefix=("public",), label=Label.INTERNAL
                ),
            ),
        )
    ids = count(1)
    with SQLiteStore(
        path, audit_id_factory=lambda: f"audit-{next(ids)}", utc_now=lambda: NOW
    ) as store:
        await store.create_task(h.store.states["task"], created_at_utc=NOW)
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
            next_call_seq=h.sequence,
            failure_audit=durable_failure_audit(store),
            utc_now=lambda: NOW,
            monotonic=lambda: h.monotonic,
        )
        try:
            yield h, store, guard
        finally:
            await guard.aclose()


def _path_call(path):
    return ProposedCall(
        tool_ref="docs__read", arguments={"path": path, "payload": ARG_MARKER}, model_call_id="call"
    )


async def _rows(store, sql, params=()):
    return await store._run_on_writer_async(
        lambda connection: [tuple(row) for row in connection.execute(sql, params).fetchall()]
    )


async def _failure_rows(store):
    return await _rows(
        store,
        "SELECT request_id,task_id,event_type,reason_code,detail,at_utc "
        "FROM audit_events WHERE event_type LIKE 'FAILURE_%' ORDER BY event_id",
    )


def _path_resolver(_tool, path):
    if PATH_MARKER in path:
        raise ResolutionError(Reason.INVALID_ARGUMENTS)
    return VISIBLE_RESOURCE


@pytest.mark.parametrize("failure", ["resolver", "checker"])
def test_before_dispatch_failure_is_durable_without_request_or_raw_input(tmp_path, failure):
    async def scenario():
        async with _runtime(tmp_path / "before.db", resolver=_path_resolver) as (h, store, guard):
            if failure == "checker":
                h.access.error = RuntimeError(ERROR_MARKER)
            result = await guard.submit(
                "task",
                _path_call(PATH_MARKER if failure == "resolver" else "public/read"),
                expected_epoch=1,
            )
            assert isinstance(result, Denied)
            reason = Reason.INVALID_ARGUMENTS if failure == "resolver" else Reason.INTERNAL_ERROR
            assert result.reasons == (reason,)
            assert await _rows(store, "SELECT request_id FROM requests") == []
            assert not h.transport.calls
            rows = await _failure_rows(store)
            assert len(rows) == 1
            request, task, event, code, detail, stamp = rows[0]
            assert (request, task, event, code) == (
                None,
                "task",
                "FAILURE_BEFORE_DISPATCH",
                reason.value,
            )
            assert json.loads(detail) == {
                "exception_type": "ResolutionError" if failure == "resolver" else "RuntimeError",
                "run_id": None,
                "epoch": None,
            }
            assert datetime.fromisoformat(stamp) == NOW
            assert all(
                x not in repr(rows) + repr(result) for x in (PATH_MARKER, ARG_MARKER, ERROR_MARKER)
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "outcome_type", "request_status", "task_status", "event"),
    [
        ("read", Withheld, RequestStatus.WITHHELD, TaskStatus.HALTED, "REQUEST_WITHHELD"),
        (
            "send",
            OutcomeUnknown,
            RequestStatus.UNKNOWN_OUTCOME,
            TaskStatus.UNKNOWN_OUTCOME,
            "REQUEST_UNKNOWN_OUTCOME",
        ),
    ],
)
def test_post_dispatch_failure_commits_request_task_and_failure_audit_together(
    tmp_path, kind, outcome_type, request_status, task_status, event
):
    async def scenario():
        async with _runtime(tmp_path / "after.db") as (h, store, guard):
            h.transport.error = RuntimeError(ERROR_MARKER)
            result = await guard.submit("task", _call(kind), expected_epoch=1)
            assert isinstance(result, outcome_type)
            assert result.reasons == (Reason.POST_DISPATCH_ERROR,)
            assert len(h.transport.calls) == 1
            assert (await store.load_task("task")).status is task_status
            assert await _rows(store, "SELECT status FROM requests") == [(request_status.value,)]
            rows = await _rows(
                store,
                "SELECT request_id,reason_code,detail FROM audit_events WHERE event_type=?",
                (event,),
            )
            assert len(rows) == 1
            assert rows[0][0:2] == ("request-1", Reason.POST_DISPATCH_ERROR.value)
            failure = await _failure_rows(store)
            assert len(failure) == 1
            assert failure[0][2:4] == ("FAILURE_AFTER_DISPATCH", Reason.POST_DISPATCH_ERROR.value)
            assert ERROR_MARKER not in repr(rows) + repr(failure) + repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["read", "send"])
def test_atomic_failure_audit_rejection_rolls_back_state_and_request(tmp_path, kind):
    async def scenario():
        async with _runtime(tmp_path / "rollback.db") as (h, store, guard):
            h.transport.error = RuntimeError(ERROR_MARKER)
            await store._run_on_writer_async(
                lambda c: c.execute(
                    "CREATE TEMP TRIGGER b68_reject_failure BEFORE INSERT ON audit_events "
                    "WHEN NEW.event_type IN ('REQUEST_WITHHELD','REQUEST_UNKNOWN_OUTCOME') "
                    "BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
                ).close(),
                write=True,
            )
            result = await guard.submit("task", _call(kind), expected_epoch=1)
            assert isinstance(result, Withheld if kind == "read" else OutcomeUnknown)
            assert guard._global_halt
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert await _rows(store, "SELECT status FROM requests") == [
                (RequestStatus.DISPATCHING.value,)
            ]
            assert (
                await _rows(
                    store,
                    "SELECT event_type FROM audit_events WHERE event_type IN "
                    "('REQUEST_WITHHELD','REQUEST_UNKNOWN_OUTCOME','FAILURE_AFTER_DISPATCH')",
                )
                == []
            )
            assert len(h.transport.calls) == 1
            assert isinstance(await guard.submit("task", _call(kind), expected_epoch=1), Denied)
            assert len(h.transport.calls) == 1

    asyncio.run(scenario())


def test_failure_raw_markers_absent_from_db_wal_and_shm_with_positive_resource(tmp_path):
    async def scenario():
        path = tmp_path / "raw.db"
        async with _runtime(path, resolver=_path_resolver) as (h, store, guard):
            h.transport.raw = RawResult.model_validate(
                {
                    "payload": {
                        "content": [{"type": "text", "text": "public"}],
                        "structuredContent": {"resource_id": VISIBLE_RESOURCE},
                    }
                }
            )
            assert isinstance(
                await guard.submit("task", _path_call("public/read"), expected_epoch=1), Released
            )
            assert isinstance(
                await guard.submit("task", _path_call(PATH_MARKER), expected_epoch=1), Denied
            )
            h.access.error = RuntimeError(ERROR_MARKER)
            assert isinstance(
                await guard.submit("task", _path_call("public/read"), expected_epoch=1), Denied
            )
            files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
            assert all(file.exists() for file in files)
            contents = [file.read_bytes() for file in files]
            assert any(VISIBLE_RESOURCE.encode() in data for data in contents)
            for data in contents:
                assert all(
                    marker.encode() not in data
                    for marker in (PATH_MARKER, ARG_MARKER, ERROR_MARKER)
                )
            assert len(await _failure_rows(store)) == 2

    asyncio.run(scenario())


def test_missing_utc_failure_event_is_rejected_without_audit(tmp_path):
    async def scenario():
        async with _runtime(tmp_path / "missing-utc.db") as (_, store, _):
            before = await _failure_rows(store)
            event = FailureEvent(
                "task", None, "before_dispatch", Reason.INTERNAL_ERROR, "RuntimeError"
            )
            with pytest.raises(StoreError) as error:
                await durable_failure_audit(store)(event)
            assert error.value.reason is Reason.INVALID_ARGUMENTS
            assert await _failure_rows(store) == before

    asyncio.run(scenario())


def test_before_dispatch_failure_audit_write_error_halts_guard(tmp_path):
    async def scenario():
        async with _runtime(tmp_path / "audit-error.db") as (h, store, guard):
            await store._run_on_writer_async(
                lambda c: c.execute(
                    "CREATE TEMP TRIGGER b68_reject_before BEFORE INSERT ON audit_events "
                    "WHEN NEW.event_type='FAILURE_BEFORE_DISPATCH' "
                    "BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
                ).close(),
                write=True,
            )
            h.access.error = RuntimeError(ERROR_MARKER)
            result = await guard.submit("task", _call(), expected_epoch=1)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert guard._global_halt
            assert await _failure_rows(store) == []
            assert await _rows(store, "SELECT request_id FROM requests") == []
            h.access.error = None
            assert isinstance(await guard.submit("task", _call(), expected_epoch=1), Denied)
            assert h.transport.calls == []
            assert ERROR_MARKER not in repr(result)

    asyncio.run(scenario())


def _snapshot(db):
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: [list(row) for row in connection.execute(sql)] for table, sql in TABLES.items()
        }


def _kill_child(argv):
    path, point, output = argv
    connect = sqlite3.connect
    state = {"armed": False, "index": 0, "statements": []}
    phase, _, number = point.partition(":")
    target = int(number) if number else 0

    class KillConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if not state["armed"]:
                return super().execute(sql, parameters)
            state["index"] += 1
            if phase == "before" and state["index"] == target:
                os._exit(KILLED)
            result = super().execute(sql, parameters)
            state["statements"].append(" ".join(str(sql).split())[:70])
            if phase == "after" and state["index"] == target:
                os._exit(KILLED)
            return result

    sqlite3.connect = lambda *args, **kwargs: connect(*args, **kwargs, factory=KillConnection)
    ids = count(1)
    store = SQLiteStore(
        path, audit_id_factory=lambda: f"kill-event-{next(ids)}", utc_now=lambda: NOW
    )
    from guard_app.domain.models import TaskState

    asyncio.run(
        store.create_task(
            TaskState(task_id="task", epoch=1, profile_id="p", policy_version="v"),
            created_at_utc=NOW,
        )
    )
    before = _snapshot(path)
    state["armed"] = True
    asyncio.run(
        store.record_failure_audit(
            FailureAuditRecord(
                task_id="task",
                request_id=None,
                phase="before_dispatch",
                reason=Reason.INVALID_ARGUMENTS,
                exception_type="ResolutionError",
                at_utc=NOW,
            )
        )
    )
    state["armed"] = False
    after = _snapshot(path)
    store.close()
    asyncio.run(store.wait_closed(5))
    Path(output).write_text(
        json.dumps({"sql": state["statements"], "before": before, "after": after}), encoding="utf-8"
    )


def _spawn(tmp_path, name, point):
    db = tmp_path / f"{name}.db"
    output = tmp_path / f"{name}.json"
    run = subprocess.run(  # noqa: S603 -- fixed executable and fixed test module
        [
            sys.executable,
            "-I",
            "-c",
            BOOT,
            str(ROOT / "src"),
            str(ROOT),
            str(db),
            point,
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return run, db, output


def test_failure_audit_commit_boundaries_survive_process_kill_and_explicit_restart(tmp_path):
    reference, _, output = _spawn(tmp_path, "reference", "none")
    assert reference.returncode == 0, reference.stderr.decode(errors="replace")
    baseline = json.loads(output.read_text(encoding="utf-8"))
    sql, before, after = baseline["sql"], baseline["before"], baseline["after"]
    insert = next(
        i
        for i, statement in enumerate(sql, 1)
        if statement.upper().startswith("INSERT INTO AUDIT_EVENTS")
    )
    commit = next(i for i, statement in enumerate(sql, 1) if statement.upper().startswith("COMMIT"))
    assert before != after
    assert insert < commit
    for phase, index, expected in (
        ("before", insert, before),
        ("after", insert, before),
        ("before", commit, before),
        ("after", commit, after),
    ):
        child, db, _ = _spawn(tmp_path, f"{phase}-{index}", f"{phase}:{index}")
        assert child.returncode == KILLED, child.stderr.decode(errors="replace")
        assert _snapshot(db) == expected

        async def recover(db=db, phase=phase, index=index, expected=expected):
            store = await _open(db, tmp_path / f"restart-{phase}-{index}")
            try:
                report = await store.recover(deadline=time.monotonic() + 20, run_id=RUN)
                assert report.unresolved_count == 0
                rows = await _failure_rows(store)
                assert len(rows) == int(expected is after)
                if rows:
                    assert rows[0][0:4] == (
                        None,
                        "task",
                        "FAILURE_BEFORE_DISPATCH",
                        "INVALID_ARGUMENTS",
                    )
                assert await _rows(store, "SELECT request_id FROM requests") == []
            finally:
                await _close(store)

        asyncio.run(recover())
