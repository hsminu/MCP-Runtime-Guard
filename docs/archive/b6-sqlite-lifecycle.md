# B6 · 현행 승인·응답 Store 구현과 검증

기준일 **2026-10-13**, 브랜치 `feat/b-security-core`, 구현 전 HEAD `5f8a003`.
사용자의 나머지 연산 구현 지시에 따라 기존 staged 변경을 보존하고 **7개 연산**을 추가했다.
현재 **현행 Store 12/12개 메서드 구현·B 단위 검증 완료**다. DB는 파일 하나, 설계 테이블은
7개이며 12는 메서드 수다. 구현 지시 당시에는 **add까지만** 진행했고, 후속 사용자 지시로
이 검증된 변경의 commit·push를 진행한다. 새 기능 추가나 공동 계약 확정 지시는 아니다.
domain·ports·서비스·lock·스키마 v1·최종 기술설계 원문은 변경하지 않았다.

이 결과는 제품 DB 구성·시작 복구·영속 순번·새 B6 공유 계약·C 독립 인수 완료가 아니다.
새 open/finish API 확정은 [공동 요청](ac-contract-review-request.md)의 실제 회신을 따른다.

## 구현 범위

제품 구현은 [SQLiteStore](../../src/guard_app/storage/sqlite_store.py), 단위 검사는
[test_sqlite_lifecycle.py](../../tests/unit/test_sqlite_lifecycle.py)에 있다.

| 단계 | 메서드 | 상태 |
| --- | --- | --- |
| 첫 단계 | create_task, load_task | 구현·검증 완료. 당시 기록은 [기초](b6-sqlite-foundation.md) |
| 두 번째 | set_task_status, record_decision, reserve_dispatch | 구현·검증 완료. 당시 기록은 [상태·판정·예약](b6-sqlite-decisions.md) |
| 이번 | create_approval, get_approval, transition_approval, consume_and_reserve | 구현·검증 완료. 현행 분리 API의 원자성 한계는 아래와 같음 |
| 이번 | commit_observation, commit_send_outcome, commit_request_failure | 구현·검증 완료. READ/SEND/실패의 원자 저장 |

mypy로 실제 `SQLiteStore → Store` 할당을 검사했다. 포트와 메서드 시그니처가 일치한다.
포트의 과거 C 소유 주석은 공유 파일 변경 없이 두며 현행 소유권은 협업 문서를 따른다.
기존 서비스에 필요한 메서드는 모두 있지만 제품 bootstrap에는 아직 주입하지 않았다.

## 승인 생성·조회·전이

- create_approval은 같은 인스턴스에서 기록한 ASK와 정확한 WAITING Task를 요구한다.
  PENDING/revision=0, 유효 기간, 요청 관계·메타데이터를 검사한다. 승인 행·요청 WAITING·
  APPROVAL_PENDING 감사를 같은 트랜잭션에 저장한다. READ/SEND 외 종류는 거부한다.
- get_approval은 승인 메타데이터만 조회한다. 없으면 None이고 UTC·상태/시각 관계·해시·
  요청/Task 존재 관계가 잘못됐으면 StoreError다. 원문·사본·실행 권한을 복원하지 않는다.
- transition_approval은 설계 8.2의 단방향 전이, 운영자·revision CAS를 검사한다.
  APPROVED에는 생성 당시 WAITING Task 전체와의 일치 및 만료 전 시각을 요구한다.
  종결 전이는 승인·요청 DENIED·감사를 함께 갱신한다. Task 재개는 이 연산에서 하지 않는다.
  CONSUMED는 일반 전이로 만들 수 없다. 종결된 승인이나 중복 전이도 거부한다.

**현행 승인 생성은 Task WAITING 전이 후 create_approval을 별도로 호출한다.**
첫 전이가 성공하고 생성/감사가 실패하면 Task가 WAITING에 남을 수 있다. 단위 검사에서
이 상태를 확인했다. 거절/만료 후 승인 종결과 Task 재개도 별도 커밋이다. 기존 12개 메서드
구현으로 두 간격이 해결됐다고 표시하지 않는다. 새 open_approval/finish_approval은 미합의다.

## 승인 소비·예약

consume_and_reserve는 승인/요청/Task 연결, APPROVED·WAITING, 해시, 운영자/승인 메타데이터,
epoch·security_version·Task 전체 결속·만료와 미예약 상태를 다시 검사한다.
소비하는 승인 revision은 +1, Task는 WAITING→RUNNING과 row_revision +1이며 보안 버전은
유지한다. 정상 전이 버전 유지라는 로컬 선택은 [두 번째 단계](b6-sqlite-decisions.md)와 같다.

**승인 CONSUMED·요청 DISPATCHING/예약 시각·Task RUNNING·CONSUMED 감사**를 한
트랜잭션에서 커밋한다. 변경 행 수가 각각 1이어야 한다. 관계/상태 충돌·중복 소비·만료·
행 수 0은 전체 rollback 후 정확한 False다. SQL·감사·커밋 장애와 잘못된 입력/손상된 저장
형식은 원문 없는 StoreError다. 저장 장애를 단순 False로 숨기지 않는다.

True는 내구성 COMMIT 이후에만 반환한다. DB 소비/예약은 실제 host dispatch·MCP wire·
수신 부작용을 증명하지 않는다. 변경·감사 실패 rollback 후에도 보관된 결속을 사용해 저장
연산을 다시 검사할 수 있으나, 결과 불명 SEND의 전송을 자동 재시도하는 기능은 없다.

## READ·SEND·예약 후 실패

| 연산 | 같은 트랜잭션의 결과 |
| --- | --- |
| commit_observation | READ 예약·요청/Task 결속 검사 후 기존 core.merge 사용. high-watermark·last label·security version·승격 이력·요청 상태·OBSERVED 감사 원자 저장 |
| READ label=None | 미분류 플래그·HALTED·security version +1, 요청 WITHHELD·감사를 함께 저장. 공개하지 않음 |
| commit_send_outcome | SEND 예약과 정확한 Task 결속 검사. 요청 SUCCEEDED·SEND_COMPLETED 감사만 저장하며 Task의 모든 필드와 승격 이력을 보존 |
| READ 알려진 무자료 실패 | 요청 FAILED, Task RUNNING 유지, 감사 원자 저장. 등급 병합 없음 |
| READ 예상 밖 실패 | 요청 WITHHELD·Task HALTED·감사 원자 저장. 기존 등급·미분류 보존 |
| SEND 결과 불명 | 요청 UNKNOWN_OUTCOME·Task UNKNOWN_OUTCOME·감사 원자 저장. 기존 등급·미분류 보존 |

Task 실제 변경은 row_revision +1이고 새 종결 상태 진입은 security_version +1이다.
READ의 같은 상태 관측은 Task 행을 불필요하게 바꾸지 않으며 요청·감사는 저장한다.
하위 등급 READ가 last_read_label을 바꾸어도 high-watermark·승격 이력은 내리지 않는다.
SEND에는 READ 병합을 호출하지 않는다. 미예약/종류 불일치·변경된 요청·중복 응답은 거부한다.

실패 처리로 변경된 비종결 Task를 임의 재개하지 않는다. 이미 HALTED/UNKNOWN/CLOSED인
Task를 다른 상태로 바꾸지 않는다. 이미 해당 실패의 목표 종결 상태인 경우에는 요청/감사를
마무리할 수 있다. 목표 상태가 다르면 StoreError며 기존 ResponseGate/Guard의 global halt로
전달된다. 그 경우 요청이 DISPATCHING에 남을 수 있다. **U8 제어/응답 경쟁·권위 반환 규칙과
시작 정리**는 공동 검토 대상이며 종결 Task를 RUNNING으로 복구해서 맞추지 않는다.

## 메모리 결속·시계·감사

v1 요청 테이블에 epoch/kind/판정 버전/revision이 없어 원문 없는 메모리 결속을 보완했다.
성공한 판정 COMMIT 이후 ALLOW/ASK에 TaskState·요청 메타데이터·kind·지문·출처/목적지 경계를
보관한다. 전체 인자·ToolSpec 정의·결과·승인 사본은 보관하지 않는다. 승인·예약 결속도 COMMIT
성공 뒤에만 게시한다. 정상 소비/응답 완료로 해당 결속을 소모하고 close/연결 폐기로 전부 버린다.

재개방하면 Task/승인 메타데이터를 읽을 수 있지만 과거 승인 전이/소비·ALLOW 예약·응답 커밋을
실행할 수 없다. 이 보수적 차단은 복구 완료를 뜻하지 않는다. 과거 WAITING/DISPATCHING 행의
정리, 새 epoch, 영속 call_seq, Registry/AGT 영속 결속은 아직 구현하지 않았다.

concrete 생성자에 선택적 `utc_now: Callable[[], datetime]`을 추가했고 기본값은
`datetime.now(UTC)`다. 신뢰 공급자는 동기·부작용 없는 UTC datetime 또는 원문 없는 StoreError를
공급해야 한다. TypeError/ValueError/OverflowError는 정리하지만 임의 callback의 모든 예외를
변환하는 경계는 아니다. 앱 연결 시 Guard의 신뢰 시계 구성과 함께 리뷰해야 한다.

승인 생성·APPROVED 전이·소비에서 SQL 트랜잭션 진입 후와 COMMIT 직전에 만료를 검사한다.
전이/소비는 호출 시각과 새 시각 중 늦은 값을 사용하고 쓰기 중 시각 역행도 차단한다.
이 UTC 검사는 기존 서비스의 monotonic deadline을 대신하지 않는다. SQL COMMIT 완료까지의
전체 I/O 상한이나 양시계/ACK 의미를 새로 보장하지 않으며 B6-03의 공동 시간 계약은 미결이다.

감사 ID는 기존 신뢰 audit_id_factory가 공급한다. READ OBSERVED에는 실제 관측 label만
저장하고 실패 감사에는 검증된 ASCII 예외 타입명(최대 128자)만 고정 JSON
`{"exception_type":"DispatchError"}`로 남긴다. 그 외 detail은 NULL이다. 인자·본문·예외 메시지·
인증정보·사본 원문을 저장하지 않는다. SQL/형식 오류는 공개 StoreError로 정리한다.

COMMIT/rollback 확정 불가에서는 연결과 모든 결속을 폐기한다. ACK를 잃어 실제 COMMIT된
경우에도 성공으로 반환하거나 전송 권한을 되살리지 않는다. 새 오류 타입·복구 게이트와 앱
연결은 미합의이며 기존 StoreError/상위 차단 경계를 유지한다.

## 실제 검증

Windows x64, Python **3.12.15**, SQLite **3.53.1**, 기존 B lock/가상환경에서 실행했다.

| 검사 | 결과 |
| --- | --- |
| 전체 B 단위·성질 검사 | **1,395 passed, 41.95초** |
| 이번 추가 | **202개**. 기존 971 + 기초 74 + 두 번째 148 + 이번 202 |
| SQLite 단위 검사 합계 | **424개**, 전체 실행에 포함 |
| Ruff check / format check | src/tests 통과, Python 50개 파일 포맷 통과 |
| strict mypy | 소스 26개 파일 통과. 별도 Store 할당 검사 포함 27개도 통과 |
| pip check | 의존성 오류 없음 |

승인 전이 행렬·운영자/CAS·요청/Task 결속·중복 소비·만료 경계/쓰기 중 만료·시각 역행,
각 승인/요청/Task/승격/감사 쓰기 실패·소비 행 수 0의 전체 rollback을 검사했다.
READ 상향/하향/동일/미분류·여러 관측 이력, SEND Task 불변, 실패 상태 쌍·종결 상태 재개 금지,
재개방 권한 차단, 원문 비저장, 감사 공급 누락, 실제 COMMIT 뒤 ACK 유실도 확인했다.

실제 SQLite를 쓰는 **B ResponseGate 단위 검사**에서는 정상 READ만 커밋 후 Released,
미분류는 Withheld, 감사 실패/ACK 유실 후 실패 기록도 불가능하면 global halt와 StoreError로
본문 공개를 차단했다. MCP·GUI·모델·wire·수신 부작용 연결이나 C 독립 인수 시험은 아니다.
I4/I5/I6/I8/I9, T07/T11/T19/S03의 저장소·B 공개 경계 단위 부분에 해당한다.

재현 명령은 저장소 루트의 가상환경에서 실행한다. basetemp가 저장소 내부인 것을 먼저
확인해야 한다. 기존 pytest cache의 Windows ACL을 피해 cacheprovider는 사용하지 않는다.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp .test-artifacts/sqlite-stage3-full
& '.\.venv\Scripts\python.exe' -m ruff check src tests
& '.\.venv\Scripts\python.exe' -m ruff format --check src tests
& '.\.venv\Scripts\python.exe' -m mypy src/guard_app
& '.\.venv\Scripts\python.exe' -m pip check
```

다음 작업은 새 공유 계약 회신과 별도로 준비할 수 있는 영속 순번·시작 복구의 상세 설계,
합의된 open/finish·오류/버전/Registry·AGT 변경 구현, A bootstrap 연결, C 독립 인수다.
이번 메서드 12/12 완료를 이 작업들의 완료로 확대하지 않는다.
