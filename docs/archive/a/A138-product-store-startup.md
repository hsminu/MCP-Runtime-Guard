# A138 · 제품 Store 명시 시작·복구와 실행 정지 연결

2026-10-29 · A13·A24–A27·A138, A139 run ID 후속, A81·A91 제품 정책 연결.

## 한 일

- 제품 시작 인자를 DB 문자열에서 C `AppFolders`로 바꿨다. `folders.db_path()`로 검증한 고정 DB를 `startup="explicit"`로 연다. `prepare_schema` → `recover` → `load_settings_profile` → Registry·Guard 조립·하네스 준비 → `activate_run` ACK → bridge bindings 반환 순서다. ACK 전에 Task 행·Codex Task 세션·도구 submit을 만들지 않는다. 설정 바이트 읽기·파싱은 기존처럼 main의 잠금 획득 뒤이고, Registry를 쓰는 Profile 검사는 복구 뒤다.
- 준비 시작의 `time.monotonic() + 60.0` 하나를 준비와 복구에 전달한다. 백업은 `folders.begin_backup`, run ID는 `new_id("run")`이다. B의 복구 내부 30초 상한은 그대로다. 제품 bridge 시작 상한은 90초로 두어 기존 10초가 저장소 준비를 먼저 취소하지 않게 했다. 이 시간값은 성능 측정 결과가 아니다.
- `ProductInputs`에 `store.allocate_call_seq`와 `durable_failure_audit(store)`를 넣는다. `assemble_guard`는 Store의 `halt_execution`을 Guard 전역 정지 콜백으로 넘긴다. 공통 조립 함수의 비제품 Store는 이 선택 기능이 없을 수 있다.
- 유효한 설정 파일이 있으면 제품에서도 C `ServerRegistry`와 실제 Guard를 조립한다. stdio는 C `server_folder`·`launch_mcp_server`·`ExecutablePin`을 거쳐 검증된 파이프 어댑터에 연결한다. HTTP는 C `normalize_endpoint` 뒤 HTTP 어댑터로 연결한다. 실행한 MCP 프로세스의 종료 확인도 제품 조립이 소유한다. C의 실행 파일 검증이나 URL 규칙을 복제하지 않는다.
- `ExecutionGate`는 활성화 ACK 뒤 revision을 기억한다. 새 Task·세션 준비, turn, 도구 응답에서 실행 허가·revision을 확인한다. JSON-RPC 전송 잠금을 기다린 프레임도 실제 전송 직전에 다시 확인한다. 허가가 닫히거나 revision이 달라지면 해당 run에서는 다시 열지 않는다. 100ms마다 SQL과 무관한 상태 조회로 대기 중 세션도 interrupt·close한다. 이 감시 간격은 실시간 지연 보장이 아니다.
- `StorePrepareStep` 5개는 bridge 진행 이벤트와 고정 문구로 전달한다. 전달 콜백은 별도 Task에서 실행하며 완료 콜백이 `Task.exception()`을 소비한다. 콜백 오류가 B 준비를 실패시키거나 원문 로그로 나가지 않는다. 새 넓은 `except`는 넣지 않았다.
- `StoreStartFailure` 8개는 고정 문구·재시작 안내와 실패 코드로 표시한다. 시작 실패 후 창을 닫으면 종료 코드 1이다. 정리 미확인은 기존 A110의 종료 코드 3·잠금 유지 경로다.
- `load_recovery_report(run_id)`의 커밋된 이번 run 보고만 요약한다. 격리 목록은 `list_recovery_items(limit=100, after=...)`로 페이지를 바꾼다. run 필터를 걸지 않아 이전 run에 격리된 항목도 다음 시작에서 조회할 수 있다. 서버·도구·목적지·종류·epoch를 plain text로 표시하고, legacy의 누락 값은 미제공으로 표시한다. 결과 원문 복원·재실행 입구·식별자 로그는 없다.
- 실패·취소는 부분 자원을 정리한 뒤 Store `close()` → `wait_closed(5.0)`를 확인한다. 활성화 대기 중 이미 Runtime이 만들어진 경우에도 종료 작업의 예외·미완료를 `BackendCleanupError`로 바꿔 bridge가 잠금을 먼저 풀지 않게 한다. 정상 종료의 A134 정지 감사 대기와 A136 writer 종료 확인 순서는 유지했다.
- `bootstrap_product` 호출부를 tests 전체에서 검색해 새 폴더 인자로 맞췄다. `test_b28_integration.py`는 A 제품/정책 흐름 시험이며, 정책 저장 실패 뒤 기존 세션이 새 turn을 시작하던 기대를 실행 전 거부로 바꿨다. 기존 `docs/a-status.md`와 `.claude/` 변경은 수정하지 않았다. Git add·commit·push는 하지 않았다.

## 검증

Windows, 저장소 `.venv` Python 3.12, `QT_QPA_PLATFORM=offscreen`에서 실행했다. 새 A138 시험은 **34개**, 최종 관련 시험은 **356 passed, 122 subtests passed**, 실패·skip 없음이다. 기존 `test_runtime.py`의 pydantic_settings `lifespan` 미완성 annotation 경고 1개가 있었다.

Git Bash에서 실행한 최종 명령:

```bash
export QT_QPA_PLATFORM=offscreen TMPDIR=.cache/a-work/A138-tmp TEMP=.cache/a-work/A138-tmp TMP=.cache/a-work/A138-tmp
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A138-final-tmp \
  tests/unit/test_a138_product_startup.py tests/unit/test_product_assembly.py \
  tests/unit/test_product_settings.py tests/unit/test_session_runtime.py \
  tests/unit/test_codex_product_flow.py tests/unit/test_application_ids.py \
  tests/unit/test_a143_writer_shutdown_race.py tests/unit/test_codex_preparation.py \
  tests/unit/test_product_entry_logging.py tests/unit/test_b28_integration.py \
  tests/unit/test_codex_session.py tests/unit/test_jsonrpc_client.py tests/unit/test_bridge.py \
  tests/unit/test_a_architecture.py tests/unit/test_codex_task_runner.py \
  tests/unit/test_runtime.py tests/unit/test_ui_demo.py tests/unit/test_sqlite_app.py \
  tests/unit/test_bridge_conversation.py tests/unit/test_bridge_streaming.py \
  tests/unit/test_bridge_policy_access.py tests/unit/test_ui_sessions.py \
  tests/unit/test_ui_responsive_detail.py --tb=short
```

- 변경 Python 24개 파일에 `ruff check`, `ruff format --check`: 통과. 전체 `python -m mypy`: 소스 116개 통과. `lint-imports.exe --config tools/ci/importlinter.ini`: 7개 계약 유지. `git diff --check`: 통과.
- 실제 SQLite와 C 앱 폴더로 정확한 빈 v2 DB의 백업 발행·v3 이관, 다음 시작의 미종결 Task 정리를 확인했다. 실제 SQLite의 준비·복구·활성화 각 대기 중 취소도 확인했다. 5초 종료 확인 실패는 False 반환 주입이다.
- 활성화 ACK 대기 중 실제 Task 목록 0, 세션 생성 0, 합성 MCP 도구 호출 0을 확인했다. Guard 전역 정지 뒤 세션 추가 생성 0·turn 전송 0, 전송 잠금에 대기하던 turn 및 도구 응답의 실제 fake wire 기록 0을 확인했다. stdio·HTTP 제품 설정 조립 시험은 실제 Registry·Store·Guard와 합성 파이프를 쓰고, C launcher/HTTP factory는 대역이다.
- A143 실제 writer 종료 경합, A110 잠금 유지, A146 로그 설치 순서, 시연 SQLite·합성 MCP 흐름을 관련 기존 시험으로 회귀 확인했다. 전체 단위·contract·C 공격 시험·실제 모델·전원 장애·성능 측정은 실행하지 않았다.

## 의도적 회귀 대조

제품 파일에 아래 회귀를 하나씩 넣고 해당 새 시험을 실행했다. 매번 `finally`에서 변경 전 바이트를 복원했다. 17종 모두 시험 실패로 탐지했다. 단순 수집 오류나 실행 시간 초과를 탐지 성공으로 세지 않았다.

| 회귀 | 실패 사례 수 | 검사 |
| --- | ---: | --- |
| recover에 다른 deadline 전달 | 1 | 공유 deadline·시작 순서 |
| activate_run ACK 생략 | 1 | ACK 전 bindings·세션·submit 차단 |
| on_step을 격리하지 않고 직접 호출 | 1 | 콜백 예외 격리 |
| 초기 시작 실패에서 wait_closed 결과 무시 | 2 | prepare/recover 취소·정리 미확인 |
| Runtime 생성 뒤 종료 오류 분류 제거 | 1 | activate 취소·정리 미확인 |
| Store 실패를 모두 STARTUP_FAILED로 표시 | 8 | 실패 코드별 bridge·문구 |
| 제품 Store를 immediate로 되돌림 | 1 | 실제 v2 백업·명시 이관 |
| 복구 Task 개수를 0으로 표시 | 1 | 실제 이전 Task 복구·화면 |
| on_global_halt 연결 제거 | 1 | Guard 정지가 Store 권한을 닫음 |
| state_revision 비교 제거 | 1 | enabled가 True여도 revision 변경 거부·영구 latch |
| 도구 결과 후 실행 권한 확인 제거 | 1 | 반환 결과 미공개 |
| 새 turn 전 실행 권한 확인 제거 | 1 | turn 시작 거부 |
| idle 상태 감시 간격을 100초로 변경 | 1 | 다른 명령 없이 interrupt·close |
| 복구 페이지 limit를 99로 변경 | 1 | 100개 페이지·커서·이전 run 조회 |
| 창 종료 때 시작 실패 종료 코드 제거 | 8 | Qt 제품 창 종료 코드 1 |
| 전송 잠금 뒤 before_send 연결 제거 | 2 | 대기 turn·응답 wire 0 |
| MCP 프로세스 종료 확인 생략 | 2 | stdio 자원 소유·미확인 실패 |

실행기와 상세 출력은 `.cache/a-work/A138-mutations.py`, `A138-mutations.json`에 있다. 격리 목록을 전체 run 조회로 마무리한 뒤 페이지 회귀는 다시 실행했고 `A138-mutations-page_limit.json`에 남겼다.

## 남은 범위와 리뷰

- B·C 공개 API·domain·ports·Store·공통 문서는 바꾸지 않았다. B는 영속 순번·감사·전역 정지 연결과 종료 순서를, C는 launcher/폴더 소유·UI 연결을 리뷰하면 된다. 추가 계약 변경 요청은 없다.
- 실제 codex.exe·외부 MCP·Windows 강제 종료 후 재시작, 복구의 실제 수신·부작용 0 계수, 대량 DB 시간 예산은 C 독립 인수 대상이다. 합성 파이프·단위 시험을 그 인수로 보고하지 않는다.
- A81·A91의 기존 Profile 정책 저장은 이제 제품 Guard에 연결된다. 첫 설정 파일 생성(A135), 서버 추가·검사·사람 확인·활성화 화면의 종단 연결(A128 등)은 별도 남은 일이다.
- bridge 종료 상한 3초가 일부 자원 정리 상한보다 짧은 기존 관계는 유지했다. 정리가 더 걸리면 성공으로 추정하지 않고 A110 미확인 종료를 따른다.
- #69 댓글 원문은 `gh pr view`가 네트워크 차단됐고 GitHub 연결 도구도 404여서 읽지 못했다. 사용자에게 전달받은 계획, 로컬 B6.8 2a·2b·3 기록, 실제 공개 코드와 contracts를 근거로 구현했다.

관련: 설계 0.4·0.5·2·10.4·11.5·12장, I1·I5·I6·I7·I8·I9, SEQ-01–03·REC-01–05·CFG-01·EXE-01·URL-01·STOP-01. T11·T19·T21·T29·T41·T42·T45 및 S01·S02·S04의 관련 A 연결 경계를 검사했으며 C 전체 인수 완료를 뜻하지 않는다.

PR: 미정
