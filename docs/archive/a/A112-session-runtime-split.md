# A112 · 리팩터링 1단계: SessionRuntime 책임 분리

2026-10-27. 동작은 바꾸지 않았다. 바뀌지 않은 것은 두 가지다.
- 진입점: `bootstrap_product`, `SessionRuntime` 생성자, `bindings()`와 거기 묶인 메서드.
- B·C와 맞물리는 곳: `SessionPreparation`·`TaskResources`, Guard의 `system_control`·`human_control`·`apply_policy_change`, `Store`, `BackendBindings`.

## 한 일

- `SessionRuntime`(702줄·메서드 30개)을 규칙 하나씩 맡는 협력 객체로 나눴다. `SessionRuntime`에는 세션 생성·종료 흐름과 `bindings()`만 남았다.

| 모듈 | 객체 | 맡는 규칙 |
| --- | --- | --- |
| `session_admission` | `SessionAdmission`, `SessionRuntimeError` | 새 세션을 거부하는 사유와 그 순서: 종료, 정리 미확인, 저장했지만 적용 못 한 정책(A104). 한 번 걸리면 재시작 전까지 풀리지 않는다. |
| `session_startup` | `StartupGate`, `call_preparation` | C 준비 콜백 호출. 앱 전체 준비는 한 번만 하고, Task마다 구성을 다시 확인한다. 콜백 오류는 원문 없는 고정 코드로 바꾸고, 취소는 삼키지 않는다. |
| `session_cleanup` | `SessionCleanup`, `CleanupLimits`, `BackgroundWork` | Task가 띄운 세션과 C 자원을 소유하고, 시간 제한 안에서 정리한다. 확인 없이는 지우지 않고, 미확인이면 다시 시도하지 않는다. |
| `session_stops` | `StopChannel`, `stop_reason` | 정지를 기록하는 경로(STOP-01). Guard가 있으면 Guard 시스템 채널로 정지한다. A가 HALTED를 직접 쓰는 것은 Guard가 없을 때뿐이다. |
| `session_policy` | `TaskPolicy` | 새 Task 행이 받는 Profile, 서버 접근 변경(B3.10), 그 둘이 함께 쓰는 정책 lock(B3.12·A105). |

- 공개 API 변화:
  - **추가:**
    - 생성자 키워드 `system_control`. 기본값은 Guard 시스템 채널이다.
    - 생성자 키워드 `cleanup_limits`. 기본값은 검토한 0.5·12·5초다.
    - 읽기 전용 `pending_cleanup`.
  - **이동:**
    - 모듈 상수 세 개(세션 정리 0.5초, 종료 확인 12초, 폴더 삭제 5초)는 `CleanupLimits` 필드가 됐다.
    - `stop_reason`은 `session_stops`로 옮겼다.
  - **제거:** `SessionRuntime.set_task_listener`·`set_partial_listener` 메서드. 밖에서 부르는 곳이 없었고, `bindings()`의 `task_events`·`answer_partials`는 그대로다.
- 정리하면서 함께 바꾼 것. 모두 동작 차이는 없다.
  - Task별 사전 셋(`_runners`·`_options`·`_codes`)을 Task 하나에 대한 `_RunningTask`로 합쳤다.
  - 생성 중 추적(`_creating`·`_creating_ids`)을 `_Creations` 하나로 합쳤다. Store 닫기를 마지막 생성자에게 넘기는 규칙도 여기로 옮겼다.
  - 구성 비교를 `require_configuration` 하나로 묶었다. 검증 결과와 자원 영수증 모두 이것으로 확인한다.
  - 시작 확인을 통과한 뒤에는 닿지 않는 `preparation is None` 재검사 1곳을 없앴다.
  - 같은 세션을 두 번 등록하던 줄을 없앴다.
  - 문서 주석에 남아 있던 옛 상수 이름을 고쳤다(`session_preparation`·`codex_preparation`).
- 테스트:
  - 비공개 속성 접근과 모듈 상수 monkeypatch를 공개 경로로 바꿨다. 쓴 것은 생성자 주입, `pending_cleanup`, 두 번째 `close()`의 결과(미확인이면 다시 거부)다.
    - `test_session_runtime`: 41곳 → 0곳
    - `test_session_preparation`: 14곳 → 0곳
    - `test_b28_integration`: 4곳 → 2곳
  - 남은 2곳은 A105 경쟁 테스트의 정책 lock 교체(`runtime._policy.lock`)다. 세션 생성이 그 lock에서 기다린다는 것을 보여 주는 공개 신호가 없다.
  - 새 `test_session_collaborators.py`(18개)가 다루는 것:
    - 거부 순서
    - 취소를 무시한 생성자 거부
    - 정리 기본 시간
    - 잘못되거나 공유된 영수증 7가지
    - 세션 중복
    - 백그라운드 예외 소비
    - Guard 없는 정지 경로: UNKNOWN_OUTCOME 등 끝난 행은 덮어쓰지 않음
    - 실패한 시스템 정지
    - Profile이 없을 때
  - `test_human_stop_with_guard_leaves_the_store_to_guard`에 Guard 정지가 아직 끝나지 않은 경우를 더했다. 원래 테스트는 A의 HALTED 함수 호출을 막았다. 바꾼 테스트는 Store 쓰기를 직접 센다.

## 검증

- 전체 테스트 4,623개가 통과했다(skip 1). Ruff, format, mypy(strict)도 통과했다.
- A 규칙 테스트(A111)에 새 모듈 5개를 A 모듈로 분류했고, 계층·위험 연산·넓은 예외 처리 규칙을 그대로 통과한다.
- 회귀 12개를 일부러 하나씩 넣었고, 모두 테스트가 잡았다.
  1. 미확인 정리를 다시 시도
  2. 생성 중인 Task를 종료 쪽이 정리
  3. 생성자가 남았는데 Store를 닫음
  4. 실패한 시스템 정지를 적용된 것으로 표시
  5. 정책 lock 밖에서 Task 행 기록
  6. Guard가 있는데 A가 HALTED 기록
  7. 공유 영수증을 소유
  8. 종료 확인 없이 자원 해제
  9. `confirm_exit`에 짧은 제한 적용
  10. 재시작 코드 해제
  11. 취소 중인 생성자 통과
  12. 거부된 시작을 다시 시도
- 처음에는 2개를 놓쳤다.
  - 6번: 바꾼 테스트가 원래보다 약해진 탓이다. Guard가 먼저 HALTED로 만들면 A의 기록 함수가 아무것도 쓰지 않는다. Guard 정지가 끝나기 전 경우를 더해서 잡았다.
  - 11번: 원래부터 테스트가 없었다. 새로 썼다.
- 측정 방법은 A111과 같다. [근거](../evidence/a112-session-runtime-split-2026-10-27.json)

| 항목 | A111 기준선 | A112 |
| --- | --- | --- |
| `SessionRuntime` | 702줄·메서드 30개 | 350줄·메서드 19개 |
| `session_runtime`의 기준 초과 함수 | 5개: `_create_session` 122줄·분기 29, `_close_session` 63줄·21, `change_server_access` 73줄·17, `close` 49줄·15, `__init__` 66줄 | 0개. 새 모듈 5개도 0개 |
| A 전체 기준 초과 함수 | 64개 | 59개 |
| A 모듈 | 41개·13,888줄 | 46개·14,247줄 |
| A 단위 테스트 함수 | 582개 | 591개 |
| 테스트의 비공개 속성 접근 | 242곳 | 192곳 |

## 남은 한계

- `SessionRuntime`은 줄 기준(300줄)을 아직 50줄 넘는다. 남은 메서드는 같은 Task 상태(`_running`)를 함께 쓴다. 남은 줄의 상당수는 리뷰 결정을 적은 docstring이다. 그래서 더 쪼개지 않았다.
- 정책 변경 두 개가 겹치면 앞의 변경이 사라질 수 있다. 동작을 바꾸는 일이라 이번에 고치지 않았다.
  - `change_server_access`는 정책 lock을 잡기 전에 현재 문서를 읽는다.
  - bridge의 control 작업자는 기본 4개라 정책 변경 명령 두 개가 동시에 돌 수 있다. 그러면 뒤의 명령이 앞의 변경 전 문서로 저장해, 앞의 변경(예: 도구 거부)을 되돌린다.
  - 코드 경로로만 확인했다. 재현 테스트와 화면에서 실제로 겹칠 수 있는지는 미확인이다.
- 실제 codex.exe로는 다시 돌리지 않았다. A107·A108 스파이크도 같은 `bindings()` 경로를 쓴다.

관련: I6·I7·I9, STOP-01, B3.10·B3.12, 개발문서 10.4·13.1. PR은 아직 없다(A 브랜치 작업).
