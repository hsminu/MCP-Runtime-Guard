"""C6.8-2: 실제 codex.exe를 거친 T21·T43·T45·T46(하니스 `c_codex_flow`).

- T21: 새 Task의 모델 요청에 이전 Task의 대화·결과가 섞이지 않는다.
- T43: 표지가 든 요청·결과를 처리하고 Task를 닫으면 앱 폴더 전체(Codex 로그 DB 포함)에 표지가 없다.
- T45: 승인 뒤 대기 중이던 도구 호출에 Codex로 가는 결과가 정확히 1회다.
- T46: 두 Task를 동시에 돌려도 CODEX_HOME 구성은 그대로이고 Task 폴더(`sqlite_home`)는 따로
  만들어져 따로 지워진다. 한 Task의 모델 인증이 실패하면 그 Task만 멈춘다.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from guard_app.domain.contracts import Released
from guard_app.domain.enums import TaskStatus
from guard_app.security.ids import new_suffix
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


def in_order(*scripts):
    """Task를 만든 순서대로 대본을 하나씩 준다."""
    queue = list(scripts)

    def next_script(_task_id: str):
        return queue.pop(0)

    return next_script


async def _session(flow, text: str):
    created = await flow.bindings.create_session(SessionOptions())
    runner = flow.bindings.runner_for_task(created.task_id)
    runner.add_user_message(text)
    return created.task_id, runner


def _outputs(request, call_id: str) -> list[dict]:
    return [
        item
        for item in request.get("input", [])
        if isinstance(item, dict)
        and item.get("type") == "function_call_output"
        and item.get("call_id") == call_id
    ]


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t21_a_new_task_sees_nothing_of_the_previous_one(tmp_path, kind, pin, dispatched):
    first_marker = f"t21first{new_suffix()}"

    async def scenario():
        scripts = in_order(
            [[call("read", {"path": f"public/{first_marker}.txt"}, "a1")]],
            [[call("read", {"path": "public/second.txt"}, "b1")]],
        )
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            first, runner = await _session(flow, f"read {first_marker}")
            await asyncio.wait_for(runner.advance(), 60)
            second, runner = await _session(flow, "read the second note")
            await asyncio.wait_for(runner.advance(), 60)
            return (
                json.dumps(flow.tasks.models[first].requests),
                json.dumps(flow.tasks.models[second].requests),
                flow.tasks.task_dirs[first] != flow.tasks.task_dirs[second],
            )

    first_requests, second_requests, separate_folders = asyncio.run(scenario())
    assert first_marker in first_requests
    assert first_marker not in second_requests
    assert separate_folders


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t43_no_marker_is_left_in_the_app_folder_after_the_task(tmp_path, kind, pin, dispatched):
    marker = f"t43mark{new_suffix()}"

    async def scenario():
        scripts = in_order(
            [
                [call("read", {"path": f"public/{marker}.txt"}, "m1")],
                [call("send", {"to": "issue", "body": f"{marker}-body"}, "m2")],
            ]
        )
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, runner = await _session(flow, f"use {marker}")
            await asyncio.wait_for(runner.advance(), 60)
            codex_files = flow.files_containing(marker)
            # 대조군: 검사기가 Task 폴더의 표지를 찾는지, 그 폴더가 정리로 사라지는지 본다.
            planted = flow.tasks.task_dirs[task_id] / "planted.txt"
            planted.write_text(marker, encoding="utf-8")
            control = flow.files_containing(marker)
            await flow.close()
            folder = flow.tasks.task_dirs[task_id].relative_to(flow.run.folders.paths.root)
            return codex_files, control, folder, flow.files_containing(marker), flow.tasks.exits

    codex_files, control, folder, after, exits = asyncio.run(scenario())
    # 고정 0.162.1은 ephemeral 스레드의 대화·도구 결과를 Task 폴더 DB·CODEX_HOME에 쓰지 않는다.
    assert codex_files == []
    assert control == [f"{folder.as_posix()}/planted.txt"]
    assert set(exits.values()) == {True}
    assert after == []


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t45_an_approved_call_gets_exactly_one_result(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = in_order(
            [
                [call("read", {"path": "internal/a.txt"}, "c1")],
                [call("send", {"to": "partner", "body": "t45-body"}, "c2")],
            ]
        )
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, runner = await _session(flow, "send the internal note to the partner")
            advance = asyncio.ensure_future(runner.advance())
            for _ in range(600):
                snapshot = await flow.bindings.snapshot(task_id)
                if snapshot.approval_id is not None:
                    break
                await asyncio.sleep(0.05)
            outcome = await flow.bindings.human_control.approve(snapshot.approval_id)
            late = await flow.bindings.human_control.approve(snapshot.approval_id)
            await asyncio.wait_for(advance, 60)
            requests = flow.tasks.models[task_id].requests
            return outcome, late, requests, flow.counts("send", "t45-body")

    outcome, late, requests, counts = asyncio.run(scenario())
    assert type(outcome) is Released
    assert type(late) is not Released
    assert counts == ONCE
    # 마지막 모델 요청(전체 기록)에 c2의 결과가 정확히 하나다.
    assert len(_outputs(requests[-1], "c2")) == 1


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t46_two_tasks_run_side_by_side_in_their_own_folders(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = in_order(
            [
                [call("read", {"path": "public/a.txt"}, "a1")],
                [call("send", {"to": "issue", "body": "t46-a"}, "a2")],
            ],
            [
                [call("read", {"path": "public/b.txt"}, "b1")],
                [call("send", {"to": "issue", "body": "t46-b"}, "b2")],
            ],
        )
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            first, first_runner = await _session(flow, "first")
            second, second_runner = await _session(flow, "second")
            updates = await asyncio.wait_for(
                asyncio.gather(first_runner.advance(), second_runner.advance()), 90
            )
            # 두 Task가 돈 뒤에도 CODEX_HOME 구성은 시작 때와 같다.
            receipt = await flow.tasks._verify(None)  # type: ignore[arg-type]
            folders = dict(flow.tasks.task_dirs)
            await flow.close()
            return (
                [update.status for update in updates],
                receipt,
                folders,
                flow.counts("send", "t46-a"),
                flow.counts("send", "t46-b"),
                dict(flow.tasks.exits),
            )

    statuses, receipt, folders, first, second, exits = asyncio.run(scenario())
    assert statuses == ["complete", "complete"]
    assert receipt is not None
    assert len(set(folders.values())) == 2
    assert all(not folder.exists() for folder in folders.values())
    assert first == ALLOWED
    assert second == ALLOWED
    assert set(exits.values()) == {True}


@pytest.mark.parametrize("kind", ["stdio", "http"])
def test_t46_a_model_auth_failure_stops_only_that_task(tmp_path, kind, pin, dispatched):
    async def scenario():
        scripts = in_order(
            [
                [call("read", {"path": "public/a.txt"}, "a1")],
                [call("send", {"to": "issue", "body": "t46-ok"}, "a2")],
            ],
            [
                [call("read", {"path": "public/b.txt"}, "b1")],
                [call("send", {"to": "issue", "body": "t46-auth"}, "b2")],
            ],
        )
        async with codex_flow(
            tmp_path, kind, pin, dispatched, scripts, wrong_token_launches=frozenset({1})
        ) as flow:
            first, first_runner = await _session(flow, "first")
            second, second_runner = await _session(flow, "second")
            results = await asyncio.wait_for(
                asyncio.gather(
                    first_runner.advance(), second_runner.advance(), return_exceptions=True
                ),
                120,
            )
            snapshots = [await flow.bindings.snapshot(task) for task in (first, second)]
            return (
                results,
                snapshots,
                flow.counts("send", "t46-ok"),
                flow.counts("send", "t46-auth"),
                flow.tasks.models[second].unauthorized,
            )

    results, snapshots, ok, failed, unauthorized = asyncio.run(scenario())
    assert getattr(results[0], "status", None) == "complete"
    assert snapshots[0].status is TaskStatus.RUNNING
    assert snapshots[1].status is not TaskStatus.RUNNING
    assert unauthorized >= 1
    assert ok == ALLOWED
    assert failed == ZERO


CODEX_TASK_DBS = {"goals_1", "logs_2", "memories_1", "queue_1", "state_5"}
# write_codex_home가 쓰는 것과 Codex가 처음 실행 때 만드는 두 파일(C6.13·C6.19 허용 항목)
CODEX_HOME_ENTRIES = {"config.toml", "model_catalog.json", "installation_id", ".sandbox_migration"}


@pytest.mark.parametrize("kind", ["stdio"])
def test_c616_codex_state_goes_to_the_task_folder_and_leaves_with_it(
    tmp_path, kind, pin, dispatched
):
    """C6.16: Codex의 DB 다섯 개는 Task 폴더(`sqlite_home`)에 생기고 CODEX_HOME에는 남지 않는다."""

    async def scenario():
        scripts = in_order([[call("read", {"path": "public/a.txt"}, "a1")]])
        async with codex_flow(tmp_path, kind, pin, dispatched, scripts) as flow:
            task_id, runner = await _session(flow, "read")
            await asyncio.wait_for(runner.advance(), 60)
            folder = flow.tasks.task_dirs[task_id]
            task_dbs = {path.stem for path in folder.glob("*.sqlite")}
            home = flow.run.folders.paths.codex_home
            during_home = {path.name for path in home.iterdir()}
            await flow.close()
            after_home = {path.name for path in home.iterdir()}
            return task_dbs, during_home, after_home, folder.exists()

    task_dbs, during_home, after_home, folder_left = asyncio.run(scenario())
    assert task_dbs == CODEX_TASK_DBS
    assert during_home <= CODEX_HOME_ENTRIES
    assert after_home <= CODEX_HOME_ENTRIES
    assert not any(name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")) for name in after_home)
    assert folder_left is False
