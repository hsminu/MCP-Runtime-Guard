"""#120·B6.19: 도구 목록 변경 알림 뒤 아직 wire로 나가지 않은 호출(C 계수, stdio·HTTP).

LIST-01은 알림을 받은 서버를 이번 실행에서 PENDING으로 보고 새 호출을 예약 전에 거부한다.
고치기 전에는 Guard의 마지막 확인(`catalog.aresolve`) 뒤, worker가 실제로 `tools/call`을 보내기
전에 알림이 오면 그 호출이 PENDING이 된 뒤에도 서버까지 갔다(#120 재현, 아래 세 경우 모두).

수정: worker가 호출을 `_execute`에 넘기기 직전에 `LiveCheckedRegistry.allows_dispatch`(서버
ACTIVE·지문 일치)를 보고, 아니면 한 바이트도 보내지 않고 `NOT_DISPATCHED`로 끝낸다. Guard는
이 종류만 결과 불명 대신 요청 FAILED·Task RUNNING·`Denied(REG_NOT_ACTIVE)`로 기록한다.

- 예약을 통과했다는 증거는 DB 요청 상태 FAILED다(예약 전 거부는 이 상태가 될 수 없다).
- `test_*_without_a_pause`는 시험용 정지 장치 없이 같은 순서를 만든다. 다른 Task의 READ가
  서버에서 처리되는 동안 SEND가 같은 서버 worker 대기열에 들어가 있으면 된다(하니스
  `notify-slow`). 고치기 전에는 stdio·HTTP 각각 10/10 재현됐다.
- 계수는 C5.4 하니스 그대로다: 승인 소비(DB), host_dispatch(`ServerWorker._execute` 진입),
  wire `tools/call`, 수신 효과.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import closing

import pytest

from guard_app.adapters.mcp_transport import McpTransport
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import RegStatus, RequestStatus, TaskStatus
from guard_app.domain.reasons import Reason

from .c_counting import KINDS, ZERO, count_dispatches, executable_pin, harness, read, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


async def _until_pending(run, wait_s: float = 5.0) -> None:
    async with asyncio.timeout(wait_s):
        # 알림 처리에는 기다릴 이벤트가 없다(Registry 상태만 바뀐다).
        while run.assembled.registry.status("docs") is not RegStatus.PENDING:  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def _rows(run, request_id: str, task_id: str) -> tuple[str, str]:
    with closing(run._read_db()) as db:
        request = db.execute("SELECT status FROM requests WHERE request_id=?", (request_id,))
        task = db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,))
        return request.fetchone()[0], task.fetchone()[0]


def _assert_not_sent(run, outcome, token: str, *, task_id: str, consumed: int = 0) -> None:
    """예약 뒤 보내지 않음 확정: 거부 결과, 요청 FAILED·Task RUNNING, 승인 외 계수 0."""
    assert type(outcome) is Denied
    assert outcome.reasons == (Reason.REG_NOT_ACTIVE,)
    assert _rows(run, outcome.request_id, task_id) == (
        RequestStatus.FAILED.value,
        TaskStatus.RUNNING.value,
    )
    assert run.request_counts(outcome.request_id, token) == ZERO | {"approval_consumed": consumed}


class _HeldSend:
    """`McpTransport.call`에서 SEND만 붙잡아 알림과 worker 전송의 순서를 고정한다(#120 PoC)."""

    def __init__(self, monkeypatch) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        original = McpTransport.call

        async def held(transport, request):
            if request.tool.tool_name == "send":
                self.waiting.set()
                await self.release.wait()
            return await original(transport, request)

        monkeypatch.setattr(McpTransport, "call", held)


@pytest.mark.parametrize("kind", KINDS)
def test_send_reserved_before_a_notice_is_not_sent_after_it(
    tmp_path, kind, pin, dispatched, monkeypatch
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            held = _HeldSend(monkeypatch)
            pending = asyncio.create_task(
                run.guard.submit(
                    "task-1", send("race", to="issue", body="race-held"), expected_epoch=1
                )
            )
            await asyncio.wait_for(held.waiting.wait(), 5)
            notified = await run.guard.submit(
                "task-2", read("public/notify", "notify"), expected_epoch=1
            )
            assert type(notified) is Released
            await _until_pending(run)
            held.release.set()
            sent = await asyncio.wait_for(pending, 10)
            _assert_not_sent(run, sent, "race-held", task_id="task-1")
            # 보내지 않음 확정 행이 남은 DB로 다시 시작해도 시작 점검(B6.8)이 통과한다.
            await run.close_guard()
            await run.restart()
            assert _rows(run, sent.request_id, "task-1")[0] == RequestStatus.FAILED.value

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_send_whose_approval_was_consumed_before_a_notice_is_not_sent_after_it(
    tmp_path, kind, pin, dispatched, monkeypatch
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            first = await run.guard.submit(
                "task-1", read("internal/approval.txt", "approval-read"), expected_epoch=1
            )
            assert type(first) is Released
            pending = await run.guard.submit(
                "task-1", send("approved", body="approved-held"), expected_epoch=1
            )
            assert type(pending) is Pending
            held = _HeldSend(monkeypatch)
            approving = asyncio.create_task(run.human().approve(pending.approval_id))
            await asyncio.wait_for(held.waiting.wait(), 5)
            await run.guard.submit("task-2", read("public/notify", "notify"), expected_epoch=1)
            await _until_pending(run)
            held.release.set()
            approved = await asyncio.wait_for(approving, 10)
            # 승인은 알림 전에 이미 소비됐다. 되돌리지 않고 전송만 하지 않는다.
            _assert_not_sent(run, approved, "approved-held", task_id="task-1", consumed=1)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_a_send_submitted_after_the_notice_is_refused_before_dispatch(
    tmp_path, kind, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-2", read("public/notify", "notify"), expected_epoch=1)
            await _until_pending(run)
            refused = await run.guard.submit(
                "task-1", send("late", to="issue", body="after-notice"), expected_epoch=1
            )
            assert type(refused) is Denied
            assert run.request_counts(refused.request_id, "after-notice") == ZERO

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
def test_send_queued_behind_a_slow_read_is_not_sent_after_its_notice_without_a_pause(
    tmp_path, kind, pin, dispatched
):
    """시험용 정지 장치 없이: Task 2의 READ가 서버에 있는 동안 Task 1의 SEND가 대기열에 선다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            reading = asyncio.create_task(
                run.guard.submit("task-2", read("public/notify-slow", "slow"), expected_epoch=1)
            )
            async with asyncio.timeout(5):
                # 서버가 READ를 받았는지는 계수 파일로만 안다.
                while not any(  # noqa: ASYNC110
                    t == "public/notify-slow" for _, t in run.counters.wire_entries()
                ):
                    await asyncio.sleep(0.01)
            sending = asyncio.create_task(
                run.guard.submit(
                    "task-1", send("natural", to="issue", body="race-natural"), expected_epoch=1
                )
            )
            assert type(await asyncio.wait_for(reading, 10)) is Released
            sent = await asyncio.wait_for(sending, 10)
            # 요청 FAILED가 곧 "알림 전에 Guard 확인을 통과해 예약됐다"는 전제다.
            _assert_not_sent(run, sent, "race-natural", task_id="task-1")

    asyncio.run(scenario())
