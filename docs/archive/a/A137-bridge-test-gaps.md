# A137 · bridge 시험 빈틈 11개와 멈춤 1개 메우기

2026-10-29. A115의 M05·M06·M07·M09·M14·M16·M18·M19·M22·M24·M30과 멈춤 M20을 시험으로 메웠다. 제품 결함은 확인되지 않아 제품 코드는 바꾸지 않았다. 공개 API·계약 변경 없음. PR: 미정.

## 한 일

- `tests/unit/test_bridge.py`: 닫힌 입구의 거부·예약 계수 0·빈 큐, 선행 ACCEPTED 없는 미확인 기록 보존, 완료 기록 2개 상한과 진행 중 기록 보존, 계정 명령의 비밀값 배제, 세 backend 줄의 종료 거부, 늦은 bootstrap 정리 확인을 단언했다. 기존 시작 실패 반복에 잘못된 `HumanControl`을 추가했다.
- `tests/unit/test_bridge_streaming.py`: 누적 70,000자까지 허용하고 초과 조각은 제외하는 경계, add 전·add 후 flush 전 종료 요청을 각각 확인했다. 타이머나 sleep을 기다리지 않고 스트림 close로 flush한다.
- `tests/unit/test_codex_account.py`: 자원 close 시점에 로그인 waiter가 이미 취소됐는지 확인했다. 종료 뒤의 전체 loop 정리가 누락을 가리지 못하게 했다. 로그아웃 성공·실패 모두 blocker를 다시 읽어 capability 신호를 보내는지 확인했다.
- `tests/unit/test_runtime.py`와 기존 bridge 재시작 시험: 첫 화면 실패 뒤 종료 대기는 2초로 제한하고, 실패해도 `finally`에서 bridge를 닫고 제한된 시간 안에 스레드 종료를 확인한다.
- 기존 `attach`·`pump_until`·`close_bridge`·`FakeControl`·`client` 도우미를 사용했다. 같은 모양은 parametrize·반복으로 묶었다. 새 sleep 없음.

M20의 기존 runtime 대기에도 기본 8초 상한은 있었지만, 그 단언이 실패하면 non-daemon backend 스레드가 남아 pytest 프로세스가 끝나지 않았다. 따라서 대기 상한을 명시하는 것과 실패 경로의 자원 정리를 함께 보강했다. `test_confirmed_cleanup_allows_new_bridge_but_not_same_instance`에도 같은 정리를 넣었다.

## 회귀 대조

지정 worktree `wip/a137`에서 제품 파일 하나에 아래 변경을 실제로 적용한 뒤 해당 시험을 별도 pytest 프로세스로 실행했다. 매번 원래 바이트를 저장하고 `finally`에서 복원·동일성을 확인했다. 각 실행은 바깥 30초 제한을 두었지만 **12개 모두 이 제한에 걸리지 않고 시험 실패(종료 코드 1)로 끝났다**. 시간은 프로세스 실행부터 종료까지이며 성능 측정값이 아니다. A115에는 주입 코드 자체가 없으므로 아래는 기록된 각 회귀 의미를 현재 코드에 재구성한 변경이다.

| ID | 실제 주입한 회귀 | 잡은 시험 | 결과 |
| --- | --- | --- | --- |
| M05 | `CommandIngress.offer`의 닫힘 검사를 제거해 닫힌 입구가 새 명령·재생을 받게 함 | `test_closed_ingress_refuses_new_and_replayed_commands` | 시험 실패로 검출, 종료 코드 1; 1.92초 |
| M06 | `CommandLedger.record`에서 선행 ACCEPTED 없이 들어온 UNCONFIRMED 기억을 제거 | `test_ledger_remembers_unconfirmed_admission_and_evicts_only_old_settled_ids` | 시험 실패로 검출, 종료 코드 1; 1.88초 |
| M07 | `CommandLedger.record`의 `_max_ids` 초과 시 오래된 완료 기록 제거를 생략 | `test_ledger_remembers_unconfirmed_admission_and_evicts_only_old_settled_ids` | 시험 실패로 검출, 종료 코드 1; 2.06초 |
| M09 | `account_command`가 API 키 이외의 모든 동작에도 `user_text=secret`을 실음 | `test_only_api_key_account_command_carries_the_secret` | 시험 실패로 검출, 종료 코드 1; 2.3초 |
| M14 | `BackendBridge._shut_down`에서 `reject_waiting(...SHUTTING_DOWN)` 호출 제거 | `test_shutdown_rejects_each_backend_lane_before_unconfirmed_fallback` | 시험 실패로 검출, 종료 코드 1; 2.3초 |
| M16 | `BackendBridge._shut_down`에서 `self._accounts.cancel_waits()` 호출 제거 | `test_shutdown_cancels_device_wait_before_closing_account_resources` | 시험 실패로 검출, 종료 코드 1; 2.38초 |
| M18 | `BindingsOwner.discard`가 `close(limit_s=...)` 결과를 버리고 항상 True 반환 | `test_late_bootstrap_requires_confirmed_cleanup` | 시험 실패로 검출, 종료 코드 1; 2.26초. 리뷰 보강 뒤 재주입: 정리 미확인 2개(완료·취소 뒤 성공) 실패 |
| M18 보강 | `discard`가 취소 뒤 늦은 bootstrap을 기다리지 않음(`asyncio.wait` 제거) | `test_late_bootstrap_requires_confirmed_cleanup[*-after-cancel]` | 취소 뒤 성공 2개 실패 |
| M19 | `usable`에서 `HumanControl` 타입 검사 제거 | `test_startup_failure_is_fixed_code_and_shutdown_refuses_work` | 시험 실패로 검출, 종료 코드 1; 4.33초 |
| M20 | `_start_backend`의 첫 화면 실패 분기에서 `_end_startup`·정리·`return None`을 `pass`로 바꿔 시작 계속 | `test_initial_snapshot_failure_or_stop_closes_resources`<br>`test_confirmed_cleanup_allows_new_bridge_but_not_same_instance` | 시험 실패로 검출, 종료 코드 1; 6.59초 |
| M22 | `AnswerStreams.add`의 누적 `MAX_PARTIAL_CHARS` 초과 검사 제거 | `test_partial_size_limit_applies_to_accumulated_text` | 시험 실패로 검출, 종료 코드 1; 2.28초 |
| M24 | `AnswerStreams.add`·`_flush`의 종료 요청 검사 둘 다 제거 | `test_stopped_stream_drops_buffered_and_new_pieces` | 시험 실패로 검출, 종료 코드 1; 2.22초 |
| M30 | 로그아웃 `finally`의 `publish_capability()`를 생략 | `test_logout_republishes_session_capability_even_on_error` | 시험 실패로 검출, 종료 코드 1; 2.39초 |

리뷰(luna)는 처음 M18 시험이 bootstrap을 끝낸 뒤 `discard`를 불러 취소 뒤 늦게 성공하는 경로를 거치지 않는다고 지적했다. 시험을 bootstrap 완료/취소 뒤 성공 × 정리 확인/미확인 4개로 넓혔다. 취소 뒤 성공은 시험 bootstrap이 취소를 억제해 흉내 낸다. 보강 뒤 수정 시험 4개 파일 70개 통과, Ruff check·format 통과.

M20은 두 시험에서 각각 2초 대기 단언으로 실패했다. 두 시험을 함께 실행한 프로세스는 6.59초에 종료됐다(pytest 보고 5.91초). 실패 뒤에도 스레드를 정리하므로 예전의 420초 프로세스 멈춤으로 이어지지 않았다. M14는 최종 UNCONFIRMED만 확인하지 않고 세 줄 모두 REJECTED/SHUTTING_DOWN 한 건과 queue.join 완료를 확인한다. M16은 계정 자원을 닫기 **전** 취소 상태를 관찰한다.

## 실제 검증

작업 경로: `C:/Project/sogaeding/MCP-Runtime-Guard/.cache/a-work/wt-A137`. 모든 소스·문서·시험 읽기와 수정은 이 worktree에서 했다. 최초 `PYTHONPATH=src` import 확인 결과도 이 worktree의 `src/guard_app/__init__.py`였다.

Git Bash 환경과 실행 명령(원래 checkout의 가상환경만 공유):

```bash
export PYTHONUTF8=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen
export TMPDIR="$PWD/.cache/a-work/A137-tmp-1"
export TEMP="$TMPDIR" TMP="$TMPDIR"
PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A137-tmp-3 tests/unit/test_bridge.py tests/unit/test_bridge_streaming.py tests/unit/test_codex_account.py tests/unit/test_runtime.py
$PY .cache/a-work/A137-tmp-1/mutate.py
$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A137-tmp-4 tests/unit/test_bridge*.py tests/unit/test_codex_account.py tests/unit/test_codex_browser_login.py tests/unit/test_runtime.py tests/unit/test_a_architecture.py
$PY -m ruff check tests/unit/test_bridge.py tests/unit/test_bridge_streaming.py tests/unit/test_codex_account.py tests/unit/test_runtime.py
$PY -m ruff format --check tests/unit/test_bridge.py tests/unit/test_bridge_streaming.py tests/unit/test_codex_account.py tests/unit/test_runtime.py
$PY -m mypy
/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
git diff -- src/guard_app
```

- 수정 파일 시험: 67개 통과(11.64초). 첫 작성 실행의 신호명 오타로 2개 실패한 것은 `session_capability_changed`로 고친 뒤 재실행한 결과다.
- 회귀 주입·복원 뒤 직접 관련 시험과 A 구조 규칙: 106개 통과(14.59초). 전체 단위 시험은 실행하지 않았다.
- Ruff check·format check: 4개 파일 통과.
- mypy: 114개 소스 파일 통과.
- import-linter: 계약 7개 유지, 깨짐 0개.
- diff 공백 검사 통과. 제품 파일 diff 없음.
- runtime 실제 MCP 시연 시험에서 `pydantic_settings`의 `lifespan` 전방 참조 `IncompleteFieldDefinitionWarning` 1건이 출력됐다. 실패는 아니다.
- 주입 명령은 `$PY -B -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A137-tmp-<M ID> <표의 시험 노드>`다. M20만 두 노드를 함께 실행했다. 같은 이름 pyc를 지워 주입 코드가 실제 import되게 했고, 매번 원래 제품 바이트로 복구했다. 임시 실행 코드·로그는 worktree의 `.cache/a-work/A137-tmp-1/`에 있다.

## 범위·남은 한계

관련 불변식: I6(거부·시작 실패·정리 미확인), I8(미확인 명령의 재생 기록), I9(비밀값 배제·종료 후 조각 억제). 개발문서 12.1–12.3·10.4, 계약 REC-01·REC-05(기존 재시작 정리 시험)와 연결된다. T20의 제어 큐·대기 취소, S04의 비밀값 배제를 보조하는 A 단위 시험이다. 이 변경만으로 C의 T20·S04 전체 검증을 완료했다고 보지 않는다. 나머지 자료구조·스트리밍 크기 상한에는 직접 대응하는 별도 T/S ID가 없다.

입구 예약·대기 큐·자원 닫기·신호 발행을 실제로 관찰했다. 새 시험은 외부 dispatch·wire·수신 부작용을 관찰하지 않으므로 그 경로는 이 작업에서 검증 불가다. 기존 runtime 관련 시험은 실행했지만 실제 codex.exe·실계정 로그인·전체 보안 통합·공격 시험은 미실행이다. C의 bridge 시험 리뷰는 남아 있다. B·C 제품 코드나 계약의 변경 요청은 없다.
