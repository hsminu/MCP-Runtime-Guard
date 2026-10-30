"""B1 검토용 포트. 구현과 실제/Fake 계약 테스트는 각 담당과 C가 작성한다.

메서드 추가 필드·반환형은 검토 전 초안이다. SDK·SQL·UI 타입을 의존하지 않는다.
Store의 날짜와 ID는 호출자가 공급하며 포트에서 생성하지 않는다.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from guard_app.domain.contracts import (
    ApprovalCommit,
    ApprovalRecord,
    FingerprintChange,
    PendingFinal,
    RawResult,
    RegisteredServer,
    RegistryEntry,
    ScanReport,
    TaskIdentity,
    TrustedContext,
)
from guard_app.domain.enums import (
    ApprovalStatus,
    Label,
    RegStatus,
    RequestStatus,
    SessionEvent,
    StorePrepareStep,
    SystemStopReason,
    TaskStatus,
)
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import (
    AccessResult,
    FailureAuditRecord,
    MigrationReport,
    PolicyDecision,
    RecoveryItemPage,
    RecoveryReport,
    ResolvedRequest,
    StoreExecutionState,
    TaskState,
    ToolSpec,
)
from guard_app.domain.queries import (
    ApprovalListPage,
    ApprovalListQuery,
    CallDetail,
    CallListPage,
    CallListQuery,
)
from guard_app.domain.reasons import Reason
from guard_app.domain.schema_jobs import SchemaJobRequest, SchemaJobResponse


class SchemaJobRunner(Protocol):
    """SCHEMA-01: 제한된 별도 프로세스의 스키마 작업.

    runner는 assemble_guard보다 먼저 살아 있어야 한다. run은 성공한 응답을 반환하고,
    시간 초과는 SchemaJobRunnerError(TIMEOUT), 응답 상한 초과는 TOO_LARGE,
    미기동·비정상 종료·응답 불명은 UNAVAILABLE로 알린다. 다른 예외도 호출자는
    UNAVAILABLE로 보며 CancelledError는 전파한다. 늦은 결과는 버린다.

    Guard 종료 뒤 Store 종료 전에 aclose를 기다린다. True는 종료 확인, False는
    종료 미확인이며 CLEANUP_UNCONFIRMED로 나타낸다.
    """

    async def run(self, request: SchemaJobRequest, *, deadline: float) -> SchemaJobResponse: ...

    async def aclose(self) -> bool: ...


class StoreQueries(Protocol):
    """합의 전 제안(B6-05, #76): 화면의 권위 있는 저장 사실을 조회한다.

    각 조회는 한 읽기 트랜잭션에서 일관된 결과를 만든다.
    인자·결과·예외 등의 원문을 반환하지 않는다.
    각 행의 revision을 화면 명령의 기대값으로 전달한다.
    정렬과 페이지 크기는 고정된 조회 계약을 따른다.
    조회 실패는 결과 없음이 아닌 알 수 없음으로 표시한다.
    """

    async def list_approvals(self, query: ApprovalListQuery) -> ApprovalListPage: ...

    async def list_calls(self, query: CallListQuery) -> CallListPage: ...

    async def get_call(self, request_id: str) -> CallDetail | None:
        """COMMITTED는 성공·실패·결과 불명인 결과를 기록했다는 뜻이다.

        성공 여부는 CallRow.status로만 판단한다.
        """
        ...


class PendingResult(Protocol):
    """NOTIFY-01 합의 전 제안: 완료가 먼저여도 유실 없는 단일 소비 대기 핸들.

    wait 취소는 대기만 취소하고 승인·타이머·완료 결과를 취소하지 않는다.
    같은 핸들로 다시 대기할 수 있다. 동시/반복 소비는 고정 ValueError다.
    프로세스 재시작을 넘는 전달 보장은 없으며 원문을 저장하지 않는다.
    """

    async def wait(self) -> PendingFinal: ...


class PendingFinals(Protocol):
    """NOTIFY-01 합의 전 제안: 신뢰된 A 대기·C 지문 변경·앱 종료 입구.

    pending_result는 submit의 Pending 반환 직후 또는 완료 후 조회한다.
    사람 approve/reject 반환값을 모델에 중계하지 않고 이 경로만 사용한다.
    aclose는 신규 승인을 거부하고 미소비 공개 원문을 폐기한다. A는 종료 시
    transport 실행을 먼저 종결시켜 예약 후 결과가 확정되도록 해야 한다.
    """

    def pending_result(self, request_id: str) -> PendingResult: ...

    async def invalidate_tool(self, server_id: str, tool_name: str) -> int: ...

    async def cancel_pending(self, task_id: str, *, expected_epoch: int) -> int: ...

    async def aclose(self) -> None: ...


class BackupDestination(Protocol):
    """신뢰된 백업 생성자가 제공한 임시 대상과 발행·폐기 경계."""

    @property
    def path(self) -> Path: ...

    def publish(self) -> Path: ...

    def discard(self) -> None: ...


class StoreLifecycle(Protocol):
    """Store 실행 권한과 종료 상태를 SQL 큐를 기다리지 않고 조회한다(SEQ-02).

    execution_enabled=True는 Store 전체의 조건일 뿐 개별 Task의 run·epoch·상태·
    정책·승인 검사를 대신하지 않는다. A는 Denied 하나나 DB의 RUNNING 행만으로
    실행 가능을 판단하지 않는다. 종료 요청은 비차단이며 어느 스레드에서든 안전하고
    반복해도 된다. wait_closed는 timeout 내 연결 종료를 확인한 경우만 True다.
    """

    def execution_state(self) -> StoreExecutionState: ...

    def halt_execution(self, reason: Reason) -> None:
        """실행 권한을 멱등으로 닫는다. 예외를 내지 않는다."""
        ...

    def close(self) -> None:
        """비차단으로 종료를 요청한다. 예외를 내지 않는다."""
        ...

    async def wait_closed(self, timeout: float) -> bool: ...  # noqa: ASYNC109

    async def prepare_schema(
        self,
        *,
        deadline: float,
        begin_backup: Callable[[str], BackupDestination],
        on_step: Callable[[StorePrepareStep], None] | None = None,
    ) -> MigrationReport:
        """A가 넘긴 time.monotonic 기준 바깥 deadline 안에서 스키마를 준비한다.

        이관 ACK는 27b 복구 ACK와 별개다. on_step은 예외를 내지 않는 동기 함수다.
        """
        ...

    async def recover(
        self,
        *,
        deadline: float,
        run_id: str,
        on_step: Callable[[StorePrepareStep], None] | None = None,
    ) -> RecoveryReport:
        """복구를 완료하고 성공한 보고만 반환한다.

        run_id는 A가 생성한 신뢰 식별자다(예: run-과 128비트 난수의 16진수).
        prepare_schema의 이관 ACK와 이 복구 ACK는 서로 별개다.
        성공한 RecoveryReport만 화면의 복구 결과로 쓴다.
        """
        ...

    async def activate_run(self) -> StoreExecutionState: ...

    async def load_recovery_report(self, run_id: str) -> RecoveryReport | None: ...

    async def list_recovery_items(
        self, *, run_id: str | None = None, after: str | None = None, limit: int = 100
    ) -> RecoveryItemPage: ...


class SequenceAllocator(Protocol):
    """SEQ-02: Task별 순번과 감사의 커밋 ACK 뒤에만 값을 반환한다.

    배정 뒤 판정 저장 실패·취소·ACK 유실이 나도 순번을 회수하지 않는다.
    Task가 다른 경우 계수는 독립적이며 epoch 변경에도 초기화하지 않는다.
    """

    async def allocate_call_seq(self, task_id: str) -> int: ...


class FailureAuditStore(Protocol):
    """B-SQL-04: 예약 전후의 원문 없는 실패 감사 커밋을 제공한다."""

    async def record_failure_audit(self, record: FailureAuditRecord) -> None: ...


class SessionAudit(Protocol):
    """STOP-01 합의 전 제안. 구성 경로가 연결하는 내구성 사실 감사.

    Task·epoch·UTC·고정 사실/사유만 받고 원문·질문·예외는 받지 않는다.
    STOPPED에는 reason이 필요하고 REQUEST_USER_INPUT에는 reason이 없다.
    실패는 StoreError이며 ACK 전에 내구성 커밋해야 한다. 기존 Store는 유지한다.
    """

    async def record_session_event(
        self,
        identity: TaskIdentity,
        event: SessionEvent,
        *,
        at_utc: datetime,
        reason: SystemStopReason | None = None,
    ) -> None: ...


class Transport(Protocol):
    """A 구현. Guard만 호출하며 서버별 worker가 세션을 소유한다(9장).

    각 명령은 반드시 완료되거나 DispatchError로 종료된다. 메시지 원문은 예외에
    넣지 않는다. 알려진 무자료 실패만 RawResult.failed이며 결과 변환 등 예상 밖
    실패는 DispatchError다. 쓰기 자동 재전송과 late 결과 공개는 금지한다(I8).
    """

    async def call(self, req: ResolvedRequest) -> RawResult: ...


class AccessChecker(Protocol):
    """같은 프로세스에서 B의 순수 접근 규칙을 감싸는 동기 판정 포트다.

    판정기에서 I/O·대기·스레드·하위 프로세스를 사용하지 않는다. 일반 예외는 Guard의
    fail_closed 경계에서 DENY가 되며, AccessResult가 아닌 값은 error=True DENY다.
    판정기가 던진 CancelledError도 예약 전 실패로 보아 그 Task만 정지하고 DENY로 끝내며
    전파하지 않는다.
    오류 원문을 reason에 넣지 않는다. ALLOW는 거부권을 행사하지 않을 뿐 사람
    승인을 대체하거나 Guard의 ASK·DENY를 완화하지 않는다(8.5·10.3절).
    """

    def check_access(self, req: ResolvedRequest, ctx: TrustedContext) -> AccessResult: ...


class Scanner(Protocol):
    """C 구현. 도구 정의 metadata만 입력한다(10.2절).

    immutable tuple 입력은 list[dict] 골격 대신 택한 초안이다. 검사 완료·skip·failed와
    지문을 보고한다. 업무 본문·인자·인증정보를 보내지 않으며 파싱 실패는 통과가 아니다.
    """

    async def scan_tool_definitions(
        self, server_id: str, tools: tuple[FrozenJsonObject, ...]
    ) -> ScanReport: ...


class Registry(Protocol):
    """C 구현의 동기 조회. 원격 tools/list 대신 저장된 등록 레코드가 근거다.

    미등록은 None이다. 서비스는 None을 fail-closed로 처리한 뒤 PolicyInput을 만든다.
    지문 변경 시 PENDING 복귀와 기존 승인 무효화는 C의 변경 경로에서 보장한다(I10).
    """

    def registration(self, server_id: str) -> RegisteredServer | None: ...

    def status(self, server_id: str) -> RegStatus | None: ...

    def fingerprint(self, tool: ToolSpec) -> str | None: ...


class RegistryStore(Protocol):
    """B6-06 합의 전 제안. 신뢰된 A/C 제어 경로만 호출하는 단일 writer 저장.

    실제 변경 쓰기는 등록 revision +1·정의/검사·원문 없는 감사를 같은 트랜잭션에
    커밋하고 전체 사본을 반환한다. 무변경 지문 관측은 쓰지 않고 저장 사본을 반환한다.
    실패는 StoreError이며 원문을 담지 않는다.
    C는 A transport의 SDK 재구성 정의 dict
    (`model_dump(mode="json", by_alias=True, exclude_unset=True)`)로
    `tool_fingerprint`를 계산한다(FP-01). 원본 수신 JSON은 쓰지 않는다.
    저장소는 형식만 검사한다.

    C: 연결 직후 실제 정의 대조 → record_fingerprint_change 커밋 → 반환 사본으로
    동기 Registry 캐시 즉시 교체(동일 사본이면 생략, 반환과 교체 사이 await 금지) →
    changed_tools마다 await GuardService.invalidate_tool. 커밋 실패 시 그 서버를 사용하지 않는다.
    이 순서만으로 이미 진행 중인 예약/전송 경쟁(S03)이 해결되지는 않는다.

    A: 화면 확인 → prepare_server_access_change → 설정 문서 저장 →
    apply_policy_change 성공 → activate_server → 캐시 즉시 교체. 앞 단계 실패는
    PENDING 유지다. 활성화 실패 시 캐시는 PENDING/실행 차단을 유지한다. COMMIT ACK
    불명이면 DB는 ACTIVE일 수도 있어 자동 실행/재시도하지 않는다. 저장 후 정책 적용
    실패 시 Guard의 global halt를 유지한다. 역순 활성화는 이전 규칙과 겹쳐 금지한다.
    A는 정책 저장/적용·활성화·Task 생성을 직렬화하고 충돌 시 다시 확인받는다.

    재시작 list_registrations는 캐시 복원용이다. DB ACTIVE만으로 실행하지 않고
    연결마다 실제 tools/list와 다시 대조한다. C는 별도 SQL 경로를 만들지 않는다.
    """

    async def list_registrations(self) -> tuple[RegistryEntry, ...]: ...

    async def load_registration(self, server_id: str) -> RegistryEntry | None: ...

    async def register_server(
        self, registration: RegisteredServer, *, at_utc: datetime
    ) -> RegistryEntry:
        """PENDING·revision 0·빈 검토 설명 입력만 허용. 중복은 APPROVAL_RACE."""
        ...

    async def record_scan_report(
        self, scan_id: str, report: ScanReport, *, at_utc: datetime
    ) -> RegistryEntry:
        """현재 등록의 도구 지문 집합과 일치하는 검사만 저장한다(#59).

        검사는 상태를 바꾸지 않는다. SDK 원문 Finding.description은 받지 않는다.
        """
        ...

    async def activate_server(
        self,
        server_id: str,
        *,
        expected_row_revision: int,
        tool_fingerprints: Mapping[str, str],
        reviewed_by: str,
        at_utc: datetime,
        reviewed_scan_id: str | None = None,
    ) -> RegistryEntry:
        """PENDING만 활성화. 본 지문 전체 일치와 선택한 검사의 서버/지문 집합 검사.

        검사 경고·실패·미완료를 자동 차단/안전으로 번역하지 않는다. reviewed_by는
        모델·SDK 출력에서 가져오지 않는 신뢰된 단일 운영자 ID다.
        """
        ...

    async def record_fingerprint_change(
        self, server_id: str, tools: tuple[ToolSpec, ...], *, at_utc: datetime
    ) -> FingerprintChange:
        """revision 조건 없이 관측 적용. ACTIVE 변경은 PENDING, BLOCKED는 유지.

        새/변경 도구의 검토 설명은 비우고 관측 입력 설명으로 대체하지 않는다.
        정의·지문이 모두 같으면 revision·감사·상태를 바꾸지 않는다.
        changed_tools=()와 현재 저장 사본을 반환하며 목록 재정렬도 쓰지 않는다.
        """
        ...

    async def record_settings_change(
        self,
        server_id: str,
        tools: tuple[ToolSpec, ...],
        *,
        expected_row_revision: int,
        at_utc: datetime,
    ) -> FingerprintChange:
        """도구 설정 변경을 revision CAS로 기록한다(2026-10-28 B·C 합의, #46)."""
        ...

    async def block_server(
        self, server_id: str, *, expected_row_revision: int, at_utc: datetime
    ) -> RegistryEntry: ...

    async def delete_server(
        self, server_id: str, *, expected_row_revision: int, at_utc: datetime
    ) -> None:
        """BLOCKED만 삭제한다(2026-10-30 사용자 결정, A208).

        revision CAS로 등록·도구·검사 행을 한 트랜잭션에서 지우고,
        원문 없는 REGISTRY_DELETED 감사를 같은 트랜잭션에 쓴다.
        기존 호출·요청·승인·감사 행은 남긴다.
        """
        ...

    async def request_review(
        self, server_id: str, *, expected_row_revision: int, at_utc: datetime
    ) -> RegistryEntry: ...

    async def save_reviewed_descriptions(
        self,
        server_id: str,
        descriptions: Mapping[str, str],
        *,
        expected_row_revision: int,
        at_utc: datetime,
    ) -> RegistryEntry:
        """PENDING에서만 A의 검토 입력을 부분 갱신. 원본 자동 대체/활성화 없음."""
        ...


class Store(Protocol):
    """C 구현. 단일 writer 소유, 명시적 트랜잭션과 감사 내구성 보장(8.3·11장).

    I/O·감사·트랜잭션 실패는 원문 없는 StoreError로 전달한다. 상태 충돌은 성공으로
    보이지 않아야 한다. record_decision 입력의 인자는 메모리에서만 사용하고 DB에는
    arguments_digest 등 허용 메타데이터만 남긴다. 원문·snapshot은 저장하지 않는다.
    날짜는 UTC-aware여야 하며 생성·wire 계수 구현은 이 포트의 책임이 아니다.
    """

    async def create_task(self, state: TaskState, *, created_at_utc: datetime) -> None: ...

    async def load_task(self, task_id: str) -> TaskState:
        """없는 Task는 StoreError(STATE_MISSING). 빈 초기 상태로 대체하지 않는다."""
        ...

    async def record_decision(
        self,
        req: ResolvedRequest,
        decision: PolicyDecision,
        *,
        arguments_digest: str,
        at_utc: datetime,
    ) -> None:
        """요청 메타데이터·판정·감사를 원자 기록. ID/Task 순번 중복은 실패한다."""
        ...

    async def reserve_dispatch(self, request_id: str, *, now_utc: datetime) -> None:
        """ALLOW DECIDED 요청만 DISPATCHING으로 예약. 이미 예약됐으면 실패한다."""
        ...

    async def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        expected_row_revision: int,
        at_utc: datetime,
        reason: Reason,
    ) -> TaskState:
        """revision·허용 상태 전이를 검사한다. HALTED/UNKNOWN_OUTCOME 재개는 금지다."""
        ...

    async def open_approval(
        self, approval: ApprovalRecord, *, expected_task: TaskState
    ) -> ApprovalCommit:
        """합의 전 제안(B6-01, #76): 승인·요청·Task 대기 전이와 감사를 원자 기록.

        관계·epoch·revision을 조건부 검사한다. 충돌과 커밋 불명을 구분하고,
        결과가 불명이면 실행 권한을 닫는다(C 조건 1·3).
        """
        ...

    async def finish_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        expected_approval_revision: int,
        task_after: TaskStatus,
        expected_task_revision: int,
        reason: Reason,
        halt_reason: Reason | None = None,
        now_utc: datetime,
    ) -> ApprovalCommit:
        """합의 전 제안(B6-01, #76): 승인·요청 종결, Task 복귀/정지와 감사 원자 기록.

        관계·epoch·revision을 조건부 검사한다. 충돌과 커밋 불명을 구분하고,
        결과가 불명이면 실행 권한을 닫는다(C 조건 2·3). 소비는 기존 연산을 쓴다.
        """
        ...

    async def create_approval(self, approval: ApprovalRecord) -> None:
        """PENDING 승인·요청 WAITING_APPROVAL·감사를 원자 기록. 원문은 받지 않는다."""
        ...

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...

    async def transition_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        expected_row_revision: int,
        approver_id: str,
        now_utc: datetime,
        reason: Reason,
    ) -> ApprovalRecord:
        """8.2의 단방향 전이만 허용. CONSUMED는 consume_and_reserve 전용이다.

        거절·만료 시 Task 재개 등 동반 전이는 C와 확정할 미정 항목이다.
        """
        ...

    async def consume_and_reserve(
        self,
        *,
        approval_id: str,
        snapshot_hash: str,
        request_id: str,
        task_id: str,
        expected_security_version: int,
        now_utc: datetime,
    ) -> bool:
        """승인 CONSUMED·요청 DISPATCHING·Task RUNNING·감사를 단일 트랜잭션.

        승인/요청/Task의 연결, APPROVED·WAITING 상태, 해시, 만료, security_version과
        각 변경 행 수 1을 검사한다. 조건 충돌은 전부 rollback 후 False, 저장 실패는
        StoreError다. True를 반환하기 전 내구성 커밋을 끝내며 raw 원문은 받지 않는다.
        """
        ...

    async def commit_observation(
        self,
        req: ResolvedRequest,
        label: Label | None,
        *,
        at_utc: datetime,
    ) -> TaskState:
        """READ 전용: 등급·security_version·승격 근거·감사·요청 상태를 원자 커밋.

        label=None은 미분류이며 Task HALTED와 요청 WITHHELD를 함께 확정한다.
        SEND나 미예약 요청을 거부한다. 커밋 성공 반환 이후에만 gate가 공개한다(I5).
        """
        ...

    async def commit_send_outcome(self, req: ResolvedRequest, *, at_utc: datetime) -> None:
        """SEND 전용: 성공 요청 상태·감사만 원자 커밋. READ 등급 병합을 호출하지 않는다."""
        ...

    async def commit_request_failure(
        self,
        req: ResolvedRequest,
        *,
        request_status: Literal[
            RequestStatus.FAILED, RequestStatus.WITHHELD, RequestStatus.UNKNOWN_OUTCOME
        ],
        task_status: Literal[TaskStatus.RUNNING, TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME],
        reason: Reason,
        at_utc: datetime,
        exception_type: str | None = None,
    ) -> TaskState:
        """예약 후 실패의 요청·Task 상태·감사를 원자 기록하는 초안(7.1·9.2절).

        무자료 READ 알려진 실패는 FAILED/RUNNING, 예상 밖 READ 실패는 WITHHELD/HALTED,
        SEND 불명은 UNKNOWN_OUTCOME/UNKNOWN_OUTCOME이다. 등급은 내리지 않는다.
        상태 쌍을 검증하고 예외는 타입명만 기록한다. 이 기록도 실패하면 호출자가
        global halt로 이후 판정을 차단한다. 상세 함수 분리는 C와 검토한다.
        """
        ...
