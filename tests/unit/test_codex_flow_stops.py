"""C6.8-2: 실제 codex.exe를 거친 T41·T42(하니스 `c_codex_flow`).

- T41: 모델이 허용 밖 항목(웹 검색)을 내면 A 세션 관리자(L3)가 그 Task를 멈춘다. Guard 도구는
  실행되지 않고 모델에 다음 요청이 가지 않는다.
- T42: 실행이 끝났지만 응답이 오지 않는 SEND는 호출 상한에서 결과 불명이 되고 Task가 멈춘다.
  같은 호출을 다시 보내지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge_types import SessionOptions

from .c_codex_flow import call, codex_flow, requires_codex
from .c_counting import ZERO, count_dispatches, executable_pin

pytestmark = requires_codex

WEB_SEARCH = {
    "type": "web_search_call",
    "id": "ws_1",
    "status": "completed",
    "action": {"type": "search", "query": "anything"},
}


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


async def _run_turn(flow, text: str, limit_s: float = 60):
    created = await flow.bindings.create_session(SessionOptions())
    runner = flow.bindings.runner_for_task(created.task_id)
    runner.add_user_message(text)
    update = await asyncio.wait_for(runner.advance(), limit_s)
    return created.task_id, update


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t41_an_item_outside_the_allow_list_halts_the_task(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = lambda _task: [[WEB_SEARCH, call("read", {"path": "public/a.txt"}, "c1")]]  # noqa: E731
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, update = await _run_turn(flow, "search the web")
            snapshot = await flow.bindings.snapshot(task_id)
            return (
                update.status,
                snapshot.status,
                len(flow.tasks.models[task_id].requests),
                flow.counts("read", "public/a.txt"),
            )

    status, task_status, requests, counts = asyncio.run(scenario())
    assert status == "halted"
    assert task_status is TaskStatus.HALTED
    assert requests == 1
    assert counts == ZERO


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t42_a_send_without_a_reply_is_unknown_and_never_resent(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = lambda _task: [  # noqa: E731
            [call("read", {"path": "public/a.txt"}, "c1")],
            [call("send", {"to": "issue", "body": "hang-t42"}, "c2")],
            [call("send", {"to": "issue", "body": "hang-t42"}, "c3")],
        ]
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts, call_timeout_s=2.0) as flow:
            task_id, update = await _run_turn(flow, "send it", limit_s=90)
            snapshot = await flow.bindings.snapshot(task_id)
            counts = flow.counts("send", "hang-t42")
            return update.status, snapshot.status, counts, flow.audit_reasons(task_id)

    status, task_status, counts, reasons = asyncio.run(scenario())
    assert task_status is TaskStatus.UNKNOWN_OUTCOME
    assert status == "halted"
    # 한 번 나가 실행됐고, 같은 표지의 두 번째 호출은 나가지 않는다.
    assert counts == {"approval_consumed": 0, "host_dispatch": 1, "wire": 1, "effect": 1}
    # 실행 뒤 응답 없음은 dispatch 뒤 오류로 기록되고, 그 Task의 Codex 세션은 끝난다.
    assert "POST_DISPATCH_ERROR" in reasons
    assert reasons[-1] == "SESSION_PROCESS_FAILED"
