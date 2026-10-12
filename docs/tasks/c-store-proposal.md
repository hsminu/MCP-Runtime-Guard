# C · Store 계약 처리 제안 v1

상태는 **C 제안 작성, B 검토·A·B·C 합의 미완료**다. 기준은 B `0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc`의 구현 초안, [Store 요구 초안](c-store-requirements.md), [B 검토 기록](c-b-review.md), [공통 계약](../contracts.md)이다. 작성 C, domain·ports·오류 타입과 B 서비스의 최종 정의는 B 담당이다. 새 인터페이스·DTO 이름은 제안이며 소스는 수정하지 않았다.

2026-10-12 사용자 결정으로 SQLite 구현·스키마·복구·단위 테스트도 [B6](b-sqlite-store.md)로 이관했다. C는 저장소 리뷰·독립 계약·통합·결함 주입·실제 계수 검증을 맡는다. 아래 처리 규칙은 C의 검토 제안으로 유지하며 이관 결정이 API 세부 동의를 대신하지 않는다. C-B-01은 후속 B `f3c2d2b`에서 수정·대역 회귀 확인됐고 실제 SQLite 결함 검증은 남아 있다.

## 1. 권장 선택

| 항목 | C 제안 규칙 | B 현황과 영향 |
| --- | --- | --- |
| 정상 상태와 security_version | READY→RUNNING, RUNNING↔WAITING_APPROVAL은 보안 버전을 유지. 실제 갱신 행의 row_revision은 증가 | WAITING 후보 상태로 snapshot을 계산. 소비 후 버전 유지·1 증가 모두 허용하던 비교를 동일 버전으로 제한 |
| 보안 상태 변화 | 등급 승격, Unclassified 설정, HALTED·UNKNOWN_OUTCOME·CLOSED 진입은 한 트랜잭션에서 보안 버전 합계 +1 | B READ merge를 사용하며 저장소가 같은 변화에 다시 +1하지 않음 |
| 승인 생성·종결 | Task·요청·승인·감사를 각각 한 트랜잭션으로 묶는 open_approval / finish_approval 추가 | 현재 별도 Store 호출로 구성된 open / finish 변경 |
| 성공 ACK | COMMIT 완료 후에만 성공 반환. False는 변경 없음/rollback이 확인된 소비 조건 충돌만 의미 | 정확한 bool 유지, 일반 충돌·커밋 확정 불가를 구분하는 오류 제안 |
| 취소 | 트랜잭션 진입 전 취소 가능. 시작 후 COMMIT/ROLLBACK과 결과 전달 사이에는 await 없음 | MVP 제안은 백엔드 단일 writer의 동기 SQLite 구간 사용. 후속 취소는 B의 해당 실행 단계에서 처리 |
| 만료 | 쓰기 잠금 획득 후와 COMMIT 직전에 주입된 신뢰 UTC 시계를 다시 확인. B의 monotonic·전송 직전 검사는 유지 | 호출 시 전달한 now_utc만으로 DB 대기 후 만료를 판정하지 않음 |
| SEND 실패 | B의 보수적 규칙 채택: isError·형식 실패·결과 커밋 실패는 UNKNOWN_OUTCOME, 성공만 commit_send_outcome | 결과 불명 쓰기는 자동 재전송하지 않음 |
| 재시작 | 잔존 승인·미완료 Task를 정리한 뒤 Guard 개방. DISPATCHING SEND는 결과 불명 | 시작 정리 연산 제안. 옛 승인·실행 권한 복원 없음 |

모두 제안이며 합의표는 채우지 않는다. 아래 잠금 대기 1000ms도 미측정 후보값이다.

## 2. API 제안과 책임

B의 Task create/load, record_decision, reserve_dispatch, set_task_status, get_approval과 세 주요 커밋 연산은 유지한다. 아래 두 승인 연산은 별도 쓰기 호출을 하나로 대체한다. 저장소 구현 내부에서 다음 서비스 호출의 의도를 추정하지 않는다.

| 제안 인터페이스 | 입력 의미 | 반환 의미 |
| --- | --- | --- |
| open_approval(approval, expected_task, now_utc) | ApprovalRecord 메타데이터, 검증된 RUNNING TaskState, 호출 시 UTC | ApprovalCommit: 커밋된 승인·TaskState·요청 상태 |
| finish_approval(approval, terminal_status, expected_task, task_action, now_utc, reason) | 승인 메타데이터와 기대 revision, 신뢰 TaskState, 종착 상태, RESUME/HALT/KEEP 동작 | ApprovalCommit: 같은 트랜잭션의 커밋 결과 메타데이터 |
| transition_approval | PENDING→APPROVED 기록에 사용. 승인 종결과 Task 복귀를 별도 호출로 조립하는 용도로 사용하지 않음 | 기존 ApprovalRecord 반환 유지 가능 |
| reconcile_on_startup | Guard 실행 개방 전에 신뢰 설정·시계로 영속 상태 정리 | ID·상태·계수만 담는 정리 보고. 원문·실행 권한 없음 |

keyword-only 인자, 전체 타입, ApprovalCommit과 task_action의 DTO/enum은 B가 domain·ports에서 정의한다. ApprovalCommit은 frozen으로 approval·task·request_status를 담아 B가 ID·epoch·기대 상태를 검증하도록 제안한다. 인자·snapshot·도구 결과 원문은 포함하지 않는다.

READ는 commit_observation의 TaskState 반환, SEND 성공은 commit_send_outcome의 None 반환을 유지한다. consume_and_reserve는 정확한 bool이며 1·None으로 ACK를 대신하지 않는다. commit_request_failure는 TaskState 반환을 유지한다.

## 3. 버전 규칙

| 변화 | security_version | row_revision |
| --- | --- | --- |
| 정상 READY→RUNNING, RUNNING→WAITING, WAITING→RUNNING | 유지 | Task 행 변경 시 +1 |
| high-watermark 승격 | +1 | Task 행 변경 시 +1 |
| last_read_label만 변경 | 유지 | Task 행 변경 시 +1 |
| unclassified 설정과 HALTED 동시 진입 | 같은 트랜잭션에서 합계 +1 | Task 행 +1 |
| 이전과 다른 HALTED·UNKNOWN_OUTCOME·CLOSED 상태로 진입 | 같은 트랜잭션에서 합계 +1 | Task 행 +1 |
| Task 변경 없이 요청·감사만 갱신 | 유지 | Task revision 유지 |
| 승인 PENDING→APPROVED/종결/소비 | Task 보안 버전을 직접 바꾸지 않음 | 변경 승인 행 revision +1 |

한 트랜잭션에서 보안 필드 여러 개가 바뀌어도 +1이다. 미분류와 HALTED가 함께 설정돼도 +2하지 않는다. B merge가 이미 버전을 올린 READ 결과는 저장소가 그대로 저장한다. 같은 종착 상태를 반복 기록해 보안 버전을 다시 올리지 않는다.

HALTED·UNKNOWN_OUTCOME·CLOSED에서 RUNNING 복귀는 금지한다. 재실행은 새 Task다. 일반 상태 갱신이 profile_id·policy_version·epoch를 바꾸지 않으며 옛 요청을 새 epoch에 결합하지 않는다.

예: INTERNAL Task의 security_version=7, row_revision=20이면 승인 생성 후 WAITING은 7/21이다. APPROVED 전이는 승인 행의 revision만 올린다. 소비 후 RUNNING은 7/22이고, 다음 READ가 CONFIDENTIAL로 승격할 때 8/23이 된다.

## 4. 승인 생성 원자성

B 호출자는 같은 Task lock에서 요청 해석·ASK 판정·신뢰 승인자·만료 설정·직렬화 검사를 먼저 완료한다. 정상 WAITING 전이는 보안 버전을 유지하므로 B가 예상 WAITING 상태로 snapshot/hash를 미리 계산할 수 있다. 원문은 B 메모리에만 둔다.

open_approval의 한 트랜잭션은 다음을 수행한다.

1. 기존 Task의 ID·epoch·profile/policy·보안 필드·버전·row_revision이 expected_task와 같고, RUNNING·분류 완료 상태인지 검사한다.
2. approval.request_id가 이 Task의 DECIDED ASK 요청인지, 같은 요청의 승인과 같은 Task의 다른 활성 승인이 없는지 검사한다.
3. 실제 처리 시계로 유효한 만료 구간을 확인한다.
4. Task와 요청을 WAITING_APPROVAL로 전이하고 PENDING 승인과 승인 대기 감사를 기록한다. 각 필수 영향 행 수를 검사한다.
5. COMMIT 후 ApprovalCommit을 반환한다. B는 ID·epoch·상태·버전·hash를 확인한 뒤 snapshot을 유지하고 정상 Pending을 반환한다.

확인된 중간 실패는 Task만 WAITING으로 남기지 않는다. 커밋 후 B의 snapshot 보관·DTO 검사가 실패하면 정상 Pending을 공개하지 않고 실행 권한을 폐기·정지한다. 커밋 확정 불가는 6절을 따른다.

B ApprovalService.open의 기존 set_task_status→create_approval 쓰기 순서를 한 원자 호출로 바꾸는 제안이다. Store에는 원문을 보내지 않는다.

## 5. 승인 종결 원자성

finish_approval은 검증한 대상 Task의 같은 lock 안에서 실행한다. 승인→요청→Task 관계, epoch, 승인·Task 기대 버전과 8.2절 단방향 전이를 검사한다. 요청도 종결해 승인만 끝나고 요청이 계속 WAITING인 상태를 남기지 않는다.

| 종결 이유 | 승인 결과 | 요청 결과 제안 | Task 동작 |
| --- | --- | --- | --- |
| 사람 거절 | PENDING→REJECTED | DENIED | 신뢰 snapshot과 현재 보안·정책 맥락이 같고 WAITING일 때 RESUME |
| 만료 | PENDING/APPROVED→EXPIRED | DENIED | 같은 조건에서 RESUME |
| 재평가가 ASK 아님·hash 변경 | PENDING→CANCELLED 또는 APPROVED→INVALIDATED | DENIED | 설정 변화가 없고 보안 맥락이 여전히 같을 때만 RESUME, 그 외 HALT |
| 정책·Profile·등록·지문 변경, 취소·긴급 차단 | PENDING→CANCELLED 또는 APPROVED→INVALIDATED | DENIED | HALT |
| 이미 HALTED·UNKNOWN_OUTCOME·CLOSED | 유효한 활성 승인 종결 가능 | DENIED | KEEP, 실행 복귀 없음 |

Task 동작은 B가 신뢰 상태로 선택하는 내부 입력이다. Store도 기대 상태·버전을 다시 검사한다. RESUME은 WAITING→RUNNING만 허용하고 미분류·정지·결과 불명을 해제하지 않는다. KEEP으로 마지막 승인이 끝난 정상 WAITING Task를 계속 기다리게 두지 않는다.

승인·요청·Task·종결 감사는 함께 커밋한다. B는 결과를 검증한 뒤 메모리 snapshot을 제거한다. 커밋 불명·실패 격리 때도 옛 snapshot은 실행 권한으로 사용하지 않는다. APPROVED 표시만 할 때는 transition_approval을 사용하고 Task를 먼저 복귀시키지 않는다.

승인과 Task를 함께 정지시킬 때 B는 finish_approval(HALT)을 한 번 호출하고 별도 halt_task·승인 전이를 덧붙이지 않는다. Task 전체 정지로 승인이 이미 종결된 경우에는 최신 승인·요청·Task 관계를 확인해 메모리 사본을 폐기하며, 이전 revision으로 다시 종결을 강제하지 않는다. 새 포트를 연결할 때 현재 B의 정지 후 _finish 호출 순서도 함께 변경한다.

실행 중 snapshot이 유실되면 올바른 Task lock을 얻지 못한 상태에서 반환 DTO의 임의 ID를 변경 대상으로 사용하지 않는다. 제안은 실행 거부·global halt 후 시작 정리로 잔존 상태를 제거하는 것이다. hash로 원문을 복원하지 않는다. 승인·종결의 Task 조회는 상태 변경 전에 요청한 Task ID·epoch를 검증하며 C-B-01 수정도 포함한다.

## 6. 성공·실패·취소 ACK

StoreConflictError와 StoreCommitUncertainError를 StoreError의 하위 타입으로 제안한다. 이름·정확한 필드는 B가 정의하며 reason은 통제된 Reason을 사용한다. SQL·경로·인자·sqlite 예외 원문·traceback은 담지 않는다.

| 상황 | Store 제안 결과 | B 동작 |
| --- | --- | --- |
| COMMIT 성공과 결과 전달 | 인터페이스별 True / TaskState / ApprovalCommit / 정상 None | 메타데이터 확인 후 다음 단계. 승인 후 전송 직전 검사는 유지 |
| 소비 상태·hash·버전·관계·만료 충돌, 변경 없음/rollback 확인 | 정확한 False | dispatch 없음, 현재 신뢰 상태에 따라 승인 종결 |
| 다른 연산의 확인된 CAS/만료 충돌 | StoreConflictError | 검증한 대상의 고정 충돌 결과로 처리. 오류를 무시하고 실행하지 않음 |
| I/O·잠금·감사 실패와 rollback 확인 | StoreError | 예약 전 실행 없음. 응답 실패는 미공개와 READ 정지/SEND 불명 격리 |
| COMMIT/ROLLBACK 확정 불가 | StoreCommitUncertainError, 해당 연결 사용 중단 | global halt. 새 실행 ACK·작업 자동 재시도·도구 재전송 없음 |
| 커밋 후 프로세스 중단/ACK 미전달 | 호출자에게 성공 실행 권한 없음 | 시작 정리로 영속 상태 처리. 승인 복원·자동 dispatch 없음 |

StoreConflictError는 부분 변경 없음이 확인된 정상 조건 충돌만 표현한다. 조회 DTO가 요청한 Task와 다르거나 DB 내부 관계 자체가 깨진 상태를 무시 가능한 충돌로 숨기지 않는다. 정상 DB의 서로 맞지 않는 ID 조합을 입력한 소비 조건 충돌은 변경 없이 기존 False로 처리한다.

reserve_dispatch/consume_and_reserve의 커밋 불명은 이미 DISPATCHING일 수 있다. B는 두 예약 지점에서 새 오류를 구분하고 req 맥락을 유지해야 한다. transport를 호출하지 않고 예약 후 경계의 READ Withheld/SEND OutcomeUnknown과 global halt로 처리한다. 현재의 일반 예약 전 래퍼가 Denied·전송 0으로 단정하지 않도록 변경한다. 실제 host/wire 계수는 별도 관측한다.

MVP 제안은 백엔드 같은 스레드의 단일 writer가 sqlite3 연결을 소유하는 방식이다. BEGIN·검사·갱신·COMMIT/ROLLBACK·성공 반환 사이에 await를 넣지 않고, 취소 뒤에도 계속 실행되는 백그라운드 쓰기를 만들지 않는다. BEGIN 전 확인된 취소는 중단할 수 있다. 트랜잭션 중 비동기 취소는 결과 반환 이후 B의 해당 단계에서 처리한다. 프로세스 중단은 ACK를 잃을 수 있으므로 시작 정리가 필요하다. 비동기 큐를 도입한다면 writer 완료 ACK까지 Task lock을 유지하며 취소를 처리하는 별도 계약 검증이 필요하다.

SQLite의 COMMIT BUSY와 다른 오류를 모두 자동 rollback으로 가정하지 않는다. 미커밋이 확인된 트랜잭션은 명시적으로 rollback하고, 확정 불가일 때 연결과 실행을 정지한다. 이 차이는 [SQLite 공식 트랜잭션 문서](https://www.sqlite.org/lang_transaction.html)로 확인했으며 실제 구현·결함 주입은 미실행이다.

## 7. 시계와 DB 대기

Store 구성 시 Guard와 같은 신뢰 utc_now를 주입한다. replay는 같은 제어 시계를 공유한다. 공개 DTO는 B의 UTC-aware datetime을 유지하고 DB는 UTC·고정 마이크로초 정밀도의 ISO 문자열로 통일한다. 저장·복원 모두 엄격히 검증하며 외부 자유 형식 문자열을 시각으로 신뢰하지 않는다.

소비 만료는 BEGIN IMMEDIATE 성공 후와 COMMIT 직전에 새로 얻은 UTC로 판정한다. 전달된 now_utc는 호출 시 메타데이터이며 실제 처리 시각을 대신하지 않는다. consumed_at은 마지막 만료 검사의 논리적 소비 시각으로, expires_at보다 이르고 decided_at보다 이르지 않아야 한다. 시계 때문에 구간이 불일치하면 fail-closed다.

- 쓰기 잠금 획득 후 이미 만료: 변경 없이 False.
- 갱신 중 COMMIT 전에 만료: 전체 rollback 후 False.
- 마지막 검사 때 유효했으나 COMMIT 완료/ACK 도착 때 만료: 커밋 성공이면 True를 반환하고 rollback이라고 보고하지 않는다. B의 UTC·monotonic 재검사가 전송을 막는다. 소비는 1일 수 있지만 host·wire·부작용은 0이어야 한다.
- 소비했으나 전송하지 않은 승인은 새 호출로 재사용하지 않는다. DB 만료 검사와 B 단조 시계 검사를 모두 수행한다.

잠금 대기 후보는 busy_timeout=1000ms다. 초기화 후 값을 확인하고 보안 연산 전체를 숨겨서 재시도하지 않는다. 이는 SQLite 잠금 대기 설정이며 모든 디스크 I/O의 강제 시간 상한이나 측정된 성능값이 아니다. 동기 트랜잭션이 백엔드 제어 명령 지연에 주는 영향은 측정 후 조정한다. 근거는 [SQLite busy_timeout](https://www.sqlite.org/pragma.html#pragma_busy_timeout)과 [Python 3.12 sqlite3](https://docs.python.org/3.12/library/sqlite3.html)이며, 실제 설정·지원 플랫폼은 B 구현과 C 독립 검증에서 확인한다.

## 8. 관계·감사·복구 데이터

요청에는 신뢰 req의 epoch·ToolKind·요청 row_revision을 저장하도록 제안한다. UNIQUE(task_id, call_seq)는 유지한다. 예약·승인·응답 커밋은 request_id→task_id→epoch와 기대 요청 상태를 검사하며, 각 조건 UPDATE의 영향 행 수는 1이어야 한다. 개별 ID의 존재만 확인하지 않는다.

APPROVAL_PENDING·승인 종결·승인 소비·DISPATCH_RESERVED 등의 이름은 감사 사건 후보이며 B가 통일해 정의한다. 해당 상태와 감사는 함께 커밋한다. DISPATCH_RESERVED는 실제 전송을 의미하지 않으며 host_dispatch·wire tools/call·receiver_effect는 각각 관측 지점에서 센다.

Task 정지 때 활성 승인과 WAITING 요청의 종결도 같은 트랜잭션에 포함한다. 실패 기록은 high-watermark·미분류를 보존하고 SEND 불명은 재전송하지 않는다. commit_request_failure는 B 초안의 상태 조합만 허용하며 이미 정지한 Task를 실패 기록으로 RUNNING에 복귀시키지 않는다.

call_seq는 독립 영속 계수 레코드로 공급하고 빈 순번을 허용하되 재시작이나 record_decision 실패 후 재사용하지 않도록 제안한다. 순번 할당이 Task.row_revision을 바꾸면 B의 load_task 뒤 할당 단계가 승인 생성 CAS를 스스로 무효화할 수 있으므로 Task 행 갱신과 분리한다. 신뢰 ID·FailureEvent 감사 callback의 정식 배치는 B 공통 계약에서 정의한다.

## 9. 시작 정리

reconcile_on_startup은 Task·제어 명령을 받기 전에 실행한다. 이 구간에는 Guard dispatch가 없다. 상태·승인·요청·복구 감사를 함께 커밋하며 실패하면 전역 실행을 닫아 둔다.

| 영속 상태 | 복구 제안 |
| --- | --- |
| PENDING·APPROVED 승인 | EXPIRED, 기존 WAITING 요청 DENIED, 해당 비종결 Task HALTED |
| DISPATCHING READ | 요청 WITHHELD, Task HALTED, 옛 결과 미공개 |
| DISPATCHING SEND | 요청·Task UNKNOWN_OUTCOME, 자동 재전송 0 |
| 그 외 비종결 Task | HALTED. 옛 대화·캐시·epoch로 실행 복원하지 않음 |
| 이미 종결 상태 | RUNNING 복귀 없이 이력·등급·소비 사실 보존 |

CONSUMED를 APPROVED로 되돌리지 않는다. 기존 종결 Task와 미완료 요청이 모순되거나 관계가 깨진 데이터는 정상 초기값으로 추정하지 않고 불일치 보고와 global halt로 처리한다. 기존 Task의 이름·등급 리셋으로 재시작하지 않으며 새 Task의 상태·대화 격리는 A/B 담당이다.

## 10. 변경·검증 계획

| 담당 | 제안 변경 |
| --- | --- |
| B | 원자 승인 API·DTO·오류 정의와 서비스 연결, SQLite·스키마·상태/감사 원자성·관계/revision·시계/ACK·영속 순번·복구·단위 테스트. C-B-01의 SQLite 연결 회귀 대응 |
| C | B 저장소 리뷰, 독립 계약·통합·공격·결함 주입·복구·실제 계수 검증, SQL 제한 정적 규칙. Registry 영속화 계약 검토 |
| A | 시작 실행 게이트·신뢰 시계·제어 큐·Released/Withheld/Unknown 처리와 결과 불명 SEND 재전송 금지 확인 |

| 계획 검증 | 기대 | 연결 |
| --- | --- | --- |
| open/finish 각 쓰기 단계 실패 | Task·요청·승인·감사 전체 rollback, 정상 Pending/Resume 오보고 없음 | I3·I6, T09–T12·T20 |
| 정상 WAITING/RUNNING·승격·정지 조합 | 정상 흐름 보안 버전 유지, 보안 변화 같은 트랜잭션 +1, 올바른 revision, 기존 승인 자체 무효화 없음 | I3·I4, T09·T35·T37 |
| 소비 경쟁·조건 충돌 | 정상 더블클릭 네 계수 각각 1, 조건 충돌·만료 네 계수 0 | I3, T11·T12 |
| DB 대기/트랜잭션/ACK 동안 만료 | 7절대로 False와 커밋된 True 구분, 만료 후 도구 전송 0 | I3·I8, T11·T12 |
| 예약 COMMIT 불명·취소·프로세스 중단 | 새 dispatch ACK 없음, 예약 전 DENY로 오보고하지 않음, SEND 추가 전송 0 | I6·I8, T11·T14–T16·T36 |
| READ/SEND 기록 실패 | READ 미공개·정지, SEND 불명·재전송 없음, 원문·표지 로그 유출 없음 | I4·I5·I8·I9, T07·T19·T35·T36 |
| 다른 Task DTO·옛 epoch 혼입 | 다른 Task 변경 0, 승인 소비·도구 전송 0, 정해진 실패 경계 | I6·I7, C-B-01·T09–T12 |
| 복구·계수 | 옛 승인 실행 권한 없음, 순번 재사용 없음, 결과 불명 SEND 재전송 없음 | I3·I8, T11·T15 |

모두 후속 SQLite 구현·실행할 검증 요구다. SQLite 트랜잭션·성능 측정은 미실행이며 B의 840개 기존 테스트 및 후속 수정의 864개 테스트 재현을 이 제안의 통과 근거로 사용하지 않는다.

## 11. 합의 체크

- [x] C의 보안 버전·원자 승인·ACK/취소·시계·복구 선택 제안 작성.
- [x] B `f3c2d2b`의 C-B-01 수정과 대역 회귀 검증 확인. 실제 SQLite 검증은 미완료.
- [ ] B가 API·DTO·오류·서비스와 SQLite 구현 변경을 검토.
- [ ] A가 결과·시작 게이트·제어 큐·시간·숨은 재전송 영향을 검토.
- [ ] 공통 계약에 구체적 버전/커밋과 실제 A·B·C 동의를 기록.
- [ ] 후속 구현 지시 후 코드 변경과 관련 단위·계약·실제 전송·SQLite 결함 검증 수행.

완료는 제안 작성이며 U1–U8과 공통 계약을 합의 완료로 표시하지 않는다.
