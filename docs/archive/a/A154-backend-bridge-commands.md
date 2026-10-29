# A154 · BackendBridge 생명주기·명령 실행 분해

2026-10-29. `wip/a154`, 기준 `e047a81`, 별도 worktree `.cache/a-work/wt-A154`. 동작 유지 리팩터링. 공개 계약 변경 없음. PR: 미정.

## 한 일과 경계

- `bridge.py`는 QObject·Qt 스레드 검사·요청 검증·신호 연결·UI 재수출을 맡는다. 공개 생성자/메서드/속성 이름과 시그니처, signal 선언, 명시적 UI 재수출을 HEAD와 AST로 대조해 동일함을 확인했다. `_ProcessStartGate`도 기존 import 위치에서 재수출하고 `_PROCESS_START_GATE` 주입 위치를 유지했다.
- 새 `bridge_host.py`의 `BackendHost`는 상태 잠금, 스레드·loop, bootstrap, 첫 화면, READY 통지, 복구 페이지, 종료·정리 확인을 소유한다. `ProcessStartGate`는 실제 스레드 종료와 정리 결과를 계속 확인한다. Qt는 가져오지 않고 `BridgeOutlet`·`LifecycleOutlet`에 주입한 콜백만 호출한다.
- 새 `bridge_context.py`의 `CommandContext`는 ingress·ledger·lanes를 소유한다. 접수, 중복 ID 재생/충돌, PROCESSING, BACKEND_RETURNED, UNCONFIRMED를 한 곳에서 처리한다. host가 제공한 **같은 상태 lock** 안에서 READY 검사·queue 삽입·ACCEPTED 기록을 한다. ingress → ledger, state → ledger 잠금 순서와 drain 8개 batch, urgent 우선순위, 완료 결과 재생 키를 유지했다.
- `bridge_commands.py`의 action handler는 명령 stage/장부를 갱신하지 않고 검증·도메인 호출·고정 결과 코드 반환을 맡는다. 호출마다 별도 `_ExecutionView`를 만들어 실행/정지/승인 네 경로의 Store projection `finally` 갱신을 하나로 모았다. 사전 검증을 통과한 시점에만 갱신을 설정한다. 승인 ID와 Task ID는 구분하며 호출 전 알던 경로 또는 호출 중 새로 얻은 경로를 쓴다.
- 의미가 다른 처리는 합치지 않았다. 새 세션 생성·정책 변경·로그아웃의 capability 통지, 등록/검토/대화 payload 발행, 부분 답변 stream 닫기 순서는 기존 action 흐름에 남겼다. `_stop_task`의 동기 queue discard → `finally`에서 fail-closed stop → 모델 세션 닫기도 그대로다.
- 종료의 ingress 거부 → backend 대기 queue 거부 → operations/login waits/workers 취소 → `shutdown_timeout` 대기 → 미종결 명령 UNCONFIRMED → bindings 정리 확인 순서를 유지했다. loop 잔여 Task의 취소와 0.1초 확인, 정리 미확인 시 재시작 거부도 그대로다.
- host 파일 안의 `_task_error`는 완료 Future의 예외만 소비한다. 호출부가 취소·완료·정리 성공을 판정하며 예외 원문/타입명은 출력하지 않는다. root Task 경계는 기존 `exception()` 판정을 유지했다. await의 취소를 잡는 새 `except`는 없다. 계약의 A82(2026-10-18) 허용 패턴을 기존 위치에서 옮긴 것이다.
- 시험은 비공개 접근·monkeypatch 대상 경로만 새 소유 객체로 바꾸고, A 구조 검사에 새 모듈 2개를 추가했다. 단언을 약하게 하거나 새 시험을 추가하지 않았다. `ui/*`, `bridge_types.py`, `bridge_registration.py`, B·C 제품 코드와 공통 문서는 수정하지 않았다.

## 측정

AST 기준 줄 수는 파일 전체, 긴 함수는 `end_lineno - lineno + 1 > 200`이다. 비공개 접근은 `self`/`cls` 이외의 수신자에서 `_`로 시작하고 `__`로 시작하지 않는 Attribute 노드 수다. 정적 근사이며 객체 타입을 추론하는 지표는 아니다. 같은 규칙으로 HEAD와 작업 결과를 비교했다. 원시 값은 worktree `.cache/a-work/A154-tmp-1/metrics.json`에 있다.

| 범위 | 전 줄 수 | 후 줄 수 | 200줄 초과 함수 전 → 후 | 외부 수신자 비공개 접근 전 → 후 |
| --- | ---: | ---: | --- | --- |
| bridge.py | 740 | 314 | 0 → 0 | 0 → 0 |
| bridge_commands.py | 521 | 548 | 0 → 0 | 0 → 0 |
| bridge_host.py | 없음 | 434 | 0 → 0 | 0 → 0 |
| bridge_context.py | 없음 | 169 | 0 → 0 | 0 → 0 |
| 위 제품 파일 합계 | 1,261 | 1,465 | 0 → 0 | 0 → 0 |
| 수정 시험 7개 합계 | 3,086 | 3,091 | 0 → 0 | 64 → 93 |

전체 줄 수 감소나 성능 개선은 주장하지 않는다. 명시적 타입·콜백·위임과 실행 문맥 때문에 총 204줄 늘었다. 시험의 내부 관찰은 제거하지 않았고 `_host` 경유로 AST 접근 노드가 늘었다. 이번 경계 분리는 Qt/생명주기/admission 책임을 분명히 하는 변경이다.

## 실제 검증

모든 명령·읽기·쓰기는 지정 worktree에서 했다. 공유한 것은 원래 checkout의 가상환경뿐이며 최초 `PYTHONPATH=src` import가 worktree의 `src/guard_app/__init__.py`임을 확인했다. 원래 checkout의 `src/`·`tests/`·`docs/`는 읽거나 수정하지 않았다.

Git Bash 환경:

```bash
export PYTHONUTF8=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen
export TMPDIR="$PWD/.cache/a-work/A154-tmp-1"
export TEMP="$TMPDIR" TMP="$TMPDIR"
PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A154-tmp-5 $(rg -l 'services.bridge|BackendBridge|A115|A137' tests/unit/test*.py)
$PY -m ruff check src/guard_app/services/bridge.py src/guard_app/services/bridge_host.py src/guard_app/services/bridge_context.py src/guard_app/services/bridge_commands.py $(git diff --name-only -- tests)
$PY -m ruff format --check src/guard_app/services/bridge.py src/guard_app/services/bridge_host.py src/guard_app/services/bridge_context.py src/guard_app/services/bridge_commands.py $(git diff --name-only -- tests)
$PY -m mypy
/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

- bridge 관련 33개 시험 파일: **454 passed, 138 subtests passed**, 56.40초. `test_bridge.py`·`test_bridge_*.py` 전부, bridge를 쓰는 UI 시험 6개, `test_a_architecture.py`, 계정·runtime·제품 시작/종료 관련 시험을 포함한다. `test_a137*` 별도 파일은 없고 A137 시험은 기존 bridge/account/runtime 파일 안에 있다.
- 직접 bridge·계정·구조 시험은 중간에 109개 통과했다. 처음에는 이동 전 private 참조 3개 시험이 실패했고 수정했다. 확장 실행은 452개 통과/2개 실패였으며 A151 private 참조가 정리 구문까지 막았다. 해당 실행의 남은 Python 프로세스를 정리하고 참조를 수정한 뒤 위 최종 실행은 정상 종료했다.
- Ruff check와 format check: 수정/신규 Python 11개 통과. mypy: 125개 소스 통과. import-linter: 7 kept, 0 broken. diff 공백 검사 통과.
- 기존 `pydantic_settings`의 `lifespan` 전방 참조 `IncompleteFieldDefinitionWarning` 1건이 출력됐다. 실제 codex.exe·실계정 로그인·전체 단위/공격/통합 인수·성능 측정은 미실행이다.
- 마지막 M18-HOST 대조 뒤 해당 정상 시험도 다시 실행해 1 passed를 확인했다(`--basetemp=.cache/a-work/A154-tmp-6`).

## 회귀 대조

`.cache/a-work/A154-tmp-1/mutate.py`, `mutate_replay.py`, `mutate_late.py`로 한 번에 제품 파일 하나만 변형했다. 각 pytest 프로세스에 40초 상한을 두고 `.cache/a-work/A154-tmp-<ID>`를 썼다. 매회 pyc를 지우고 `-B`로 실행했으며 `finally`에서 원래 바이트로 복원·동일성 확인했다. 13개 유효 회귀 모두 검출됐다. 로그/원시 결과 JSON은 같은 임시 폴더에 있다.

| ID | 주입한 회귀 | 잡은 기존 시험 | 결과 |
| --- | --- | --- | --- |
| M14 | shutdown의 세 lane 대기 명령 거부 생략 | `test_shutdown_rejects_each_backend_lane_before_unconfirmed_fallback` | exit 1, 2.27초 |
| M16 | 자원 정리 전 로그인 waiter 취소 생략 | `test_shutdown_cancels_device_wait_before_closing_account_resources` | exit 1, 2.09초 |
| M18-HOST | 늦은 bootstrap discard 결과를 무시하고 정리 성공 판정 | `test_late_bootstrap_close_error_is_fixed_unconfirmed_code` | exit 1, 2.22초 |
| M19 | bootstrap bindings의 usable 검사 생략 | `test_startup_failure_is_fixed_code_and_shutdown_refuses_work` | exit 1, 3.97초 |
| M20 | 첫 snapshot 실패 뒤 정리/실패 전이 대신 시작 계속 | `test_initial_snapshot_failure_or_stop_closes_resources`, `test_confirmed_cleanup_allows_new_bridge_but_not_same_instance` | exit 1, 6.28초 |
| CLEANUP | shutdown 정리 미확인을 CLOSED로 바꿈 | `test_unconfirmed_final_result_permanently_blocks_restart` | exit 1, 2.00초 |
| REPLAY-ACTUAL | 저장된 결과 방출 대신 같은 command 실행 Task를 만듦 | `test_run_queue_full_and_duplicate_command_cannot_reexecute` | exit 1, 2.47초 |
| LEDGER | replay 실패가 원래 command 장부를 UNCONFIRMED로 덮음 | `test_replay_error_does_not_lose_original_processing_record` | exit 1, 4.11초 |
| RESULT | 실행 실패 stage를 BACKEND_RETURNED로 바꿈 | `test_approval_failure_always_republishes_store_projection` | exit 1, 2.01초 |
| REFRESH | 공통 finally Store projection 갱신 생략 | 승인 실패 갱신 시험, 정지 queue 순서 시험 | exit 1, 2.14초 |
| STOP | control 호출 전 동기 queue discard 생략 | `test_stop_discards_queued_commands_before_control_starts` | exit 1, 2.11초 |
| EXPORT | facade의 ControlAction 재수출 제거 | `test_ui_approvals.py` 수집 | 예상한 ImportError, exit 2, 2.66초 |
| CONSUME | 완료 Future 예외를 검사하지 않고 항상 None 반환 | 정리 미확인 재시작 차단·시작 실패 시험 | exit 1, 2.16초 |

한 초기 변형(REPLAY)은 `_admit`의 중복 branch에서 return만 지웠고 시험이 통과했다. ingress가 중복을 replay item으로 보내 이 시험에서는 해당 branch에 도달하지 않아 실제 재실행을 만들지 못한 변형이다. 이를 숨기지 않고 원시 결과에 남겼고, replay 방출 지점에서 실제로 재실행시키는 REPLAY-ACTUAL로 확인했다. 독립 `_admit` 호출의 방어 분기 검증을 추가했다고 주장하지 않는다.

A137의 나머지 M05·M06·M07은 그대로인 ingress/ledger 구현, M09는 그대로인 account command 생성, M22·M24는 그대로인 streams, M30은 그대로인 로그아웃 capability 경로다. 해당 기존 시험은 최종 실행에 모두 포함했고 그 제품 파일에 회귀를 재주입하지 않았다. M18의 bindings 내부 구현도 그대로이며 이번에는 옮긴 host의 결과 소비 경계를 변형했다.

## 범위·남은 한계

관련: I1(계층/Guard 진입), I3·I8(중복 명령 재생과 재실행 금지), I5(확정 projection), I6(정지·정리 미확인), I9(고정 코드); 설계 0.4·0.5·2·10.4·12.1–12.3; T08·T12·T18·T20·S04 관련 A 단위/구조 검증, REC-01·REC-04·REC-05, STOP-01, A82. C 소유 T/S 인수 완료를 뜻하지 않는다.

회귀 대조는 runner 호출 횟수·queue 거부·로그인 waiter 취소 시점·정리 결과·Qt 신호를 관찰한다. 관련 runtime/SQLite 시험에는 합성 실제 MCP 경로도 포함되지만 이번 신규 경계 자체의 독립 dispatch·wire 계수 인수는 하지 않았다. 실제 모델/로그인·전체 공격 경로는 검증 불가/미실행으로 남긴다.

C의 bridge 생명주기·replay·종료 순서 리뷰가 남았다. B·C 계약이나 구현 변경 요청은 없다. Git add/commit/push는 하지 않았고 `docs/a-status.md`는 사용자가 합치도록 보고에 문안만 남긴다.
