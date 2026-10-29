"""C3.5·C3.6 자식 프로세스: Store 연산 하나를 돌리다 지정한 SQL 경계에서 프로세스를 끝낸다.

`test_c3_store_kill`이 `python -I`로 띄운다. B 시험 도우미(`test_sqlite_decisions`·
`test_sqlite_lifecycle`)로 연산 직전 상태를 만들고, 그 연산의 트랜잭션 안에서 실행되는 SQL을 센다.

인자: DB 경로, 연산, 끝낼 지점, 결과 JSON 경로
- 지점 `none`: 끝내지 않고 연산 안에서 실행된 SQL 순서와 연산 전후 상태를 JSON으로 남긴다
  (기준 실행).
- 지점 `before:N`·`after:N`: 연산 안의 N번째 SQL 직전·직후에 `os._exit(KILLED)`. 롤백 코드가
  돌 틈이 없다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from itertools import count

KILLED = 86
TABLES = {
    "tasks": "SELECT * FROM tasks ORDER BY 1",
    "escalations": "SELECT * FROM escalations ORDER BY 1,2",
    "requests": "SELECT * FROM requests ORDER BY 1",
    "approvals": "SELECT * FROM approvals ORDER BY 1",
    "audit_events": "SELECT * FROM audit_events ORDER BY 1",
    "task_call_sequences": "SELECT * FROM task_call_sequences ORDER BY 1",
}


def snapshot(path: str, connect) -> dict[str, list[list[object]]]:
    with connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {
            table: [list(row) for row in connection.execute(sql).fetchall()]
            for table, sql in TABLES.items()
        }


def main(argv: list[str]) -> None:
    path, operation, point, out = argv
    connect = sqlite3.connect
    state = {"armed": False, "index": 0, "executed": []}
    phase, _, number = point.partition(":")
    target = int(number) if number else 0

    class Kill(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if state["armed"]:
                state["index"] += 1
                if phase == "before" and state["index"] == target:
                    os._exit(KILLED)
                result = super().execute(sql, parameters)
                state["executed"].append(" ".join(str(sql).split())[:40])
                if phase == "after" and state["index"] == target:
                    os._exit(KILLED)
                return result
            return super().execute(sql, parameters)

        def executemany(self, sql, parameters, /):
            if state["armed"]:
                state["index"] += 1
                if phase == "before" and state["index"] == target:
                    os._exit(KILLED)
                result = super().executemany(sql, parameters)
                state["executed"].append(" ".join(str(sql).split())[:40])
                if phase == "after" and state["index"] == target:
                    os._exit(KILLED)
                return result
            return super().executemany(sql, parameters)

    sqlite3.connect = lambda *args, **kwargs: connect(*args, **kwargs, factory=Kill)

    from guard_app.domain.enums import ApprovalStatus, Decision, Label, TaskStatus
    from guard_app.domain.reasons import Reason
    from guard_app.storage import SQLiteStore
    from tests.unit.test_sqlite_decisions import LATER, NOW, decision, record, request, task
    from tests.unit.test_sqlite_lifecycle import approval

    ids = count(1)
    store = SQLiteStore(path, audit_id_factory=lambda: f"event-{next(ids)}", utc_now=lambda: LATER)
    asyncio.run(store.create_task(task(), created_at_utc=NOW))
    ask = decision(decision=Decision.ASK, reasons=(Reason.INTERNAL_TO_EXTERNAL,))

    def run_operation() -> None:
        if operation == "decide":
            record(store, result=ask)
        elif operation == "open":
            asyncio.run(store.open_approval(approval(), expected_task=task()))
        elif operation == "finish":
            asyncio.run(
                store.finish_approval(
                    "approval-1",
                    ApprovalStatus.REJECTED,
                    expected_approval_revision=0,
                    task_after=TaskStatus.RUNNING,
                    expected_task_revision=12,
                    reason=Reason.OK,
                    now_utc=LATER,
                )
            )
        elif operation == "consume":
            reserved = asyncio.run(
                store.consume_and_reserve(
                    approval_id="approval-1",
                    snapshot_hash="b" * 64,
                    request_id="request-1",
                    task_id="task-1",
                    expected_security_version=7,
                    now_utc=LATER,
                )
            )
            assert reserved
        elif operation == "observe":
            asyncio.run(store.commit_observation(request(), Label.INTERNAL, at_utc=LATER))
        else:
            raise SystemExit(f"unknown operation {operation}")

    # 연산 직전 상태를 만든다
    if operation in ("open", "finish", "consume"):
        record(store, result=ask)
    if operation in ("finish", "consume"):
        asyncio.run(store.open_approval(approval(), expected_task=task()))
    if operation == "consume":
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
    if operation == "observe":
        record(store)
        asyncio.run(store.reserve_dispatch("request-1", now_utc=LATER))

    before = snapshot(path, connect)
    state["armed"] = True
    run_operation()
    state["armed"] = False
    after = snapshot(path, connect)
    store.close()
    asyncio.run(store.wait_closed(5))
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"executed": state["executed"], "before": before, "after": after}, handle)


if __name__ == "__main__":
    main(sys.argv[1:])
