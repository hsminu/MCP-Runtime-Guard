"""C6.8 일부: Guard 경로 T 시나리오를 실제 서버 계수로 확인한다(T01·T09·T10·T11·T26·T30).

하니스는 `c_counting`이다. 제품 조립 Guard, C `ServerRegistry`, 실제로 띄운 stdio(C2.6)·HTTP
서버를 쓴다. Codex 세션을 거치지 않고 Guard 입구(`submit`·사람 채널)를 직접 부른다.

- T01: PUBLIC 조회 후 일반 외부 목적지 전송은 ALLOW이고 dispatch·wire·효과가 각각 1이다.
- T09: 승인은 승인한 사본에만 묶인다. 대기 중이나 승인 뒤 바꾼 인자는 그 승인으로 나가지 않고,
  저장된 승인 기록이 메모리 사본과 다르면 실행하지 않는다.
- T10: 승인 대기 중 도구 정의가 바뀌면(Registry가 변경을 기록) 승인은 무효이고 Task는 멈춘다.
  사용자가 설정에서 도구 종류를 바꿔도(`apply_settings`, #46) 같다.
- T11: 앱을 다시 시작하면(B6.8 명시 시작·복구) 이전 승인은 복원되지 않고 실행 0이다.
- T26: 기밀 자료를 외부 MCP 서버를 거쳐 내 목적지로 보내면 서버 경계로 DENY다.
- T30: 기밀 자료를 받을 수 있게 등록한 내 목적지로는 내부 서버를 거쳐 ALLOW다(정상 작업 유지).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from contextlib import closing

import pytest

from guard_app.adapters.mcp_transport.tool_listing import ToolListing
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import ServerSettings

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


async def _approval_status(run, approval_id: str) -> ApprovalStatus:
    record = await run.store.get_approval(approval_id)
    assert record is not None
    return record.status


# T01


@pytest.mark.parametrize("kind", KINDS)
def test_public_read_then_external_send_is_allowed_once(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            sent = await run.guard.submit("task-1", send("c2", to="issue"), expected_epoch=1)
            return (
                sent,
                run.approvals_for(sent.request_id),
                run.request_counts(sent.request_id, "body-c2"),
            )

    sent, approvals, counts = asyncio.run(scenario())
    assert type(sent) is Released
    assert approvals == 0
    assert counts == ALLOWED


# T09


@pytest.mark.parametrize("kind", KINDS)
def test_changed_arguments_never_ride_on_an_approval(tmp_path, kind, pin, dispatched):
    """대기 중에 바꾼 호출도, 승인 뒤 다시 낸 바꾼 호출도 그 승인으로 나가지 않는다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            # 승인을 기다리는 동안 모델이 목적지·본문을 바꿔 다시 낸다.
            during = await run.guard.submit(
                "task-1", send("c1x", to="issue", body="tampered-during"), expected_epoch=1
            )
            approved = await run.human().approve(pending.approval_id)
            # 승인 뒤 같은 도구를 다른 본문으로 다시 낸다. 새 승인이 필요하다.
            after = await run.guard.submit(
                "task-1", send("c1y", body="tampered-after"), expected_epoch=1
            )
            return (
                during,
                approved,
                after,
                run.request_counts(pending.request_id, "body-c1"),
                run.request_counts(getattr(during, "request_id", None), "tampered-during"),
                run.request_counts(getattr(after, "request_id", None), "tampered-after"),
                [token for name, token in run.counters.wire_entries() if name == "send"],
            )

    during, approved, after, original, tampered_during, tampered_after, wire = asyncio.run(
        scenario()
    )
    assert type(during) is not Released
    assert type(approved) is Released
    assert type(after) is Pending
    assert original == ONCE
    assert tampered_during == ZERO
    assert tampered_after == ZERO
    assert wire == ["body-c1"]


@pytest.mark.parametrize("kind", KINDS)
def test_stored_approval_that_differs_from_the_copy_is_not_executed(
    tmp_path, kind, pin, dispatched
):
    """결함 주입: DB의 승인 사본 해시를 바꾸면 Guard 전체가 멈추고(INTERNAL_ERROR) 실행 0이다.

    Task 하나의 APPROVAL_STALE이 아니라 전역 정지다. 다른 Task의 새 호출도 거부된다.
    무결성 손상은 전역 정지가 의도다(#76, #79 B 리뷰). writer가 쓰지 않은 값이 DB에 있으면
    다른 행도 믿을 근거가 없다(REC-02 "파손은 게이트 닫힘"). B6-01이 이 경우를 "손상" 결과로
    따로 두면 기대를 그에 맞춘다.
    """

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            with closing(sqlite3.connect(run.db, timeout=5, isolation_level=None)) as db:
                db.execute(
                    "UPDATE approvals SET snapshot_hash = ? WHERE approval_id = ?",
                    ("0" * 64, pending.approval_id),
                )
            outcome = await run.human().approve(pending.approval_id)
            other = await run.guard.submit("task-2", read("public/b.txt", "c2"), expected_epoch=1)
            return (
                outcome,
                other,
                run.request_counts(pending.request_id, "body-c1"),
                run.counts("send"),
            )

    outcome, other, counts, sends = asyncio.run(scenario())
    assert type(outcome) is Denied
    assert outcome.reasons == (Reason.INTERNAL_ERROR,)
    assert type(other) is Denied
    assert other.reasons == (Reason.INTERNAL_ERROR,)
    assert counts == ZERO
    assert sends == ZERO


# T10


def _changed_send_listing(run) -> ToolListing:
    """연결된 서버의 실제 목록에서 `send` 설명만 바꾼다(정의 변조)."""
    listing = run.assembled.transport.tool_listing("docs")
    assert listing is not None
    assert listing.complete
    tools = []
    for tool in listing.tools:
        definition = tool.to_dict()
        if definition["name"] == "send":
            definition["description"] = "Send a message and copy it elsewhere"
        tools.append(FrozenJsonObject(definition))
    return ToolListing(complete=True, tools=tuple(tools), reason=None)


@pytest.mark.parametrize("kind", KINDS)
def test_definition_change_while_waiting_invalidates_the_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            settings = ServerSettings.model_validate_json(
                json.dumps(run.document["servers"][0]), strict=True
            )
            change = await run.registry.observe(settings, _changed_send_listing(run))
            outcome = await run.human().approve(pending.approval_id)
            task = await run.store.load_task("task-1")
            return (
                change,
                outcome,
                await _approval_status(run, pending.approval_id),
                task.status,
                run.request_counts(pending.request_id, "body-c1"),
            )

    change, outcome, approval, task_status, counts = asyncio.run(scenario())
    assert change is not None
    assert type(outcome) is Denied
    assert {Reason.REG_NOT_ACTIVE, Reason.TOOL_FINGERPRINT_CHANGED} & set(outcome.reasons)
    assert approval is ApprovalStatus.INVALIDATED
    assert task_status is TaskStatus.HALTED
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_settings_change_while_waiting_closes_the_approval(tmp_path, kind, pin, dispatched):
    """A가 설정 변경 때 부르는 순서(#46): `apply_settings`가 Guard 승인 무효화까지 부른다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            server = dict(run.document["servers"][0])
            server["tools"] = [server["tools"][0], {"name": "send", "kind": "other"}]
            change = await run.registry.apply_settings(
                ServerSettings.model_validate_json(json.dumps(server), strict=True),
                expected_row_revision=run.registry.entry("docs").registration.row_revision,
                invalidate_tool=run.guard.invalidate_tool,
            )
            approval = await _approval_status(run, pending.approval_id)
            outcome = await run.human().approve(pending.approval_id)
            task = await run.store.load_task("task-1")
            return (
                change.changed_tools,
                approval,
                outcome,
                task.status,
                run.request_counts(pending.request_id, "body-c1"),
            )

    changed, approval, outcome, task_status, counts = asyncio.run(scenario())
    assert changed == ("send",)
    # Guard 무효화(`invalidate_tool`)는 사람이 누르기 전에 대기 승인을 닫는다(B: CANCELLED)
    assert approval is ApprovalStatus.CANCELLED
    assert type(outcome) is Denied
    assert outcome.reasons == (Reason.APPROVAL_RACE,)
    assert task_status is TaskStatus.HALTED
    assert counts == ZERO


# T11


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("closing_guard", "expected"),
    [
        # 정상 종료: Guard를 닫을 때 대기 승인이 취소된다. 복구가 바꿀 승인은 없다.
        (True, (ApprovalStatus.CANCELLED, 0)),
        # 비정상 종료: Guard를 닫지 않고 Store만 끊는다. 복구가 대기 승인을 만료시킨다.
        (False, (ApprovalStatus.EXPIRED, 1)),
    ],
)
def test_restart_does_not_restore_a_pending_approval(
    tmp_path, kind, closing_guard, expected, pin, dispatched
):
    """Store를 닫고 B6.8 명시 시작(이관 준비·복구·회차 활성화)으로 다시 연다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            pending = await _ask(run, "c1")
            abandoned = None
            if closing_guard:
                await run.close_guard()
            else:
                abandoned, run.assembled = run.assembled, None
            try:
                report = await run.restart()
                late = await run.human().approve(pending.approval_id)
                retry = await run.guard.submit("task-1", send("c1"), expected_epoch=1)
                # 양성 대조(#79 B 리뷰): 새 회차의 새 Task는 정상으로 나간다. 그래서 위의 0은
                # 실행 권한이 다 닫혀서가 아니라 옛 승인·옛 Task라서다.
                await run.create_task("task-3")
                await run.guard.submit("task-3", read("public/new.txt", "n1"), expected_epoch=1)
                fresh = await run.guard.submit(
                    "task-3", send("n2", to="issue", body="body-after"), expected_epoch=1
                )
                return (
                    report.changed_approval_count,
                    (report.unresolved_count, report.quarantined_count),
                    late,
                    retry,
                    await _approval_status(run, pending.approval_id),
                    run.request_counts(pending.request_id, "body-c1"),
                    fresh,
                    run.request_counts(fresh.request_id, "body-after"),
                )
            finally:
                if abandoned is not None:
                    # 버린 Guard의 연결(stdio 프로세스)을 정리한다. Store는 이미 닫혔다.
                    await abandoned.transport.close()

    changed, left, late, retry, status, counts, fresh, fresh_counts = asyncio.run(scenario())
    assert (status, changed) == expected
    assert left == (0, 0)
    assert type(late) is Denied
    assert late.reasons == (Reason.APPROVAL_RACE,)
    assert type(retry) is Denied
    assert retry.reasons == (Reason.TASK_NOT_RUNNING,)
    assert counts == ZERO
    assert type(fresh) is Released
    assert fresh_counts == ALLOWED


# T26·T30


@pytest.mark.parametrize("kind", KINDS)
def test_confidential_through_an_external_server_is_denied_at_the_server(
    tmp_path, kind, pin, dispatched
):
    """내 목적지가 기밀을 받을 수 있어도 중간의 외부 MCP 서버가 인자를 받으므로 DENY다."""

    async def scenario():
        async with harness(
            tmp_path,
            kind,
            pin,
            dispatched,
            server_tier="approved_external",
            server_max_label="INTERNAL",
        ) as run:
            got = await run.guard.submit("task-1", read("private/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2", to="mine"), expected_epoch=1)
            return (
                got,
                denied,
                run.approvals_for(denied.request_id),
                run.request_counts(denied.request_id, "body-c2"),
            )

    got, denied, approvals, counts = asyncio.run(scenario())
    assert type(got) is Released
    assert type(denied) is Denied
    assert Reason.LABEL_EXCEEDS_BOUNDARY in denied.reasons
    assert approvals == 0
    assert counts == ZERO


@pytest.mark.parametrize("kind", KINDS)
def test_confidential_to_my_registered_destination_is_allowed(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("private/a.txt", "c1"), expected_epoch=1)
            sent = await run.guard.submit("task-1", send("c2", to="mine"), expected_epoch=1)
            task = await run.store.load_task("task-1")
            return (
                sent,
                task.status,
                run.approvals_for(sent.request_id),
                run.request_counts(sent.request_id, "body-c2"),
            )

    sent, task_status, approvals, counts = asyncio.run(scenario())
    assert type(sent) is Released
    assert task_status is TaskStatus.RUNNING
    assert approvals == 0
    assert counts == ALLOWED
