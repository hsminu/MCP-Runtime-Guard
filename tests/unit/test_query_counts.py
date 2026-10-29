"""C3.8: 화면 조회(B6.16 `list_calls`·`get_call`·`list_approvals`)를 요청별 C 계수와 대조한다.

#76 C 조건 6을 #86 C 리뷰의 임시 대조에서 정식 시험으로 옮겼다. 하니스는 `c_counting`이다(제품
조립 Guard, C `ServerRegistry`, 실제로 띄운 stdio(C2.6)·HTTP 서버). Task 넷을 돌린 뒤 조회 결과가
요청마다 다음과 맞는지 본다.

- `DISPATCHING` 단계가 있음 = Guard host_dispatch 1 = 서버 wire `tools/call` 1 = 수신 효과 1
- `approval_status`가 CONSUMED = DB 승인 소비 1
- 판정·상태·승인·단계·관측 등급이 시나리오의 기대값(T01·T02·T03·C5.6 응답 유실)
- Task를 지정하지 않은 목록의 OLDEST 순서 = 제출 순서(#86 C 리뷰 참고 2, 같은 시각은 감사 삽입 순서)

조회는 Store 포트까지다. A 화면이 이 조회를 연결하면 화면 쪽 대조는 그때 더한다.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released
from guard_app.domain.enums import ApprovalStatus, Decision, Label, RequestStatus
from guard_app.domain.queries import (
    ApprovalListLane,
    ApprovalListQuery,
    CallListQuery,
    ListSort,
)
from guard_app.domain.reasons import Reason

from .c_counting import (
    ALLOWED,
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

SENT = ("DECIDED", "DISPATCHING", "COMMITTED")
# READ는 분류·등급 반영을 한 번에 커밋하는 관측(OBSERVED)이 결과 기록이다(I4)
READ_STAGES = ("DECIDED", "DISPATCHING", "OBSERVED")

# 제출 순서대로: (Task, 도구, 판정, 상태, 승인, 단계, 관측 등급, 계수)
EXPECTED = (
    ("task-1", "read", Decision.ALLOW, RequestStatus.SUCCEEDED, None, READ_STAGES, Label.PUBLIC),
    ("task-1", "send", Decision.ALLOW, RequestStatus.SUCCEEDED, None, SENT, None),
    ("task-2", "read", Decision.ALLOW, RequestStatus.SUCCEEDED, None, READ_STAGES, Label.INTERNAL),
    (
        "task-2",
        "send",
        Decision.ASK,
        RequestStatus.SUCCEEDED,
        ApprovalStatus.CONSUMED,
        ("DECIDED", "WAITING_APPROVAL", "DISPATCHING", "COMMITTED"),
        None,
    ),
    (
        "task-3",
        "read",
        Decision.ALLOW,
        RequestStatus.SUCCEEDED,
        None,
        READ_STAGES,
        Label.CONFIDENTIAL,
    ),
    ("task-3", "send", Decision.DENY, RequestStatus.DENIED, None, ("DECIDED",), None),
    # 응답 유실: 결과를 기록했으므로 COMMITTED지만 성공 여부는 status(UNKNOWN_OUTCOME)로만 본다
    ("task-4", "send", Decision.ALLOW, RequestStatus.UNKNOWN_OUTCOME, None, SENT, None),
)
COUNTS = (ALLOWED, ALLOWED, ALLOWED, ONCE, ALLOWED, ZERO, ALLOWED)


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


async def _scenario(run) -> dict[str, object]:
    for task_id in ("task-3", "task-4"):
        await run.create_task(task_id)
    order: list[tuple[str, str]] = []  # (request_id, wire·효과 표지)

    async def submit(task_id: str, call, token: str):
        outcome = await run.guard.submit(task_id, call, expected_epoch=1)
        order.append((outcome.request_id, token))
        return outcome

    # T01: PUBLIC 조회 후 일반 외부 목적지로 ALLOW
    assert type(await submit("task-1", read("public/q1.txt", "r1"), "public/q1.txt")) is Released
    assert type(await submit("task-1", send("s1", to="issue"), "body-s1")) is Released
    # T02: INTERNAL 조회 후 승인된 외부 목적지로 ASK. 낡은 revision 승인은 거부, 맞는 값으로 승인
    assert (
        type(await submit("task-2", read("internal/q2.txt", "r2"), "internal/q2.txt")) is Released
    )
    pending = await submit("task-2", send("s2"), "body-s2")
    assert type(pending) is Pending
    waiting = await run.store.list_approvals(
        ApprovalListQuery(lane=ApprovalListLane.PENDING, sort=ListSort.OLDEST, offset=0, limit=100)
    )
    assert [row.approval_id for row in waiting.rows] == [pending.approval_id]
    revision = waiting.rows[0].row_revision
    stale = await run.human().approve(pending.approval_id, expected_row_revision=revision + 1)
    after_stale = run.request_counts(pending.request_id, "body-s2")
    approved = await run.human().approve(pending.approval_id, expected_row_revision=revision)
    # T03: CONFIDENTIAL 조회 후 일반 외부 목적지로 DENY
    assert type(await submit("task-3", read("private/q3.txt", "r3"), "private/q3.txt")) is Released
    assert type(await submit("task-3", send("s3", to="issue"), "body-s3")) is Denied
    # C5.6 응답 유실: 서버가 실행하고 답하지 않는다
    lost = await submit("task-4", send("s4", to="issue", body="hang-s4"), "hang-s4")
    assert type(lost) is OutcomeUnknown

    calls = await run.store.list_calls(CallListQuery(sort=ListSort.OLDEST, offset=0, limit=100))
    newest = await run.store.list_calls(CallListQuery(sort=ListSort.NEWEST, offset=0, limit=100))
    task_2 = await run.store.list_calls(
        CallListQuery(task_id="task-2", sort=ListSort.OLDEST, offset=0, limit=100)
    )
    history = await run.store.list_approvals(
        ApprovalListQuery(lane=ApprovalListLane.HISTORY, sort=ListSort.OLDEST, offset=0, limit=100)
    )
    still_waiting = await run.store.list_approvals(
        ApprovalListQuery(lane=ApprovalListLane.PENDING, sort=ListSort.OLDEST, offset=0, limit=100)
    )
    details = {row.request_id: await run.store.get_call(row.request_id) for row in calls.rows}
    return {
        "order": order,
        "pending": pending,
        "stale": stale,
        "after_stale": after_stale,
        "approved": approved,
        "calls": calls,
        "newest": newest,
        "task_2": task_2,
        "history": history,
        "still_waiting": still_waiting,
        "details": details,
        "counts": [run.request_counts(request_id, token) for request_id, token in order],
        "dispatches": [
            sum(1 for d in run.dispatched if d.request_id == request_id) for request_id, _ in order
        ],
    }


@pytest.mark.parametrize("kind", KINDS)
def test_call_and_approval_queries_match_the_c_counts(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, call_timeout_s=1.5) as run:
            return await _scenario(run)

    seen = asyncio.run(scenario())
    order = [request_id for request_id, _ in seen["order"]]
    calls = seen["calls"]

    # 기대 revision: 1 큰 값은 APPROVAL_RACE이고 아무것도 나가지 않는다
    assert type(seen["stale"]) is Denied
    assert seen["stale"].reasons == (Reason.APPROVAL_RACE,)
    assert seen["after_stale"] == ZERO
    assert type(seen["approved"]) is Released

    # 계수 자체가 시나리오대로다(대조가 0=0만 맞추는 것이 아님)
    assert seen["counts"] == list(COUNTS)

    # 목록: 건수·순서
    assert calls.total_count == len(calls.rows) == len(EXPECTED)
    assert [row.request_id for row in calls.rows] == order
    assert [row.request_id for row in seen["newest"].rows] == order[::-1]
    assert [row.request_id for row in seen["task_2"].rows] == order[2:4]
    assert seen["task_2"].total_count == 2

    for row, expected, counts in zip(calls.rows, EXPECTED, seen["counts"], strict=True):
        task_id, tool, decision, status, approval, stages, label = expected
        detail = seen["details"][row.request_id]
        assert detail is not None
        assert detail.row == row
        shown = tuple(stage.stage_code for stage in detail.stages)
        assert (row.task_id, row.tool_name, row.decision, row.status) == (
            task_id,
            tool,
            decision,
            status,
        )
        assert (row.approval_status, shown, detail.observed_label) == (approval, stages, label)
        # 요청별 대조: 조회가 보여 주는 것 = C가 센 것
        dispatched_once = int("DISPATCHING" in shown)
        assert dispatched_once == counts["host_dispatch"] == counts["wire"] == counts["effect"]
        assert int(row.approval_status is ApprovalStatus.CONSUMED) == counts["approval_consumed"]
    assert seen["dispatches"] == [c["host_dispatch"] for c in seen["counts"]]

    # 승인 목록: 대기는 비고, 이력에는 소비된 승인 한 행(revision 생성 0 → 승인 1 → 소비 2)
    assert seen["still_waiting"].rows == ()
    assert seen["still_waiting"].total_count == 0
    history = seen["history"]
    assert history.total_count == len(history.rows) == 1
    (consumed,) = history.rows
    assert consumed.approval_id == seen["pending"].approval_id
    assert consumed.request_id == order[3]
    assert (consumed.status, consumed.row_revision) == (ApprovalStatus.CONSUMED, 2)
