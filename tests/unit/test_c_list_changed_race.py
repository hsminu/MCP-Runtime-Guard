"""#120·B6.19: 도구 목록 변경 알림 뒤 아직 wire로 나가지 않은 호출(C 계수, stdio·HTTP).

LIST-01은 알림을 받은 서버를 이번 실행에서 PENDING으로 보고 새 호출을 예약 전에 거부한다.
그러나 Guard의 마지막 확인(`catalog.aresolve`) 뒤 worker가 실제로 `tools/call`을 보내기 전에
알림이 오면, 그 호출은 PENDING이 된 뒤에도 서버까지 간다(#120 재현).

- 고쳐지기 전 경우는 `xfail(strict=True, raises=StillSentError)`다. SEND가 서버에 닿았을 때만
  `StillSentError`를 던지므로, 전제(알림 뒤 PENDING 확인 등)가 깨진 실패는 숨지 않는다. 고쳐지면
  XPASS가 실패로 나오고, 그때 표시를 지운다.
- `test_*_without_a_pause`는 시험용 정지 장치 없이 같은 순서를 만든다. 다른 Task의 READ가
  서버에서 처리되는 동안 SEND가 같은 서버 worker 대기열에 들어가 있으면 된다(하니스
  `notify-slow`).
- 계수는 C5.4 하니스 그대로다: 승인 소비(DB), host_dispatch(`ServerWorker._execute` 진입),
  wire `tools/call`, 수신 효과.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from guard_app.adapters.mcp_transport import McpTransport
from guard_app.adapters.mcp_transport.worker import ServerWorker
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import RegStatus

from .c_counting import KINDS, ZERO, count_dispatches, executable_pin, harness, read, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")


class StillSentError(Exception):
    """알림으로 PENDING이 된 뒤에도 SEND가 서버에 닿았다(#120). 계수만 싣는다."""


def unfixed(test):
    return pytest.mark.xfail(
        strict=True,
        raises=StillSentError,
        reason="#120·B6.19: worker가 전송 직전에 도구 정의 변경을 확인하지 않는다",
    )(test)


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


def _check_not_sent(counts: dict[str, int]) -> None:
    if counts["wire"] or counts["effect"]:
        raise StillSentError(counts)


class _HeldSend:
    """`McpTransport.call`에서 SEND만 붙잡아 알림과 wire 전송의 순서를 고정한다(#120 PoC)."""

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
@unfixed
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
            _check_not_sent(run.request_counts(sent.request_id, "race-held"))

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
@unfixed
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
            await asyncio.wait_for(approving, 10)
            counts = run.request_counts(pending.request_id, "approved-held")
            assert counts["approval_consumed"] == 1  # 승인은 알림 전에 이미 소비됐다
            _check_not_sent(counts)

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
@unfixed
def test_send_queued_behind_a_slow_read_is_not_sent_after_its_notice_without_a_pause(
    tmp_path, kind, pin, dispatched, monkeypatch
):
    """시험용 정지 장치 없이: Task 2의 READ가 서버에 있는 동안 Task 1의 SEND가 대기열에 선다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            seen: list[tuple[str, RegStatus | None]] = []
            observed = ServerWorker._execute

            async def observe(worker, session, cmd):
                # 지연 없이 관찰만 한다: worker가 실제로 보내기 직전의 등록 상태
                seen.append((cmd.req.tool.tool_name, run.assembled.registry.status("docs")))
                return await observed(worker, session, cmd)

            monkeypatch.setattr(ServerWorker, "_execute", observe)
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
            # 전제: SEND는 알림 전에 Guard 확인을 통과했고(Denied 아님),
            # worker가 보낼 때는 서버가 이미 PENDING이었다.
            assert type(sent) is not Denied
            assert ("send", RegStatus.PENDING) in seen
            _check_not_sent(run.request_counts(sent.request_id, "race-natural"))

    asyncio.run(scenario())
