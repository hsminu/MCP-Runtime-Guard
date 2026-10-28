"""C6.9: 자체 접근 규칙의 거절·오류·누락에서 실제 MCP 서버에 아무것도 가지 않는지 계수로 본다.

제품 조립(`assemble_guard`)이 설정 문서의 `access_rules`로 만든 실제 `AccessRulesChecker`와,
B SQLiteStore 위의 C `ServerRegistry`(C4.10)를 쓴다. 서버는 진짜로 띄운다. 하니스와 계수 네 개
(승인 소비·host_dispatch·wire `tools/call`·수신 효과)는 `c_counting`에 있다.

대조군(시나리오 0)에서 계수가 실제로 올라가는 것을 보여, 다른 시나리오의 0이 의미 있게 한다.
사유 코드는 AGT-04(2026-10-28) 전후 이름을 모두 받는다.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

import pytest

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.domain.models import AccessResult, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import prepare_server_access_change
from guard_app.services.guard import GuardConfigError, PolicyChangeError

from .c_counting import (
    DENY_REASONS,
    ERROR_REASONS,
    KINDS,
    ZERO,
    count_dispatches,
    executable_pin,
    harness,
    identity,
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


# 0. 대조군: 허용된 READ와 승인된 SEND는 실제로 서버에 닿는다


@pytest.mark.parametrize("kind", KINDS)
def test_allowed_read_and_approved_send_reach_the_server(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            public = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            internal = await run.guard.submit(
                "task-1", read("internal/b.txt", "c2"), expected_epoch=1
            )
            pending = await run.guard.submit("task-1", send("c3"), expected_epoch=1)
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            return public, internal, pending, approved, run.counts("read"), run.counts("send")

    public, internal, pending, approved, reads, sends = asyncio.run(scenario())
    assert type(public) is Released
    assert type(internal) is Released
    assert type(pending) is Pending
    assert type(approved) is Released
    assert reads == {"approval_consumed": 1, "host_dispatch": 2, "wire": 2, "effect": 2}
    assert sends == {"approval_consumed": 1, "host_dispatch": 1, "wire": 1, "effect": 1}


# 1·2. deny 규칙에 걸린 도구, allow에 없는 도구(활성화 때 끈 도구)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("allow", "deny"), [(("read", "send"), ("send",)), (("read",), ())], ids=["deny", "missing"]
)
def test_denied_or_missing_tool_never_reaches_the_server(
    tmp_path, kind, allow, deny, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=allow, deny=deny) as run:
            control = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            # PUBLIC 등급이면 경계만으로는 ALLOW다. 막는 것은 접근 규칙뿐이다.
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            state = await run.store.load_task("task-1")
            return control, denied, state, run.counts("read"), run.counts("send")

    control, denied, state, reads, sends = asyncio.run(scenario())
    assert type(control) is Released
    assert reads["host_dispatch"] == reads["wire"] == reads["effect"] == 1
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert sends == ZERO
    assert state.status is TaskStatus.RUNNING


@pytest.mark.parametrize("kind", KINDS)
def test_tool_missing_from_allow_is_denied_even_for_an_ask_call(tmp_path, kind, pin, dispatched):
    """ASK가 될 SEND도 접근 규칙이 먼저 거부한다. 승인 대기가 만들어지지 않는다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=("read",)) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            return denied, run.counts("send")

    denied, sends = asyncio.run(scenario())
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert sends == ZERO


# 3. 판정기 예외: 예약 전 거절. 일반 예외는 INTERNAL_ERROR DENY, CancelledError는 그 Task만 정지
#    (ports.AccessChecker 설명, guard._pre_dispatch_failure). 둘 다 I6을 만족한다.


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("error", "status"),
    [(RuntimeError, TaskStatus.RUNNING), (asyncio.CancelledError, TaskStatus.HALTED)],
    ids=["exception", "cancelled"],
)
def test_checker_exception_is_denied_before_dispatch(
    tmp_path, kind, error, status, pin, dispatched, monkeypatch
):
    original = AccessRulesChecker.check_access
    marker = "checker-exception-secret-marker"

    def failing(self, req, ctx):
        if req.task_id == "task-1" and req.tool.tool_name == "send":
            raise error(marker)
        return original(self, req, ctx)

    monkeypatch.setattr(AccessRulesChecker, "check_access", failing)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            first = await run.store.load_task("task-1")
            other = await run.guard.submit("task-2", send("c3"), expected_epoch=1)
            second = await run.store.load_task("task-2")
            return denied, first, other, second, run.counts("send"), run.audits

    denied, first, other, second, sends, audits = asyncio.run(scenario())
    assert type(denied) is Denied
    assert denied.reasons == (Reason.INTERNAL_ERROR,)
    assert first.status is status
    # 감사에는 예외 형식만 남고 메시지는 남지 않는다(I9).
    assert [(event.phase, event.exception_type) for event in audits] == [
        ("before_dispatch", error.__name__)
    ]
    assert marker not in repr(audits)
    assert marker not in repr(denied)
    # 다른 Task는 같은 Guard로 계속 실행된다. 그 SEND 1건이 대조군이다.
    assert type(other) is Released
    assert second.status is TaskStatus.RUNNING
    assert sends == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}


# 4. 판정기가 AccessResult가 아닌 값을 돌려줌


class _Lookalike(AccessResult):
    """AccessResult처럼 보이는 하위 클래스도 정확한 형식이 아니다."""


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "value",
    [
        {"allowed": True, "reason": "", "error": False},
        True,
        None,
        _Lookalike(allowed=True, reason="", error=False),
    ],
    ids=["dict", "bool", "none", "subclass"],
)
def test_checker_returning_a_non_result_is_an_error_denial(
    tmp_path, kind, value, pin, dispatched, monkeypatch
):
    original = AccessRulesChecker.check_access

    def odd(self, req, ctx):
        return value if req.tool.tool_name == "send" else original(self, req, ctx)

    monkeypatch.setattr(AccessRulesChecker, "check_access", odd)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            control = await run.guard.submit("task-1", read("public/a.txt", "c1"), expected_epoch=1)
            denied = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            return control, denied, run.counts("send")

    control, denied, sends = asyncio.run(scenario())
    assert type(control) is Released
    assert type(denied) is Denied
    assert set(denied.reasons) & ERROR_REASONS
    assert sends == ZERO


# 5. ASK 대기 중 정책을 deny로 바꾼 뒤 승인


@pytest.mark.parametrize("kind", KINDS)
def test_policy_change_to_deny_while_waiting_blocks_the_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            pending = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            assert type(pending) is Pending
            change = prepare_server_access_change(
                run.document,
                run.assembled.registry,
                server_id="docs",
                tool_permissions={"read": True, "send": False},
                resource_resolver=identity,
            )
            await run.guard.apply_policy_change(
                change,
                AccessRulesChecker(change.loaded_profile.access_rules),
                task_source=run.store.list_tasks,
            )
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            record = await run.store.get_approval(pending.approval_id)
            settings = change.loaded_profile.guard_settings
            await run.store.create_task(
                TaskState(
                    task_id="task-3",
                    epoch=1,
                    profile_id=settings.profile_id,
                    policy_version=settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
            after = await run.guard.submit("task-3", send("c3"), expected_epoch=1)
            return approved, record, after, run.counts("send")

    approved, record, after, sends = asyncio.run(scenario())
    assert type(approved) is Denied
    # 정책 변경이 대기 승인을 먼저 무효화했다. 사람의 승인은 이미 처리된 승인에 닿는다.
    assert approved.reasons == (Reason.APPROVAL_RACE,)
    assert record is not None
    # Task가 POLICY_CHANGED로 정지하면서 대기 승인이 취소됐다.
    assert record.status is ApprovalStatus.CANCELLED
    # 바뀐 규칙으로 만든 새 Task에서도 SEND는 접근 규칙이 막는다.
    assert type(after) is Denied
    assert set(after.reasons) & DENY_REASONS
    assert sends == ZERO


# 6. async 판정기


@pytest.mark.parametrize("kind", KINDS)
def test_async_checker_is_refused_when_guard_is_built(tmp_path, kind, pin, dispatched, monkeypatch):
    async def coroutine_check(self, req, ctx):
        return AccessResult(allowed=True, reason="", error=False)

    monkeypatch.setattr(AccessRulesChecker, "check_access", coroutine_check)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, assemble=False) as run:
            with pytest.raises(GuardConfigError):
                await run.assemble()
            return run.counts("read"), run.counts("send")

    reads, sends = asyncio.run(scenario())
    assert reads == ZERO
    assert sends == ZERO


class _AsyncChecker:
    async def check_access(self, req, ctx):
        return AccessResult(allowed=True, reason="", error=False)


@pytest.mark.parametrize("kind", KINDS)
def test_async_checker_is_refused_by_a_policy_change(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, allow=("read",)) as run:
            change = prepare_server_access_change(
                run.document,
                run.assembled.registry,
                server_id="docs",
                tool_permissions={"read": True, "send": True},
                resource_resolver=identity,
            )
            with pytest.raises(PolicyChangeError):
                await run.guard.apply_policy_change(
                    change, _AsyncChecker(), task_source=run.store.list_tasks
                )
            state = await run.store.load_task("task-1")
            # 거부된 변경은 적용되지 않았다. SEND는 여전히 allow에 없다.
            denied = await run.guard.submit("task-1", send("c1"), expected_epoch=1)
            control = await run.guard.submit("task-1", read("public/a.txt", "c2"), expected_epoch=1)
            return state, denied, control, run.counts("send")

    state, denied, control, sends = asyncio.run(scenario())
    assert state.status is TaskStatus.RUNNING
    assert type(denied) is Denied
    assert set(denied.reasons) & DENY_REASONS
    assert type(control) is Released
    assert sends == ZERO


# 7. 승인 대기 중 서버 BLOCKED(실제 ServerRegistry). 승인 재판정이 등록 상태를 다시 보고 거부한다.
#    판정기 실행 도중 바뀐 경우의 APPROVAL_STALE은 B test_sync_access가 본다.


@pytest.mark.parametrize("kind", KINDS)
def test_server_blocked_while_waiting_rejects_the_approval(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("internal/b.txt", "c1"), expected_epoch=1)
            pending = await run.guard.submit("task-1", send("c2"), expected_epoch=1)
            assert type(pending) is Pending
            entry = run.registry.entry("docs")
            await run.registry.block("docs", expected_row_revision=entry.registration.row_revision)
            approved = await run.guard.human_control("local-operator").approve(pending.approval_id)
            record = await run.store.get_approval(pending.approval_id)
            state = await run.store.load_task("task-1")
            return approved, record, state, run.counts("send")

    approved, record, state, sends = asyncio.run(scenario())
    assert type(approved) is Denied
    assert approved.reasons == (Reason.REG_NOT_ACTIVE,)
    assert record is not None
    assert record.status is ApprovalStatus.INVALIDATED
    assert state.status is TaskStatus.HALTED
    assert sends == ZERO
