# A153 · SessionRuntime 조립 경계 정리

2026-10-29 · A · worktree wt-A153 / wip/a153.
관련: 개발문서 0.4·0.5·2·10.4·11.5, I1·I6·I9·I11, T08·T21·T22·T39·T40, REC-01·REC-05, SEQ-02, STOP-01, A105·A128·A49·A134·A136·A138·A151.
PR: 미정

## 한 일

- 제품 시작과 실패 정리 도우미를 services/product_startup.py로 이동했다. session_runtime.bootstrap_product는 동일 함수의 명시적 재수출이다. 새 모듈이 SessionRuntime을 함수 안에서 import해 재수출 초기화 순환을 피한다.
- 실행 게이트 연결(connect_execution_gate), Codex 팩토리 연결(connect_session_factory), 실행 감시 시작(start_execution_watch)을 공개 단계로 만들었다. 게이트 연결은 기존처럼 즉시 실행 권한을 확인한다. 감시 시작은 latch로 한 번만 수행하며 close가 기존 타이머를 취소한다. 생성자에 execution_gate를 주는 기존 사용법도 감시를 시작한다.
- 등록 조립은 runtime.registration_commands 경계로 연결했다. saver를 내부 TaskPolicy에 연결하고 같은 정책 객체·잠금을 RegistrationCommands에 넘긴다. 제품 시작 코드에 TaskPolicy를 반환하거나 getter로 노출하지 않는다. 생성자 콜백은 공개 check_execution·close_policy_stopped로 바꿨다. bridge_registration.py와 정책 구현은 수정하지 않았다.
- Store 명시 시작 → schema 준비 → 복구 → 설정·Registry → Guard → L1/L2 → activate_run ACK → bindings 구성 → 감시 시작 → 반환 순서다. 기존 코드에서 등록 Registry의 추가 load보다 앞에 있던 감시 시작은 요청한 순서에 맞춰 bindings 구성 직후로 옮겼다. 실제 외부 공개는 함수 반환이며 감시는 그 직전에 시작한다. ACK 직후 실행 권한 확인과 Task 접수 전 검사는 유지했다.
- 실패·취소 finally, runtime이 Store 소유권을 받기 전후의 분기, 정리 Task의 bounded wait·취소·예외 판정, BackendCleanupError, Store close/wait_closed 확인은 그대로 이동했다. 상한·고정 코드·잠금 유지 판정·정리 순서를 변경하지 않았다.
- 기존 시험 5개 파일은 조립 의존성을 monkeypatch하는 모듈 위치만 맞췄다. 기존 bootstrap import 및 entry-point monkeypatch는 유지했다. A 아키텍처 시험에 새 모듈 분류, 비공개 attribute 접근 금지, 동일 함수 재수출 검사를 추가했다. 새 A153 시험은 실제 Store 활성화 ACK → bindings → 감시 순서, 반복 시작 요청에도 타이머 1개, 종료 시 취소를 확인한다.

## 정적 측정

HEAD 원본은 worktree에서 git show HEAD:<경로>로 읽었다. 함수 길이는 AST의 end_lineno - lineno + 1, 비공개 접근은 AST Attribute 중 runtime 이름의 밑줄 멤버 직접 접근 개수다. 이 수치는 프로젝트 전체 별칭 추적 분석이 아니다.

| 대상 | 줄 수 전 → 후 | 200줄 초과 함수 전 → 후 | 조립의 runtime 비공개 접근 전 → 후 |
| --- | ---: | ---: | ---: |
| session_runtime.py | 837 → 695 | 0 → 0 | 8 → 0 |
| product_startup.py | 없음 → 219 | 없음 → 0 | 없음 → 0 |
| session_execution.py (무변경) | 17 → 17 | 0 → 0 | 0 → 0 |

분리한 두 파일 합계는 914줄이다. 줄 수 총량·성능 개선을 주장하지 않는다.

## 실제 검증

모든 실행은 지정 worktree에서 수행했다. 원래 checkout의 src/tests/docs는 읽거나 고치지 않았다. 최초 PYTHONPATH=src import에서 guard_app.__file__이 wt-A153/src/guard_app/__init__.py임을 확인했다.

환경: Git Bash, Python은 /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe, PYTHONPATH=src, PYTHONUTF8=1, QT_QPA_PLATFORM=offscreen. TMPDIR·TEMP·TMP는 worktree의 .cache/a-work/A153-tmp-0 절대 경로다.

관련 시험 선택은 rg -l 'bootstrap_product|SessionRuntime|session_runtime' tests --glob '*.py'와 사용자 지정 파일의 합집합이다. 실제 명령:

```bash
A153_PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
$A153_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A153-tmp-3 $(cat .cache/a-work/A153-tmp-0/tests.txt) --tb=short
$A153_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A153-tmp-4 tests/unit/test_a153_session_runtime_boundary.py tests/unit/test_a_architecture.py --tb=short
PYTHONPATH=src $A153_PY -m mypy
PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

- 관련 묶음: 327 passed, 149 subtests passed, 45.01초. 검색 경로 구분자 차이로 test_a105_activation.py가 실행 목록에 두 번 들어갔다. 고유 시험 개수라고 주장하지 않는다.
- 새 경계·아키텍처 최종 실행: 10 passed, 2.28초. 위 관련 묶음과 아키텍처 시험은 겹친다.
- 파일 목록: test_session_runtime, test_a138_product_startup, test_a151_l2_startup, test_a143_writer_shutdown_race, test_product_key_and_log, test_b28_integration, test_sqlite_app, test_bridge_registration, test_a105_activation, test_a49_registration, test_mcp_launcher_pipes, test_a_architecture, test_account_runtime, test_application_ids, test_codex_preparation, test_codex_product_flow, test_product_assembly, test_product_entry_logging, test_product_settings, test_session_collaborators, test_session_preparation, test_ui_sessions (모두 tests/unit/*.py).
- 변경 Python 9개 파일의 python -m ruff check 및 ruff format --check 통과. 마지막 문서 문자열·시험 대기 상한 변경 후 해당 3개 파일도 재검사 통과.
- 전체 mypy: 124 source files 통과. import-linter: 7 kept, 0 broken. git diff --check 통과.
- 기존 pydantic_settings lifespan의 미완성 annotation 경고 1개. 전체 단위·C 계약/공격·실제 Codex·실제 외부 MCP·성능 측정은 실행하지 않았다.
- 기존 A138·제품 흐름 시험의 합성 dispatch/wire 및 Store 활성화 전 Task·세션 0 계수 단언은 그대로 통과했다. 새 시험은 감시 예약 계수 1과 취소를 관찰한다. 이것을 실제 모델·외부 부작용 인수로 확대하지 않는다.

## 고의 회귀 대조

worktree .cache/a-work/A153-tmp-0의 임시 실행기가 제품 파일 바이트를 저장하고 매번 finally로 복원·일치 확인했다. 각 실행은 프로세스 상한 40초, 시험의 기존/새 대기 상한 안에서 수행했다.

| 고의 회귀 | 탐지 시험 | 결과 |
| --- | --- | --- |
| product_startup에서 runtime._execution_gate 직접 접근 | A 아키텍처 public runtime operations | 1 failed |
| bootstrap_product 재수출을 None으로 대체 | A 아키텍처 compatibility export | 1 failed |
| 감시 시작 latch 무시 | A153 bindings/single watch/close | 1 failed, 중복 예약 |
| bindings 전에 감시 시작 | A153 bindings/single watch/close | 1 failed, 순서 불일치 |
| 실패 정리의 wait_closed 확인 생략 | product_assembly failed start unconfirmed | 1 failed |
| 등록 saver 연결 생략 | bridge_registration 제품 등록·재시작 | 1 failed, 확인 버튼 조건 미충족 |
| activate_run ACK 호출 생략 | A153 bindings/single watch/close | 1 failed, CODEX_STORE_HALTED |

ACK 생략을 기존 A138 시험에 먼저 넣었을 때는 진입 이벤트가 오지 않아 10초 timeout으로 실패했다. 이를 유효한 탐지로 세지 않고 새 A153 시험으로 다시 실행해 즉시 Store 권한 거부를 확인했다. 등록 saver 회귀는 UI 대기 상한에서 기대 상태의 assert 실패로 탐지했다. 복원 뒤 관련 시험 및 새 시험을 다시 통과했다.

## 남은 한계와 인수

- B·C 공개 API·domain·ports 변경은 없다. B에 Store 활성화/종료 확인 보존 리뷰, C에 시작·감시·정리 경계 리뷰가 남는다. 리뷰 요청 메시지는 보내지 않았다.
- 공개 초기화 메서드는 신뢰하는 제품 조립 코드용이며 새로운 사용자 명령이나 모델 도구로 등록하지 않는다.
- 실제 Codex L2, Windows 프로세스 종료·앱 잠금 유지의 독립 인수는 이번 리팩터링 완료에 포함하지 않는다. 기존 A138·A151·A134·A136 한계를 유지한다.
- a-status·공통 문서·다른 담당 코드·Git index는 변경하지 않았다.
