"""서버 정의 검사 입구와 앱 전체 예산(C4.14, 2026-10-15 Scanner 운영 상한 결정).

`ScanService.scan`은 등록된 서버의 목록을 검사해 B `ScanReport`로 저장하고 `scan_id`를 돌려준다.
A 등록 화면은 그 보고서로 경고를 보여 주고, 사용자가 확인하면 `ServerRegistry.activate`에
`reviewed_scan_id`로 넘긴다. 검사는 서버 상태를 바꾸지 않는다.

예산은 앱 전체에 하나다.

- 서버 검사는 한 번에 1건, 대기는 8건까지다. 넘는 요청은 검사·기록 없이 `SCAN_QUEUE_FULL`이다.
- 작업 시간은 실행 시작부터 10분이다(대기 시간 제외). 넘으면 끝난 결과는 남기고 나머지는
  `SCAN_INCOMPLETE`로 미완료 보고서를 기록한다.
- LLM 호출은 동시에 4개, YARA는 전용 작업자 1개에서 도구마다 native 5초 상한으로 돈다.
- 자동 재시도·주기 재검사·결과 캐시는 없다.

사용자 취소나 앱 종료로 검사가 취소되면 보고서를 기록하지 않는다. 그 검사로는 활성화할 수 없고,
다시 검사하면 된다. 진행 중인 LLM 요청은 바로 끊기고, YARA는 native 상한 안에 끝난다.

목록 지문이 지금 등록과 다르면 검사하지 않는다(`SCAN_LISTING_NOT_REGISTERED`). 그 보고서로는
활성화 대조를 통과할 수 없기 때문이다. 오류는 고정 코드이고 서버 텍스트를 싣지 않는다.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from guard_app.adapters.cisco_aggregation import (
    InternalMetadataScanner,
    ScannerAggregationError,
    ScanReportError,
    connect_metadata_scanner,
    scan_report,
)
from guard_app.domain.contracts import RegistryEntry, ScanReport
from guard_app.services.registry import (
    RegistryError,
    ServerRegistry,
    ToolListingView,
    listing_fingerprints,
)

SCAN_QUEUE_LIMIT = 8
SCAN_JOB_SECONDS = 600.0
SCAN_LLM_CONCURRENCY = 4
# 검사기는 작업 시간에 맞춰 스스로 끝난다. 연결이 멈춘 경우에만 이 여유 뒤에 끊는다.
_HARD_STOP_GRACE_S = 30.0

ConnectScanner = Callable[[Executor], AbstractAsyncContextManager[InternalMetadataScanner]]


class ScanError(RuntimeError):
    """검사를 기록하지 않은 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    """기록한 검사. `entry`는 `last_scan_id`가 반영된 Registry 저장 사본이다."""

    scan_id: str
    report: ScanReport
    entry: RegistryEntry


def _utc_now() -> datetime:
    return datetime.now(UTC)


def cisco_connector(environ: Mapping[str, str] | None = None) -> ConnectScanner:
    """제품 검사기 연결. 키는 `environ`으로만 받고 `.env`·프로세스 환경을 읽지 않는다.

    C2.10(자격증명 관리자) 전에는 빈 값이라 LLM은 키 없음으로 준비 실패, YARA만 돈다.
    """
    values = dict(environ or {})

    def connect(worker: Executor) -> AbstractAsyncContextManager[InternalMetadataScanner]:
        return connect_metadata_scanner(environ=values, yara_executor=worker)

    return connect


class ScanService:
    """앱 전체 검사 대기열과 예산. `connect`는 YARA 작업자를 받아 검사기를 여는 함수다.

    제품은 `cisco_connector(environ)`을 넘긴다. 끝낼 때 `close`로 YARA 작업자를 정리한다.
    """

    def __init__(
        self,
        registry: ServerRegistry,
        connect: ConnectScanner,
        *,
        clock: Callable[[], datetime] = _utc_now,
        queue_limit: int = SCAN_QUEUE_LIMIT,
        job_seconds: float = SCAN_JOB_SECONDS,
        llm_concurrency: int = SCAN_LLM_CONCURRENCY,
    ) -> None:
        if (
            type(queue_limit) is not int
            or queue_limit < 0
            or type(job_seconds) is not float
            or not job_seconds > 0
            or type(llm_concurrency) is not int
            or llm_concurrency < 1
        ):
            raise ScanError("SCAN_BUDGET_INVALID")
        self._registry = registry
        self._connect = connect
        self._clock = clock
        self._queue_limit = queue_limit
        self._job_seconds = job_seconds
        self._llm_concurrency = llm_concurrency
        self._llm_slots = asyncio.Semaphore(llm_concurrency)
        self._yara_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guard-yara")
        self._turn = asyncio.Lock()
        self._waiting = 0

    async def scan(self, server_id: str, listing: ToolListingView) -> ScanOutcome:
        """목록을 검사하고 기록한다. 대기열이 차면 `SCAN_QUEUE_FULL`, 기록 전 오류는 고정 코드다."""
        self._registered_tools(server_id, listing)
        # 깨어난 대기자가 아직 lock을 잡기 전에도 대기 수로 센다.
        if self._waiting >= self._queue_limit and (self._turn.locked() or self._waiting > 0):
            raise ScanError("SCAN_QUEUE_FULL")
        self._waiting += 1
        try:
            await self._turn.acquire()
        finally:
            self._waiting -= 1
        try:
            return await self._run(server_id, listing)
        finally:
            self._turn.release()

    @property
    def waiting(self) -> int:
        """대기 중인 검사 수. 진행 중인 1건은 세지 않는다."""
        return self._waiting

    def close(self) -> None:
        """YARA 작업자를 정리한다. 진행 중인 native 검사는 상한 안에 끝난다."""
        self._yara_worker.shutdown(wait=False, cancel_futures=True)

    async def _run(self, server_id: str, listing: ToolListingView) -> ScanOutcome:
        # 기다리는 사이 정의가 바뀌었을 수 있다.
        tools = self._registered_tools(server_id, listing)
        deadline = asyncio.get_running_loop().time() + self._job_seconds
        try:
            async with (
                asyncio.timeout_at(deadline + _HARD_STOP_GRACE_S),
                self._connect(self._yara_worker) as scanner,
            ):
                result = await scanner.scan(
                    server_id,
                    listing.tools,
                    deadline=deadline,
                    tool_concurrency=self._llm_concurrency,
                    llm_slots=self._llm_slots,
                )
        except TimeoutError:
            raise ScanError("SCAN_JOB_TIMEOUT") from None
        except ScannerAggregationError as error:
            raise ScanError(str(error)) from None
        try:
            report = scan_report(result, list(tools.items()), scanned_at_utc=self._clock())
        except ScanReportError as error:
            raise ScanError(str(error)) from None
        scan_id = f"scan-{secrets.token_hex(16)}"
        entry = await self._registry.record_scan(scan_id, report)
        return ScanOutcome(scan_id, report, entry)

    def _registered_tools(self, server_id: str, listing: ToolListingView) -> dict[str, str]:
        registration = self._registry.registration(server_id)
        if registration is None:
            raise ScanError("SCAN_SERVER_UNKNOWN")
        try:
            fingerprints = listing_fingerprints(listing)
        except RegistryError as error:
            raise ScanError(str(error)) from None
        registered = {tool.tool_name: tool.fingerprint for tool in registration.tools}
        if fingerprints != registered:
            raise ScanError("SCAN_LISTING_NOT_REGISTERED")
        return fingerprints
