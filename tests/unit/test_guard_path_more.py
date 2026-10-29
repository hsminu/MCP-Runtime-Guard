"""C6.8-3: Guard 경로 T/S 추가(T04·T29·T33·T34·T35·S05).

하니스는 C5.4 `c_counting`이다. 제품 조립 Guard, C `ServerRegistry`, B SQLiteStore,
실제 stdio(C2.6)·HTTP 서버를 쓰고 Guard 입구를 직접 부른다(Codex 경유 아님). stdio와 HTTP에서
한 번씩 돌린다.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import closing
from datetime import UTC, datetime

import pytest

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.domain.contracts import Denied, Pending, Released, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services import guard as guard_module
from guard_app.services.catalog import prepare_policy_change

from .c_counting import (
    ALLOWED,
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


def _reasons(run, task_id: str) -> list[str]:
    """그 Task의 감사 사유 코드(원문 없음)."""
    with closing(run._read_db()) as db:
        rows = db.execute(
            "SELECT reason_code FROM audit_events WHERE task_id = ? ORDER BY rowid", (task_id,)
        )
        return [row[0] for row in rows if row[0] is not None]


# T04: 서버 또는 목적지 미등록 → DENY


@pytest.mark.parametrize("kind", KINDS)
def test_t04_unregistered_server_or_destination_is_denied(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("public/a.txt", "r1"), expected_epoch=1)
            nowhere = await run.guard.submit(
                "task-1", send("d1", to="nowhere", body="body-d1"), expected_epoch=1
            )
            ghost = await run.guard.submit(
                "task-2",
                ProposedCall(
                    tool_ref="ghost__send",
                    arguments={"to": "issue", "body": "body-g1"},
                    model_call_id="g1",
                ),
                expected_epoch=1,
            )
            return (
                nowhere,
                ghost,
                run.request_counts(nowhere.request_id, "body-d1"),
                run.request_counts(ghost.request_id, "body-g1"),
                [d.tool for d in run.dispatched],
            )

    nowhere, ghost, first, second, tools = asyncio.run(scenario())
    assert type(nowhere) is Denied
    assert nowhere.reasons == (Reason.UNKNOWN_DESTINATION,)
    assert type(ghost) is Denied
    assert ghost.reasons == (Reason.UNKNOWN_SERVER,)
    assert first == second == ZERO
    # 서버에 간 것은 앞의 PUBLIC 읽기 하나뿐이다.
    assert tools == ["read"]


# T29: 실행 중 Profile·정책 변경 → POLICY_CHANGED, HALTED


@pytest.mark.parametrize("kind", KINDS)
def test_t29_policy_change_while_running_halts_tasks(tmp_path, kind, pin, dispatched):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            # task-1은 승인 대기, task-2는 실행 중이다.
            await run.guard.submit("task-1", read("internal/a.txt", "r1"), expected_epoch=1)
            pending = await run.guard.submit("task-1", send("p1", body="body-p1"), expected_epoch=1)
            assert type(pending) is Pending
            await run.guard.submit("task-2", read("public/b.txt", "r2"), expected_epoch=1)
            # public 출처 등급을 INTERNAL로 올리는 변경이다.
            sources = run.document["profile"]["sources"]
            edited = {
                **run.document,
                "profile": {
                    **run.document["profile"],
                    "sources": [
                        {**source, "label": "INTERNAL"}
                        if source["prefix"] == ["public"]
                        else source
                        for source in sources
                    ],
                },
            }
            change = prepare_policy_change(
                run.document, edited, run.assembled.registry, resource_resolver=identity
            )
            changed = await run.guard.apply_policy_change(
                change,
                AccessRulesChecker(change.loaded_profile.access_rules),
                task_source=run.store.list_tasks,
            )
            first = await run.store.load_task("task-1")
            second = await run.store.load_task("task-2")
            approved = await run.human().approve(pending.approval_id)
            retry = await run.guard.submit(
                "task-2", send("s2", to="issue", body="body-s2"), expected_epoch=1
            )
            record = await run.store.get_approval(pending.approval_id)
            # 양성 대조: 바뀐 정책으로 만든 새 Task는 새 등급으로 읽는다.
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
            fresh = await run.guard.submit("task-3", read("public/c.txt", "r3"), expected_epoch=1)
            return (
                changed,
                (first.status, second.status),
                approved,
                retry,
                record,
                fresh,
                (_reasons(run, "task-1"), _reasons(run, "task-2")),
                (
                    run.request_counts(pending.request_id, "body-p1"),
                    run.request_counts(retry.request_id, "body-s2"),
                ),
            )

    changed, statuses, approved, retry, record, fresh, reasons, counts = asyncio.run(scenario())
    assert changed.changed is True
    assert set(changed.stopped_task_ids) == {"task-1", "task-2"}
    assert changed.invalidated_approval_count == 1
    assert statuses == (TaskStatus.HALTED, TaskStatus.HALTED)
    assert type(approved) is Denied
    assert approved.reasons == (Reason.APPROVAL_RACE,)
    assert record is not None
    assert record.status is ApprovalStatus.CANCELLED
    assert type(retry) is Denied
    assert retry.reasons == (Reason.POLICY_CHANGED,)
    assert reasons[0][-1] == reasons[1][-1] == "POLICY_CHANGED"
    assert counts == (ZERO, ZERO)
    assert type(fresh) is Released
    assert fresh.label is Label.INTERNAL


# T33: 결과 resource_id와 요청 경로 불일치 → Unclassified, HALTED, 결과 미공개


@pytest.mark.parametrize("kind", KINDS)
def test_t33_forged_resource_id_is_unclassified_and_withheld(tmp_path, kind, pin, dispatched):
    """서버가 기밀 경로를 읽고 공개 경로(`public/...`)를 읽었다고 보고한다."""

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            forged = await run.guard.submit(
                "task-1", read("private/forge.txt", "f1"), expected_epoch=1
            )
            state = await run.store.load_task("task-1")
            after = await run.guard.submit(
                "task-1", send("s1", to="issue", body="body-s1"), expected_epoch=1
            )
            # 대조군: 보고가 요청과 같은 경로면 정상 공개된다.
            honest = await run.guard.submit(
                "task-2", read("public/forge.txt", "f2"), expected_epoch=1
            )
            return (
                forged,
                state,
                after,
                honest,
                run.request_counts(forged.request_id, "private/forge.txt"),
                run.request_counts(after.request_id, "body-s1"),
                _reasons(run, "task-1"),
            )

    forged, state, after, honest, read_counts, send_counts, reasons = asyncio.run(scenario())
    assert type(forged) is Withheld
    assert forged.reasons == (Reason.RESULT_UNCLASSIFIED,)
    assert (state.status, state.unclassified) == (TaskStatus.HALTED, True)
    # 서버가 주장한 낮은 등급을 받아들이지 않는다. 등급은 올라가지도 내려가지도 않았다.
    assert state.high_watermark is Label.PUBLIC
    assert reasons[-1] == "RESULT_UNCLASSIFIED"
    assert type(after) is Denied
    assert after.reasons == (Reason.TASK_UNCLASSIFIED,)
    # 서버는 읽기를 실행했지만 결과는 모델에 가지 않고, 그 Task에서는 더 보내지 않는다.
    assert read_counts == ALLOWED
    assert send_counts == ZERO
    assert type(honest) is Released
    assert honest.label is Label.PUBLIC


# T34: 등급 세탁(기밀 조회 → 공개 조회 → 외부 전송)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("accumulate", Denied), ("last_read", Released), ("none", Released)],
)
def test_t34_label_laundering_depends_on_the_taint_mode(
    tmp_path, kind, mode, expected, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched, taint_mode=mode) as run:
            await run.guard.submit("task-1", read("private/a.txt", "r1"), expected_epoch=1)
            await run.guard.submit("task-1", read("public/b.txt", "r2"), expected_epoch=1)
            outcome = await run.guard.submit(
                "task-1", send("s1", to="issue", body="body-s1"), expected_epoch=1
            )
            state = await run.store.load_task("task-1")
            return outcome, state, run.request_counts(outcome.request_id, "body-s1")

    outcome, state, counts = asyncio.run(scenario())
    assert type(outcome) is expected
    # 세 모드 모두 기록된 최고 등급은 같다. 다른 것은 전송 판정에 쓰는 유효 등급뿐이다.
    assert (state.high_watermark, state.last_read_label) == (Label.CONFIDENTIAL, Label.PUBLIC)
    if expected is Denied:
        assert outcome.reasons == (Reason.LABEL_EXCEEDS_BOUNDARY,)
        assert counts == ZERO
    else:
        assert counts == ALLOWED


# T35: SEND 성공 직후 같은 Task의 다음 호출 → RUNNING 유지, 등급 불변, Unclassified 아님


@pytest.mark.parametrize("kind", KINDS)
def test_t35_a_successful_send_keeps_the_task_running_and_its_label(
    tmp_path, kind, pin, dispatched
):
    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            await run.guard.submit("task-1", read("internal/a.txt", "r1"), expected_epoch=1)
            before = await run.store.load_task("task-1")
            sent = await run.guard.submit(
                "task-1", send("s1", to="mine", body="body-s1"), expected_epoch=1
            )
            after_send = await run.store.load_task("task-1")
            following = await run.guard.submit(
                "task-1", read("public/b.txt", "r2"), expected_epoch=1
            )
            after_read = await run.store.load_task("task-1")
            return (
                before,
                sent,
                after_send,
                following,
                after_read,
                run.request_counts(sent.request_id, "body-s1"),
            )

    before, sent, after_send, following, after_read, counts = asyncio.run(scenario())
    assert type(sent) is Released
    assert counts == ALLOWED
    # SEND 성공은 Task 행을 바꾸지 않는다(상태·등급·보안 버전·revision 모두 그대로).
    assert after_send == before
    assert type(following) is Released
    assert following.label is Label.PUBLIC
    assert (after_read.status, after_read.unclassified, after_read.high_watermark) == (
        TaskStatus.RUNNING,
        False,
        Label.INTERNAL,
    )


# S05: 판정 단계 임의 예외 주입 → DENY(INTERNAL_ERROR)


@pytest.mark.parametrize("kind", KINDS)
def test_s05_exception_in_the_policy_decision_is_denied(
    tmp_path, kind, pin, dispatched, monkeypatch
):
    """B 정책 판정 함수(`core.policy.evaluate`) 자체가 예외를 낸다. C6.9는 접근 규칙 판정기였다."""
    marker = "policy-exception-secret-marker"
    original = guard_module.evaluate

    def failing(inp):
        if inp.request.task_id == "task-1":
            raise RuntimeError(marker)
        return original(inp)

    monkeypatch.setattr(guard_module, "evaluate", failing)

    async def scenario():
        async with harness(tmp_path, kind, pin, dispatched) as run:
            denied = await run.guard.submit(
                "task-1", send("s1", to="issue", body="body-s1"), expected_epoch=1
            )
            first = await run.store.load_task("task-1")
            other = await run.guard.submit(
                "task-2", send("s2", to="issue", body="body-s2"), expected_epoch=1
            )
            return (
                denied,
                first,
                other,
                run.request_counts(denied.request_id, "body-s1"),
                run.request_counts(other.request_id, "body-s2"),
                run.audits,
            )

    denied, first, other, counts, other_counts, audits = asyncio.run(scenario())
    assert type(denied) is Denied
    assert denied.reasons == (Reason.INTERNAL_ERROR,)
    assert counts == ZERO
    # 그 요청만 거부한다. Task는 실행 중으로 남고 Guard 전체는 멈추지 않는다.
    assert first.status is TaskStatus.RUNNING
    assert type(other) is Released
    assert other_counts == ALLOWED
    # 감사에는 예외 형식만 남고 메시지는 남지 않는다(I9).
    assert [(event.phase, event.exception_type) for event in audits] == [
        ("before_dispatch", "RuntimeError")
    ]
    assert marker not in repr(audits)
    assert marker not in repr(denied)
