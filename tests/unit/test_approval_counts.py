"""인수 기준 1·2(C5.4): 승인 경로의 계수를 요청별로 끝까지 센다.

- 기준 1: DENY와 승인 전·거절·만료(그리고 작업 취소) ASK에서 dispatch·wire·부작용 0
- 기준 2: 승인 1회와 더블클릭(연속·동시 두 번)에서 승인 소비·dispatch·wire·부작용이 각각 1

하니스는 `c_counting`이다. 제품 조립 Guard, C `ServerRegistry`, 실제로 띄운 stdio(C2.6)·HTTP
서버를 쓴다. 요청별 계수는 승인 소비·host_dispatch를 Guard `request_id`로, wire·수신 효과를
인자 표지(`body`)로 잇는다. 같은 실행에서 승인한 요청이 실제로 1이 되어 다른 요청의 0이 의미 있다.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus
from guard_app.domain.reasons import Reason

from .c_counting import (
    KINDS,
    ONCE,
    ZERO,
    count_dispatches,
    executable_pin,
    harness,
    read,
    send,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.6 launcher는 Windows 전용이다")


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


async def _ask(run, call_id: str) -> Pending:
    """INTERNAL 자료를 읽은 뒤 승인된 외부 목적지로 보내 ASK를 만든다(5.1 행렬)."""
    await run.guard.submit(
        "task-1", read(f"internal/{call_id}.txt", f"r-{call_id}"), expected_epoch=1
    )
    pending = await run.guard.submit("task-1", send(call_id), expected_epoch=1)
    assert type(pending) is Pending
    return pending


async def _status(run, approval_id: str) -> ApprovalStatus:
    record = await run.store.get_approval(approval_id)
    assert record is not None
    return record.status


# 기준 1: DENY와 승인 전·거절·만료·취소 ASK에서 0


@pytest.mark.parametrize("kind", KINDS)
def test_deny_by_boundary_sends_nothing_and_opens_no_approval(tmp_path, kind, pin, dispatched):
    """CONFIDENTIAL 자료를 일반 외부 목적지로: max_label을 넘어 DENY. 승인 대기 자체가 없다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("private/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2", to="issue"), expected_epoch=1)
            return (
                denied,
                run.approvals_for(denied.request_id),
                run.request_counts(denied.request_id, "body-c2"),
            )

    denied, approvals, counts = asyncio.run(scenario())
    assert type(denied) is Denied
    assert Reason.LABEL_EXCEEDS_BOUNDARY in denied.reasons
    assert approvals == 0
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_pending_ask_sends_nothing_before_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            # 사람의 응답을 기다리는 동안 아무것도 나가지 않는다.
            await asyncio.sleep(0.5)
            return await _status(run, pending.approval_id), run.request_counts(
                pending.request_id, "body-c1"
            )

    status, counts = asyncio.run(scenario())
    assert status is ApprovalStatus.PENDING
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_rejected_ask_sends_nothing_and_cannot_be_approved_later(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            rejected = await run.human().reject(pending.approval_id)
            late = await run.human().approve(pending.approval_id)
            return (
                rejected,
                late,
                await _status(run, pending.approval_id),
                run.request_counts(pending.request_id, "body-c1"),
            )

    rejected, late, status, counts = asyncio.run(scenario())
    assert type(rejected) is Denied
    assert status is ApprovalStatus.REJECTED
    assert type(late) is Denied
    assert late.reasons == (Reason.APPROVAL_RACE,)
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_expired_ask_sends_nothing_and_cannot_be_approved_later(tmp_path, kind, pin, dispatched):
    """유효 시간 1초. 만료 확정은 Guard 타이머다(NOTIFY-01)."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, approval_ttl_seconds=1) as run:
            pending = await _ask(run, "c1")
            # 승인하지 않고 기다린다. 사람의 다음 조작은 만료가 기록된 뒤에 온다.
            for _ in range(100):
                if await _status(run, pending.approval_id) is not ApprovalStatus.PENDING:
                    break
                await asyncio.sleep(0.1)
            late = await run.human().approve(pending.approval_id)
            return (
                late,
                await _status(run, pending.approval_id),
                run.request_counts(pending.request_id, "body-c1"),
            )

    late, status, counts = asyncio.run(scenario())
    assert status is ApprovalStatus.EXPIRED
    assert type(late) is Denied
    assert late.reasons == (Reason.APPROVAL_RACE,)
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_cancelled_task_sends_nothing_for_its_pending_ask(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            cancelled = await run.human().cancel("task-1")
            late = await run.human().approve(pending.approval_id)
            return (
                cancelled,
                late,
                await _status(run, pending.approval_id),
                run.request_counts(pending.request_id, "body-c1"),
            )

    cancelled, late, status, counts = asyncio.run(scenario())
    assert type(cancelled) is Denied
    assert status is ApprovalStatus.CANCELLED
    assert type(late) is Denied
    assert counts == ZERO


# 기준 2: 승인 1회와 더블클릭에서 각각 1


@pytest.mark.parametrize("kind", KINDS)
def test_one_approval_sends_exactly_once(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            approved = await run.human().approve(pending.approval_id)
            return approved, run.request_counts(pending.request_id, "body-c1")

    approved, counts = asyncio.run(scenario())
    assert type(approved) is Released
    assert counts == ONCE


@pytest.mark.parametrize("kind", KINDS)
def test_approving_twice_in_a_row_sends_once(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            first = await run.human().approve(pending.approval_id)
            second = await run.human().approve(pending.approval_id)
            return first, second, run.request_counts(pending.request_id, "body-c1")

    first, second, counts = asyncio.run(scenario())
    assert type(first) is Released
    assert type(second) is Denied
    assert second.reasons == (Reason.APPROVAL_RACE,)
    assert counts == ONCE


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("clicks", [2, 5])
def test_concurrent_approvals_send_once(tmp_path, kind, clicks, pin, dispatched):
    """더블클릭·Enter 반복: 같은 승인을 동시에 여러 번 보내도 실행은 1회다(I3)."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            human = run.human()
            outcomes = await asyncio.gather(
                *(human.approve(pending.approval_id) for _ in range(clicks))
            )
            return outcomes, run.request_counts(pending.request_id, "body-c1")

    outcomes, counts = asyncio.run(scenario())
    assert [type(outcome) for outcome in outcomes].count(Released) == 1
    assert all(
        outcome.reasons == (Reason.APPROVAL_RACE,)
        for outcome in outcomes
        if type(outcome) is Denied
    )
    assert counts == ONCE


# C5.4: 같은 실행의 요청들을 요청별로 나눠 센다


@pytest.mark.parametrize("kind", KINDS)
def test_counts_are_linked_to_each_request(tmp_path, kind, pin, dispatched):
    """두 Task의 ASK 중 하나만 승인한다. 승인한 요청만 1이고 나머지는 0이다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            approved_ask = await _ask(run, "c1")
            await run.guard.submit("task-2", read("internal/c2.txt", "r-c2"), expected_epoch=1)
            rejected_ask = await run.guard.submit("task-2", send("c2"), expected_epoch=1)
            assert type(rejected_ask) is Pending
            await run.human().approve(approved_ask.approval_id)
            await run.human().reject(rejected_ask.approval_id)
            return (
                run.request_counts(approved_ask.request_id, "body-c1"),
                run.request_counts(rejected_ask.request_id, "body-c2"),
                run.counts("send"),
            )

    approved, rejected, total = asyncio.run(scenario())
    assert approved == ONCE
    assert rejected == ZERO
    assert total == ONCE
