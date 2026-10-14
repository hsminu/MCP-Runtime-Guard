# B6 · 영속 호출 순번과 재시작 복구 상세 설계안

기준일 2026-10-13, 작성 B. **영속 배정/상태 복구 기능과 이 문서의 A/C 동의는 미완료**다.
후속 사용자 지시로 [읽기 전용 내부 사전 점검](b6-startup-preflight.md)만 구현한다.
기록 MAX·기존 관계 조회의 단위 검증을 아래 기능 전체의 구현/시험 완료로 확대하지 않는다.
코드 기준은 B [`6115fa6`](https://github.com/hsminu/MCP-Runtime-Guard/commit/6115fa65236b58250e9ee9ca2e3d686130e35416),
대조 자료는 A `ab82919`, C/main `4179f98`다. 이 문서로 원본 기술설계나 공유 계약을 변경하지 않는다.
[검증 시나리오](b6-sequence-recovery-validation.md), [공동 회신 입구](ac-contract-review-request.md),
[B6 상위 계약 제안](b6-contract-proposal.md)을 함께 읽는다.

## 1. 이미 정해진 요구와 이번 제안의 경계

| 구분 | 요구 / 근거 |
| --- | --- |
| 최종 설계 요구 | Task 안의 호출 순번과 `UNIQUE(task_id, call_seq)` 유지. 0.2·3.3·11.2절 |
| 최종 설계 요구 | 재시작 후 승인 사본·실행 권한 복원 없음. 8.1–8.2절·ADR-4·T11 |
| 최종 설계 요구 | HALTED/UNKNOWN_OUTCOME에서 RUNNING 복귀 없음. 새 Task로 복구. 7.3절 |
| 최종 설계 요구 | 결과 불명 SEND 자동 재전송 없음. I8·9.2–9.3절·ADR-7·T14–T16 |
| 최종 설계 요구 | 등급·미분류·승격 근거 보존, 결과 커밋 전 공개 없음, 원문 비저장. I4/I5/I9·11.3–11.4절 |
| 기존 사용자 결정 | SQLite·순번·복구·단위 구현은 B, 저장소 리뷰·독립 검증은 C, 앱 시작/Runner/transport는 A |
| A·C 합의 필요 | 계수 스키마·초기값·콜백 연결/오류, 복구 상태 쌍·legacy 처리·epoch 기록·버전·감사·시작 ACK와 실패 표시 |

기술설계 0.2절은 재시작·복구 때 epoch 증가를 정의하고, 7.3절은 복구를 새 Task로 제한한다.
둘의 기록 방식을 아래 REC-03에서 조정해야 한다. 단순한 상태 초기화나 기존 Task 재개로 해석하지 않는다.
C의 [Store 제안 8–9절](tasks/c-store-proposal.md)은 검토 근거이며 동의 기록은 아니다.

## 2. 현재 구현에서 확인한 제약

- Store 포트는 12개 연산이고, 순번/시작 복구 메서드가 없다. Guard는
  `next_call_seq(task_id) -> Awaitable[int]` 콜백을 받는다.
- Guard는 Task를 읽은 뒤 순번을 얻고 Catalog에서 요청을 해석한다. 판정 저장·승인 생성은
  먼저 읽은 Task의 버전/revision에 결속된다. 순번 할당이 Task 행을 변경하면 이 CAS를 깨뜨릴 수 있다.
- v1은 정확한 7개 테이블 DDL을 검사하며 `user_version=1`만 재개방한다.
  새 계수 테이블을 기존 v1에 조용히 추가할 수 없다.
- `requests`에 call_seq는 있지만 epoch·READ/SEND kind·요청 revision은 없다.
  종류/epoch 확인은 같은 Store 인스턴스의 원문 없는 메모리 맥락에 의존한다.
- 재개방 후 과거 승인의 소비·전이, ALLOW 예약, 응답 커밋은 차단한다.
  이는 실행 권한 복원 차단이며 WAITING/DISPATCHING 잔존 행의 정리는 아니다.
- v1은 영속 순번 할당 이력이 없다. 기록되기 전에 사라진 메모리 순번은
  `MAX(requests.call_seq)`로 알아낼 수 없다.

근거 소스는 [`guard.py`](../src/guard_app/services/guard.py),
[`sqlite_store.py`](../src/guard_app/storage/sqlite_store.py), [`ports.py`](../src/guard_app/ports.py)다.
[현행 Store 기록](b6-sqlite-lifecycle.md)과 [QA-09](b-qa09-task-lifecycle.md)의 보수적 차단은 유지한다.

## 3. 영속 순번의 권장 의미 · SEQ-01/02

**B 권장안, A·C 합의 전이다.** `call_seq`는 같은 Task ID의 생애 동안 증가한다.
epoch가 바뀌어도 같은 Task ID의 계수를 0으로 되돌리지 않는다. 기존 UNIQUE와 승격 근거
키가 Task ID 기준이므로 `(task_id, epoch)`별로 순번을 재사용하는 안은 채택하지 않는 쪽을 권장한다.
새 Task ID는 독립 계수를 갖는다. request_id·model_call_id·승인 ID와 순번은 서로 다른 식별자다.

첫 신규 순번은 1, 계수의 초기 `last_issued`는 0으로 제안한다.
기존 순번 0의 이력은 유효하게 보존한다. 빈 순번을 허용한다.
**커밋된 배정은 이후 요청 해석/판정 저장 실패·취소·ACK 유실 때문에 회수하거나 재사용하지 않는다.**
BEGIN/쓰기 실패로 확실히 rollback한 비공개 후보는 아직 배정된 순번이 아니다.
COMMIT 전 후보를 콜백 결과나 실행 권한으로 노출하지 않는다.

계수만으로 승인·예약·전송 권한을 만들지 않는다. `record_decision`의 Task ID/epoch·현재 상태·
판정 버전 결속과 승인 소비/예약 검사는 별도로 유지한다. Task 순번과 새 request_id가 있어도
종결 Task의 SEND를 다시 실행할 수 없다.

### 저장 구조와 이관 · SEQ-03

| 필드 후보 | 제약 / 의미 |
| --- | --- |
| `task_call_sequences.task_id` | tasks 외래키·PRIMARY KEY, 신뢰 Task ID |
| `last_issued` | STRICT INTEGER, 0 이상. 구현의 `_integer`와 같은 상한 `(1 << 63) - 1` 검사 |

독립 계수 테이블을 권장한다. **Task.row_revision/security_version/등급/상태/epoch는 할당으로 변경하지 않는다.**
tasks에 계수 열을 추가하는 대안은 Task 모델·CAS·읽기 DTO 영향까지 대조해야 한다.
테이블/열/감사 사건 이름, 새 schema version 및 요청 kind/epoch 확장과 같은 migration에
묶을지는 합의 대상이다. 아래에서는 다음 스키마를 편의상 v2라고 부를 뿐 번호를 확정하지 않는다.

새 Task 생성은 기존 `create_task` 트랜잭션에서 Task·승격 이력·계수 초기화까지 함께 COMMIT하는 안이다.
정상 새 Task의 last_issued는 0이다. 지원하는 신뢰 초기 State에 승격 순번이 이미 있으면 그 MAX 이상으로
초기화해야 하며, 옛 Task의 상태/대화를 새 Task로 자동 복사한다는 뜻은 아니다.
Task만 생성되고 계수는 없는 부분 성공을 허용하지 않는다. 기존 API 시그니처는 유지하는 안이지만,
내부 쓰기/감사·이관/대역 계약의 변화는 SEQ-03에서 검토한다.

1. 현재 v1의 application_id·정확한 DDL·외래키·정수/관계·승격 이력을 검사한다.
2. 같은 migration 트랜잭션에서 계수 구조를 만들고, Task별 보존된
   `MAX(requests.call_seq)`, `MAX(escalations.at_seq)`, 기존 영속 계수(있는 지원 버전)의 최댓값으로 채운다.
   빈 이력은 0이다. 높은 기존 계수를 낮추거나 행 삭제 후 요청 MAX로 덮어쓰지 않는다.
3. 요청 epoch/kind/revision 확장을 함께 하기로 했다면, 신규 요청은 신뢰 ResolvedRequest 값으로
   필수 저장한다. v1 과거 행의 모르는 kind/epoch를 현재 Registry·도구 이름·인자/결과 원문으로 채우지 않는다.
   legacy NULL 허용 범위와 읽기 표시·복구 거부 조건은 REC-02와 함께 정한다.
4. 데이터 이관·제약 검사·DDL·버전 태그를 함께 COMMIT하고 성공 후 새 DDL을 검증한다.
   지원하지 않는 버전/변조된 스키마는 자동 채택하지 않는다.
5. migration COMMIT과 복구 COMMIT은 별도 단계다. 복구가 실패해도 이미 완료한 스키마 이관은
   남을 수 있지만 실행 게이트는 닫힌 상태다. 두 단계를 하나의 원자 작업이라고 보고하지 않는다.

v1에서 사라진 미기록 배정까지 재구성했다고 주장하지 않는다.
v1의 옛 비종결 Task는 복구로 정지하고 다시 실행하지 않는 안을 적용해야 한다.
새 보장의 시작은 영속 계수 migration 이후의 확인된 배정이다.
그 전에 할당했지만 기록하지 못한 숫자의 복원 보장이 필요하면 추가 신뢰 증거/이관 규칙을 논의해야 한다.

### 할당 트랜잭션

| 순서 | B 구현 후보 |
| --- | --- |
| 1 | A 시작 게이트/Store 준비 상태와 신뢰 task_id를 확인한다. 없는 Task·종결/미분류 Task는 배정하지 않는다 |
| 2 | 지정 storage 모듈에서 `BEGIN IMMEDIATE`. 현재 Task와 계수 행을 읽고 형식·관계·범위를 검증한다 |
| 3 | 예상 `last_issued`를 조건으로 +1 UPDATE하고 영향 행 수가 정확히 1인지 확인한다. 상한이면 쓰기 전에 실패한다 |
| 4 | 할당 감사가 계약상 필수라면 원문 없는 감사도 이 트랜잭션에 포함한다. 감사 실패 시 배정 성공을 반환하지 않는다 |
| 5 | COMMIT 성공 ACK 이후에만 새 순번 반환. 이후 Catalog/record_decision 실패로 계수를 되돌리지 않는다 |

런타임에서 기존 Task의 계수 행이 없거나 기록 MAX보다 낮으면 migration 누락/무결성 오류로
실행을 차단한다. 매 호출마다 MAX로 계수를 재생성하는 fallback은 두지 않는다.
기록 삭제·백업 이관·새 스키마에서의 원문 없는 할당 감사 필요 여부도 SEQ-03 검토 대상이다.

호출 입구 후보는 기존 한 인자 `next_call_seq(task_id)`를 유지하고 신뢰 bootstrap에서
B의 영속 할당 구현을 주입하는 안이다. Store Protocol 메서드 추가 여부/이름은 SEQ-02에서 정한다.
이 콜백에는 expected_epoch가 없다. 따라서 반환 int를 epoch 결속의 증거로 삼지 않는다.
expected_epoch 인자를 추가하는 대안은 Guard 생성자·A 공급자·C 대역의 계약 변경이다.

### 실패와 ACK

| 상황 | 제안 동작 |
| --- | --- |
| 없는 Task / 잘못된 입력 / 상한 | 원문 없는 고정 오류, 순번 반환·요청 저장·dispatch 없음. 정확한 Reason은 SEQ-02에서 합의 |
| 확인된 rollback | 배정 성공 아님. speculative 후보 미노출. Guard의 예약 전 실패 경계 유지 |
| COMMIT/rollback 불명 또는 ACK 유실 | 연결/메모리 결속 폐기, 같은 연산 자동 재시도 없음. 실행을 닫고 새 연결의 시작 복구 이후에만 후속 작업 허용 |
| 배정 COMMIT 뒤 record_decision 실패 | 배정값 보존·번호 공백 허용. 다음 승인 CAS용 Task 맥락은 할당 전과 동일해야 함 |

할당 실패는 dispatch 예약 실패와 다르다. 할당 자체가 `DISPATCHING`을 쓰지 않으므로
순번 COMMIT 불명만으로 SEND가 전송됐다고 추정하지 않는다. 기존 요청 예약의 COMMIT 불명은
B6-03의 별도 예약 후 경계 계약으로 다룬다.

## 4. 재시작 실행 게이트 · REC-01

권장 시작 순서는 다음과 같다. 단계/보고 필드의 이름은 개념 표기이며 새 public enum/API가 아니다.

```text
실행·제어 접수 차단
  → 이전 실행자/DB 소유권 확인
  → B 연결 설정·스키마 검사/migration
  → B 시작 복구 트랜잭션·ACK
  → C Registry/지문·A AGT/transport 준비 검증
  → 새 Guard·Runner 생성 / 권위 상태 projection
  → 실행·제어 개방
어느 단계든 실패·확정 불가 → 닫힌 상태 유지
```

- A는 모델 호출·Task 생성·submit·승인/취소 실행 큐를 복구 완료 전 개방하지 않는다.
  초기 화면과 고정 진행/실패 표시의 읽기 경로는 별도로 정한다.
- 기존 Runner/Guard/worker/AGT와 대기 SEND를 새 실행자로 옮기지 않는다.
  같은 프로세스 재초기화는 이전 실행자의 실제 종료 확인이 먼저다. 종료 미확인은 개방 조건이 아니다.
- **SQLite의 BEGIN IMMEDIATE는 앱 인스턴스의 전체 수명 소유권을 보장하지 않는다.**
  두 번째 앱이 기존 실행자의 전송과 겹치지 않도록 A·C가 DB 인스턴스 단일 소유의
  획득·유지·종료 방식을 정한다. B가 C의 안전 파일/프로세스 연산을 복제하지 않는다.
- DB 복구 성공만으로 전체 앱 READY를 보고하지 않는다. Registry·정책·AGT 준비는 각 담당의
  별도 성공 조건이다. 예전 registrations의 ACTIVE만 읽고 현재 지문 검증을 생략하지 않는다.
- Store의 실행 연산에도 준비 전 거부 검사를 둘지, A의 barrier와 어떤 ACK로 연결할지 합의한다.
  열 수 있다는 것과 복구 완료·제품 실행 가능하다는 것은 서로 다른 상태다.

## 5. 복구 트랜잭션과 상태 대조 · REC-02/04/05

`reconcile_on_startup`/`RecoveryReport`는 상위 B6의 **API 후보**다.
정상 승인/응답 포트를 반복 호출해 조립하지 않는다. 현재 포트는 잃어버린 메모리 맥락을
요구하고 여러 COMMIT을 만든다. 시작 전용 B storage 트랜잭션이 필요하다.

권장 경계는 **한 번의 BEGIN IMMEDIATE에서 전체 대상의 사전 검증 → 관계/상태 변경 →
허용 감사 → COMMIT**이다. 사람·MCP·모델·AGT를 이 트랜잭션에서 기다리지 않는다.
대용량 DB를 여러 batch로 처리하는 대안은 영속 진행 표식·완료 ACK·중간 실패 규칙을
추가로 합의해야 하며, partial recovery 중 실행을 열지 않는다.

### 상태 후보와 우선순위

모든 행을 먼저 검증한 뒤 계획을 만든다. 다음 표는 권장안이며 정확한 상태·Reason·감사는 합의 전이다.

| 재시작 때 남은 상태 | 요청 처리 후보 | 승인 처리 후보 | Task 처리 후보 |
| --- | --- | --- | --- |
| 예약 전 DECIDED, WAITING_APPROVAL | 미실행 요청 DENIED. 예약 시각/소비 사실과 모순되면 정상 차단으로 덮지 않음 | PENDING/APPROVED 실행 권한 폐기 | 옛 비종결 Task HALTED |
| PENDING/APPROVED 승인 | 관련 미예약 요청 DENIED | EXPIRED 권장. 소비 시각/요청 DISPATCHING과 모순되면 실패 | 옛 비종결 Task HALTED |
| DISPATCHING + 신뢰 READ kind | WITHHELD | CONSUMED는 그대로 보존 | HALTED, 결과 미공개 |
| DISPATCHING + 신뢰 SEND kind | UNKNOWN_OUTCOME | CONSUMED는 그대로 보존 | UNKNOWN_OUTCOME, 추가 SEND 0 |
| READY/RUNNING/WAITING, 진행 요청 없음 또는 과거 종결 요청만 있음 | 과거 요청 결과 유지 | 과거 종결 승인 유지 | 옛 Task HALTED. 자동 RUNNING 복원 없음 |
| 이미 종결된 정상 Task·요청·승인 | 결과/이력 그대로 | CONSUMED 포함 종결 사실 그대로 | RUNNING 복귀 없음 |
| DISPATCHING kind/epoch 유실, 관계 파손, 종결 Task와 미완료 상태의 미합의 조합 | 추정해서 수정하지 않음 | 권한 복원 없음 | 기본은 복구 실패·게이트 닫힘. 명시적 격리 대안은 REC-02 회신 필요 |

PENDING→EXPIRED의 재시작 전이는 기술설계 8.2절에 있다.
APPROVED도 EXPIRED로 표시할지, INVALIDATED와 구분할지는 ADR-4의 실행 무효 요구와
현재 enum 허용 전이를 대조해 합의한다. CONSUMED를 APPROVED/PENDING으로 돌리지 않는다.
재시작 시 원문 없이 저장된 승인·대화·결과를 복원하는 기능은 만들지 않는다.

한 Task에 여러 요청이 남으면 전체 집합으로 종결 상태를 계산한다. **SEND 결과 불명이
있는데 READY/RUNNING/WAITING 정리 규칙으로 HALTED로 덮는 순차 UPDATE를 하지 않는다.**
제안 우선순위는 신뢰 DISPATCHING SEND의 UNKNOWN_OUTCOME > 그 외 비종결 HALTED다.
이미 HALTED/CLOSED인데 DISPATCHING SEND가 남은 U8 조합은 현재 허용 전이로
UNKNOWN_OUTCOME에 바꿀 수 없다. 그 예외 전이 또는 별도 격리 방식을 REC-02에서 결정한다.
종결 Task를 RUNNING으로 되돌려 정리하지 않는다.

### 현재 분리 API가 남길 수 있는 정상 중간 상태

| 실제 중단 지점 | 해석 / 복구 후보 |
| --- | --- |
| Task WAITING COMMIT 뒤 create_approval 전 | Task WAITING + DECIDED ASK + 승인 없음은 현행 두 커밋의 가능한 중단 상태. 예약/소비 증거가 없으면 요청 DENIED·Task HALTED로 정리하는 안 |
| 승인 종결 COMMIT 뒤 Task resume 전 | REJECTED/EXPIRED 등 종결 승인·DENIED 요청 + Task WAITING. 승인은 보존하고 Task HALTED로 정리하는 안 |
| 소비/예약 COMMIT 뒤 전송 또는 결과 저장 전 | CONSUMED + DISPATCHING. kind/epoch에 따라 READ 미공개/SEND 불명, 소비는 보존 |
| 결과 COMMIT 뒤 모델/UI 공개 전 | SUCCEEDED 및 관측 이력 보존. 결과 원문은 DB에서 복원하지 않고 옛 Task는 재개하지 않음 |

정상 중간 상태와 관계 파손을 구분한다. 예를 들어 승인 request_id의 FK 유실이나
PENDING 승인과 DISPATCHING 요청의 모순을 전부 정상 crash로 보정하지 않는다.
최종 허용 조합은 C의 U3/U7/U8과 B6-01/02에 대조한다.

### v1의 미완료 요청

v1의 kind/epoch 유실은 현재 Registry의 같은 server_id/tool_name으로 해결하지 않는다.
도구 종류·정의가 바뀌었을 수 있고, 지문까지 v1 요청 행에는 없기 때문이다.
권장 기본은 해당 DISPATCHING 행 발견 시 전체 복구를 성공으로 보고하지 않고 gate를 닫는 것이다.
대안은 종류 미확정 요청을 결과 불명으로 격리하고 새 Task만 허용하는 안이다.
그 경우 legacy NULL 표시·Task 상태 충돌·사용자 확인·감사·READY 조건을 REC-02에서 정해야 한다.
READ라고 추정해 처리하거나 재전송해서 확인하는 대안은 두지 않는다.

## 6. epoch·버전·시간·감사 · REC-03/04

| 항목 | 비교할 선택과 권장 경계 |
| --- | --- |
| recovery epoch | A안: 복구로 정지하는 옛 Task epoch 증가 + 과거 requests의 old epoch 보존. B안: 옛 terminal Task epoch 보존 + 새 Task ID/새 회차 증가의 영속 관계 기록. 기술설계 0.2·7.3절을 함께 만족하는 정확한 기록을 A·B·C가 확정 |
| 같은 ID의 순번 | 어느 epoch 안이든 Task ID가 같으면 과거 계수보다 작아지지 않음. 옛 종결 Task는 순번 배정/실행 불가 |
| Task 버전 | 상태/epoch의 실제 보안 변화는 한 트랜잭션 합계 security_version +1, 실제 Task 행 변경은 row_revision +1 권장. 이미 정리된 행은 매 시작마다 무조건 올리지 않음. B6-02 합의 전 |
| 승인/요청 revision | 실제 변경한 객체만 +1 권장. 요청 revision은 v1에 없으므로 migration/API 합의 필요 |
| UTC 기록 | 승인 종결 기록은 QA-01처럼 생성·직전 결정/소비 시각 이상. 기존 expires_at은 바꾸지 않음. 재시작 승인 폐기는 시간 만료를 기다리지 않음 |
| 감사 | 상태 변경과 같은 트랜잭션. 신뢰 ID·고정 코드·old/new 상태·epoch/revision·배정값 등 허용 메타데이터만 후보. 원문·인증정보·ResolvedRequest 전체는 저장하지 않음 |
| 반복 실행 | 같은 복구 결과에 대해 중복 상태 변경·revision 증가·소비·전송·행별 감사 없음. 별도 시작 점검 감사와 복구 run_id/완료표식의 필요/형식은 합의 |

epoch 증가는 같은 복구를 다시 실행할 때 계속 올리는 방식으로 정하지 않는다.
변경 대상이 이미 종결됐음을 확인하거나, 영속 run_id/진행 표식으로 중복 적용을 구분해야 한다.
프로세스 강제 종료·COMMIT ACK 유실 후에도 이 판별은 메모리 boolean에만 의존하지 않는다.

RecoveryReport는 schema version·복구 run 식별·변경 Task/요청/승인 수·불명/미해결 수 등의
원문 없는 후보 필드를 갖는다. 성공 반환은 내구성 COMMIT ACK를 뜻한다.
불명/미해결이 있는 report를 성공 READY 토큰처럼 사용하지 않는다.
읽기 projection과 변경 ACK는 구분하고, UI에는 고정 실패 코드와 권위 상태만 전달한다.
이름·DTO·필드·감사 사건/Reason은 아직 코드에 추가하지 않았다.

## 7. 장애·기존 백업·메모리 수명 · REC-05

- migration/복구 쓰기·감사·검증 실패는 원자 rollback을 확인하고 실행을 닫아 둔다.
  COMMIT/rollback 불명이면 연결과 실행 맥락을 폐기하고 성공 ACK나 자동 재시도를 만들지 않는다.
- 복구 ACK 유실 뒤 새 인스턴스는 실제 DB를 다시 읽어 정리 결과를 판별한다.
  반복 정리는 MCP 재전송이 아니지만, 살아 있는 Store 연산을 자동 반복하는 것과 구분한다.
- `busy_timeout_ms=1000`은 현재 설정 후보이며 COMMIT·디스크 I/O 전체 상한이나
  async 취소 가능 시간을 보장하지 않는다. 제품 예산·측정·UI 응답성은 A·C와 검토한다.
- 과거 DB 백업으로 바꾸면 최신 배정 계수·결과/소비 사실이 사라질 수 있다.
  **같은 파일의 통상 재시작 보장을 임의의 오래된 백업 복원까지 확대하지 않는다.**
  백업 교체/Task ID 재사용·이력 삭제는 별도 관리 절차와 새 실행 회차/Task 격리가 필요하다.
- 같은 프로세스의 실패 후 재초기화도 이전 작업 종료와 startup gate가 먼저다.
  QA-09가 남기는 미확정 stop/closing 맥락을 TTL로 지우고 기존 Guard를 재개하지 않는다.
  새 Guard의 빈 메모리 사본은 이전 승인 권한을 복원하지 않는다.

## 8. A·C 회신 표

아래 **8개 항목은 미합의**다. 답변은 `동의 / 수정안 / 미결`, 기준 커밋·검토자·날짜·
자기 모듈 영향·검증 조건을 포함한다. 문서 작성이나 응답 없음은 동의가 아니다.

| ID | 결정할 것 / B 권장안 | A가 확인할 것 | C가 확인할 것 | 상위 계약 |
| --- | --- | --- | --- | --- |
| SEQ-01 | Task ID 생애 단위 증가, 신규 1부터, 공백/커밋 배정 회수 금지 | model_call_id·UI 호출 번호와 혼동 없는 표시, 새 Task 생성 | UNIQUE/승격 이력·여러 epoch·상한·경합 조건 | B6-02 |
| SEQ-02 | 기존 한 인자 콜백 유지/영속 구현 주입 권장, API·오류·감사 ACK | bootstrap 주입·실패 표시, expected_epoch 확장 필요 여부 | allocator 반환 형식·불명 ACK·자동 재시도 차단, 대역 계약 | B6-02/03 |
| SEQ-03 | 독립 계수 구조·새 schema/migration·legacy 보장 범위 | 지원 DB 버전·백업/교체 표시 | DDL·MAX 이관·계수 행 누락·기록 삭제·원문 비저장 | B6-02/03/06 |
| REC-01 | 단일 인스턴스 소유·준비 전 실행/제어 차단·성공 ACK barrier | 이전 Runner/worker 종료·시작 순서·UI와 backend 준비 | 안전 소유권 연산·다중 인스턴스·실제 계수/종료 확인 | B6-04/05, AGT-03 |
| REC-02 | 상태 표·분리 API 중단 상태·legacy 종류 유실/종결 충돌 처리 | READ 비공개/SEND 불명 표시·오래된 승인 제거·자동 replay 0 | U3/U7/U8·실제 crash 행·미확정/파손 구분·수신 계수 | B6-01/02/04 |
| REC-03 | epoch 증가와 새 Task 복구의 영속 관계, 과거 요청 epoch 보존 | 새 Task 대화/캐시 격리·옛 명령 처리 | 요청/승인/Task old epoch 결속·반복 복구·범위 초과 | B6-02/04 |
| REC-04 | 버전·run_id/멱등 적용·감사/UTC·RecoveryReport | ACK와 projection 구분·준비 표시·UI 읽기 필드 | +1/변경 0 규칙·감사 원자성/중복·시계 역행·I9 | B6-02/03/05 |
| REC-05 | 전체 복구 TX·불명 COMMIT·오류/대기 예산·백업/잔존 메모리 처리 | 실패 시 계속 닫기·재시작 안내·이전 실행자 정리 | rollback/프로세스 중단·불명 ACK·큰 DB/잠금·백업 경계 | B6-03/04, QA-03/10 |

실제 합의는 [contracts.md](contracts.md)에 기록한다. 설계·시나리오 준비는 이번 사용자 요청이고,
공유 모델/포트·migration·복구 코드 구현과 실제 시험 실행은 후속 작업이다.

## 9. 합의 후 구현·인수 순서

1. SEQ-01–03·REC-01–05 회신 대조, 기존 B6-01–05와 충돌 정리, 공통 계약 기록.
2. B: schema/migration·계수 할당·ACK 경계와 단위 검증. A: 기존 콜백 주입 준비.
3. B: 시작 전용 복구·관계 검증·버전/감사/반복 적용·단위 검증.
4. A: 단일 소유·시작 barrier·새 Guard/Task/Runner·UI 상태 연결. C: 독립 계약/결함 검증.
5. C: 프로세스 강제 종료·stdio/HTTP 실제 수신 계수 검증. A: 자동 replay/모델 호출 차단 확인.

최초 설계 준비는 문서 add까지만 진행했다. 이후 사용자 지시로 B 내부 읽기 전용 점검기와
그 단위 테스트를 추가한다. 계수/새 schema·복구 상태·공유 API·A/C 연결은 미합의라 구현하지 않는다.
최초 변경은 add까지 진행했고, 이후 2026-10-13 사용자가 설계/점검기 묶음의 commit·push를 지시했다.
자료 공개는 SEQ/REC 계약 동의·기능 구현 또는 팀원 메시지 전달 완료를 뜻하지 않는다.
