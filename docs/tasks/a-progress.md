# A 진행 현황

2026-10-13 현재 기준은 병합 **`4388298`**(B `origin/feat/b-security-core` **`6115fa6`** 반영)이다.
SQLite 앱 선택·새 Task 저장/재조회 시작 게이트·같은 백엔드 스레드의 마지막 Store 종료를 연결했다.
1라운드 작업 트리 검증은 **전체 2,155 passed, 0 failed**(119.96초), 새 SQLite 앱 **27 passed, 0 failed**(9.71초)다.
1라운드 변경 Python 5개 Ruff check·format check 통과. 네트워크 제한으로 `uv run --no-sync`를 사용했다.
2라운드는 T ID 매핑을 정정하고 SQLite/mock/HTTP 정상 ASK·ALLOW·DENY를 추가 검증했다.
최종 전체 **2,158 passed, 0 failed**(97.61초), SQLite 앱 **30 passed, 0 failed**(11.69초), 변경 Python 1개 Ruff check·format check 통과. 아래 SQLite 절에 라운드별 명령·결과·한계를 기록한다.
시작 복구·영속 call_seq·조회/감사 API는 [B-SQL-01–05](a-b-sqlite-requests-2026-10-13.md) 후속이다.

아래 기존 동기화/QA 수치는 당시 기록이다. A `281cbae`에 B `3bf414c`(코드 보완 `29025e0`)를 반영했다.
환경 분리·현재 AGT 차이·검증은 [최신 B 동기화 기록](a-b-sync-2026-10-13.md)을 따른다.
2026-10-13 전체 QA 2차 반영은 [QA 2차 반영 기록](a-qa-fixes-2026-10-13.md)을 따른다. 반영 후 전체 **1,485 passed**,
spike 32경로 통과. B 소유 결함 10건은 같은 문서의 B 요청으로 남겼다.
동기화 후 전체 **1,371 passed**, 별도 B 환경 **971 passed**, AGT real/mock × stdio/HTTP **32경로 통과**.
변경 이력은 현재 브랜치의 git log에서 확인한다. 과거 세부 증거와 미정 항목은
[이전 기록 및 로컬 QA 수정 근거](a-progress-history.md), 현재 재현법과 한계는 [리뷰 반영 기록](a-review-fixes.md)을 따른다.

| 항목 | 상태 | 막힌 이유 | 다음 행동 |
| --- | --- | --- | --- |
| A01·A03 계약 | B ref·테스트 동기화, Runner epoch 전달 | domain/ports 합의 표는 미확정 | B ref 변경마다 전체 단위 검증 |
| A02·A06 환경 | AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현 | Scanner 선정·공통 제품 조합 미완료 | C Scanner 조합 인수 |
| A04 C 연결 | 요구사항 전달 | C launcher·Registry 미연결 | C 인수 환경에서 대조 |
| A05 UI 계약 | 앱 내부 Task projection·사람 review 연결 | 공통 조회 DTO 합의 미완료 | B·C 정식 계약 검토 |
| A07·A11 stdio | 실제 SDK·Guard 왕복 스파이크 실행 | 정식 C 수신 fixture 미사용 | C 인수 증거로 확장 |
| A08·A12 HTTP | [Streamable HTTP adapter·loopback 왕복·JSON/SSE·재전송 차단](a-http-integration.md) | C endpoint 검증·독립 fixture 인수·인증 확장 미완료 | C 등록/endpoint 연결과 독립 계수 인수 |
| A09·A10 worker | 구현·회귀 검증 | 수신 직전 wire-size 상한은 C 의존 | C stream ingress 연결 |
| A13 종료 | SQLite 같은 스레드 생성/close·runner→transport→AGT→Store 종료 순서·중간 실패 정리·close 실패 미확인 표시 검증 | 이전 Task 종결/시작 복구 미지원 | B-SQL-02 복구 계약·C 독립 인수 |
| A14 원시 출력 | 필수 errlog·stdio/HTTP SDK와 합성 HTTP 서버 로그 격리 | 정식 C 공개 handler 인수 없음 | C 로그 정책·A39 함께 검증 |
| A15·A16 등록 | 요구·단위 구현 | 지문/Registry/launcher 미연결 | 실제 등록 계약 연결 |
| A17 AGT 선정 | [core 5.0.0 선정](a-agt-selection.md), Windows 판정 스파이크 8경로 통과 | Scanner와 함께 쓸 공통 제품 조합 미검증 | C 조합 인수 |
| A18 AGT 연결 | [실제 로컬 판정·명시적 real/mock·timeout/취소 검증](a-agt-integration.md), 10-13 QA 반영(동시 16·멈춘 worker 교체·종료 확인·한국어 결과 문구) | [AGT 역할·규칙 관리 결정](a-agt-role-decision.md) 미확정, 영속 mode 감사·C 독립 인수 미완료, B Guard 4개 상한 연결 단위 검증 완료(독립 인수 별도) | 역할 결정, B 규칙/감사 검토, C dispatch 0 인수 |
| A19·A20 모델 | 중립 adapter·한계 단위 검증 | 실제 제공자 미선정 | 승인 환경 확정 |
| A21·A22 Runner | expected_epoch·승인 resume 연결 | 실제 모델 세션 격리 미검증 | 제공자 선정 후 T21 |
| A23 B 리뷰 | AGT 지연 중 긴급 차단 수정·회귀 검증 | C 독립 wire 검증 미실행 | B·C 소유자 리뷰 |
| A24–A27 bridge·앱 | `--store sqlite --db-path` 연결, Store 열기·새 Task 저장/재조회 후 시작, 실패 시 FAILED/STARTUP_FAILED·run 거부 | 기본 Store는 메모리, Registry는 B 대역. 복구/전체 조회 없음 | B-SQL-01–05 및 C 구성 연결 |
| A28–A32 화면 | 기존 읽기 화면 유지 | 제품 Registry/Scanner 조회 없음 | 합의된 조회 연결 |
| A33·A34 승인 | Runner가 관찰한 ID·Store.get_approval로 PENDING 표시, SQLite 승인·중복 승인·재시작 권한 미복원 검증 | 과거 승인/제품 목록 통합 미완료 | B-SQL-02/03 이후 동일 사람 capability로 연결 |
| A35 로그 | enum 값 매핑 수정 | 영속 감사 조회 미연결 | B6 조회 연결 |
| A36·A37 제어·표시 | 확정 상태/요청됨 분리, 한글 표시 수정 | C 전체 GUI 인수 미실행 | 실제 운영 구성 검증 |
| A38–A40 통합 | 기존 합성 경로 + SQLite/mock/stdio·HTTP ASK·ALLOW·DENY 계수 검증(HTTP 정상은 2라운드), stdio 재시작·stdio/HTTP 시작 실패 게이트 검증 | 전체 복구·C launcher/endpoint·독립 인수는 이번에 미실행 | C 정식 T/S·독립 wire 계수 검증 |
| A41 패키징 | lock·설치·단위 검증 | 전체 보안 스캔/CI는 미실행 | C CI 연결 |
| A42 인수 | 현재 문서 간소화 | 제품 인수 의존성 위 표 참조 | 리뷰 결과와 검증을 함께 인계 |

원격 리뷰 당시 검증: 전체 단위·앱 smoke **1153 passed**, lock 새 환경의 stdio 6경로 재현.

현재 실행: 저장소 루트에서 `uv sync --locked`, `uv run python -m guard_app --demo --agt-mode real`.
합성 자료 전용이다. `--store sqlite --db-path <파일>`로 B Store를 선택할 수 있으며 기존 부모 디렉터리가 필요하다.
실제 모델·외부 송신·시작 복구를 포함한 제품 인수 완료로 표시하지 않는다.

## 2026-10-13 동기화

로컬 QA 수정 `0882890`–`dc8d77b`와 원격 A `62546cf`를 병합했다.
세션 종료 확인·늦은 bootstrap 자원 정리·승인 경합 뒤 Task 상태 재확인과
상세창 재사용·공개 텍스트 표시·stdio 입력 상한을 원격 앱 실행 경로와 함께 유지한다.
미추적 QA 보고서·재현 자료는 사용자 작업 트리에 보존하며 병합 커밋에 포함하지 않는다.

공통 기준 `origin/main`의 `4179f98`도 병합해 SQLite B6 소유권과 C 독립 검증 문서를 반영했다.
병합 후 전체 단위·앱 테스트 **1179 passed** (29.74초, Qt offscreen), 충돌한 Python 파일과 Runner 회귀 테스트의 Ruff 검사를 통과했다.
Runner 테스트 대역도 `expected_epoch`를 받아 검증하도록 원격 계약에 맞췄다.

실제 stdio 합성 서버의 allow·deny·승인·거절·취소·긴급 차단 6경로도 통과했다.
bridge·앱 runtime 집중 재검증은 **41 passed** (3.98초)였다. 정식 C wire·SQLite 인수와는 구분한다.

## 2026-10-13 실제 AGT 연결

`agent-governance-toolkit-core==5.0.0`의 로컬 PolicyEngine을 기존 AccessChecker에 연결했다.
CLI의 `--agt-mode real|mock`은 필수이고, real 초기화 실패 시 mock으로 전환하지 않는다.
규칙은 신뢰 도구의 ID·종류를 해시한 정확 일치 목록이며 SDK에 전체 인자·문서를 주지 않는다.
timeout·취소·늦은 결과 폐기·종료 미확인과 Guard의 거부권 합성을 검증했다.

새 lock 환경 설치 66개 배포판의 의존성 검사 성공, 전체 테스트 **1,243 passed**
(40.11초, Qt offscreen, SDK deprecation 경고 4건), real/mock stdio 각각 8경로 통과.
상세 계수·재현법·B 규칙/감사와 C 독립 인수 의존성은 [A18 연결 기록](a-agt-integration.md)을 따른다.

## 2026-10-13 HTTP MCP 연결

Streamable HTTP factory·로컬 TCP 서버를 연결했고 CLI에서 `--mcp-transport http`로 실행한다.
JSON/SSE × stateful/stateless, 307/308·인증/세션 오류·응답 유실·timeout·취소의
SEND 추가 전송 0을 검증했다. SDK/client·합성 서버 로그도 같은 context에서 격리한다.

새 lock 환경 설치·66개 배포판 의존성 검사 성공, 전체 **1,282 passed**
(22.55초, Qt offscreen, SDK 경고 5건), HTTP 앱의 AGT real/mock 각각 8경로 통과.
HTTP 단위 probe의 wire 계수와 제품 endpoint/인증·C 독립 인수 한계는
[A08/A12 연결 기록](a-http-integration.md)을 따른다.

## 2026-10-13 SQLite 앱 연결과 시작 게이트

- 변경 기준: HEAD `4388298`, B `6115fa6`. A 조립/CLI·Runner·bridge·새 A 단위 테스트만 수정했다.
  B/C 소유 파일, 사용자 미추적 QA 보고서·증거 디렉터리는 수정하지 않았다.
- 생성자는 설치 SDK 추정 대신 현재 B 코드의 `SQLiteStore(path, *, busy_timeout_ms=1000,
  audit_id_factory=None, utc_now=None)`를 확인했다. UUID 감사 ID를 공급하며 B 기본 busy timeout을 유지한다.
  B Store와 Guard 기본 UTC 시계는 모두 `datetime.now(UTC)`다. 경로는 CLI 신뢰 설정에서만 받으며 부모를 만들지 않는다.
- sqlite/memory 인자 조합을 검증하고 Store 종류를 제목에 표시한다. DB 전체 경로는 제목·실패 문구에 넣지 않는다.
  SQLite의 빈 경로·`:memory:` 거부도 검사했다. 기본 메모리 bootstrap과 기존 AGT/bridge/runtime 호출은 전체 회귀에 포함했다.
- SQLite 열기/스키마 검증 → 새 UUID Task create/load 동등성 확인 → AGT/MCP → Guard/Runner 순서다.
  실패를 숨기지 않고 bridge의 기존 시작 경계로 전달한다. Task·request·approval·audit ID는 실행 간 충돌을 피한다.
  영속 call_seq는 2026-10-13 사용자 결정으로 B 보류이며 새 Task별 메모리 카운터만 사용한다.
- 대기 승인 ID는 Runner가 실제 받은 Pending에서만 관찰하고 공개 `get_approval`로 PENDING을 확인한다.
  SQLite 비공개 속성이나 메모리 Store 전용 `approvals`를 조회하지 않는다.
- AsyncExitStack에 Store close를 가장 먼저 등록하고 Runner도 등록한다. 중간 실패에도 역순 정리한다.
  Store close 실패는 A 내부 `BackendCleanupError`(StoreError 하위 타입)로 고정 코드만 전파한다.
  정상 실행 종료는 CLEANUP_UNCONFIRMED, bootstrap 도중 정리 실패는 STARTUP_CLEANUP_UNCONFIRMED로 표시하며
  일반 열기 실패의 FAILED/STARTUP_FAILED와 구분한다. 새 공통 Store 오류 계약을 정의한 것은 아니다.

### 실행한 검증

아래 기존 행은 1라운드 결과이며 수치를 보존한다.
Windows, 기존 `.venv`, `QT_QPA_PLATFORM=offscreen`. PATH 대신 `.devtools/uv/bin/uv.exe`를 사용했다.
`UV_CACHE_DIR=.cache/uv`, `UV_PYTHON_INSTALL_DIR=.devtools/python`는 저장소 절대 경로로 지정했다.
기본 `uv run`은 editable 패키지 동기화 중 `setuptools==84.0.0` 다운로드가 네트워크 제한(os error 10013)에
막혀 검사 실행 전에 실패했다. 의존성/lock을 변경하지 않고 설치된 환경에서 `--no-sync`로 실행했다.
이 실패는 실행 환경 원인이며 A/B 테스트 결함으로 분류하지 않는다.

| 실제 명령 (`uv`는 위 로컬 실행 파일, `<R>`은 저장소 절대 경로) | 결과 |
| --- | --- |
| `uv run pytest -q`, `uv run ruff check <변경 Python 5개>`, `uv run ruff format --check <변경 Python 5개>` | 각각 종료 코드 1, 패키지 동기화 단계 네트워크 차단. 테스트/검사 본체 미실행 |
| `uv run --no-sync pytest -q tests/unit/test_sqlite_app.py` | **27 passed, 0 failed**, 9.71초, 경고 5개 |
| `uv run --no-sync pytest -q` | **2,155 passed, 0 failed**, 119.96초, 경고 6개 |
| `uv run --no-sync ruff check examples/synthetic_app.py src/guard_app/__main__.py src/guard_app/services/bridge.py src/guard_app/services/runner.py tests/unit/test_sqlite_app.py` | All checks passed |
| `uv run --no-sync ruff format --check examples/synthetic_app.py src/guard_app/__main__.py src/guard_app/services/bridge.py src/guard_app/services/runner.py tests/unit/test_sqlite_app.py` | 5 files already formatted |
| `git diff --check` | 통과 |
| 2라운드 최초: `<R>\.venv\Scripts\ruff.exe format --check tests/unit/test_sqlite_app.py` | 종료 코드 1, 1 file would be reformatted; 포맷 적용 후 재검증 |
| 2라운드 최초: `<R>\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/unit/test_sqlite_app.py` | **23 passed, 7 failed**, 13.35초, 경고 5개; 추가 계수 검사의 콜백 타입 오해 수정 |
| 2라운드 최종: `<R>\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/unit/test_sqlite_app.py` | **30 passed, 0 failed**, 11.69초, 경고 5개 |
| 2라운드 최초 전체 실행 시도: `<R>\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | 비동기 실행의 완료 출력·종료 코드를 회수하지 못해 결과 미확정; 통과 집계 제외 |
| 2라운드 최종: `<R>\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | **2,158 passed, 0 failed**, 97.61초, 경고 5개 |
| 2라운드 최종: `<R>\.venv\Scripts\ruff.exe check tests/unit/test_sqlite_app.py` | 종료 코드 0, All checks passed |
| 2라운드 최종: `<R>\.venv\Scripts\ruff.exe format --check tests/unit/test_sqlite_app.py` | 종료 코드 0, 1 file already formatted |
| 2라운드 최종: `git diff --check` | 종료 코드 0 |

1라운드 전체 경고는 기존 AGT deprecated import 4개·Pydantic forward reference 1개·pytest 캐시 ACL 1개다.
1라운드 집중 시험 경고는 AGT 4개와 캐시 ACL 1개다. 1라운드 실패 테스트는 없었다.

2라운드는 `QT_QPA_PLATFORM=offscreen`, `PYTHONIOENCODING=utf-8`(재검증 출력)로 기존 `.venv` 실행 파일을 직접 호출했다.
셸 도구가 프로세스 생성 단계에서 실패해 사용자 지시대로 `node_repl`의 `child_process.execFile`을 사용했다.
초기 추가 검사의 7개 실패는 dispatch 콜백이 문자열 request ID를 받는데 테스트가 요청 객체로 취급한 A 테스트 오류였다.
실제 콜백 계수의 실행 전후 차이와 projection·서버 수신 계수를 대조하도록 수정했다. B/C 구현은 변경하지 않았다.
최초 format check는 1개 파일 재포맷 필요로 실패했고 포맷 적용 후 표의 최종 검사에서 통과했다.
커밋 전 별도 재실행(같은 .venv, offscreen): 전체 **2,158 passed, 0 failed**(139.73초).
전체 검사의 최초 비동기 실행 시도는 결과 미확정으로 남기고, 도구 호출을 완료까지 유지해 다시 실행한 결과만 집계했다.
2라운드 최종 집중/전체 경고는 각각 AGT deprecated import 4개·Pydantic forward reference 1개다.
2라운드 변경 파일은 `tests/unit/test_sqlite_app.py`, 이 문서, `a-b-sqlite-requests-2026-10-13.md`뿐이다.

- 정상 ASK(stdio 및 2라운드 HTTP): 승인 전 READ dispatch/receiver_calls=1, SEND 효과=0. 승인·중복 승인 후 전체 dispatch/receiver_calls=2,
  SEND 효과=1, 승인 CONSUMED와 Task RUNNING을 공개 Store API로 확인했다.
- ALLOW(stdio/HTTP): 전체 dispatch/receiver_calls=2, 효과=1. DENY(stdio/HTTP): READ만 1회, SEND dispatch/효과=0.
  HTTP는 실제 localhost TCP 합성 서버의 `spike.counts`와 `TaskProjection.host_dispatch/receiver_effect`를 대조했다.
  승인 전 1/1/0, 승인·중복 승인 후 2/2/1, DENY 1/1/0(dispatch/receiver_calls/effect)이며 AGT는 mock이다.
- 없는 부모·손상 파일·빈 경로·`:memory:`: stdio/HTTP 모두 transport.start=0, host_dispatch=0,
  AGT 시작=0, stdio stream/counts 파일 구성=0 또는 HTTP 서버 시작=0. Guard는 시작되지 않는다.
  Task create/load 실패·재조회 불일치도 실행 자원 시작 0과 Store close를 확인했다.
- 재시작: 새 Store·HumanControl·Task, 과거 승인 review=None·approve=Denied, 2회차 dispatch/수신 효과=0.
  테스트만 과거 행을 조회해 변경 없음 확인. 동일 DB 연속 정상 승인 실행은 각각 SEND 1회였고 다시 열기도 성공했다.
- 종료: 같은 소유 스레드에서 Store가 마지막 종료. AGT/transport 중간 시작 실패·Store close 실패 표시도 검사했다.
- 관련 I/T(2라운드 정정): I1–I9 중 해당 경계와 T01/T02/T03/T11/T12/T13/T19의 A 부분 검증.
  T01–T03은 판정·승인 표시·수신 계수, T11은 재시작 시 과거 승인 실행 0, T12는 순차 중복 승인 후
  CONSUMED·SEND 1회, T13은 같은 내용의 별개 정상 요청 각각 처리, T19는 오류·CLI 출력의 경로/표지 비노출이다.
  시작 게이트·설정 거부·종료 정리는 직접 대응 T/S ID 없음(A 시작 게이트 I1/I6, 설정 I6/I7/I9, 자원 수명 I6).
  T07은 실행 중 커밋 실패→HALTED, T08은 정적 import 규칙이므로 시작 실패 검증에 붙이지 않는다.
  T21 대화·캐시 분리와 T35 SEND 직후 다음 호출도 이 파일에서 검사하지 않아 매핑에서 제외했다.
  I10 전체 변경 무효화나 C 독립 T/S 인수를 이번 작업 완료로 표시하지 않는다.

### 시작 복구 전 허용 범위와 남은 한계

막는 것: 이전 Task 재개, 이전 PENDING/APPROVED 승인 실행 권한 복원, 이전 DISPATCHING/UNKNOWN_OUTCOME
요청의 재전송·재시도, Store 열기/스키마·새 Task 저장 확인 실패 시 Guard 시작.
허용하는 것: 같은 DB에 새 Task를 생성한 새 실행. 앱 bootstrap은 기존 행을 조회·수정하지 않는다.

이전 RUNNING/WAITING_APPROVAL Task·DISPATCHING 요청은 DB에 남고 화면에 보이지 않는다.
결과 불명 SEND의 사람 확인·종결 경로가 없다. 목록 조회, failure_audit 영속 저장, AGT mode/규칙 버전 감사는
[B-SQL-01–05](a-b-sqlite-requests-2026-10-13.md)에 A 임시 처리·재현 근거와 함께 기록했다.

다른 application_id/미래 schema DB는 B 공개 API로 만들 수 없어 새 A 케이스에서 생략했다(SQL 실행 없음).
기존 B 스키마 단위 테스트는 전체 실행에 포함된다. SQLite HTTP 정상 ASK·ALLOW·DENY는 2라운드에서 실행했다.
과거 APPROVED/DISPATCHING/
UNKNOWN_OUTCOME 행의 A 재시작 시험, C 독립 통합·결함 주입·보안 스캔·성능 측정은 이번 작업에서 **미실행**이다.
stdio/HTTP wire tools/call은 이 파일에서 별도 관찰하지 않아 독립 계수는 **검증 불가**다.
HTTP `spike.counts.receiver_calls`는 도구 함수 수신 수이며 ASGI 앞단 wire 계수가 아니다. 시작 실패는 stream/spawn 경로 자체가
만들어지지 않는 것을 확인했으며 정상 경로의 host_dispatch/receiver_calls/receiver_effect와 구분한다.
