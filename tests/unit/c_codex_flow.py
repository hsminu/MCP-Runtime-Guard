"""C Codex 경유 하니스(C6.8-2): 실제 codex.exe와 대본 가짜 모델로 화면 없이 끝까지 돌린다.

- **모델:** C6.14의 `MockModel`에 대본을 준다. 요청 순서대로 정해 둔 도구 호출을 내고, 다 쓰면
  최종 답이다. OpenAI 계정은 쓰지 않는다.
- **Codex:** 고정 0.162.1 `codex.exe`(`GUARD_TEST_CODEX_EXE`). L1 `CODEX_HOME`을 쓰고 앱 시작 때
  실제 L2(`run_l2`)를 돌려 그 결과를 시작 영수증으로 넘긴다. Task마다 C launcher의 `l2_spec`으로
  그 Task 전용 가짜 모델에 붙인다.
- **A:** 제품 시작 `bootstrap_product`, 세션 관리자, Guard relay를 그대로 쓴다. 이 하니스는
  `SessionPreparation`과 `ProductInputs`만 채운다.
- **Guard·서버:** `c_counting`의 제품 조립 입력(C Registry 등록·활성화, 실제 stdio·HTTP
  서버)과 계수.

제품과 다른 점
- Store는 `bootstrap_product`가 여는 즉시 모드다(B6.8 명시 시작은 A138).
- Registry는 `c_counting`의 Store에 있고, call_seq·실패 감사는 하니스 안에서 센다. 제품
  `bootstrap_product`가 조립 입력을 받기 전에 Store를 만들기 때문이다(A138에서 맞출 일).
"""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from guard_app.adapters.codex_protocol import DynamicToolsPlan, thread_start_params
from guard_app.security.codex_home import load_codex_pins, verify_codex_home, write_codex_home
from guard_app.security.codex_l2 import MockModel, run_l2
from guard_app.security.ids import new_token
from guard_app.security.launcher import CodexLauncher, ExecutablePin, LaunchedProcess
from guard_app.security.paths import AppFolders
from guard_app.services.bridge_types import SessionOptions
from guard_app.services.codex_preparation import configuration_receipt
from guard_app.services.codex_prompt import BASE_INSTRUCTIONS
from guard_app.services.guard import FailureEvent
from guard_app.services.product_assembly import ProductInputs
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)
from guard_app.services.session_runtime import bootstrap_product

from .c_counting import Dispatch, Run, harness, identity

REAL_CODEX = os.environ.get("GUARD_TEST_CODEX_EXE")
requires_codex = pytest.mark.skipif(
    sys.platform != "win32" or not REAL_CODEX,
    reason="실제 codex.exe(GUARD_TEST_CODEX_EXE)와 Windows launcher가 필요하다",
)

Item = dict[str, object]
Script = list[list[Item]]


def call(name: str, arguments: dict[str, object], call_id: str) -> Item:
    """모델이 `docs` 서버의 Guard 도구를 부르는 항목(A `build_dynamic_tools` namespace)."""
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "namespace": "docs",
        "name": name,
        "arguments": json.dumps(arguments),
    }


def thread_params(tools: object) -> dict[str, object]:
    """L2가 쓰는 `thread/start` 인자. A 세션과 같은 모델·지시문·도구 형식이다."""
    plan = DynamicToolsPlan(tuple(tools), (), frozenset())  # type: ignore[arg-type]
    return thread_start_params(
        model=SessionOptions().model, base_instructions=BASE_INSTRUCTIONS, plan=plan
    )


@dataclass
class CodexTasks:
    """C 앱 폴더·CODEX_HOME·launcher 위의 `SessionPreparation`. Task마다 대본 모델 하나."""

    folders: AppFolders
    executable: Path
    scripts: Callable[[str], Script]
    # 이 순번(0부터)으로 띄우는 Task는 모델과 다른 토큰으로 띄운다(인증 실패, T46).
    wrong_token_launches: frozenset[int] = frozenset()
    models: dict[str, MockModel] = field(default_factory=dict)
    task_dirs: dict[str, Path] = field(default_factory=dict)
    launched: list[str] = field(default_factory=list)
    exits: dict[str, bool] = field(default_factory=dict)
    l2_failures: tuple[str, ...] = ()
    _launcher: CodexLauncher | None = None
    _digest: str = ""

    async def _startup(self) -> StartupReceipt:
        pins = load_codex_pins()
        self._digest = write_codex_home(self.folders.paths.codex_home)
        self._launcher = CodexLauncher(self.executable, self.folders.paths.codex_home, self._digest)
        report = await run_l2(self._launcher, self.folders, thread_params=thread_params)
        self.l2_failures = report.failures
        l2 = ConfigurationReceipt(report.home_digest, report.executable_sha256, report.model_sha256)
        return StartupReceipt(configuration_receipt(self._digest, pins), l2, report.passed)

    async def _verify(self, _expected: ConfigurationReceipt) -> ConfigurationReceipt:
        verify_codex_home(self.folders.paths.codex_home, self._digest)
        return configuration_receipt(self._digest, load_codex_pins())

    async def _prepare(self, task_id: str, expected: ConfigurationReceipt) -> TaskResources:
        assert self._launcher is not None
        script = self.scripts(task_id)
        token = new_token()
        model = MockModel(token, first_response=script[0], then=script[1:])
        port = await model.start()
        folder = self.folders.create_task_folder(task_id)
        self.models[task_id] = model
        self.task_dirs[task_id] = folder
        if len(self.launched) in self.wrong_token_launches:
            token = new_token()
        process = await self._launcher.launch_l2(folder, port, token)
        self.launched.append(task_id)
        return TaskResources(task_id, expected, f"owner-{task_id}", process)

    async def _confirm(self, resources: TaskResources) -> bool:
        process = resources.handle
        assert isinstance(process, LaunchedProcess)
        exited = await process.close()
        self.exits[resources.task_id] = exited
        return exited

    async def _cleanup(self, resources: TaskResources) -> None:
        model = self.models.get(resources.task_id)
        if model is not None:
            await model.close()
        folder = self.task_dirs.get(resources.task_id)
        if folder is not None and folder.exists():
            self.folders.remove_task_folder(folder)

    def preparation(self) -> SessionPreparation:
        return SessionPreparation(
            self._startup, self._verify, self._prepare, self._confirm, self._cleanup
        )


@dataclass
class Flow:
    run: Run
    tasks: CodexTasks
    bindings: object
    db: Path
    audits: list[FailureEvent]

    def approvals(self, status: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM approvals"
        args: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            args = (status,)
        with closing(sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)) as db:
            return int(db.execute(query, args).fetchone()[0])

    async def close(self) -> None:
        """모든 세션을 닫는다(Task 폴더 정리까지). 여러 번 불러도 된다."""
        await self.bindings.close()  # type: ignore[attr-defined]

    def files_containing(self, marker: str) -> list[str]:
        """앱 폴더 전체에서 표지(UTF-8·UTF-16LE)를 담은 파일. 경로는 앱 폴더 기준이다.

        단일 실행 잠금 `app.lock`은 잠긴 채 열려 있어 읽지 않는다(내용 없는 잠금 파일).
        """
        root = self.run.folders.paths.root
        needles = (marker.encode("utf-8"), marker.encode("utf-16-le"))
        found = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != root / "app.lock":
                data = path.read_bytes()
                if any(needle in data for needle in needles):
                    found.append(path.relative_to(root).as_posix())
        return found

    def audit_reasons(self, task_id: str) -> list[str]:
        """그 Task의 감사 사유 코드(원문 없음)."""
        with closing(sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)) as db:
            rows = db.execute(
                "SELECT reason_code FROM audit_events WHERE task_id = ? ORDER BY rowid", (task_id,)
            )
            return [row[0] for row in rows if row[0] is not None]

    def counts(self, tool: str, token: str) -> dict[str, int]:
        """승인 소비는 이 실행 전체, dispatch·wire·효과는 그 도구 호출의 인자 표지로 센다."""
        return {
            "approval_consumed": self.approvals("CONSUMED"),
            "host_dispatch": sum(
                1 for d in self.run.dispatched if d.tool == tool and d.token == token
            ),
            "wire": sum(1 for t in self.run.counters.wire_entries() if t == (tool, token)),
            "effect": sum(1 for t in self.run.counters.effect_entries() if t == (tool, token)),
        }


@asynccontextmanager
async def codex_flow(
    tmp_path: Path,
    kind: str,
    pin: ExecutablePin,
    dispatched: list[Dispatch],
    scripts: Callable[[str], Script],
    *,
    wrong_token_launches: frozenset[int] = frozenset(),
    call_timeout_s: float = 30.0,
    **harness_options: object,
) -> AsyncIterator[Flow]:
    assert REAL_CODEX is not None
    async with harness(tmp_path, kind, pin, dispatched, assemble=False, **harness_options) as run:
        tasks = CodexTasks(run.folders, Path(REAL_CODEX), scripts, wrong_token_launches)
        sequence = itertools.count(1)
        audits: list[FailureEvent] = []

        async def next_call_seq(_task_id: str) -> int:
            return next(sequence)

        async def failure_audit(event: FailureEvent) -> None:
            audits.append(event)

        inputs = ProductInputs(
            profile_document=run.document,
            registry=run.registry,
            server_streams={"docs": await run.connect()},
            next_call_seq=next_call_seq,
            failure_audit=failure_audit,
            operator_id="local-operator",
            resource_resolver=identity,
            call_timeout_s=call_timeout_s,
        )
        db = tmp_path / "flow.db"
        bindings = await bootstrap_product(str(db), preparation=tasks.preparation(), product=inputs)
        flow = Flow(run, tasks, bindings, db, audits)
        try:
            yield flow
        finally:
            await flow.close()
