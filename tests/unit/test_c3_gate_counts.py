"""C3.3·C3.4·C3.7: 응답 게이트와 저장의 순서를 실제 서버 계수와 디스크로 확인한다.

하니스는 `c_counting`이다(제품 조립 Guard, C Registry, 실제 stdio·HTTP 서버, B SQLiteStore).

- C3.3 READ 관측 원자성과 커밋 전 공개 0(I4, 개발문서 11.3):
  - 관측 커밋이 끝나기 전에는 `submit`이 결과를 돌려주지 않는다.
  - 커밋이 실패하면 결과를 공개하지 않고 Task를 멈춘다. 등급·감사·요청 상태 중 어느 것도
    반쯤 남지 않는다.
  - 서버는 실제로 받았다(wire 1). 막은 것은 공개다.
- C3.4 SEND 성공에서 Task 보안 상태 불변(I5): ALLOW 전송과 승인한 전송 모두 등급·미분류·보안 버전·
  승격 이력이 그대로다.
- C3.7 원문 비기록(11.4):
  - 인자 원문 표지가 DB·WAL·앱 폴더 어느 파일에도 없다.
  - 대상은 ALLOW·승인 대기·승인·거절·응답 유실 전송이다.
  - 반대로 자원 ID(허용된 메타데이터)는 같은 검사에서 보인다(양성 대조).
  - 재시작 뒤 승인 미복원·SEND 재전송 0은 T11(`test_guard_path_counts`)과
    C5.6(`test_write_loss_counts`)이 본다.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import closing

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason

from .c_counting import KINDS, count_dispatches, executable_pin, harness, read, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


def _rows(run, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with closing(run._read_db()) as connection:
        return [tuple(row) for row in connection.execute(sql, params).fetchall()]


def _labels(state) -> tuple[object, ...]:
    """등급·미분류·승격 이력. 보안 버전은 종결 전이(HALTED)에서도 오르므로 따로 본다."""
    return (state.high_watermark, state.last_read_label, state.unclassified, state.escalations)


def _security(state) -> tuple[object, ...]:
    return (*_labels(state), state.security_version)


# C3.3


@pytest.mark.parametrize("kind", KINDS)
def test_read_result_is_not_released_before_its_commit(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            original = run.store.commit_observation
            entered, release = asyncio.Event(), asyncio.Event()

            async def held(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)

            run.store.commit_observation = held
            task = asyncio.create_task(
                run.guard.submit("task-1", read("internal/c33-held.txt", "r1"), expected_epoch=1)
            )
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.sleep(0.2)
            done_before = task.done()
            wire_before = run.counts("read")["wire"]
            observed_before = _rows(
                run, "SELECT count(*) FROM audit_events WHERE event_type='OBSERVED'"
            )[0][0]
            release.set()
            outcome = await task
            state = await run.store.load_task("task-1")
            return done_before, wire_before, observed_before, outcome, state

    done_before, wire_before, observed_before, outcome, state = asyncio.run(scenario())
    # 서버는 이미 받았지만, 커밋 전에는 결과가 나가지 않았다
    assert (done_before, wire_before, observed_before) == (False, 1, 0)
    assert type(outcome) is Released
    assert state.high_watermark.name == "INTERNAL"


@pytest.mark.parametrize("kind", KINDS)
def test_read_commit_failure_releases_nothing_and_leaves_no_half_state(
    tmp_path, kind, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            before = await run.store.load_task("task-1")

            async def fail(*args, **kwargs):
                raise StoreError(Reason.STATE_COMMIT_FAILED)

            run.store.commit_observation = fail
            outcome = await run.guard.submit(
                "task-1", read("private/c33-fail.txt", "r1"), expected_epoch=1
            )
            after = await run.store.load_task("task-1")
            request = _rows(
                run,
                "SELECT request_id, status FROM requests WHERE task_id='task-1' "
                "ORDER BY call_seq DESC LIMIT 1",
            )[0]
            observed = _rows(
                run,
                "SELECT count(*) FROM audit_events WHERE event_type='OBSERVED' AND request_id=?",
                (request[0],),
            )[0][0]
            return before, outcome, after, request[1], observed, run.counts("read")

    before, outcome, after, request_status, observed, counts = asyncio.run(scenario())
    # 결과는 공개되지 않고(고정 사유) 요청은 결과 미공개로 끝난다
    assert type(outcome) is Withheld
    assert outcome.reasons == (Reason.STATE_COMMIT_FAILED,)
    assert request_status == "WITHHELD"
    assert counts["wire"] == 1  # 서버는 받았다
    assert observed == 0  # 관측 감사가 반쯤 남지 않았다
    assert _labels(after) == _labels(before)  # 민감 자료 등급이 반쯤 오르지 않았다
    assert after.status is TaskStatus.HALTED  # 종결 전이라 보안 버전만 오른다
    assert after.security_version == before.security_version + 1


# C3.4


@pytest.mark.parametrize("kind", KINDS)
def test_successful_send_leaves_task_security_state_unchanged(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("public/c34-a.txt", "r1"), expected_epoch=1)
            allow_before = await run.store.load_task("task-1")
            allowed = await run.guard.submit(
                "task-1", send("s1", to="issue", body="c34-allow"), expected_epoch=1
            )
            allow_after = await run.store.load_task("task-1")
            await run.guard.submit("task-2", read("internal/c34-b.txt", "r2"), expected_epoch=1)
            ask_before = await run.store.load_task("task-2")
            pending = await run.guard.submit("task-2", send("s2", body="c34-ask"), expected_epoch=1)
            approved = await run.human().approve(pending.approval_id)
            ask_after = await run.store.load_task("task-2")
            return allow_before, allowed, allow_after, ask_before, pending, approved, ask_after

    allow_before, allowed, allow_after, ask_before, pending, approved, ask_after = asyncio.run(
        scenario()
    )
    assert (type(allowed), type(pending), type(approved)) == (Released, Pending, Released)
    # ALLOW 전송은 Task 행 자체를 바꾸지 않는다
    assert allow_after == allow_before
    # 승인 전송은 대기·복귀로 상태·revision만 움직이고 보안 상태는 그대로다
    assert _security(ask_after) == _security(ask_before)
    assert ask_after.status is TaskStatus.RUNNING


# C3.7


@pytest.mark.parametrize("kind", KINDS)
def test_raw_arguments_leave_no_trace_on_disk(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=1.5) as run:
            for task_id in ("task-c37-c", "task-c37-d", "task-c37-e"):
                await run.create_task(task_id)
            g = run.guard
            await g.submit("task-1", read("public/c37-visible-path.txt", "r1"), expected_epoch=1)
            allowed = await g.submit(
                "task-1", send("s1", to="issue", body="C37RAW-allow"), expected_epoch=1
            )
            # 등급을 올리는 읽기의 자원 ID는 승격 이력(source_id)에 남는다(허용된 메타데이터)
            await g.submit(
                "task-2", read("internal/c37-visible-source.txt", "r2"), expected_epoch=1
            )
            asked = await g.submit("task-2", send("s2", body="C37RAW-asked"), expected_epoch=1)
            approved = await run.human().approve(asked.approval_id)
            await g.submit("task-c37-c", read("internal/c37-c.txt", "r3"), expected_epoch=1)
            waiting = await g.submit(
                "task-c37-c", send("s3", body="C37RAW-waiting"), expected_epoch=1
            )
            await g.submit("task-c37-d", read("private/c37-d.txt", "r4"), expected_epoch=1)
            denied = await g.submit(
                "task-c37-d", send("s4", to="issue", body="C37RAW-denied"), expected_epoch=1
            )
            lost = await g.submit(
                "task-c37-e", send("s5", to="issue", body="hang-C37RAW-lost"), expected_epoch=1
            )
            assert (type(allowed), type(approved), type(waiting), type(denied), type(lost)) == (
                Released,
                Released,
                Pending,
                Denied,
                OutcomeUnknown,
            )
            # 승인 대기 사본이 메모리에 있는 동안의 디스크를 본다(Guard를 닫기 전)
            return run.folders.paths.root

    root = asyncio.run(scenario())
    files = [path for path in root.rglob("*") if path.is_file()]
    names = {path.name for path in files}
    assert any(name.endswith(".sqlite3") for name in names)
    found_raw, found_resource = [], []
    for path in files:
        try:
            data = path.read_bytes()
        except PermissionError:
            continue  # 잠긴 파일(app.lock 등)은 내용이 없다
        if b"C37RAW" in data:
            found_raw.append(path.name)
        if b"c37-visible-source" in data:
            found_resource.append(path.name)
    assert found_raw == []
    # 양성 대조: 자원 ID는 저장되고 같은 검사로 보인다(DB 또는 WAL)
    assert found_resource
