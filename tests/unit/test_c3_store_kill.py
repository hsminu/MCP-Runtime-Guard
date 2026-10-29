"""C3.5·C3.6: Store 연산 도중 프로세스가 끊겨도 반쪽 상태가 남지 않고, 다음 시작이 닫힌 쪽으로
복구한다.

B 단위 시험(`test_atomic_approval_store` 등)은 같은 경계에서 SQL 오류를 일으켜 B 코드가 롤백하는
것을 본다. 여기서는 자식 프로세스(`c_store_kill`)를 그 경계에서 `os._exit`로 끝낸다. 롤백 코드가
돌 틈이 없으므로 SQLite 저널(WAL)과 다음 시작의 복구만으로 원자성이 지켜지는지를 본다(B6-01
"부분 상태 0을 Store 연산 단위로 센다", C 결함 주입 인수).

- C3.5: 승인 생성(`open_approval`)·종결(`finish_approval`)·소비(`consume_and_reserve`)
- C3.6: 판정 기록(`record_decision`)·READ 관측 커밋(`commit_observation`)
- C3.6 잠금 대기: 다른 연결이 쓰기 잠금을 쥐고 있을 때 Store 쓰기가 기다리는 시간을 잰다
  (B6-03 "잠금 대기 1000ms는 C 측정 뒤 확정"). WAL 읽기는 막히지 않는다.

각 연산마다 먼저 기준 실행으로 연산 안에서 실행되는 SQL 순서와 연산 전·후 상태를 얻는다. 그다음
쓰기·트랜잭션 문장 하나하나의 직전·직후에서 끊고, DB가 "전" 또는 "후"와 정확히 같은지 본다.
COMMIT이 실행되기 전에 끊기면 "전", 실행된 뒤면 "후"여야 한다. 마지막으로 B6.8 명시 시작으로
복구해, 옛 승인이 살아 있지 않고 Task가 실행 상태로 남지 않는지 본다(개발문서 11.5 REC-02).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.enums import ApprovalStatus, RequestStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.storage import SQLiteStore
from tests.unit.c_store_kill import KILLED, TABLES
from tests.unit.test_store_recovery import RUN, _close
from tests.unit.test_store_recovery import _open as _prepare

ROOT = Path(__file__).resolve().parents[2]
BOOT = (
    "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
    "from tests.unit.c_store_kill import main; main(sys.argv[3:])"
)
OPERATIONS = ("decide", "open", "finish", "consume", "observe")
_WRITES = ("BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE", "COMMIT")
_LIVE_APPROVALS = {ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value}
_RUNNING_TASKS = {
    TaskStatus.READY.value,
    TaskStatus.RUNNING.value,
    TaskStatus.WAITING_APPROVAL.value,
}


def _child(tmp_path: Path, name: str, operation: str, point: str) -> tuple[int, Path, Path]:
    db = tmp_path / f"{name}.db"
    out = tmp_path / f"{name}.json"
    completed = subprocess.run(  # noqa: S603  고정 인자로 이 Python만 띄운다
        [
            sys.executable,
            "-I",
            "-c",
            BOOT,
            str(ROOT / "src"),
            str(ROOT),
            str(db),
            operation,
            point,
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return completed.returncode, db, out


def _snapshot(db: Path) -> dict[str, list[list[object]]]:
    # 일반 연결로 연다. 끊긴 프로세스의 WAL을 다음 시작처럼 복구해 읽는다.
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: [list(row) for row in connection.execute(sql).fetchall()]
            for table, sql in TABLES.items()
        }


async def _recover(db: Path, folder: Path):
    store = await _prepare(db, folder)
    try:
        report = await store.recover(deadline=time.monotonic() + 15, run_id=RUN)
    finally:
        await _close(store)
    return report


@pytest.mark.skipif(sys.platform != "win32", reason="제품과 같은 Windows 파일 잠금에서 본다")
@pytest.mark.parametrize("operation", OPERATIONS)
def test_killed_operation_leaves_before_or_after_and_recovers_closed(tmp_path, operation):
    code, _, reference = _child(tmp_path, "reference", operation, "none")
    assert code == 0
    baseline = json.loads(reference.read_text(encoding="utf-8"))
    executed, before, after = baseline["executed"], baseline["before"], baseline["after"]
    assert before != after
    commit = next(i for i, sql in enumerate(executed, 1) if sql.upper().startswith("COMMIT"))
    points = [
        (phase, index)
        for index, sql in enumerate(executed, 1)
        if sql.upper().startswith(_WRITES)
        for phase in ("before", "after")
    ]
    assert points
    results = {}
    for phase, index in points:
        name = f"{phase}-{index}"
        code, db, _ = _child(tmp_path, name, operation, f"{phase}:{index}")
        assert code == KILLED, name
        state = _snapshot(db)
        committed = index > commit or (index == commit and phase == "after")
        results[name] = "after" if state == after else "before" if state == before else "partial"
        assert state == (after if committed else before), name
        report = asyncio.run(_recover(db, tmp_path / name))
        assert (report.unresolved_count, report.quarantined_count) == (0, 0), name
        recovered = _snapshot(db)
        approvals = {row[4] for row in recovered["approvals"]}
        tasks = {row[7] for row in recovered["tasks"]}
        assert not approvals & _LIVE_APPROVALS, name  # 옛 승인은 살아 있지 않다
        assert not tasks & _RUNNING_TASKS, name  # 옛 Task는 실행 상태로 남지 않는다
        if committed and operation == "consume":
            # 예약된 READ는 결과 미공개(REC-02)
            assert recovered["requests"][0][9] == RequestStatus.WITHHELD.value
    print(f"\n{operation}: {len(points)} kills -> {sorted(set(results.values()))}")


# C3.6 잠금 대기


def _task(task_id: str) -> TaskState:
    return TaskState(task_id=task_id, epoch=1, profile_id="p", policy_version="v")


@pytest.mark.skipif(sys.platform != "win32", reason="제품과 같은 Windows 파일 잠금에서 본다")
def test_write_waits_for_a_held_lock_about_the_busy_timeout_and_reads_do_not(tmp_path):
    db = tmp_path / "lock.db"
    ids = count(1)
    waited, reads = [], []
    with SQLiteStore(db, audit_id_factory=lambda: f"event-{next(ids)}") as store:
        asyncio.run(store.create_task(_task("task-0"), created_at_utc=datetime.now(UTC)))
        for attempt in range(5):
            with closing(sqlite3.connect(db, timeout=0)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                start = time.monotonic()
                asyncio.run(store.load_task("task-0"))  # WAL 읽기는 기다리지 않는다
                reads.append(time.monotonic() - start)
                start = time.monotonic()
                with pytest.raises(StoreError):
                    asyncio.run(
                        store.create_task(
                            _task(f"task-{attempt + 1}"), created_at_utc=datetime.now(UTC)
                        )
                    )
                waited.append(time.monotonic() - start)
                blocker.rollback()
            # 잠금이 풀리면 다시 쓴다(잠금 대기 실패가 Store를 닫지 않음)
            asyncio.run(
                store.create_task(_task(f"task-ok-{attempt}"), created_at_utc=datetime.now(UTC))
            )
    print(
        f"\nlock wait s: median {statistics.median(waited):.3f} "
        f"min {min(waited):.3f} max {max(waited):.3f}; read s max {max(reads):.3f}"
    )
    assert min(waited) >= 0.9
    assert max(waited) <= 3.0
    assert max(reads) < 0.5
