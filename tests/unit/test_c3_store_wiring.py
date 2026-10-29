"""C3.1·C3.2: 실제 Store 연결을 감싸 단일 writer·명시적 트랜잭션·바인딩 SQL·스키마 제약을 확인한다.

B 단위 시험은 저장소 함수를 하나씩 본다. 여기서는 제품 조립 Guard와 실제 stdio·HTTP 서버로
T01·T02·T03·응답 유실을 돌리는 동안 Store가 실제로 실행한 SQL을 모두 기록해 독립으로 본다
(개발문서 11.1·11.2, B6-02 "바인딩 SQL·관계·epoch·revision 독립 검증").

- C3.1: 연결은 `isolation_level=None`, 연결 직후 `foreign_keys=ON`·`synchronous=FULL`,
  DB 파일은 WAL이다. 쓰기 SQL은 Store마다 writer 스레드 하나(이벤트 루프 스레드가 아님)에서만,
  명시적 트랜잭션 안에서만 실행된다.
- C3.2: Task·요청·승인 ID와 인자 표지는 SQL 문 텍스트에 한 번도 나오지 않고 바인딩 값으로만
  들어간다. 시나리오 뒤 DB는 `integrity_check`·`foreign_key_check`를 통과하고,
  외래 키·UNIQUE·CHECK 제약이 DB 수준에서 직접 쓰기를 막는다.
  낡은 revision·다른 epoch는 쓰기·실행 없이 거부된다.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason

from .c_counting import KINDS, ZERO, count_dispatches, executable_pin, harness, read, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")

_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")
_INSERT_REQUEST = (
    "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,arguments_digest,"
    "status,epoch,kind) VALUES (?,?,?,?,?,?,?,?,?)"
)


@dataclass(frozen=True, slots=True)
class _Executed:
    connection: int
    thread: int
    sql: str
    params: tuple[object, ...]
    in_transaction: bool


@dataclass(slots=True)
class _Recorder:
    executed: list[_Executed]
    opened: dict[int, dict[str, object]]
    lock: threading.Lock


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    """`sqlite3.connect`가 만드는 모든 연결의 `execute`·`executemany`를 기록한다."""
    seen = _Recorder([], {}, threading.Lock())
    connect = sqlite3.connect

    class Recording(sqlite3.Connection):
        def _note(self, sql: object, params: object) -> None:
            rows = params if isinstance(params, list) else [params]
            with seen.lock:
                for row in rows:
                    seen.executed.append(
                        _Executed(
                            id(self),
                            threading.get_ident(),
                            str(sql),
                            tuple(row) if isinstance(row, tuple | list) else (),
                            self.in_transaction,
                        )
                    )

        def execute(self, sql, parameters=(), /):
            self._note(sql, parameters)
            return super().execute(sql, parameters)

        def executemany(self, sql, parameters, /):
            parameters = list(parameters)
            self._note(sql, parameters)
            return super().executemany(sql, parameters)

    def recording_connect(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=Recording)
        with seen.lock:
            seen.opened[id(connection)] = {"args": args, "kwargs": dict(kwargs)}
        return connection

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    return seen


async def _scenario(run) -> dict[str, object]:
    """T01(ALLOW)·T02(ASK 승인)·T03(DENY)·응답 유실을 실제 서버로 돌린다."""
    for task_id in ("task-c32-c", "task-c32-d"):
        await run.create_task(task_id)
    t01 = await run.guard.submit("task-1", read("public/c32-a.txt", "r1"), expected_epoch=1)
    s01 = await run.guard.submit(
        "task-1", send("s1", to="issue", body="C32MARK-allow"), expected_epoch=1
    )
    await run.guard.submit("task-2", read("internal/c32-b.txt", "r2"), expected_epoch=1)
    pending = await run.guard.submit("task-2", send("s2", body="C32MARK-ask"), expected_epoch=1)
    assert type(pending) is Pending
    approved = await run.human().approve(pending.approval_id)
    await run.guard.submit("task-c32-c", read("private/c32-c.txt", "r3"), expected_epoch=1)
    denied = await run.guard.submit(
        "task-c32-c", send("s3", to="issue", body="C32MARK-deny"), expected_epoch=1
    )
    lost = await run.guard.submit(
        "task-c32-d", send("s4", to="issue", body="hang-C32MARK-lost"), expected_epoch=1
    )
    assert (type(t01), type(s01), type(approved), type(denied), type(lost)) == (
        Released,
        Released,
        Released,
        Denied,
        OutcomeUnknown,
    )
    return {"main_thread": threading.get_ident()}


def _identifiers(db) -> dict[str, set[str]]:
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        return {
            "task": {row[0] for row in connection.execute("SELECT task_id FROM tasks")},
            "request": {row[0] for row in connection.execute("SELECT request_id FROM requests")},
            "approval": {row[0] for row in connection.execute("SELECT approval_id FROM approvals")},
        }


@pytest.mark.parametrize("kind", KINDS)
def test_store_runs_bound_sql_from_one_writer_inside_explicit_transactions(
    tmp_path, kind, pin, dispatched, recorder
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=1.5) as run:
            info = await _scenario(run)
            return run.db, info

    db, info = asyncio.run(scenario())
    with recorder.lock:
        executed = list(recorder.executed)
        opened = dict(recorder.opened)
    # Store 연결: 읽기 전용 URI(`mode=ro`, 시험 쪽 읽기)가 아닌 연결
    store_connections = {
        key
        for key, meta in opened.items()
        if "mode=ro" not in str(meta["args"][0] if meta["args"] else "")
    }
    assert store_connections
    for key in store_connections:
        meta = opened[key]
        assert meta["kwargs"].get("isolation_level", "missing") is None
        assert meta["kwargs"].get("check_same_thread", True) is True
        statements = [item.sql.strip().upper() for item in executed if item.connection == key]
        # 연결 직후 설정이 다른 어떤 SQL보다 먼저 온다
        assert statements[:2] == ["PRAGMA FOREIGN_KEYS=ON", "PRAGMA SYNCHRONOUS=FULL"]

    writes = [
        item
        for item in executed
        if item.connection in store_connections
        and item.sql.lstrip().upper().startswith(_WRITE_VERBS)
    ]
    assert writes
    # 명시적 트랜잭션 밖의 쓰기 0
    assert [item.sql for item in writes if not item.in_transaction] == []
    # 연결마다 쓰기 스레드는 하나이고 이벤트 루프(주) 스레드가 아니다
    for key in store_connections:
        threads = {item.thread for item in writes if item.connection == key}
        assert len(threads) <= 1
        assert info["main_thread"] not in threads
    # 바인딩: ID·인자 표지는 SQL 문 텍스트에 나오지 않고 바인딩 값에는 나온다
    identifiers = _identifiers(db)
    texts = "\n".join(item.sql for item in executed)
    bound = {str(value) for item in executed for value in item.params}
    for group in ("task", "request", "approval"):
        assert identifiers[group]
        assert [value for value in identifiers[group] if value in texts] == []
        assert identifiers[group] <= bound
    assert "C32MARK" not in texts
    # 인자 원문은 바인딩 값으로도 저장소에 가지 않는다(원문 대신 digest)
    assert [value for value in bound if "C32MARK" in value] == []
    # DB 파일 자체가 WAL이다
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize("kind", ["stdio"])
def test_schema_constraints_hold_after_a_real_run(tmp_path, kind, pin, dispatched):
    """시나리오 뒤 DB가 일관되고, 직접 쓰기도 제약이 막는다(쓰기는 Store를 닫은 뒤)."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=1.5) as run:
            await _scenario(run)
            return run.db

    db = asyncio.run(scenario())
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        task_id, call_seq = connection.execute(
            "SELECT task_id, call_seq FROM requests LIMIT 1"
        ).fetchone()
        request_id = connection.execute("SELECT request_id FROM approvals LIMIT 1").fetchone()[0]
        digest = "0" * 64
        attempts = {
            "요청의 Task 없음(FK)": (
                _INSERT_REQUEST,
                ("c32-orphan", "no-such-task", 900, "docs", "send", digest, "DECIDED", 1, "send"),
            ),
            "같은 Task·순번 두 번(UNIQUE)": (
                _INSERT_REQUEST,
                ("c32-dup", task_id, call_seq, "docs", "send", digest, "DECIDED", 1, "send"),
            ),
            "요청 하나에 승인 둘(UNIQUE)": (
                "INSERT INTO approvals (approval_id,request_id,snapshot_hash,approver_id,status,"
                "created_at_utc,expires_at_utc,row_revision) VALUES (?,?,?,?,?,?,?,?)",
                ("c32-second", request_id, digest, "op", "PENDING", "t", "t", 0),
            ),
            "승인의 요청 없음(FK)": (
                "INSERT INTO approvals (approval_id,request_id,snapshot_hash,approver_id,status,"
                "created_at_utc,expires_at_utc,row_revision) VALUES (?,?,?,?,?,?,?,?)",
                ("c32-ghost", "no-such-request", digest, "op", "PENDING", "t", "t", 0),
            ),
            "모르는 요청 상태(CHECK)": (
                "UPDATE requests SET status=? WHERE request_id=(SELECT request_id FROM requests "
                "LIMIT 1)",
                ("BOGUS",),
            ),
            "모르는 요청 종류(CHECK)": (
                "UPDATE requests SET kind=? WHERE request_id=(SELECT request_id FROM requests "
                "LIMIT 1)",
                ("execute",),
            ),
            "등급 범위 밖(CHECK)": (
                "UPDATE tasks SET high_watermark=? WHERE task_id=?",
                (3, task_id),
            ),
            "등급을 낮추는 승격 이력(CHECK)": (
                "INSERT INTO escalations (task_id,at_seq,source_id,from_label,to_label) "
                "VALUES (?,?,?,?,?)",
                (task_id, 950, "x", 2, 1),
            ),
            "인자 원문 모양의 digest(CHECK)": (
                _INSERT_REQUEST,
                ("c32-raw", task_id, 960, "docs", "send", "C32MARK body", "DECIDED", 1, "send"),
            ),
        }
        refused = {}
        for name, (sql, params) in attempts.items():
            connection.execute("SAVEPOINT attempt")
            try:
                connection.execute(sql, params)
                refused[name] = False
            except sqlite3.IntegrityError:
                refused[name] = True
            connection.execute("ROLLBACK TO attempt")
            connection.execute("RELEASE attempt")
    assert refused == dict.fromkeys(attempts, True)


@pytest.mark.parametrize("kind", ["stdio"])
def test_stale_revision_and_other_epoch_change_nothing(tmp_path, kind, pin, dispatched):
    """revision CAS·epoch 결속(B6-02): 쓰기 없이 거부되고 아무것도 나가지 않는다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            before = await run.store.load_task("task-1")
            with pytest.raises(StoreError) as stale:
                await run.store.set_task_status(
                    "task-1",
                    TaskStatus.HALTED,
                    expected_row_revision=before.row_revision + 1,
                    at_utc=datetime.now(UTC),
                    reason=Reason.TASK_NOT_RUNNING,
                )
            after_stale = await run.store.load_task("task-1")
            other_epoch = await run.guard.submit(
                "task-1", send("e1", to="issue", body="C32MARK-epoch"), expected_epoch=2
            )
            after_epoch = await run.store.load_task("task-1")
            return (
                before,
                stale.value.reason,
                after_stale,
                other_epoch,
                after_epoch,
                run.request_counts(None, "C32MARK-epoch"),
            )

    before, stale_reason, after_stale, other_epoch, after_epoch, counts = asyncio.run(scenario())
    # Task 행 CAS 충돌의 고정 사유(set_task_status)
    assert stale_reason is Reason.STATE_COMMIT_FAILED
    assert after_stale == before
    assert type(other_epoch) is Denied
    assert after_epoch.status is TaskStatus.RUNNING
    assert (after_epoch.high_watermark, after_epoch.security_version) == (
        before.high_watermark,
        before.security_version,
    )
    assert counts == ZERO
