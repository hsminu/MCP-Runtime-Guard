"""인수 기준 3(C5.6): 쓰기 응답 유실 뒤 UNKNOWN_OUTCOME, 새 호출 ID·재연결로도 추가 전송 0.

하니스는 `c_counting`이다(제품 조립 Guard, C `ServerRegistry`, 실제 stdio·HTTP 서버). 서버는
SEND를 실행한 뒤 응답을 잃는다. 응답 없음(`hang-`), 시간 제한 뒤 늦은 응답(`late-`), 실행 뒤
프로세스 종료(`exit-`, stdio), 실행 뒤 500(`error500-`, HTTP) 네 가지다. HTTP는 실행하지 않고
307·308로 다른 경로를 가리키는 경우도 본다(리다이렉트를 따라가면 안 된다).

I8: 결과를 모르는 쓰기는 자동으로 다시 보내지 않는다. 그 Task는 UNKNOWN_OUTCOME으로 잠기고,
새 호출 ID로 같은 SEND를 내도, Guard를 새 연결로 다시 조립해도 서버에 다시 가지 않는다.
같은 실행에서 다른 Task의 SEND가 실제로 1이 되어 0이 의미 있다.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Released
from guard_app.domain.enums import TaskStatus

from .c_counting import KINDS, count_dispatches, executable_pin, harness, send

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")

CALL_TIMEOUT_S = 1.5
LOST_ONCE = {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}

# HTTP에서 전송 뒤 세션이 실패하면(응답 없음·500) Guard를 닫을 때 transport가 그 실패를 알린다.
# 결과 불명 처리와는 별개다. 제품 종료 경로가 이것을 정리 미확인으로 다루는지는 A 확인 사항이다.
CLOSE_ERRORS = {
    ("http", "hang"): ["POST_DISPATCH_ERROR"],
    ("http", "error500"): ["POST_DISPATCH_ERROR"],
}

LOSSES = [
    ("stdio", "hang"),
    ("http", "hang"),
    ("stdio", "late"),
    ("http", "late"),
    ("stdio", "exit"),
    ("http", "error500"),
]


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


@pytest.mark.parametrize(("kind", "loss"), LOSSES, ids=[f"{k}-{loss}" for k, loss in LOSSES])
def test_lost_write_response_is_unknown_and_never_resent(tmp_path, kind, loss, pin, dispatched):
    token = f"{loss}-c1"

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=CALL_TIMEOUT_S) as run:
            # PUBLIC Task에서 승인된 외부 목적지로의 SEND는 ALLOW다. 승인 없이 바로 나간다.
            lost = await run.guard.submit("task-1", send("c1", body=token), expected_epoch=1)
            first = run.request_counts(lost.request_id, token)
            locked = await run.store.load_task("task-1")
            # 같은 SEND를 새 호출 ID로 다시 낸다.
            retry = await run.guard.submit("task-1", send("c2", body=token), expected_epoch=1)
            # 늦은 응답이 도착할 시간과 자동 재전송을 기다린다.
            await asyncio.sleep(3.5)
            settled = await run.store.load_task("task-1")
            # 새 연결(stdio는 새 프로세스)로 같은 Store에 Guard를 다시 조립한다.
            await run.close_guard()
            close_errors = list(run.close_errors)
            await run.assemble(create_tasks=False)
            reconnected = await run.guard.submit("task-1", send("c3", body=token), expected_epoch=1)
            restarted = await run.store.load_task("task-1")
            # 대조군: 다시 조립한 Guard로 다른 Task의 SEND는 실제로 나간다.
            control = await run.guard.submit("task-2", send("c4"), expected_epoch=1)
            return (
                lost,
                first,
                locked,
                retry,
                settled,
                reconnected,
                restarted,
                control,
                run.request_counts(lost.request_id, token),
                run.request_counts(control.request_id, "body-c4"),
                close_errors,
            )

    (
        lost,
        first,
        locked,
        retry,
        settled,
        reconnected,
        restarted,
        control,
        final,
        control_counts,
        close_errors,
    ) = asyncio.run(scenario())
    # 서버는 실행했지만 Guard는 결과를 모른다.
    assert type(lost) is OutcomeUnknown
    assert first == LOST_ONCE
    assert locked.status is TaskStatus.UNKNOWN_OUTCOME
    # 새 호출 ID·늦은 응답·재연결 뒤에도 그 Task는 잠겨 있고 아무것도 다시 가지 않는다.
    assert type(retry) is Denied
    assert settled.status is TaskStatus.UNKNOWN_OUTCOME
    assert type(reconnected) is Denied
    assert restarted.status is TaskStatus.UNKNOWN_OUTCOME
    assert final == LOST_ONCE
    assert type(control) is Released
    assert control_counts == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}
    assert close_errors == CLOSE_ERRORS.get((kind, loss), [])


@pytest.mark.parametrize("status", [307, 308])
def test_redirect_is_not_followed_and_the_write_is_not_resent(tmp_path, status, pin, dispatched):
    """307·308은 메서드와 본문을 그대로 다시 보내라는 응답이다. transport는 따라가지 않는다."""
    token = f"redirect{status}-c1"

    async def scenario():
        async with harness(tmp_path, "http", pin, dispatched, call_timeout_s=CALL_TIMEOUT_S) as run:
            outcome = await run.guard.submit("task-1", send("c1", body=token), expected_epoch=1)
            state = await run.store.load_task("task-1")
            retry = await run.guard.submit("task-1", send("c2", body=token), expected_epoch=1)
            await asyncio.sleep(1)
            counts = run.request_counts(outcome.request_id, token)
            await run.close_guard()
            return outcome, state, retry, counts, list(run.counters.redirected), run.close_errors

    outcome, state, retry, counts, redirected, close_errors = asyncio.run(scenario())
    # 서버는 실행하지 않았지만 Guard는 그것을 알 수 없다. 쓰기이므로 결과 불명으로 잠근다.
    assert type(outcome) is OutcomeUnknown
    assert state.status is TaskStatus.UNKNOWN_OUTCOME
    assert type(retry) is Denied
    assert redirected == []
    assert counts == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 0}
    # 리다이렉트도 HTTP 세션 실패라 닫을 때 같은 코드가 남는다(CLOSE_ERRORS 설명).
    assert close_errors == ["POST_DISPATCH_ERROR"]


@pytest.mark.parametrize("kind", KINDS)
def test_other_tasks_are_unaffected_by_a_lost_write(tmp_path, kind, pin, dispatched):
    """결과 불명은 그 Task만 잠근다. 같은 연결의 다른 Task는 계속 보낸다(연결이 살아 있으면)."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=CALL_TIMEOUT_S) as run:
            lost = await run.guard.submit("task-1", send("c1", body="late-c1"), expected_epoch=1)
            await asyncio.sleep(3.5)
            other = await run.guard.submit("task-2", send("c2"), expected_epoch=1)
            first = await run.store.load_task("task-1")
            second = await run.store.load_task("task-2")
            return lost, other, first, second, run.request_counts(other.request_id, "body-c2")

    lost, other, first, second, counts = asyncio.run(scenario())
    assert type(lost) is OutcomeUnknown
    assert first.status is TaskStatus.UNKNOWN_OUTCOME
    assert type(other) is Released
    assert second.status is TaskStatus.RUNNING
    assert counts == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}
