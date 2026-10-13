# B6 · Store·승인·A 연결 계약 제안 v1

기준일: **2026-10-13**. 작성 B, 상태는 **검토용 제안·A/B/C 공동 합의 미완료**다.
SQLite 담당 이관은 확정됐지만 이 문서의 API·DTO·필드·상태 선택은 확정 계약이 아니다.
이 문서는 구현 요구를 구체화하는 일반 저장소 문서다. domain·ports 소스 변경,
SQLite 구현, 아래 검증 실행, A/C 동의 기록은 이 문서 작성으로 수행하지 않는다.

2026-10-13 사용자 결정은 **A·C 검토를 받은 뒤 확정**이다. B가 새 Store API·DTO·오류·
버전·복구 규칙을 먼저 확정하지 않는다. 검토 대상과 회신 항목은 10절에 정리했다.

## 1. 기준과 범위

- 최종 기준은 [기술설계](technical-design.md) 3.4·6–8·11–12장이다. 원문을 수정하지 않는다.
- Store 계약 대조 기준은 B `f3c2d2b`의 12개 메서드다. B `f22968c`와 후속 B 변경도 이 포트를 유지한다.
- SQLite 담당 이관 근거는 [B6 작업](tasks/b-sqlite-store.md)과 [공통 계약](contracts.md)이다.
- C의 [요구 U1–U8](tasks/c-store-requirements.md)·[처리 제안](tasks/c-store-proposal.md)을 대조했다.
- A `5598009`의 `docs/tasks/a-port-review.md` 6–7절과 UI 계약 제안을 검토했다.
  A 문서의 요청은 합의가 아니다. 후속 A `655aef5`의 B 소유 긴급 차단·상세 조회 반영은
  이번 B6 공유 API 변경과 분리하며 아래 7절에 현재 동작을 기록한다.
- B는 저장소·단위 테스트, C는 리뷰·독립 계약/통합/결함 검증, A는 앱 시작·제어 큐·UI를 맡는다.

## 2. 유지할 모델과 추가 DTO 제안

기존 TaskState·ApprovalRecord·Outcome의 의미를 유지하는 것을 우선한다.
추가 DTO도 frozen·strict·extra forbid를 적용하고 UTC-aware datetime을 사용하도록 제안한다.
다음은 필드 선택안이며 이름·null 규칙·버전 필드는 공동 검토 대상이다.

| DTO 후보 | 필드 제안 | 용도와 공개 경계 |
| --- | --- | --- |
| RequestState | request_id, task_id, epoch, call_seq, tool_kind, decision, status, row_revision, created_at_utc, dispatch_reserved_at_utc? | 승인→요청→Task 관계와 예약/종결 확인. 전체 인자·결과 없음 |
| ApprovalCommit | approval: ApprovalRecord, task: TaskState, request: RequestState | 같은 트랜잭션의 승인 결과를 호출자가 검증. raw snapshot 없음 |
| ApprovalTaskAction | RESUME, HALT, KEEP | B가 신뢰 상태로 선택하고 Store가 재검증하는 내부 동작 |
| RecoveryReport | recovered_at_utc, expired_approval_count, halted_task_count, withheld_read_count, unknown_send_count, 대상 ID·상태 | 실행 권한 없이 시작 정리 결과 전달. 실패 시 정상 완료로 반환하지 않음 |

RequestState의 decision은 최초 저장 판정이고 현재 실행 권한이 아니다.
ApprovalCommit에 request_status만 넣는 대안보다 요청 ID·epoch·revision을 제공하는 안을 권장한다.
Task·승인·요청 revision은 각 객체 범위다. 서로 다른 객체의 revision을 직접 크기 비교하지 않는다.
공개 UI 요약에는 필요 필드만 선별하며, 내부 DTO 전체를 일반 이벤트로 직렬화하지 않는다.

## 3. Store API 변경 초안

현재 create_task, load_task, record_decision, reserve_dispatch, set_task_status,
create_approval, get_approval, transition_approval, consume_and_reserve,
commit_observation, commit_send_outcome, commit_request_failure가 있다.
READ→TaskState, SEND 성공→None, 소비→정확한 bool 반환을 유지하는 안을 권장한다.
아래 추가/변경 시그니처는 **문서 제안**이며 실제 Protocol을 변경한 것이 아니다.

```python
async def open_approval(
    self, approval: ApprovalRecord, *,
    expected_task: TaskState, now_utc: datetime,
) -> ApprovalCommit: ...

async def finish_approval(
    self, approval: ApprovalRecord, *,
    expected_task: TaskState, terminal_status: ApprovalStatus,
    task_action: ApprovalTaskAction, approver_id: str,
    now_utc: datetime, reason: Reason,
) -> ApprovalCommit: ...

async def load_approval_state(
    self, approval_id: str,
) -> ApprovalCommit | None: ...

async def reconcile_on_startup(
    self, *, now_utc: datetime,
) -> RecoveryReport: ...

async def consume_and_reserve(
    self, *, approval_id: str, snapshot_hash: str,
    request_id: str, task_id: str, expected_epoch: int,
    expected_security_version: int, expected_task_row_revision: int,
    expected_approval_row_revision: int, now_utc: datetime,
) -> bool: ...
```

open/finish의 expected_task는 ID·epoch·보안 맥락·row_revision을 함께 고정한다.
finish의 approval은 기대 승인 revision과 요청 관계를 담고, approver_id는 신뢰 제어 주체다.
load_approval_state는 일관된 읽기의 결과이지 변경 COMMIT의 ACK가 아니다.
이름을 분리해 ApprovalState로 반환하는 대안도 검토 가능하다.
create_approval은 open 연결 완료 후 별도 공개 쓰기 경로에서 제거하거나 내부화하는 안을 권장한다.
transition_approval은 PENDING→APPROVED 표시만 유지하고 CONSUMED는 소비 연산 전용으로 둔다.
승인 종결을 Task 복귀와 별도 커밋으로 조립하는 사용은 새 계약에서 허용하지 않는 안이다.

## 4. 상태·버전·관계

| 변화 | security_version 권장 | row_revision 권장 |
| --- | --- | --- |
| READY→RUNNING, RUNNING↔WAITING_APPROVAL | 유지 | 변경된 Task 행 +1 |
| high-watermark 승격 | 트랜잭션 합계 +1 | 변경된 Task 행 +1 |
| last_read_label만 변경 | 유지 | 변경된 Task 행 +1 |
| unclassified 설정·HALTED 동시 진입 | 트랜잭션 합계 +1 | Task 행 +1 |
| HALTED·UNKNOWN_OUTCOME·CLOSED 새 진입 | 트랜잭션 합계 +1 | Task 행 +1 |
| 승인 상태·요청 상태만 변경 | Task 버전 유지 | 변경된 해당 행 +1 |
| SEND 정상 요청·감사 커밋 | Task 보안 필드·상태 유지 | Task revision 유지 |

이는 기술설계 3.4와 소비 SQL의 정상 RUNNING 복귀 시 보안 버전 유지에 맞춘 제안이다.
B merge가 이미 증가시킨 버전을 Store가 다시 증가시키지 않는다.
같은 종착 상태 반복 기록과 한 트랜잭션의 여러 보안 필드 변경으로 중복 증가시키지 않는다.
HALTED·UNKNOWN_OUTCOME·CLOSED에서 RUNNING 복귀와 unclassified 해제는 금지한다.
일반 상태 갱신은 profile_id·policy_version·epoch를 바꾸지 않는다.

요청에는 epoch·ToolKind·row_revision을 저장하는 스키마 확장을 제안한다.
승인→요청→Task 관계, call_seq, 기대 상태·버전과 조건 UPDATE 영향 행 수 1을 검사한다.
Task ID 존재만 확인해서 다른 정상 요청·승인의 ID 조합을 받아들이지 않는다.
영속 call_seq는 별도 계수 행으로 관리하고 빈 순번은 허용하되 실패·재시작 후 재사용하지 않는다.
순번 할당 때문에 Task row_revision이 바뀌어 승인 생성 CAS를 스스로 실패시키지 않게 한다.
ID·순번·실패 감사 callback의 실제 배치는 B/C와 검토하며 C의 위험 ID 모듈을 복제하지 않는다.

## 5. 승인 생성·종결 원자성

open은 같은 Task lock에서 ASK·운영자·만료·직렬화를 확인하고 예상 WAITING snapshot을 만든다.
정상 WAITING 전이의 보안 버전 유지 규칙이 확정되면 같은 snapshot hash를 미리 계산할 수 있다.
Store는 RUNNING Task와 DECIDED ASK 요청의 관계·epoch·revision·만료를 검사한다.
Task·요청 WAITING, PENDING 승인, APPROVAL_PENDING 감사를 한 트랜잭션으로 확정한다.
B는 COMMIT 결과의 ID·epoch·상태·보안 버전·hash를 검사한 후 메모리 snapshot을 보관한다.
사본 보관/검증 실패는 정상 Pending을 반환하지 않고 실행 권한 폐기·정지로 격리한다.

| 종결 이유 | 승인 종착 상태 | 요청 상태 제안 | Task 동작 제안 |
| --- | --- | --- | --- |
| 사람 거절 | PENDING→REJECTED | DENIED | 동일 보안·정책 맥락의 WAITING이면 RESUME |
| 만료 | PENDING/APPROVED→EXPIRED | DENIED | 동일 조건에서 RESUME |
| 재평가 ASK 불일치·hash 변경 | PENDING→CANCELLED 또는 APPROVED→INVALIDATED | DENIED | 맥락 보존 확인 시 RESUME, 설정 변화면 HALT |
| 정책·등록·지문 변경, 취소·긴급 차단 | CANCELLED 또는 INVALIDATED | DENIED | HALT |
| 이미 HALTED·UNKNOWN_OUTCOME·CLOSED | 유효 활성 승인 종결 | DENIED | KEEP, 실행 복귀 없음 |

finish는 승인·요청·Task·종결 감사를 함께 커밋하고 B는 검증 후 snapshot을 제거한다.
KEEP으로 마지막 승인이 끝난 정상 WAITING Task를 남기거나 정지 상태를 해제하지 않는다.
Task 전체 정지 시 활성 승인·WAITING 요청의 종결도 같은 트랜잭션에 포함하도록 제안한다.
현재 B의 set_task_status→create_approval 및 transition_approval→set_task_status는 두 커밋이다.
기존 Store 12개 메서드 구현만으로 이 틈을 닫았다고 보고할 수 없으며 호출부 교체가 필요하다.
사본 부재·다른 Task DTO·epoch 불일치는 임의 ID를 변경 대상으로 쓰지 않고 실행 차단한다.

## 6. ACK·시계·취소·오류

| 상황 | 반환/오류 제안 | 호출자 동작 |
| --- | --- | --- |
| 내구성 COMMIT 확인 | 인터페이스별 True/DTO/None | 메타데이터 검증 후 진행 |
| 소비 조건 충돌·만료, 변경 없음/rollback 확인 | 정확한 False | dispatch 없음, 현재 신뢰 상태로 종결 |
| 다른 연산의 확인된 CAS/조건 충돌 | StoreConflictError(StoreError) | 실행 권한 없음. 부분 저장 없음 |
| I/O·잠금·감사 실패, rollback 확인 | StoreError | 단계별 미공개·정지/결과 불명 격리 |
| COMMIT/ROLLBACK 확정 불가 | StoreCommitUncertainError(StoreError) | 연결 사용 중단·global halt·자동 재시도 없음 |

오류에는 고정 Reason만 담고 SQL·경로·인자·sqlite 예외 원문을 공개하지 않는다.
다른 Task 조회 DTO나 깨진 내부 관계를 무시 가능한 충돌로 숨기지 않는다.
예약 커밋 불명은 일반 예약 전 fail_closed의 Denied로 단정하지 않도록 별도 처리가 필요하다.
req 맥락을 유지하고 transport 호출 없이 READ Withheld/SEND OutcomeUnknown·global halt로 격리한다.
DB 예약은 host dispatch·wire·receiver effect가 아니다. 각각 관측된 계수만 보고한다.

MVP 권장안은 백엔드 같은 스레드의 단일 writer 동기 SQLite 구간이다.
BEGIN부터 COMMIT/ROLLBACK·반환 사이에 await를 넣지 않고 취소 후 계속되는 백그라운드 쓰기를 피한다.
writer 큐 대안은 취소와 완료 ACK의 별도 계약·검증이 필요하다.
WAL·foreign_keys·synchronous FULL·명시적 BEGIN IMMEDIATE 설정과 실제 값을 검증한다.
busy_timeout=1000ms는 C의 미측정 후보이며 모든 디스크 I/O 시간 상한을 뜻하지 않는다.

Guard와 Store에 같은 신뢰 UTC 시계를 주입하고 DB는 UTC 고정 마이크로초 ISO 표현을 제안한다.
소비 시 잠금 획득 후와 COMMIT 직전에 새 UTC를 확인하며 now_utc만으로 처리 시각을 대신하지 않는다.
만료 경계는 now >= expires_at이면 만료다. consumed_at은 decided_at 이상·expires_at 미만이어야 한다.
COMMIT 직전 만료는 전체 rollback False, COMMIT 성공 뒤 ACK 시 만료는 True로 구분한다.
후자는 Guard의 UTC·monotonic 전송 직전 검사가 전송을 막으며 소비 1·실제 전송 0일 수 있다.

## 7. A 연결·인증 조회·이벤트 제안

- submit(task_id, call, *, expected_epoch=None): trusted Runner가 공급하고 Task lock 안에서 먼저 비교한다.
  불일치면 교체 Task를 정지시키지 않고 예약·전송 전에 거절한다. ProposedCall에 epoch를 넣지 않는다.
  이 선택적 B 서비스 인자는 2026-10-13에 구현·단위 검증했다. epoch 불일치는
  Denied(TASK_NOT_RUNNING), 신뢰 인자 타입 오류는 Denied(STATE_MISSING)이며,
  생략 시 기존 호출과 호환된다. 실제 A Runner 전달·C 경합 검증과 공동 계약 동결은 별도다.
- 권위 조회: Store의 일관된 승인/요청/Task 조회를 B 서비스에서 관계·타입 검증 후 제공한다.
  Denied(APPROVAL_RACE/TASK_NOT_RUNNING)만으로 reject/expire/cancel 적용 여부를 추론하지 않는다.
- 승인 상세: HumanControl.view_approval(approval_id, *, expected_approval_revision) 형태를 제안한다.
  운영자·메모리 snapshot·ID/epoch/hash·활성 상태·양시계 만료·WAITING 맥락을 재검사한다.
  같은 snapshot의 전체 인자·경계·등급·수신처를 반환하고 일반 조회·감사·CSV·모델에는 넣지 않는다.
  사본 부재·종결·만료 시 거부하며 A는 plain text 표시와 상세 수명 종료를 맡는다(T18).
  위 revision 지정 API는 미구현 공동 제안이다. 현재는 사용자 지시로 A `655aef5`의
  `HumanControl.review(approval_id) -> ApprovalSnapshot | None`을 B 브랜치에 반영했다.
  strict 승인 모델·승인 ID·request ID·PENDING·hash·Task ID/epoch·현재 ACTIVE 등록·지문을 확인하고,
  마지막 await 뒤 UTC·monotonic 만료와 메모리 사본 현재성을 재검사한다. 조회는 승인 소비·
  상태 쓰기 없이 같은 메모리 사본만 반환하며 원문을 일반 이벤트·감사·DB에서 재구성하지 않는다.
  만료·정지·조회 취소는 `None`, 깨진 Store·모델·Task 관계는 `None`과 global halt다.
  승인 ID 대조는 승인·거절·만료·무효화·재조회·오류 정리 및 전이 반환에도 공통 적용한다.
  오연결 행은 추가 행 변경·소비·전송 없이 봉쇄하며 관계없는 승인·다른 Task 사본은 유지한다.
- 제어 접수 ACK와 적용 결과는 분리한다. A bridge의 BACKEND_RETURNED는 적용 완료 증명이 아니다.
  적용 결과는 command_id·Task epoch·객체별 revision·확정 상태·Reason·UTC에 연결하도록 제안한다.
  중복·오래된 이벤트는 무시하고 revision 갭·재연결에는 snapshot을 읽는다. 실행을 재발행하지 않는다.
- durable 명령 중복 방지, 목록 pagination·집계, Registry 영속 API는 별도 공동 계약으로 남긴다.
  B의 승인 1회 소비만으로 모든 제어 명령·UI 재동기화가 완성됐다고 표시하지 않는다.

같은 B 변경에서 긴급 차단은 인증 후 Task lock 전에 정지 요청을 기록하고 소비·예약·전송
직전에 재확인한다. 취소한 AGT는 실제 완료 전 추적·동시 실행 슬롯을 유지하며, 미종료 작업이
있으면 새 검사를 생성하지 않는다. 기본 상한 `max_access_checks=4`와 정리 대기
`access_cleanup_timeout_seconds=0.05`는 실측 최적값이 아닌 보수적 설정이다.
정지 latch는 Task ID에 남으므로 새 Task ID로 재시작한다. 같은 ID/epoch 복구는 8절 미정 항목이다.
세 지적과 동일 ID 오류의 상태 변경 경로 회귀를 포함한 관련 250개·전체 971개 테스트와
정적 검사를 통과했다. 사용자는 이번 B 브랜치 변경의 commit·push를 승인했다.
실제 A·C 제품 연결·MCP·AGT·SQLite·C 독립 검증은 미완료다.
이 반영 지시는 B6·정규식 공동 계약 전체의 동의를 대신하지 않는다.

## 8. 복구 권고와 해석이 남은 질문

Guard 개방 전 reconcile을 수행하고 실패하면 전역 실행을 닫아 둔다.
잔존 PENDING/APPROVED는 EXPIRED, WAITING 요청은 DENIED, 해당 비종결 Task는 HALTED로 제안한다.
DISPATCHING READ는 WITHHELD/HALTED, SEND는 UNKNOWN_OUTCOME으로 기록하고 재전송하지 않는다.
그 외 비종결 Task도 정지하며 CONSUMED·종결 이력·누적 등급을 보존한다. 원문·승인 권한은 복원하지 않는다.

다음 두 해석은 권고를 확정 계약으로 승격하기 전에 별도 결정해야 한다.

1. SEND 실패: 설계 6.3절의 공통 커밋 실패 HALTED 표현과 7.1·I8의 SEND 결과 불명 의미를 대조한다.
   현재 B의 보수적 UNKNOWN_OUTCOME을 권장하지만 명확한 SEND 업무 실패·커밋 실패까지 같은 처리를
   확정할지는 미정이다. 상태 쌍·Reason·응답 공개·자동 재전송 금지를 A/B/C가 함께 기록해야 한다.
2. recovery epoch: 설계 용어는 재시작·복구 시 epoch 증가지만 C 제안은 옛 Task 정지·새 Task 복구다.
   옛 terminal epoch 보존+새 Task ID와 기존 Task epoch 증가+과거 요청 epoch 보존 중 기록 방식을
   결정해야 한다. 일반 상태 갱신에서 epoch를 바꾸거나 옛 요청을 새 epoch 실행에 결합하지 않는다.

## 9. U1–U8 대조·검증·착수 경계

| 항목 | 이 제안의 답 | 남은 확인 |
| --- | --- | --- |
| U1 | DTO·정확한 bool·충돌/저장/커밋 불명 오류 | 실제 API 이름·오류 공개와 호출부 경계 |
| U2 | 정상 상태 버전 유지, 보안 변화 트랜잭션 +1 | snapshot 생성·소비 후 동일 버전 검증 |
| U3 | 요청 epoch/kind/revision, 관계·행 수 검사 | 스키마·기대 revision 인자 확정 |
| U4 | 소비·예약 감사 원자 커밋 | 사건명·허용 메타데이터·감사 실패 rollback |
| U5 | SEND 성공은 등급 불변, 현재 B 실패는 결과 불명 | 8절 SEND 설계 해석·상태 쌍 확정 |
| U6 | fresh UTC·동기 writer·확정 불가 연결 폐기 | UTC 표현·잠금 후보·취소 지연 측정 |
| U7 | 시작 정리·승인 복원/자동 dispatch 없음 | 8절 recovery epoch와 A 시작 게이트 |
| U8 | open/finish 원자성·terminal Task 재개 금지 | 제어/응답 경쟁·실제 SQLite 식별자 회귀 |

이 문서의 새 Store 계약·SQLite 검증은 아직 실행하지 않았다. B 단위 계획은 원자 단계 실패·버전·관계/epoch/CAS·중복 소비·만료,
READ 미공개·SEND 등급 불변·복구·원문 기록 제한이다(I3–I9, T07·T09–T12·T19·T35–T37·S03/S04).
C가 독립 계약·통합·결함·실제 계수를 검증하고 A가 expected_epoch·상세/T18·시작 게이트를 연결한다.
기존 포트 범위의 독립 저장소 작업은 진행 가능하나 새 공유 API·DTO·오류·상태 규칙은 공동 합의 대상이다.
합의 없는 open/finish 교체·UI 이벤트 확정이나 기존 두 커밋의 원자성 완료 표시는 하지 않는다.
공동 검토는 승인/버전, writer/ACK/시계, A 조회/제어 세 묶음으로 제공한다. 실제 동의만 공통 계약에 기록한다.

## 10. A·C 검토 후 확정 절차

| 검토 담당 | 확인할 부분 | 필요한 회신 |
| --- | --- | --- |
| A | 3·7·8절: 원자 승인 반환·권위 조회·상세·제어 적용 결과·시작 복구 게이트 | 사용할 API/필드와 동의 또는 수정안, Runner/UI/시작 경로 영향 |
| C | 3–6·8–9절: U1–U8·관계/CAS·버전·감사·시계·커밋 불명·복구 | 독립 검증 가능한 조건, 오류/상태 선택 동의 또는 수정안 |
| A·C·B | 8절 SEND 실패·recovery epoch, 새 공유 DTO/Protocol/Reason | 선택안과 근거, 남은 질문, 검토 문서/커밋 식별자 |

회신을 받은 뒤 B가 제안을 갱신하고 실제 합의 근거를 `docs/contracts.md`에 기록한다.
그 뒤 공유 모델·포트와 호출부를 변경하고 B6 SQLite·단위 테스트를 구현한다.
로컬 제안 작성이나 기존 A/C 리뷰를 이번 새 계약에 대한 동의로 대신 기록하지 않는다.
현재는 검토 자료 준비 완료이며, 팀원에게 전달·리뷰 완료·SQLite 착수로 표시하지 않는다.
