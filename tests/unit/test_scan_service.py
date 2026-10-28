"""services.scanner.ScanService(C4.14) 단위 테스트. 실제 집계기·SQLiteStore·Registry에 분석기 대역.

예산은 2026-10-15 결정값이다. 서버 검사 1건·대기 8건, 작업 10분, LLM 동시 4개, YARA 작업자 1개.
실제 SDK·native YARA 상한은 tests/contract/test_scan_service_sdk.py와 adapters 테스트가 본다.
"""

import asyncio
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard_app.adapters.cisco_aggregation import InternalMetadataScanner
from guard_app.adapters.mcp_transport import ToolListing
from guard_app.domain.enums import RegStatus, ScanErrorCode
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services import scanner as scanner_module
from guard_app.services.catalog import ServerSettings
from guard_app.services.registry import RegistryError, ServerRegistry
from guard_app.services.scanner import ScanError, ScanService
from guard_app.storage import SQLiteStore

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
SCAN_ID = re.compile(r"scan-[0-9a-f]{32}")

SERVER = {
    "id": "docs",
    "transport": "stdio",
    "command": "C:/PRIVATE_COMMAND/python.exe",
    "tier": "internal",
    "max_label": "CONFIDENTIAL",
    "tools": [{"name": "tool_0", "kind": "read", "resource_arg": "path"}],
}


def listing(count=3, *, changed=None):
    tools = []
    for index in range(count):
        definition = {"name": f"tool_{index}", "inputSchema": {"type": "object"}}
        if index == changed:
            definition["description"] = "changed"
        tools.append(FrozenJsonObject(definition))
    return ToolListing(complete=True, tools=tuple(tools), reason=None)


class Llm:
    """동시 호출 수를 재고, `hold`에 든 도구는 풀어 줄 때까지 붙잡는다."""

    def __init__(self, hold=(), delay=0.0):
        self.hold = set(hold)
        self.delay = delay
        self.release = asyncio.Event()
        self.holding = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.calls = 0
        self.cancelled = 0

    async def analyze_prepared(self, prepared):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if json.loads(prepared.definition_json)["name"] in self.hold:
                self.holding.set()
                await self.release.wait()
            await asyncio.sleep(self.delay)
            return []
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


class Yara:
    """받은 작업자에서 동기로 돈다. 스레드 안의 동시 실행 수를 잰다."""

    def __init__(self, worker, delay=0.02):
        self.worker = worker
        self.delay = delay
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = 0

    def _match(self):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return []

    async def analyze_prepared(self, prepared):
        return await asyncio.get_running_loop().run_in_executor(self.worker, self._match)


class Harness:
    def __init__(self, store: SQLiteStore, llm: Llm | None = None):
        self.registry = ServerRegistry(store, clock=lambda: NOW)
        self.llm = llm or Llm()
        self.yara = None
        self.opened = 0
        self.connect_gate: asyncio.Event | None = None

    def connect(self, worker):
        @asynccontextmanager
        async def opened():
            self.opened += 1
            if self.connect_gate is not None:
                await self.connect_gate.wait()
            self.yara = self.yara or Yara(worker)
            yield InternalMetadataScanner(self.llm, self.yara)

        return opened()

    def service(self, **budget):
        return ScanService(self.registry, self.connect, clock=lambda: NOW, **budget)

    async def register(self, count=3):
        await self.registry.load()
        server = ServerSettings.model_validate_json(json.dumps(SERVER), strict=True)
        return await self.registry.register(server, listing(count))


async def _settle():
    """준비된 Task가 모두 첫 대기 지점까지 가게 몇 번 양보한다."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def store(tmp_path: Path):
    sequence = iter(range(100_000))
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as instance:
        yield instance


def test_scan_is_recorded_and_used_to_activate(store):
    async def scenario():
        harness = Harness(store)
        await harness.register()
        service = harness.service()
        try:
            outcome = await service.scan("docs", listing())
        finally:
            service.close()
        assert SCAN_ID.fullmatch(outcome.scan_id)
        assert outcome.entry.last_scan_id == outcome.scan_id
        assert harness.registry.entry("docs") == outcome.entry
        assert outcome.report.analyzers_completed == ("LLM", "YARA")
        assert harness.registry.status("docs") is RegStatus.PENDING
        active = await harness.registry.activate(
            "docs",
            expected_row_revision=outcome.entry.registration.row_revision,
            reviewed_by="operator",
            reviewed_scan_id=outcome.scan_id,
        )
        assert active.registration.status is RegStatus.ACTIVE

    asyncio.run(scenario())


def test_listing_must_be_the_registered_one_before_any_analysis(store):
    async def scenario():
        harness = Harness(store)
        await harness.register()
        service = harness.service()
        try:
            for server_id, observed, code in (
                ("docs", listing(changed=1), "SCAN_LISTING_NOT_REGISTERED"),
                ("docs", listing(2), "SCAN_LISTING_NOT_REGISTERED"),
                ("other", listing(), "SCAN_SERVER_UNKNOWN"),
                (
                    "docs",
                    ToolListing(complete=False, tools=(), reason="TOOLS_LIST_TIMEOUT"),
                    "REG_LISTING_INCOMPLETE",
                ),
            ):
                with pytest.raises(ScanError) as raised:
                    await service.scan(server_id, observed)
                assert str(raised.value) == code
        finally:
            service.close()
        assert harness.opened == 0
        assert harness.llm.calls == 0
        assert harness.registry.entry("docs").last_scan_id is None

    asyncio.run(scenario())


def test_llm_runs_at_most_four_at_once_and_yara_on_one_worker(store):
    async def scenario():
        harness = Harness(store, Llm(delay=0.05))
        await harness.register(count=12)
        service = harness.service()
        try:
            outcome = await service.scan("docs", listing(12))
        finally:
            service.close()
        assert harness.llm.calls == 12
        assert harness.llm.peak == 4
        assert harness.yara.calls == 12
        assert harness.yara.peak == 1
        assert outcome.report.analyzers_completed == ("LLM", "YARA")

    asyncio.run(scenario())


def test_one_scan_runs_and_eight_wait_then_the_queue_is_full(store):
    async def scenario():
        harness = Harness(store)
        harness.connect_gate = asyncio.Event()
        await harness.register()
        service = harness.service()
        try:
            tasks = [asyncio.create_task(service.scan("docs", listing())) for _ in range(9)]
            await _settle()
            assert (harness.opened, service.waiting) == (1, 8)
            with pytest.raises(ScanError) as raised:
                await service.scan("docs", listing())
            assert str(raised.value) == "SCAN_QUEUE_FULL"
            assert harness.opened == 1  # 대기 중인 8건은 아직 열리지 않았다
            harness.connect_gate.set()
            outcomes = await asyncio.gather(*tasks)
        finally:
            service.close()
        assert service.waiting == 0
        assert len({outcome.scan_id for outcome in outcomes}) == 9
        assert harness.opened == 9

    asyncio.run(scenario())


def test_cancel_while_waiting_frees_the_place(store):
    async def scenario():
        harness = Harness(store)
        harness.connect_gate = asyncio.Event()
        await harness.register()
        service = harness.service(queue_limit=1)
        try:
            running = asyncio.create_task(service.scan("docs", listing()))
            waiting = asyncio.create_task(service.scan("docs", listing()))
            await _settle()
            assert service.waiting == 1
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert service.waiting == 0
            again = asyncio.create_task(service.scan("docs", listing()))
            await _settle()
            assert service.waiting == 1
            harness.connect_gate.set()
            await asyncio.gather(running, again)
        finally:
            service.close()

    asyncio.run(scenario())


def test_job_time_limit_records_finished_results_and_marks_the_rest_incomplete(store):
    async def scenario():
        harness = Harness(store, Llm(hold={"tool_2", "tool_3", "tool_4", "tool_5"}))
        await harness.register(count=6)
        service = harness.service(job_seconds=0.3)
        started = asyncio.get_running_loop().time()
        try:
            outcome = await service.scan("docs", listing(6))
        finally:
            service.close()
        assert asyncio.get_running_loop().time() - started < 2.0
        report = outcome.report
        assert report.analyzers_completed == ("YARA",)
        assert report.analyzers_failed == ("LLM",)
        incomplete = {d.tool_name for d in report.analyzer_diagnostics}
        assert incomplete == {"tool_2", "tool_3", "tool_4", "tool_5"}
        assert {d.code for d in report.analyzer_diagnostics} == {ScanErrorCode.SCAN_INCOMPLETE}
        assert harness.llm.cancelled == 4
        assert harness.registry.entry("docs").last_scan_id == outcome.scan_id

    asyncio.run(scenario())


def test_job_limit_keeps_unstarted_tools_as_not_run(store):
    async def scenario():
        held = {f"tool_{index}" for index in range(4)}
        harness = Harness(store, Llm(hold=held))
        await harness.register(count=6)
        service = harness.service(job_seconds=0.2)
        try:
            outcome = await service.scan("docs", listing(6))
        finally:
            service.close()
        # 4개가 붙잡혀 tool_4·tool_5는 시작하지 못했다. 두 분석기 모두 그 도구에서 미완료다.
        report = outcome.report
        assert report.analyzers_failed == ("LLM", "YARA")
        unstarted = {
            (d.analyzer, d.tool_name)
            for d in report.analyzer_diagnostics
            if d.tool_name in {"tool_4", "tool_5"}
        }
        assert unstarted == {(a, t) for a in ("LLM", "YARA") for t in ("tool_4", "tool_5")}

    asyncio.run(scenario())


def test_cancelled_scan_records_nothing_and_releases_the_turn(store):
    async def scenario():
        harness = Harness(store, Llm(hold={"tool_1"}))
        await harness.register()
        service = harness.service()
        try:
            task = asyncio.create_task(service.scan("docs", listing()))
            await asyncio.wait_for(harness.llm.holding.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert harness.llm.cancelled >= 1
            assert harness.registry.entry("docs").last_scan_id is None
            harness.llm.hold.clear()
            outcome = await service.scan("docs", listing())
        finally:
            service.close()
        assert outcome.entry.last_scan_id == outcome.scan_id

    asyncio.run(scenario())


def test_stuck_scanner_connection_is_stopped_without_a_record(store, monkeypatch):
    monkeypatch.setattr(scanner_module, "_HARD_STOP_GRACE_S", 0.1)

    async def scenario():
        harness = Harness(store)
        harness.connect_gate = asyncio.Event()  # 연결이 끝나지 않는다
        await harness.register()
        service = harness.service(job_seconds=0.1)
        try:
            with pytest.raises(ScanError) as raised:
                await service.scan("docs", listing())
        finally:
            service.close()
        assert str(raised.value) == "SCAN_JOB_TIMEOUT"
        assert harness.registry.entry("docs").last_scan_id is None

    asyncio.run(scenario())


def test_closed_service_refuses_new_and_waiting_scans_without_records(store):
    """#59 A 리뷰 1번: 닫힌 뒤와 닫히는 도중의 오류도 고정 코드다."""

    async def scenario():
        harness = Harness(store)
        harness.connect_gate = asyncio.Event()
        await harness.register()
        service = harness.service()
        running = asyncio.create_task(service.scan("docs", listing()))
        waiting = asyncio.create_task(service.scan("docs", listing()))
        await _settle()
        service.close()
        service.close()  # 여러 번 불러도 된다
        harness.connect_gate.set()
        results = await asyncio.gather(running, waiting, return_exceptions=True)
        assert [str(result) for result in results] == ["SCAN_SERVICE_CLOSED"] * 2
        assert all(type(result) is ScanError for result in results)
        with pytest.raises(ScanError) as raised:
            await service.scan("docs", listing())
        assert str(raised.value) == "SCAN_SERVICE_CLOSED"
        assert harness.registry.entry("docs").last_scan_id is None

    asyncio.run(scenario())


def test_scan_closed_after_its_analyzers_finished_is_not_recorded(store):
    async def scenario():
        harness = Harness(store, Llm(hold={"tool_0"}))
        await harness.register()
        service = harness.service()
        task = asyncio.create_task(service.scan("docs", listing()))
        await asyncio.wait_for(harness.llm.holding.wait(), 5)
        service.close()
        harness.llm.release.set()
        with pytest.raises(ScanError) as raised:
            await task
        assert str(raised.value) == "SCAN_SERVICE_CLOSED"
        assert harness.registry.entry("docs").last_scan_id is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (StoreError(Reason.TOOL_FINGERPRINT_CHANGED), "SCAN_LISTING_NOT_REGISTERED"),
        (StoreError(), "SCAN_RECORD_UNKNOWN"),
        (RegistryError("REG_SERVER_UNKNOWN"), "SCAN_SERVER_UNKNOWN"),
        (RegistryError("REG_SCAN_INVALID"), "SCAN_REPORT_INVALID"),
    ],
    ids=["fingerprint-changed", "store-unknown", "server-unknown", "report-invalid"],
)
def test_record_failures_become_scan_errors(store, monkeypatch, error, code):
    """#59 A 리뷰 2번·B 리뷰 2번: `scan`이 올리는 것은 `ScanError`뿐이다."""

    async def scenario():
        harness = Harness(store)
        await harness.register()

        async def failing(scan_id, report):
            raise error

        monkeypatch.setattr(harness.registry, "record_scan", failing)
        service = harness.service()
        try:
            with pytest.raises(ScanError) as raised:
                await service.scan("docs", listing())
        finally:
            service.close()
        assert str(raised.value) == code
        assert raised.value.__cause__ is None

    asyncio.run(scenario())


def test_on_started_marks_the_turn_not_the_request(store):
    """#59 A 리뷰 3번: 대기 중과 검사 중을 화면이 나눌 수 있게 차례가 온 때만 알린다."""

    async def scenario():
        harness = Harness(store)
        harness.connect_gate = asyncio.Event()
        await harness.register()
        service = harness.service()
        started = []
        try:
            first = asyncio.create_task(
                service.scan("docs", listing(), on_started=lambda: started.append("first"))
            )
            second = asyncio.create_task(
                service.scan("docs", listing(), on_started=lambda: started.append("second"))
            )
            await _settle()
            assert started == ["first"]
            assert service.waiting == 1
            harness.connect_gate.set()
            await asyncio.gather(first, second)
        finally:
            service.close()
        assert started == ["first", "second"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "budget",
    [
        {"queue_limit": -1},
        {"queue_limit": 8.0},
        {"job_seconds": 0.0},
        {"job_seconds": 600},
        {"llm_concurrency": 0},
    ],
)
def test_budget_values_are_checked(store, budget):
    harness = Harness(store)
    with pytest.raises(ScanError) as raised:
        harness.service(**budget)
    assert str(raised.value) == "SCAN_BUDGET_INVALID"


def test_defaults_are_the_decided_values():
    assert scanner_module.SCAN_QUEUE_LIMIT == 8
    assert scanner_module.SCAN_JOB_SECONDS == 600.0
    assert scanner_module.SCAN_LLM_CONCURRENCY == 4
