# A151 · 제품 시작에 Codex L2 연결

2026-10-29 · worktree wt-A151 / wip/a151.
관련: 개발문서 0.4·0.5·2·10.4, I6·I9·I11, T22·T39·T40, REC-01, C6.14, A72·A82·A101·A138.
PR: 미정

## 한 일

- CodexProcessPreparation이 앱 시작 때 L1 작성·검증과 launcher 준비 뒤 C run_l2를 한 번 호출한다. 기존 StartupGate의 1회 게이트와 실패 고정을 유지한다. 개발용 건너뛰기는 없다.
- l2_thread_params는 제품 세션의 adapters.codex_protocol.thread_start_params, CODEX_MODEL_ID, BASE_INSTRUCTIONS를 사용한다. C 시험용 도구만 DynamicToolsPlan으로 감싼다. 제품 세션의 가짜 app-server가 받은 thread/start 인자와 직접 대조했다.
- 시작 영수증에 L2 통과 여부, 실패 코드 튜플, home·exe·model 지문을 넣는다. 기존 게이트가 세 지문을 시작 구성과 대조한다. Task별 구성 재검증도 유지한다.
- 실패 튜플이 있으면 통과 플래그와 무관하게 거부한다. 알려진 L2 고정 코드의 첫 항목을 세션 거부 사유로 쓴다. 전체 튜플은 영수증에 보존한다. 미등록 코드(launcher/path 계열 포함)는 CODEX_L2_FAILED로 축약한다. UI에는 고정 문구만 표시한다.
- L2 Task 예외는 Task.exception()으로 확인해 CODEX_L2_FAILED로 변환한다. 원문·타입명·경로를 로그나 Qt에 보내지 않는다. A82 허용 패턴이며 새 넓은 except는 없다. 취소는 정리 후 그대로 전달한다.
- CODEX_L2_CHECK 이벤트를 bridge에서 Qt로 보낸다. main이 준비 소유자의 진행 콜백을 bridge에 연결한다. L2는 백엔드 asyncio 스레드에서 실행한다. L2 대기 중 Qt 타이머가 계속 동작함을 시험했다.
- A 제품 파일 6개와 A 시험 4개를 변경했다. B·C 코드·공통 문서·a-status·Git index/브랜치 상태는 수정하지 않았다.

## 시작 순서와 상한

A138의 prepare_schema → recover → 설정/Registry/Guard 조립 → runtime.prepare_startup(L1/L2) → activate_run ACK → bindings 순서를 유지한다. L2 도중에는 Store 실행 권한을 활성화하지 않는다. 보통의 L2 불통과·예외·시간 초과는 세션만 거부하고 앱은 시작한다.

C 기본 deadline 30초에 정리 약 15초가 더해진다. Store 준비·복구 60초와 합치면 기존 bridge 90초로는 부족하다.

- L2 전체 대기: 50초(30 + 정리·준비 여유 20).
- 전체 대기 초과 또는 종료 요청: L2 Task를 취소하고 추가 20초까지 정리를 기다린다.
- 제품 bridge 시작: 140초(Store 60 + L2 50 + 취소 정리 20 + 조립·활성화 여유 10).
- 제품 bridge 종료: 30초(L2 취소 정리 20 + runtime/Store 정리 5 + 여유 5).
- 설계 예산이며 성능 측정에 따른 보장값은 아니다. 조립 등이 남은 예산을 넘으면 bridge 전체 시작 상한이 적용된다.

정리 대기까지 끝나지 않은 경우는 단순 L2 불통과와 구분한다. BackendCleanupError를 기존 준비 예외 변환에서 보존해 bridge가 정리 미확인으로 처리한다. main의 기존 잠금 유지·프로세스 종료 경로를 사용한다. 정리를 확인하지 못했는데 정상 종료로 보고하지 않는다.

## 실제 검증

모든 명령은 지정 worktree에서 실행했다. 원래 checkout의 src/tests/docs는 읽거나 고치지 않았다. Python은 원래 checkout의 .venv/Scripts/python.exe다. 최초 PYTHONPATH=src import 확인에서 guard_app 경로가 wt-A151/src임을 확인했다.

환경: PYTHONPATH=src, QT_QPA_PLATFORM=offscreen, PYTHONIOENCODING=utf-8. TEMP·TMP·TMPDIR는 worktree의 .cache/a-work/A151-tmp-0 절대 경로.

최종 명령은 /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A151-tmp-5 뒤에 다음 파일들을 넘겼다(모두 tests/unit/).

- test_a151_l2_startup.py, test_codex_preparation.py, test_session_preparation.py, test_session_runtime.py
- test_a138_product_startup.py, test_bridge.py, test_ui_sessions.py, test_codex_browser_login.py
- test_product_key_and_log.py, test_mcp_launcher_pipes.py, test_a_architecture.py, test_codex_session.py
- test_application_ids.py, test_product_entry_logging.py, test_product_settings.py, test_sqlite_app.py

결과: **320 passed, 108 subtests passed**, 35.08초. pydantic_settings의 기존 lifespan 미완성 annotation 경고 1개. 새 시험은 12개다. 통과·불통과·C deadline 보고·예외·세 지문 불일치·바깥 상한·정리 미확인·제품 인자 일치·bridge 진행/종료 취소를 검사한다. 거부 시 대역 세션 생성 0회, 통과 시 1회다. 실제 wire·모델·MCP 부작용 계수 검증은 아니다.

- 변경 Python 10개에 python -m ruff check: 통과.
- 같은 파일에 python -m ruff format --check: 10 files already formatted.
- PYTHONPATH=src python -m mypy: 117 source files 통과.
- PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini: 7 kept, 0 broken.
- git diff --check: 통과.
- 첫 확대 실행은 진행 콜백이 빠진 기존 SimpleNamespace 대역 2곳에서 12건 실패했다. 대역을 보정하고 최종 실행에서 해소했다.

## 고의 회귀 대조

각 변형마다 해당 pytest 노드 하나를 실행해 exit 1 / 1 failed를 확인했다. 제품 파일은 매번 finally에서 원래 바이트로 복원하고 바이트 일치를 확인했다. 임시 실행기는 worktree .cache/a-work/A151-tmp-0에만 두었다.

| 고의 회귀 | 잡은 시험 | 결과 |
| --- | --- | --- |
| 통과 보고를 False로 바꿈 | receipt/pass | 실패 1 |
| 불통과 코드 소실 | receipt/fail | 실패 1 |
| C deadline 코드 소실 | receipt/deadline | 실패 1 |
| 예외를 고정 실패로 바꾸지 않음 | receipt/exception | 실패 1 |
| L2 home 지문 대신 시작 지문 사용 | receipt/home_digest | 실패 1 |
| L2 exe 지문 대신 시작 지문 사용 | receipt/executable_sha256 | 실패 1 |
| L2 model 지문 대신 시작 지문 사용 | receipt/model_sha256 | 실패 1 |
| 바깥 상한 후 정리 대기 0초 | outer_timeout | 실패 1 |
| 미완료 정리를 정상 실패로 취급 | unconfirmed_cleanup | 실패 1 |
| L2 프롬프트만 변경 | product_thread_start_arguments | 실패 1 |
| 진행 이벤트 제거 | bridge/False | 실패 1 |
| 종료 취소 후 정리 기다리지 않고 반환 | bridge/True | 실패 1 |
| main 진행 콜백 연결 제거 | 기존 product_key_and_log/saved 보강 | 실패 1 |

첫 회귀 대조에서는 즉시 끝나는 정리 대역 때문에 정리 대기 회귀 2개를 놓쳤다. 정리를 이벤트로 보류하고 시작 반환 시점의 정리 완료 여부를 검사하도록 보강했다. 이후 위 13건 모두 검출했다.

## 남은 한계와 인수

- GUARD_TEST_CODEX_EXE가 없어 실제 codex.exe L2는 실행하지 않았다. C6.14 환경변수 기반 실제 시험은 수정하지 않았다. A19·A20·A120·A21–22 및 로그인 상태 T43·실제 provider 검증은 완료로 바꾸지 않는다.
- C 내부 프로세스 종료·Job·폴더 정리는 C 공개 함수에 맡긴다. 여기서는 취소 전달과 반환/미반환을 대역으로 확인했다. 실제 종료 시간·원문 비잔존은 실프로세스 인수에서 확인해야 한다.
- C가 새 L2 코드를 추가하면 A 고정 표시 목록을 함께 검토한다. 미등록 코드도 세션은 허용하지 않는다.
- B·C API 변경 요청은 없다. C의 L2 연결·정리 예산·진행 UI 리뷰와 B의 시작 활성화 경계 보존 리뷰가 남는다. 실제 리뷰 요청 메시지는 전송하지 않았다.
