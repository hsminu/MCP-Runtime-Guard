# B6 · Task 상태·판정 감사·ALLOW 예약 구현

기준일 **2026-10-13**, B `feat/b-security-core`, 구현 전 HEAD `5f8a003`.
이전 [첫 단계](b6-sqlite-foundation.md)의 staged 변경을 보존한 후 사용자의 이어서 작업
지시에 따라 기존 Store의 **set_task_status, record_decision, reserve_dispatch**를 추가했다.
이 단계 당시 **5/12개 연산**과 전체 1,193개 검사를 완료했다. 현재는
[승인·응답 저장](b6-sqlite-lifecycle.md) 추가로 현행 12/12개·전체 1,395개까지 검증했다.
로컬 검증·add까지만 하며 commit·push는 별도 승인 대상이다.
domain·ports·기존 서비스·v1 스키마·lock·최종 기술설계를 변경하지 않았다.
제품 Store/Guard 연결·공동 API 합의·C 독립 인수는 미완료다.

## 구현한 연산

| 연산 | 같은 트랜잭션에서 확인/기록하는 내용 |
| --- | --- |
| set_task_status | 현재 Task·기대 revision·허용 전이·미분류를 검사하고 상태/버전·목표 상태 감사 함께 갱신 |
| record_decision | strict 요청/판정·UTC·실제 인자 해시, Task ID/epoch·RUNNING/분류·policy/security 버전을 검사하고 요청·DECIDED 감사 저장 |
| reserve_dispatch | 같은 인스턴스의 ALLOW 결속·현재 Task/요청 일치·DECIDED/미예약을 검사하고 DISPATCHING·예약 시각·DISPATCH_RESERVED 감사 저장 |

Task 변경·요청 저장·예약 중 감사 ID 충돌/감사 쓰기 실패는 해당 연산 전체 rollback이다.
COMMIT 오류에서는 기존 연결/ALLOW 결속을 폐기하고 성공 ACK나 자동 재시도를 만들지 않는다.
DB 예약을 host dispatch·wire·수신 부작용 성공으로 기록하지 않는다.

## 상태·버전: 구현 선택과 공동 합의의 경계

설계 7.3절의 전이를 적용하고 기존 Guard의 READY 정지 호출과 호환되도록 READY→HALTED를
허용했다. HALTED/UNKNOWN_OUTCOME/CLOSED의 RUNNING 복귀는 금지한다.
미분류 Task의 RUNNING/WAITING 진입도 거절한다. 일반 갱신에서 등급·미분류·epoch·
profile_id·policy_version·승격 근거를 변경하지 않는다.

현재 구현은 정상 READY→RUNNING·RUNNING↔WAITING_APPROVAL에서 security_version을
유지하고 새 HALTED/UNKNOWN_OUTCOME/CLOSED 진입에서 +1 한다. 실제 Task 행 변경에서
row_revision은 +1, 같은 상태 반복은 기대 revision 검사 후 UPDATE/감사 없는 no-op다.
오래된 revision·정수 overflow는 성공으로 처리하지 않는다. 판정 기록/예약은 Task 행을
갱신하지 않으므로 기존 ApprovalService.open의 revision CAS를 스스로 깨지 않는다.

이 값/전이는 **이번 SQLite adapter의 검토용 구현 선택**이다. B6-02 정상 전이 버전 규칙의
A/B/C 합의 기록은 아직 없다. 승인 생성·종결 원자 연산이나 새 공유 API 확정으로 확대하지 않는다.
DENY는 요청 DENIED, ALLOW/ASK는 DECIDED로 저장하는 것도 이 단계의 저장 선택이다.
ASK는 일반 ALLOW 예약 API로 실행할 수 없다.

## 감사 구성과 공개 경계

concrete SQLiteStore의 선택적 구성 인자 `audit_id_factory: Callable[[], str] | None`을
추가했다. 실제 감사 쓰기에는 호출자가 신뢰 ID 공급자를 주입해야 하며, 없으면 rollback한다.
기존 Task 생성/조회는 그대로 사용할 수 있다. C 위험 ID 모듈을 복제하거나 기본 ID 생성기를
만들지 않았다. 제품 구성 루트의 실제 C 공급자 연결/담당 리뷰는 별도다.

공급자는 **동기·부작용 없는 함수**로 검증 가능한 비어 있지 않은 UTF-8 문자열 또는
원문 없는 StoreError를 공급해야 한다. 일반 TypeError/ValueError/OverflowError 검증 실패는
고정 StoreError로 정리하지만 임의 callback의 모든 예외를 변환한다고 보장하지 않는다.
이 구성 계약을 지키는 공급자를 사용하고 상위 허용 실패 경계를 함께 리뷰해야 한다.

감사 사건은 목표에 따라 TASK_RUNNING, TASK_WAITING_APPROVAL, HALTED,
TASK_UNKNOWN_OUTCOME, TASK_CLOSED이며 판정은 DECIDED, 예약은 DISPATCH_RESERVED다.
Task/요청 ID·고정 reason·UTC만 저장한다. 적용 판정 등급을 받는 필드가 없으므로
label=None이고 detail=None이다. high-watermark를 last_read/none 모드의 적용 등급으로
잘못 기록하지 않는다. 모든 reason enum은 requests.reasons의 JSON 배열에, 대표 첫 reason은
감사 reason_code에 기록한다. 인자·도구 정의·SDK reason·결과·승인 사본 원문은 저장하지 않는다.

## v1 예약의 제한

현 v1 요청 테이블에는 epoch·판정 policy/security/revision 필드가 없다. 합의 전 스키마를
확장하는 대신 **검증된 ALLOW 기록의 COMMIT 성공 뒤에만** 원문 없는 결속을 같은
인스턴스 메모리에 게시한다. 결속은 TaskState와 요청의 저장 메타데이터이며 인자/ToolSpec/
승인 사본을 포함하지 않는다.

예약 트랜잭션에서 현재 Task와 요청 메타데이터를 정확히 대조한다. 상태 왕복·등급·정책·
epoch·revision·요청 메타데이터 변경은 옛 ALLOW 예약을 차단한다. 실제 상태 변경은 해당
Task 결속을, 예약 성공은 해당 요청 결속을, close/연결 폐기는 모든 결속을 버린다.
재개방/다른 인스턴스에서 옛 ALLOW를 조회해도 예약 권한을 재구성하지 않는다.

이는 **영속 epoch 결속·복구 완료를 뜻하지 않는다.** 예약 뒤 최신 ACTIVE/지문/정책 재검증은
기존 Guard의 책임이다. 당시 승인 소비·응답 커밋은 미구현이었다. 현재 두 연산을 포함한
12개 구현은 후속 기록을 따르며 시작 정리·영속 순번·Registry/AGT 계약과 제품 주입은 미완료다.

## 실제 검증

Windows x64, Python **3.12.15**, SQLite **3.53.1**, 기존 B lock 환경.
첫 단계 74개를 유지하고 이번 **148개** 단위 검사를 추가했다.

| 검사 | 실제 결과 |
| --- | --- |
| 전체 B 단위·성질 검사 | **1,193 passed, 20.42초** (기존 971+첫 단계 74+이번 148) |
| Ruff check / format check | src/tests 통과, Python 49개 파일 포맷 통과 |
| strict mypy | 소스 26개 파일 오류 없음 |
| pip check | 의존성 오류 없음 |

허용/금지 전이·CAS/no-op·미분류·overflow, 판정 버전/epoch·digest·strict 모델,
중복 요청/순번·ASK/DENY/재예약 차단, 상태 왕복/메타데이터 오염/재개방,
감사 ID/쓰기 실패 rollback·SQL 바인딩·본문 비저장을 실제 파일 DB로 확인했다.
관련 I4/I6/I9와 I2/I3의 저장소 전제, T07/T11/T19/S03 단위 부분을 대조한다.
실제 승인 소비·MCP 전송·프로세스 중단·C 독립 계약/통합 인수 통과로 보고하지 않는다.

재현은 첫 단계와 동일한 환경에서 테스트 전용 경로를 사용한다.

```powershell
New-Item -ItemType Directory -Force .test-artifacts | Out-Null
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp .test-artifacts/sqlite-stage2-full
```

당시 남은 기존 승인 생성/조회/전이/소비, READ/SEND/실패 커밋은 후속 기록에 구현·검증했다.
새 open/finish·버전·오류·복구/Registry/AGT 계약은 [공동 요청](ac-contract-review-request.md)의
실제 합의 후 확정한다. 현재 세 연산 추가를 원자 승인 생성/종결 완료로 표시하지 않는다.
