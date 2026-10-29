"""#106 B1.5: host_dispatch는 `ServerWorker._execute` 진입에서 센다(개발문서 계수 표).

하니스는 `c_counting`이다(제품 조립 Guard, C `ServerRegistry`, 실제 stdio·HTTP 서버).
`McpTransport.call`에 들어왔어도 worker가 실행하기 전에 정리한 명령은 host_dispatch가 아니다.

시나리오: 응답 없는 SEND(`hang-`)가 서버 worker를 쥐고 있는 동안 다른 Task의 SEND가 대기열에
들어간다. 사람이 그 Task를 멈출 때 제품 순서(A `bridge_commands._stop_task`)대로 먼저
`discard_queued`로 대기 명령을 정리하고 취소한다. 대기했던 SEND는 transport 입구는 지났지만
host_dispatch·wire·효과가 모두 0이다. 옮기기 전 하니스(`McpTransport.call` 진입)라면 1로 셌을
경우다. 응답 없는 SEND는 실행됐으므로 각 1이다(양성 대조: 같은 계수가 실행은 센다).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import pytest

from guard_app.adapters.mcp_transport import McpTransport
from guard_app.domain.contracts import OutcomeUnknown, Released

from .c_counting import KINDS, count_dispatches, executable_pin, harness, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")

CALL_TIMEOUT_S = 1.5
ZERO = {"approval_consumed": 0, "host_dispatch": 0, "wire": 0, "effect": 0}
SENT = {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


@pytest.fixture
def entered(monkeypatch):
    """대조용: Guard가 `McpTransport.call`에 들어간 요청 ID(옮기기 전 계수 지점)."""
    ids: list[str] = []
    original = McpTransport.call

    async def entering(self, req):
        ids.append(req.request_id)
        return await original(self, req)

    monkeypatch.setattr(McpTransport, "call", entering)
    return ids


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not condition():  # noqa: ASYNC110 - 계수 목록에는 알릴 이벤트가 없다
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("kind", KINDS)
def test_a_queued_send_discarded_before_execute_is_not_a_dispatch(
    tmp_path, kind, pin, dispatched, entered
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=CALL_TIMEOUT_S) as run:
            hung = asyncio.create_task(
                run.guard.submit("task-1", send("c1", body="hang-c1"), expected_epoch=1)
            )
            await _until(lambda: len(dispatched) == 1)
            queued = asyncio.create_task(run.guard.submit("task-2", send("c2"), expected_epoch=1))
            await _until(lambda: len(entered) == 2)
            assert run.assembled is not None
            discarded = run.assembled.transport.discard_queued("task-2")
            await run.human().cancel("task-2")
            outcome = await queued
            lost = await hung
            return (
                discarded,
                entered[1],
                outcome,
                run.request_counts(entered[1], "body-c2"),
                lost,
                run.request_counts(entered[0], "hang-c1"),
            )

    discarded, queued_id, outcome, queued, lost, hung = asyncio.run(scenario())
    assert discarded == 1
    assert type(outcome) is not Released
    assert queued_id not in {d.request_id for d in dispatched}
    assert queued == ZERO
    assert type(lost) is OutcomeUnknown
    assert hung == SENT
