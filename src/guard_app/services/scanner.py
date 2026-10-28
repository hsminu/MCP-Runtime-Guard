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

기록을 시작하기 전에 사용자 취소나 앱 종료로 검사가 취소되면 보고서를 기록하지 않는다. 그 검사로는
활성화할 수 없고, 다시 검사하면 된다. 진행 중인 LLM 요청은 바로 끊기고, YARA는 native 상한 안에
끝난다. 기록 중에 취소되면 기록이 남을 수 있다(B6.13 뒤 저장소는 시작한 쓰기를 끝낸다). 그 서버는
결과 불명 표시가 되므로(C4.19) 화면은 `ServerRegistry.refresh`로 다시 읽는다.

`scan`이 올리는 오류는 `ScanError`(고정 코드)와 취소뿐이다.

| 코드 | 뜻 | 화면 |
| --- | --- | --- |
| `SCAN_QUEUE_FULL` | 대기 8건이 참. 접수하지 않음 | 나중에 다시 |
| `SCAN_SERVICE_CLOSED` | 앱 종료로 닫힘. 기록하지 않음 | 없음(종료 중) |
| `SCAN_SERVER_UNKNOWN` | 등록되지 않은 서버 | 등록부터 |
| `SCAN_LISTING_NOT_REGISTERED` | 목록이 지금 등록과 다름 | 목록을 다시 받음 |
| `REG_LISTING_*` | 목록을 지문으로 만들 수 없음 | 연결 문제 |
| `SCAN_JOB_TIMEOUT` | 검사기 연결이 멈춤. 기록하지 않음 | 다시 검사 |
| `SCAN_RECORD_UNKNOWN` | 저장 결과를 모름 | `refresh` 뒤 다시 검사(새 `scan_id`) |
| 그 밖의 `SCAN_*` | 번역·입력 오류 | 다시 검사 |

`asyncio.Lock`·`Semaphore`는 처음 쓰인 이벤트 루프에 묶인다. 앱의 백엔드 루프 안에서 하나 만들고
그 루프에서만 부른다.

목록 지문이 지금 등록과 다르면 검사하지 않는다(`SCAN_LISTING_NOT_REGISTERED`). 그 보고서로는
활성화 대조를 통과할 수 없기 때문이다. 오류는 고정 코드이고 서버 텍스트를 싣지 않는다.
"""

from __future__ import annotations

import asyncio
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
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.security.ids import new_id
from guard_app.security.secrets import scanner_key_environ
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


def cisco_connector(
    environ: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
) -> ConnectScanner:
    """제품 검사기 연결. 키는 넘긴 값으로만 받고 `.env`·프로세스 환경을 읽지 않는다.

    기본(None)은 검사를 열 때마다 Windows 자격증명 관리자에서 읽는다(`scanner_key_environ`,
    C2.10·C2.20). 그래서 화면에서 바꾼 키가 다음 검사부터 쓰이고, 키가 없으면 LLM은
    `SCAN_LLM_KEY_MISSING_OR_INVALID`로 준비 실패하고 YARA만 돈다. 테스트는 고정 값(mapping)이나
    읽는 함수를 넘긴다.
    """
    if environ is None:
        source: Callable[[], Mapping[str, str]] = scanner_key_environ
    elif callable(environ):
        source = environ
    else:
        fixed = dict(environ)

        def source() -> Mapping[str, str]:
            return fixed

    def connect(worker: Executor) -> AbstractAsyncContextManager[InternalMetadataScanner]:
        return connect_metadata_scanner(environ=dict(source()), yara_executor=worker)

    return connect


class ScanService:
    """앱 전체 검사 대기열과 예산. `connect`는 YARA 작업자를 받아 검사기를 여는 함수다.

    제품은 `cisco_connector()`를 넘긴다(키는 검사마다 자격증명 관리자에서). 끝낼 때 `close`로 YARA
    작업자를 정리한다.
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
        self._closed = False

    async def scan(
        self,
        server_id: str,
        listing: ToolListingView,
        *,
        on_started: Callable[[], None] | None = None,
    ) -> ScanOutcome:
        """목록을 검사하고 기록한다. 오류는 모듈 설명의 `ScanError` 코드다.

        `on_started`는 대기가 끝나 검사를 시작할 때 한 번 부른다(대기 중·검사 중 표시용). 예외를
        내지 않는 동기 함수여야 한다.
        """
        self._require_open()
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
            self._require_open()
            return await self._run(server_id, listing, on_started)
        finally:
            self._turn.release()

    @property
    def waiting(self) -> int:
        """대기 중인 검사 수. 진행 중인 1건은 세지 않는다."""
        return self._waiting

    def close(self) -> None:
        """닫고 YARA 작업자를 정리한다. 여러 번 불러도 된다.

        뒤의 `scan`과 대기 중이던 검사는 `SCAN_SERVICE_CLOSED`다. 진행 중인 검사도 기록하지 않고
        같은 코드로 끝난다. 진행 중인 native YARA는 상한(5초) 안에 끝나며, 그 스레드 때문에
        프로세스 종료가 그만큼 늦어질 수 있다.
        """
        self._closed = True
        self._yara_worker.shutdown(wait=False, cancel_futures=True)

    def _require_open(self) -> None:
        if self._closed:
            raise ScanError("SCAN_SERVICE_CLOSED")

    async def _run(
        self,
        server_id: str,
        listing: ToolListingView,
        on_started: Callable[[], None] | None,
    ) -> ScanOutcome:
        # 기다리는 사이 정의가 바뀌었을 수 있다.
        tools = self._registered_tools(server_id, listing)
        if on_started is not None:
            on_started()
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
        except RuntimeError:
            # 닫힌 작업자에 YARA를 맡기면 executor가 RuntimeError를 낸다.
            self._require_open()
            raise
        self._require_open()
        try:
            report = scan_report(result, list(tools.items()), scanned_at_utc=self._clock())
        except ScanReportError as error:
            raise ScanError(str(error)) from None
        scan_id = new_id("scan")
        try:
            entry = await self._registry.record_scan(scan_id, report)
        except RegistryError as error:
            code = (
                "SCAN_SERVER_UNKNOWN"
                if str(error) == "REG_SERVER_UNKNOWN"
                else "SCAN_REPORT_INVALID"
            )
            raise ScanError(code) from None
        except StoreError as error:
            # 활성화 대조와 같은 기준으로 B가 지문 다름을 거부한다. 그 밖에는 써졌는지 모른다.
            changed = error.reason is Reason.TOOL_FINGERPRINT_CHANGED
            raise ScanError(
                "SCAN_LISTING_NOT_REGISTERED" if changed else "SCAN_RECORD_UNKNOWN"
            ) from None
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
