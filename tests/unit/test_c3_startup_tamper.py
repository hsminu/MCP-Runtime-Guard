"""C3.9·C3.10: 앱을 닫은 뒤 DB를 직접 고치면 다음 시작이 멈추는지, 멈추지 않으면 실행 권한이 없는지.

2026-10-15 결정: DB 변조는 위협 모델 밖이지만 손상·불일치는 fail-closed로 다루고, 관계가 깨진
행·모르는 상태값은 시작 점검에서 멈춘다. 등급·미분류는 감사 기록과 대조해 낮아졌거나 사라졌으면
복구하지 않는다(개발문서 11.5 REC-02).

하니스(`c_counting`)로 실제 stdio 서버를 띄워 상태를 만든다.
- T01: PUBLIC 읽기 + ALLOW 전송
- T02: INTERNAL 읽기 + 승인 대기
- 민감 자료 읽기
- 규칙 없는 경로 읽기로 미분류

그다음 Guard를 닫거나(정상 종료) 버리고(비정상 종료), Store를 닫는다. 외래 키·CHECK를 끈 별도 연결로
행을 고친 뒤 B6.8 명시 시작(이관 준비 → 복구 → 회차 활성화)을 다시 한다. 결과는 둘 중 하나여야 한다.

- **멈춤:** `StorePrepareError(STORE_INTEGRITY_FAILED)`로 시작하지 않는다.
- **복구:** 옛 승인은 실행되지 않는다(늦은 승인 거부, 전송 0). 새 Task만 정상이다(양성 대조).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from contextlib import closing

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.errors import StorePrepareError
from guard_app.security.ids import new_id
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore

from .c_counting import ZERO, count_dispatches, executable_pin, harness, read, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")

# {pending}: 승인 대기 SEND 요청 ID, {approval}: 그 승인 ID
TAMPER = {
    # C3.9: 관계가 깨진 행
    "request-task-missing": ["UPDATE requests SET task_id='c39-ghost' WHERE request_id={pending}"],
    "approval-request-missing": [
        "UPDATE approvals SET request_id='c39-ghost' WHERE approval_id={approval}"
    ],
    # C3.9: 모르는 상태값(CHECK를 끄고 쓴다)
    "request-status-unknown": ["UPDATE requests SET status='BOGUS' WHERE request_id={pending}"],
    "task-status-unknown": ["UPDATE tasks SET status='bogus' WHERE task_id='task-2'"],
    "approval-status-unknown": ["UPDATE approvals SET status='BOGUS' WHERE approval_id={approval}"],
    # C3.9: #96 B6.10 요청 결속 네 필드
    "request-epoch": ["UPDATE requests SET epoch=epoch+1 WHERE request_id={pending}"],
    "request-kind": ["UPDATE requests SET kind='read' WHERE request_id={pending}"],
    "request-revision": [
        "UPDATE requests SET row_revision=row_revision+1 WHERE request_id={pending}"
    ],
    "request-legacy": ["UPDATE requests SET legacy_source='v1' WHERE request_id={pending}"],
    # C3.10: 감사 기록과 맞지 않는 등급·미분류
    "grade-lowered": ["UPDATE tasks SET high_watermark=0 WHERE task_id='task-c39-secret'"],
    "grade-and-history-erased": [
        "DELETE FROM escalations WHERE task_id='task-c39-secret'",
        "UPDATE tasks SET high_watermark=0, last_read_label=0 WHERE task_id='task-c39-secret'",
    ],
    "unclassified-cleared": ["UPDATE tasks SET unclassified=0 WHERE task_id='task-c39-unknown'"],
    # 한계 확인: 감사 기록까지 함께 낮추면 대조할 근거가 남지 않는다(행 서명 없음, 결정 2026-10-15)
    "grade-audit-history-all-lowered": [
        "DELETE FROM escalations WHERE task_id='task-c39-secret'",
        "UPDATE tasks SET high_watermark=0, last_read_label=0 WHERE task_id='task-c39-secret'",
        "UPDATE audit_events SET label=0 WHERE task_id='task-c39-secret' AND label=2",
    ],
}

STOPPED = "STORE_INTEGRITY_FAILED"
RECOVERED = "recovered"
# 기대 결과. 요청 결속 세 필드는 시작 점검이 대조하지 않는다(발견 C-C3-01). 그래도 옛 승인은
# 실행되지 않고 전송 0이다. 감사까지 함께 낮춘 변조는 대조 근거가 없어 복구된다(알려진 한계).
EXPECTED = dict.fromkeys(TAMPER, STOPPED) | {
    "request-epoch": RECOVERED,
    "request-kind": RECOVERED,
    "request-revision": RECOVERED,
    "grade-audit-history-all-lowered": RECOVERED,
}


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


async def _state(run) -> dict[str, str]:
    for task_id in ("task-c39-secret", "task-c39-unknown"):
        await run.create_task(task_id)
    await run.guard.submit("task-1", read("public/c39-a.txt", "r1"), expected_epoch=1)
    sent = await run.guard.submit("task-1", send("s1", to="issue"), expected_epoch=1)
    await run.guard.submit("task-2", read("internal/c39-b.txt", "r2"), expected_epoch=1)
    pending = await run.guard.submit("task-2", send("s2", body="c39-pending"), expected_epoch=1)
    await run.guard.submit("task-c39-secret", read("private/c39-c.txt", "r3"), expected_epoch=1)
    await run.guard.submit("task-c39-unknown", read("elsewhere/c39-d.txt", "r4"), expected_epoch=1)
    assert type(sent) is Released
    assert type(pending) is Pending
    return {"pending": pending.request_id, "approval": pending.approval_id}


async def _reopen(run, statements: list[str], ids: dict[str, str]):
    """Store를 닫고 DB를 고친 뒤 명시 시작을 다시 한다. (결과, 복구 보고)를 돌려준다.

    Guard는 부른 쪽이 먼저 닫거나(정상 종료) 버린다(비정상 종료).
    """
    run.store.close()
    assert await run.store.wait_closed(5.0)
    with closing(sqlite3.connect(run.db)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        for statement in statements:
            # 고칠 행의 ID만 바인딩한다
            keys = [key for key in ("pending", "approval") if "{" + key + "}" in statement]
            sql = statement.replace("{pending}", "?").replace("{approval}", "?")
            assert connection.execute(sql, tuple(ids[key] for key in keys)).rowcount >= 1
        connection.commit()
    store = SQLiteStore(run.db, startup="explicit")
    run.reopened.append(store)
    try:
        await store.prepare_schema(
            deadline=time.monotonic() + 30, begin_backup=run.folders.begin_backup
        )
        report = await store.recover(deadline=time.monotonic() + 30, run_id=new_id("run"))
    except StorePrepareError as error:
        return error.code.value, None
    await store.activate_run()
    run.store = store
    run.registry = ServerRegistry(store)
    await run.registry.load()
    await run.assemble(create_tasks=False)
    return RECOVERED, report


@pytest.mark.parametrize("crash", [False, True], ids=["clean-close", "crash"])
@pytest.mark.parametrize("case", list(TAMPER))
def test_tampered_database_stops_the_start_or_grants_nothing(
    tmp_path, case, crash, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, "stdio", pin, dispatched) as run:
            ids = await _state(run)
            abandoned = None
            if crash:
                # 비정상 종료: Guard를 닫지 않아 승인이 대기 상태로 남는다(T11과 같은 방식)
                abandoned, run.assembled = run.assembled, None
            else:
                await run.close_guard()
            try:
                outcome, report = await _reopen(run, TAMPER[case], ids)
                late = fresh = None
                if outcome == RECOVERED:
                    late = await run.human().approve(ids["approval"])
                    await run.create_task("task-c39-new")
                    await run.guard.submit(
                        "task-c39-new", read("public/c39-n.txt", "n1"), expected_epoch=1
                    )
                    fresh = await run.guard.submit(
                        "task-c39-new", send("n2", to="issue", body="c39-fresh"), expected_epoch=1
                    )
                return (
                    outcome,
                    report,
                    late,
                    fresh,
                    run.request_counts(ids["pending"], "c39-pending"),
                    run.request_counts(None, "c39-fresh"),
                )
            finally:
                if abandoned is not None:
                    # 버린 Guard의 연결(stdio 프로세스)을 정리한다. Store는 이미 닫혔다.
                    await abandoned.transport.close()

    outcome, report, late, fresh, counts, fresh_counts = asyncio.run(scenario())
    assert outcome == EXPECTED[case]
    # 옛 승인 대기 SEND는 어느 경우에도 나가지 않는다
    assert counts == ZERO
    if outcome == RECOVERED:
        # 옛 승인은 실행되지 않고(정상 종료는 닫을 때 취소, 비정상 종료는 복구가 만료),
        # 새 회차의 새 Task는 정상으로 나간다(양성 대조)
        assert type(late) is Denied
        assert report.changed_approval_count == int(crash)
        assert (report.unresolved_count, report.quarantined_count) == (0, 0)
        assert type(fresh) is Released
        assert fresh_counts["wire"] == 1
