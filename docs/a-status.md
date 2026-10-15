# A 진행 현황

2026-10-13 현재 기준은 **HEAD `631e116` + W1–W4 작업 트리 변경**이다. 커밋하지 않았다.
같은 프로세스의 미종결 bridge·정리 미확인 후 재시작을 차단했고(W1), 실제 SQLite에서 재현한 옛 Task 제어·projection 결함을 A 입구에서 막았다(W2).
T18의 승인 상세·목록·runtime·알림 카드와 네 URL 스킴을 대조하고 QLabel 링크 상호작용을 명시적으로 껐다(W3).
전체 **2,261 passed, 0 failed**(159.17초), 지정 6개+신규 T18 집중 **179 passed, 0 failed**(29.95초), 변경 Python 12개 Ruff check·format check 통과다.
Codex CLI 연동 결정(미연결), AGT 역할·규칙 관리 경로의 2026-10-14 회의 보류 결정을 반영했다(W4).
검증 명령·실패 후 수정 이력·T18 필드 대조는 아래 W1–W4 절, B 제어 범위 검토 요청은 [B-SQL-06](archive/tasks/a-b-sqlite-requests-2026-10-13.md)에 있다.

이하 SQLite 1·2라운드는 이전 병합 **`4388298`**(B **`6115fa6`**) 시점의 기록이다.
SQLite 앱 선택·새 Task 저장/재조회 시작 게이트·같은 백엔드 스레드의 마지막 Store 종료를 연결했다.
1라운드 작업 트리 검증은 **전체 2,155 passed, 0 failed**(119.96초), 새 SQLite 앱 **27 passed, 0 failed**(9.71초)다.
1라운드 변경 Python 5개 Ruff check·format check 통과. 네트워크 제한으로 `uv run --no-sync`를 사용했다.
2라운드는 T ID 매핑을 정정하고 SQLite/mock/HTTP 정상 ASK·ALLOW·DENY를 추가 검증했다.
최종 전체 **2,158 passed, 0 failed**(97.61초), SQLite 앱 **30 passed, 0 failed**(11.69초), 변경 Python 1개 Ruff check·format check 통과. 아래 SQLite 절에 라운드별 명령·결과·한계를 기록한다.
시작 복구·영속 call_seq·조회/감사 API는 [B-SQL-01–05](archive/tasks/a-b-sqlite-requests-2026-10-13.md) 후속이다.

아래 기존 동기화/QA 수치는 당시 기록이다. A `281cbae`에 B `3bf414c`(코드 보완 `29025e0`)를 반영했다.
환경 분리·현재 AGT 차이·검증은 [최신 B 동기화 기록](archive/tasks/a-b-sync-2026-10-13.md)을 따른다.
2026-10-13 전체 QA 2차 반영은 [QA 2차 반영 기록](archive/tasks/a-qa-fixes-2026-10-13.md)을 따른다. 반영 후 전체 **1,485 passed**,
spike 32경로 통과. B 소유 결함 10건은 같은 문서의 B 요청으로 남겼다.
동기화 후 전체 **1,371 passed**, 별도 B 환경 **971 passed**, AGT real/mock × stdio/HTTP **32경로 통과**.
변경 이력은 현재 브랜치의 git log에서 확인한다. 과거 세부 증거와 미정 항목은
[이전 기록 및 로컬 QA 수정 근거](archive/tasks/a-progress-history.md), 현재 재현법과 한계는 [리뷰 반영 기록](archive/tasks/a-review-fixes.md)을 따른다.

| 항목 | 상태 | 막힌 이유 | 다음 행동 |
| --- | --- | --- | --- |
| A01·A03 계약 | B ref·테스트 동기화, Runner epoch 전달 | domain/ports 합의 표는 미확정 | B ref 변경마다 전체 단위 검증 |
| A02·A06 환경 | AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현 | Scanner 선정·공통 제품 조합 미완료 | C Scanner 조합 인수 |
| A04 C 연결 | 요구사항 전달 | C launcher·Registry 미연결 | C 인수 환경에서 대조 |
| A05 UI 계약 | 앱 내부 Task projection·사람 review 연결 | 공통 조회 DTO 합의 미완료 | B·C 정식 계약 검토 |
| A07·A11 stdio | 실제 SDK·Guard 왕복 스파이크 실행 | 정식 C 수신 fixture 미사용 | C 인수 증거로 확장 |
| A08·A12 HTTP | [Streamable HTTP adapter·loopback 왕복·JSON/SSE·재전송 차단](archive/tasks/a-http-integration.md) | C endpoint 검증·독립 fixture 인수·인증 확장 미완료 | C 등록/endpoint 연결과 독립 계수 인수 |
| A09·A10 worker | 구현·회귀 검증 | 수신 직전 wire-size 상한은 C 의존 | C stream ingress 연결 |
| A13 종료 | W1: 미종결 거부·정리 미확인 영구 오염 게이트와 실제 종료 확인, SQLite 같은 스레드 생성/close·runner→transport→AGT→Store 종료 순서·중간 실패 정리·close 실패 미확인 표시 검증 | 이전 Task 종결/시작 복구 미지원 | B-SQL-02 복구 계약·C 독립 인수 |
| A14 원시 출력 | 필수 errlog·stdio/HTTP SDK와 합성 HTTP 서버 로그 격리 | 정식 C 공개 handler 인수 없음 | C 로그 정책·A39 함께 검증 |
| A15·A16 등록 | 요구·단위 구현 | 지문/Registry/launcher 미연결 | 실제 등록 계약 연결 |
| A17 AGT 선정 | [core 5.0.0 선정](archive/tasks/a-agt-selection.md), Windows 판정 스파이크 8경로 통과 | Scanner와 함께 쓸 공통 제품 조합 미검증 | C 조합 인수 |
| A18 AGT 연결 | [실제 로컬 판정·명시적 real/mock·timeout/취소 검증](archive/tasks/a-agt-integration.md), 10-13 QA 반영(동시 16·멈춘 worker 교체·종료 확인·한국어 결과 문구) | [AGT 역할·규칙 관리 결정](archive/tasks/a-agt-role-decision.md)은 보류, 2026-10-14 회의에서 결정(사용자(A), 2026-10-13), 영속 mode 감사·C 독립 인수 미완료, B Guard 4개 상한 연결 단위 검증 완료(독립 인수 별도) | 역할 결정, B 규칙/감사 검토, C dispatch 0 인수 |
| A19·A20 모델 | 실제 제공자: Codex CLI 연동으로 결정(사용자, 2026-10-13), 미연결 | 연결 방식·승인 환경 미확정, 아직 연결하지 않음 | Codex CLI adapter 연결·승인 환경 확정(T22)·제한 검증 |
| A21·A22 Runner | expected_epoch·승인 resume 연결 | 실제 모델 세션 격리 미검증 | Codex CLI 연결 후 T21 |
| A23 B 리뷰 | AGT 지연 중 긴급 차단 수정·회귀 검증 | C 독립 wire 검증 미실행 | B·C 소유자 리뷰 |
| A24–A27 bridge·앱 | W1 프로세스 시작 게이트·W2 옛 Task 실행/제어 거부, `--store sqlite --db-path` 연결, Store 열기·새 Task 저장/재조회 후 시작, 실패 시 FAILED/STARTUP_FAILED·run 거부 | 기본 Store는 메모리, Registry는 B 대역. 복구/전체 조회 없음 | B-SQL-01–06 및 C 구성 연결 |
| A28–A32 화면 | 기존 읽기 화면 유지 | 제품 Registry/Scanner 조회 없음 | 합의된 조회 연결 |
| A33·A34 승인 | W3 T18 필드·위젯·4스킴 검증(아래 표), Runner가 관찰한 ID·Store.get_approval로 PENDING 표시, SQLite 승인·중복 승인·재시작 권한 미복원 검증 | 과거 승인/제품 목록 통합 미완료 | B-SQL-02/03 이후 동일 사람 capability로 연결 |
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
상세 계수·재현법·B 규칙/감사와 C 독립 인수 의존성은 [A18 연결 기록](archive/tasks/a-agt-integration.md)을 따른다.

## 2026-10-13 HTTP MCP 연결

Streamable HTTP factory·로컬 TCP 서버를 연결했고 CLI에서 `--mcp-transport http`로 실행한다.
JSON/SSE × stateful/stateless, 307/308·인증/세션 오류·응답 유실·timeout·취소의
SEND 추가 전송 0을 검증했다. SDK/client·합성 서버 로그도 같은 context에서 격리한다.

새 lock 환경 설치·66개 배포판 의존성 검사 성공, 전체 **1,282 passed**
(22.55초, Qt offscreen, SDK 경고 5건), HTTP 앱의 AGT real/mock 각각 8경로 통과.
HTTP 단위 probe의 wire 계수와 제품 endpoint/인증·C 독립 인수 한계는
[A08/A12 연결 기록](archive/tasks/a-http-integration.md)을 따른다.

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
[B-SQL-01–05](archive/tasks/a-b-sqlite-requests-2026-10-13.md)에 A 임시 처리·재현 근거와 함께 기록했다.

다른 application_id/미래 schema DB는 B 공개 API로 만들 수 없어 새 A 케이스에서 생략했다(SQL 실행 없음).
기존 B 스키마 단위 테스트는 전체 실행에 포함된다. SQLite HTTP 정상 ASK·ALLOW·DENY는 2라운드에서 실행했다.
과거 APPROVED/DISPATCHING/
UNKNOWN_OUTCOME 행의 A 재시작 시험, C 독립 통합·결함 주입·보안 스캔·성능 측정은 이번 작업에서 **미실행**이다.
stdio/HTTP wire tools/call은 이 파일에서 별도 관찰하지 않아 독립 계수는 **검증 불가**다.
HTTP `spike.counts.receiver_calls`는 도구 함수 수신 수이며 ASGI 앞단 wire 계수가 아니다. 시작 실패는 stream/spawn 경로 자체가
만들어지지 않는 것을 확인했으며 정상 경로의 host_dispatch/receiver_calls/receiver_effect와 구분한다.


## 2026-10-13 W1–W4 · 재시작·옛 Task·T18·사용자 결정

기준은 HEAD `631e116` + 이번 작업 트리다. A 소유 코드·단위 테스트·A 문서만 수정했다.
B/C 코드·테스트·공동 계약·날짜가 고정된 회신 `a-seq-rec-reply-2026-10-13.md`·사용자 미추적 QA 자료는 변경하지 않았다.
넓은 except·SQL 실행·제품 로그·테스트용 reset API는 추가하지 않았고 커밋하지 않았다.

### W1 · 재시작 경로 조사와 시작 게이트

- 조사: `BackendBridge.start()`는 같은 인스턴스가 NEW가 아니면 기존대로 ValueError를 내며 bootstrap을 더 호출하지 않는다.
  `rg -n 'BackendBridge\(|bridge\.start\(' src examples`에서 제품 생성/시작은 `__main__.py`의 각 1곳뿐이었다.
  UI·다른 A 제품 코드에 bridge 재생성·자동 재start 경로는 없다. 클래스 자체로 새 bridge를 만드는 경로는 가능해 게이트를 추가했다.
- `_ProcessStartGate`의 Lock 안에서 판정과 활성 등록을 함께 수행한다. STARTING/READY/CLOSING,
  최종 보고 전, 최종 보고 뒤라도 스레드가 살아 있으면 BACKEND_ALREADY_ACTIVE로 거부한다.
- CLEANUP_UNCONFIRMED·STARTUP_CLEANUP_UNCONFIRMED·BACKEND_FAILURE는 영구 오염이다.
  **BACKEND_FAILURE는 _serve의 예상 밖 종료로 자원 정리를 확인하지 못했으므로 오염**으로 분류했다(코드 주석에도 명시).
  오염 해제 API는 없다. 거부 시 STARTING 이벤트·백엔드 Thread 생성·bootstrap 없이 FAILED와 고정 코드를 내고 thread_done을 설정한다.
  run/control은 NOT_READY, 거부 bridge의 close는 CLOSED이며 게이트를 다시 보고하거나 오염시키지 않는다.
- 최종 보고는 _thread_main의 루프 정리·지연 오류 처리·마지막 상태 갱신 뒤, thread_done 직전에 1회다.
  CLOSED, STARTUP_FAILED, INITIAL_SNAPSHOT_FAILED, 스레드 생성/시작 실패 START_THREAD_FAILED는 정리 확인으로 처리한다.
  NEW에서 close는 등록할 자원이 없다. 확인 종료와 실제 스레드 종료 뒤 새 인스턴스 시작은 가능하다.
- __init__의 start_gate 주입은 테스트에만 썼다. 제품 __main__/examples/ui는 전역 기본값을 사용한다.
  bridge/runtime/sqlite_app 테스트 각각의 autouse fixture가 전역 인스턴스를 교체해 의도된 정리 미확인 시험의 오염을 격리한다.
  모든 Qt 시그널은 게이트/상태 락 밖에서 발행한다.
- 실제 계수: 오염 게이트 + 실제 bootstrap wrapper는 stdio/HTTP × mock/real 4조합에서
  bootstrap=0, McpTransport.start=0, 실제 AGT start=0, stdio counts 파일 미생성, HTTP 서버 시작=0이다.
  mock AGT에는 별도 start가 없으며 bootstrap=0이어서 mock 객체도 생성되지 않는다.
  정상 종료 뒤 실제 stdio 두 실행은 각각 READ+SEND dispatch/receiver_calls/effect=2/2/1이다.
  순수 게이트 12스레드 경쟁은 통과 1·거부 11, 최종 보고 후 살아 있는 스레드도 추가 시작을 막았다.
- UI는 두 거부 코드의 고정 한국어 안내를 표시하고 기존 승인 사본을 비우며 모든 실행·제어 버튼을 끈다.
- 관련 불변식: I1 관리 도구는 Guard 경유, I6 불확실 시 차단. REC-01·REC-05 자체에는 직접 대응 T/S ID 없음.
  정상 ASK 재실행 계수만 T02의 A 부분에 연결했다. 프로세스 간 DB 잠금은 구현하지 않았다(C 후속).

- 2라운드 D1: 정상 재시작 두 회차 모두 승인 요청 직전 WAITING_APPROVAL, host_dispatch=1(READ만),
  projection receiver_effect=0, 실제 counts의 receiver_effect 증가량=0을 추가 단언했다.
  기존 승인 후 dispatch/receiver_calls/effect=2/2/1 단언은 유지했다. 집중 D1·D2 시험 4개 통과.
- 동작 변화 명시: BACKEND_FAILURE는 이제 _cleanup_unconfirmed를 세우므로 이후 close()의 종료 코드는
  CLOSED가 아니라 CLEANUP_UNCONFIRMED다.
- 한계: 최종 보고 뒤에도 백엔드 스레드가 살아 있으면 BACKEND_ALREADY_ACTIVE로 거부하므로,
  thread_done 직후(스레드 반환 전)의 새 시작은 드물게 거부될 수 있다(fail-closed, 제품 경로에는 같은 프로세스 재시작이 없음).

### W2 · 수정 전 재현과 현재 검증

- 수정 전 `test_old_task_commands_are_rejected_before_model_control_and_projection` 실패:
  옛 run은 RUNNER_UNAVAILABLE, 옛 cancel/halt는 CONTROL_DENIED_QUERY_REQUIRED였다.
  모델/dispatch/receiver_calls/effect=0이지만 B cancel/halt 각 1회, discard 2회, 옛 load_task 6회,
  옛 projection 2회가 발생했고 정리 후 공개 Store 재조회에서 옛 행 row_revision 2→3으로 전체 상태 비교가 실패했다.
- 수정 전 `test_snapshot_rejects_foreign_task_before_store_read`도 실패:
  현재 Runner 밖 ID를 Store에 넘겼고 STATE_MISSING을 받았다. 조회 전 고정 실패 요구를 충족하지 못했다.
- A 수정: cancel/halt의 discard·B capability 호출·finally snapshot **전에** 반환값이 TaskRunner인지와 task_id 일치를 검사한다.
  실패는 기존 RUNNER_UNAVAILABLE로 통일하고 snapshot을 발행하지 않는다. run의 기존 모델 호출 전 검사도 유지한다.
  synthetic snapshot 자체에도 현재 Runner ID 검사를 추가해 조회 전 고정 ValueError로 거부한다.
  runtime은 실행·취소·긴급 차단 모두 이 거부 코드의 고정 한국어 문구를 보여준다.
- 수정 후 두 재현 테스트 통과. SQLite 2회차 옛 ID 세 명령에서 모델=0, host dispatch=0,
  receiver_calls/effect=0/0, B cancel/emergency_halt/approve/reject=모두 0, discard=0,
  옛 Store 조회=0·옛 projection=0이다. 정리 후 새 SQLiteStore의 공개 load_task로 옛 Task 전체 상태·epoch 불변을 확인했다.
- 같은 테스트에서 새 Task는 영향을 받지 않았다. ASK 전 READ dispatch/receiver_calls/effect=1/1/0,
  승인 후 2/2/1·Task RUNNING, 모델 1회·approve 1회다. 과거 승인 권한 복원 검증은 기존 테스트를 보존했다.
- 기존 공개 동작: 현재 소유 Task의 run/stop과 승인 처리 코드는 그대로다. 외부 Task 제어만 거부하도록 바뀌었다.
  기존 큐 격리 시험 5개는 다른 Task도 명시적인 Runner 맵으로 등록했다. 큐/우선순위/제어 호출 계수 assertion은 유지했다.
  "아무 ID에 같은 Runner 반환" 대역이 새 소유 검사를 우회하도록 기대값을 약하게 바꾸지 않았다.
- 원인/후속: A 소유 검사·snapshot 결함은 해결했다. B capability의 회차별 허용 범위는
  [B-SQL-06](archive/tasks/a-b-sqlite-requests-2026-10-13.md) 검토 요청이며 B 결함 확정·공동 계약 변경이 아니다.
  REC-03, I1/I3/I7·T11 재시작 부분에 연결하고 T21 대화·캐시 격리 인수로 확대하지 않는다.

- 2라운드 D2: 실제 Pending에서 얻은 옛 approval ID로 REVIEW·APPROVE·REJECT를 각각 독립된 SQLite
  재시작 2회차 bridge(mock AGT/stdio)에 보냈다. 최종 코드는 REVIEW_UNAVAILABLE,
  CONTROL_DENIED_QUERY_REQUIRED, CONTROL_DENIED_QUERY_REQUIRED였다.
  각 명령의 모델/host dispatch/receiver_calls/receiver_effect/옛 Task projection은 모두 0이다.
- 1회차 정리 후와 2회차 정리 후 각각 새 SQLiteStore의 공개 get_approval/load_task로 비교했다.
  세 경우 모두 옛 승인 PENDING→PENDING, 옛 Task WAITING_APPROVAL→WAITING_APPROVAL,
  양쪽 row_revision 증가량=0이며 전체 DTO가 동일했다. A 차단 코드는 추가하지 않았고 기존 bridge 승인 테스트도 유지했다.
  B-SQL-06에 결과를 덧붙였다. REC-03·I3·T11 재시작 부분이며 B/C 인수나 복구 계약 확정은 아니다.

### W3 · T18 필드·위젯 대조

설계 T18은 "승인 화면의 HTML·링크 → 일반 텍스트, 외부 요청 0"이며 표의 I 열은 —다.
현재 코드의 실제 표시 필드와 비표시 필드를 구분했다. 아래의 공통 새 시험은
`test_ui_approval_t18.py::test_all_approval_fields_are_literal_and_never_open_links` 4스킴 매개변수 시험이다.

| 표면 / 필드 | 위젯·렌더링 | 검증 연결 |
| --- | --- | --- |
| 승인 상세: 전체 목적지, 전체 인자 UTF-8 바이트 | 읽기 전용 QPlainTextEdit 2개, visible_review_text → setPlainText, 원본 bytes 불변 | 기존 test_approval_detail의 markup/loopback·전체 길이/작은 화면 + 공통 새 시험 |
| 상세 제목·설명·필드 이름 | plain_label의 QLabel, 고정 한국어·PlainText | 공통 새 시험의 모든 QLabel 검사 |
| 승인 목록: approval ID, server ID, task/request ID, 목적지 요약 | QTableWidgetItem, public_display_text, 기본 텍스트 delegate | 기존 test_approval_table_cells_escape_ids_and_destination·test_waiting_hint_does_not_expire_or_interpret_markup + 공통 새 시험의 각 셀 리터럴/클릭 |
| 목록 선택 요약: 위 5개 문자열, 상태, 요청·처리 시각 | QPlainTextEdit.setPlainText, one_line 가시화 | 기존 test_public_summary_cannot_spoof_status_line + 공통 새 시험 |
| 목록 상태·만료(EXPIRED)·대기 시간, 요청/처리 시각, tooltip·접근성 문자열 | enum→고정 문구, datetime→고정 KST 형식, 테이블 항목 | 기존 test_ui_approvals의 상태/시각 시험 + 새 test_approval_status_and_times_use_typed_fixed_presentation 7상태; HTML status/created_at 거부 |
| 목록 머리말·건수·페이지·조회 상태, 검색/서버 필터·탭·정렬·버튼 | plain QLabel, QLineEdit, 고정 QTabBar/QComboBox/QPushButton | 공통 새 시험: QLabel 설정·신호, 필터 원문 리터럴. 고정 문구에는 비신뢰 입력 없음 |
| runtime 검토: 도구 이름·종류, MCP 서버 ID·서버 경계, 목적지 경계, Task 등급, 만료 시각, 전체 인자 | ApprovalReview.receiving_boundaries + arguments_bytes → visible_review_text → QPlainTextEdit.setPlainText | 실제 _review_header 생성 + 공통 새 시험, 기존 test_human_review_approve_and_shutdown_use_real_mcp는 사본 표시 연결 부분 |
| runtime 요약: Task ID·상태, AGT/모델 모드, dispatch/effect; lifecycle/명령 결과 | plain QLabel, 상태/결과는 고정 문구 | 공통 새 시험의 Task ID 리터럴·모든 QLabel 설정/신호 검사, 기존 runtime 상태 시험 |
| overview 승인 알림: 종류·사유, server/task/request ID, 발생·대기 시각 | plain QLabel, ID 가시화, reason은 고정 매핑(미지정 코드는 "미확인"), 시각 고정 형식 | 공통 새 시험에서 관련 ID 리터럴·악성 reason 원문 미표시·링크 상호작용 0 |
| 현재 비표시: 목록 reason_code/row_revision, 인자 요약, runtime의 approval/request ID·epoch 자체 | 목록 인자 필드 없음. runtime approval ID·epoch는 사본 대조에만 사용. 전체 인자는 승인 상세에만 표시 | 표시 경로가 없으므로 렌더링 검증 대상 아님. 공급 계약/향후 연결을 추측하지 않음 |

- HTML `<b>`, `<img src>`, `<a href>`와 http/https/file/javascript를 주입했다.
  QPlainTextEdit의 toPlainText 일치·toHtml 이스케이프·문서 fragment의 anchor/image 없음, 테이블 리터럴을 검사했다.
  QLabel은 PlainText·openExternalLinks=False·마우스/키보드 링크 상호작용 비트를 모두 검사했다.
- 발견/수정: plain_label에 Qt 기본 LinksAccessibleByMouse가 남아 있었다.
  PlainText로 HTML은 이미 해석하지 않았으며 실제 링크 열기 결함은 관찰하지 않았다.
  스펙의 명시적 링크 상호작용 금지 요구에 맞춰 NoTextInteraction·setOpenExternalLinks(False)를 지정했다.
  선택 가능한 overview 라벨은 기존 TextSelectableByMouse 재설정을 유지해 텍스트 선택을 보존한다.
- 계수: hover/click/Enter 뒤 linkActivated=0, linkHovered=0,
  Python QDesktopServices.openUrl=0, 네이티브 Qt URL handler=0, loopback HTTP 요청=0(4스킴 각각).
  handler 4종이 호출을 실제 잡는지는 사전 probe 4회로 확인하고 그 뒤 계수를 비웠다. probe 호출은 UI 외부 열기 계수에서 제외한다.
  기존 상세 HTTP 시험은 250ms+250ms, 신규 모든 표면 시험은 마지막 이벤트 뒤 100ms 관찰한다.
- 기존 관련 테스트와 신규 테스트 docstring에 T18을 연결했다. 기존 assertion을 제거하지 않았다.
- **검증 불가**: 실제 화면 픽셀·OS 기본 브라우저/파일 핸들러 실행 여부(프로브가 가로챔), 클립보드 붙여넣기,
  보조기기의 실제 발화, 외부 인터넷의 독립 패킷 계수. 현재 미연결인 제품 승인 목록 조회·새 표시 경로도 검증 불가다.
  Qt 문서/위젯과 local HTTP 관찰을 C 독립 전체 승인 공격 인수로 표시하지 않는다.

### W4 · 2026-10-13 사용자 결정

- 실제 모델은 **Codex CLI 연동으로 결정했으며 아직 연결하지 않는다**. A19·A20 및 남은 작업 표에 기록했다.
  연결 방식·SDK 시그니처는 미확정이고 새 adapter를 구현하지 않았다. 다음 일은 연결·T22 승인 환경 확정·제한 검증이다.
- AGT-01 역할과 규칙 관리 경로는 **보류 · 2026-10-14 회의에서 결정**, 결정자는 **사용자(A), 2026-10-13**이다.
  역할 결정 문서의 머리말/결정 표와 남은 항목을 일치시켰다. 규칙 로더는 미구현이며 A·B·C 합의로 쓰지 않았다.

### 실제 실행·실패 이력·한계

Windows의 기존 .venv, QT_QPA_PLATFORM=offscreen. 기본 셸은 helper_unknown_error: setup refresh had errors로 실패해 테스트 본체가 실행되지 않았다.
사용자 지시대로 Node child_process.spawnSync(shell:false, 인자 배열, cwd=저장소, maxBuffer=200MiB, timeout=20분)을 사용했다.
node_repl이 process 전역·node:process import를 제공하지 않아 PowerShell의 GetEnvironmentVariables로 상속 환경을 읽고 offscreen을 더했다.
이후 Python 출력은 PYTHONIOENCODING=utf-8을 지정했다. 환경 변수 값은 출력하지 않았다. uv/네트워크 동기화는 사용하지 않았다.

아래 P는 `.venv/Scripts/python.exe`, R은 `.venv/Scripts/ruff.exe`다.
F는 `tests/unit/test_bridge.py tests/unit/test_runtime.py tests/unit/test_sqlite_app.py tests/unit/test_approval_detail.py tests/unit/test_ui_display_escaping.py tests/unit/test_ui_approvals.py tests/unit/test_ui_approval_t18.py`다.
C는 변경 Python 12개: `examples/synthetic_app.py`, `src/guard_app/services/bridge.py`, `src/guard_app/ui/{presentation,runtime,widgets}.py`, F의 7개 파일이다(중괄호는 파일 목록 설명).

| 실제 명령 / 단계 | 결과 |
| --- | --- |
| P -m pytest -q -p no:cacheprovider tests/unit/test_sqlite_app.py -k "old_task_commands or snapshot_rejects" (수정 전) | **2 failed, 30 deselected**, 4.46초; W2 A 결함 재현 |
| 같은 명령 (W2 수정 후) | **2 passed, 30 deselected**, 4.36초 |
| P -m pytest -q -p no:cacheprovider tests/unit/test_bridge.py tests/unit/test_runtime.py tests/unit/test_sqlite_app.py | **118 passed, 0 failed**, 22.90초, 경고 5개 |
| P -m pytest -q -p no:cacheprovider tests/unit/test_ui_approval_t18.py -x (초기·2차) | 각각 **1 failed**, 0.71/0.77초. 첫 실행은 A 테스트의 사유 fallback 기대값 오류, 둘째는 QLabel 링크 비트 잔존 |
| P -m pytest -q -p no:cacheprovider tests/unit/test_ui_approval_t18.py (QLabel 수정 후) | **4 failed**, 0.72초. A 테스트가 첫 필드 Enter로 상세를 닫은 뒤 지워진 두 번째 필드를 검사함 |
| 같은 파일 -x 진단 / Enter를 전체 필드 검사 뒤로 옮긴 재실행 | **1 failed**, 0.70초 / **4 passed**, 1.36초 |
| R check C / R format --check C (최초) | 종료 1: lint 10건 / 8개 포맷 필요. A 변경/테스트의 import·조건문·루프 캡처를 수정 |
| R check --select I --fix C, R format C | import 2건 수정, **8 files reformatted, 4 files left unchanged** |
| P -m pytest -q -p no:cacheprovider F (7상태 시험 포함) | **179 passed, 0 failed**, 28.06초, 경고 5개 |
| P -m pytest -q -p no:cacheprovider (전체) | **2,261 passed, 0 failed**, 159.17초, 경고 5개. 기준 2,227 + 신규 34 |
| P -m pytest -q -p no:cacheprovider F (최종) | **179 passed, 0 failed**, 29.95초, 경고 5개. 전체 통과 뒤 큐 테스트의 명시 소유 맵과 counts.json 미생성 직접 assertion만 보강해 재검증 |
| R check C / R format --check C (최종) | 종료 0 / 0, **All checks passed! / 12 files already formatted** |

최종 집중/전체 경고는 기존 AGT deprecated import 4개와 Pydantic forward reference 1개다. timing flaky나 B/C 테스트 실패는 없었다.
제품 코드는 전체 통과 이후 바꾸지 않았다. 보강한 A 테스트는 최종 집중 실행으로 확인했다.
별도 C 독립 인수·Semgrep/보안 스캔·성능 측정·실제 Codex CLI 연결·프로세스 간 DB 잠금 시험은 **미실행**이다.
독립 wire tools/call 계수는 **검증 불가**이며 이번 host dispatch/도구 함수 receiver_calls/receiver_effect와 구분한다.
B-SQL-01–05 복구·순번·조회·감사 의존성과 새 B-SQL-06 검토는 남아 있다.

최종 `git diff --check`는 종료 코드 0으로 통과했다. Git의 LF→CRLF 안내만 있었고 whitespace 오류는 없었다.

### 2라운드 D1–D3 · 실제 실행 결과 (2026-10-14)

1라운드 수치는 위에 보존했다. 이번 변경은 tests/unit/test_sqlite_app.py와 A 문서 2개뿐이며 커밋하지 않았다.
기본 셸은 같은 helper_unknown_error로 실행되지 않아 node_repl의 child_process.spawnSync를 사용했다.
기존 .venv, shell:false, cwd=저장소, 상속 환경 + QT_QPA_PLATFORM=offscreen/PYTHONIOENCODING=utf-8,
maxBuffer=200MiB, timeout=20분이다. codex-spec-1.md는 저장소에서 찾지 못했고 이번 요청에 재명시된 절대 규칙을 따랐다.
P는 .venv/Scripts/python.exe, R은 .venv/Scripts/ruff.exe다.

| 실제 명령 | 결과 |
| --- | --- |
| P -m pytest -q -p no:cacheprovider tests/unit/test_sqlite_app.py -k "old_approval_controls or clean_bridge_restart" | **4 passed, 0 failed, 36 deselected**, 7.91초 |
| P -m pytest -q -p no:cacheprovider tests/unit/test_sqlite_app.py tests/unit/test_bridge.py tests/unit/test_runtime.py | **121 passed, 0 failed**, 27.93초, 기존 경고 5개 |
| P -m pytest -q -p no:cacheprovider | **2,264 passed, 0 failed**, 109.06초, 기존 경고 5개(1라운드 2,261 + D2 매개변수 3개) |
| R check tests/unit/test_sqlite_app.py | 최초·최종 모두 종료 0, All checks passed! |
| R format --check tests/unit/test_sqlite_app.py | 최초 종료 1, 신규 함수 선언 줄바꿈 1곳. R format tests/unit/test_sqlite_app.py로 1 file reformatted 후 최종 종료 0, 1 file already formatted |
| git diff --check | 종료 0, 공백 오류 0(LF→CRLF 안내만 있음) |

테스트 이후 Python 변경은 Ruff의 함수 선언 줄바꿈뿐이다. W3·W4의 제품 동작·사용자 결정은 이번 범위에서 변경하지 않았다.
D3은 A24–A27의 다음 의존성을 B-SQL-01–06으로 맞추고 W1의 종료 코드 변화·스레드 반환 전 재시작 한계를 명시했다.
독립 wire 계수는 검증 불가, C 독립 인수·추가 보안 스캔·성능 측정은 미실행이다.
