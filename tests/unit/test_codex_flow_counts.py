"""C6.8-2: 실제 codex.exe를 거친 T01·T02·T03 계수(하니스 `c_codex_flow`).

모델 대본이 읽기 뒤 보내기를 부르고, A 세션 관리자·Guard relay·제품 조립 Guard·실제 MCP
서버가 그 호출을 처리한다. 계수는 Guard 입구에서 잰 C5.4·C6.8-1과 같아야 한다.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from guard_app.domain.contracts import Released
from guard_app.domain.enums import TaskStatus
from guard_app.security.codex_l2 import L2_FINAL_TEXT
from guard_app.services.bridge_types import SessionOptions

from .c_codex_flow import call, codex_flow, requires_codex
from .c_counting import ALLOWED, ONCE, ZERO, count_dispatches, executable_pin

pytestmark = requires_codex


@pytest.fixture(scope="module")
def pin():
    return executable_pin()


@pytest.fixture
def dispatched(monkeypatch):
    return count_dispatches(monkeypatch)


def read_then_send(source: str, to: str, body: str):
    def scripts(_task_id: str):
        return [
            [call("read", {"path": source}, "c1")],
            [call("send", {"to": to, "body": body}, "c2")],
        ]

    return scripts


async def _wait_for(predicate, *, limit_s: float = 30.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while True:
        value = await predicate()
        if value:
            return value
        if loop.time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


async def _start(flow, text: str):
    bindings = flow.bindings
    created = await bindings.create_session(SessionOptions())
    assert created.tools_available
    runner = bindings.runner_for_task(created.task_id)
    runner.add_user_message(text)
    return created.task_id, asyncio.ensure_future(runner.advance())


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t01_public_read_then_external_send_runs_once_through_codex(
    tmp_path, kind, pin, dispatched
):
    async def scenario():
        scripts = read_then_send("public/a.txt", "issue", "t01-body")
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, advance = await _start(flow, "share the public note")
            update = await asyncio.wait_for(advance, 60)
            snapshot = await flow.bindings.snapshot(task_id)
            return update, snapshot, flow.counts("send", "t01-body"), flow.tasks.l2_failures

    update, snapshot, counts, l2_failures = asyncio.run(scenario())
    assert l2_failures == ()
    assert update.status == "complete"
    assert update.assistant_text == L2_FINAL_TEXT
    assert snapshot.status is TaskStatus.RUNNING
    assert counts == ALLOWED


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t02_internal_send_waits_for_approval_then_runs_once(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = read_then_send("internal/a.txt", "partner", "t02-body")
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, advance = await _start(flow, "send the internal note to the partner")

            async def waiting():
                snapshot = await flow.bindings.snapshot(task_id)
                return snapshot if snapshot.approval_id is not None else None

            during = await _wait_for(waiting)
            before = flow.counts("send", "t02-body")
            assert not advance.done()
            outcome = await flow.bindings.human_control.approve(during.approval_id)
            update = await asyncio.wait_for(advance, 60)
            return during, before, outcome, update, flow.counts("send", "t02-body")

    during, before, outcome, update, after = asyncio.run(scenario())
    assert during.status is TaskStatus.WAITING_APPROVAL
    assert before == ZERO
    assert type(outcome) is Released
    assert update.status == "complete"
    assert after == ONCE


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t03_confidential_external_send_is_denied_and_nothing_is_sent(
    tmp_path, kind, pin, dispatched
):
    async def scenario():
        scripts = read_then_send("private/a.txt", "issue", "t03-body")
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, advance = await _start(flow, "post the private note")
            update = await asyncio.wait_for(advance, 60)
            snapshot = await flow.bindings.snapshot(task_id)
            model = flow.tasks.models[task_id]
            return update, snapshot, flow.counts("send", "t03-body"), flow.approvals(), model

    update, snapshot, counts, approvals, model = asyncio.run(scenario())
    assert update.status == "complete"
    assert approvals == 0
    assert counts == ZERO
    assert snapshot.status is TaskStatus.RUNNING
    # 모델은 거부를 고정 문구로만 받는다(원문·사유 코드 없음).
    outputs = [
        item
        for request in model.requests
        for item in request.get("input", [])
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    denied = [item for item in outputs if item.get("call_id") == "c2"]
    assert len(denied) >= 1
    text = json.dumps(denied)
    assert "LABEL_EXCEEDS_BOUNDARY" not in text
    assert "private/a.txt" not in text
