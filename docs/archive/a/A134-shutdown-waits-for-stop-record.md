# A134 · 앱 종료가 세션 정지 기록을 기다린 뒤 Store를 닫음(#58 B 답변)

2026-10-28. B가 [#58](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6468080908)에서 `SystemControlError`를 본 조건을 알려 줬다. `test_l3_violation_halts_only_this_task_and_interrupts_codex`를 #58 writer 초안에서 돌렸을 때다. 원인은 A 쪽 종료 순서다.

## 원인

- 세션이 실패로 멈추면(L3 위반, 인증 실패 등) A는 Guard 시스템 채널의 `stop_task`를 백그라운드로 시작한다(STOP-01). Guard는 Task lock을 잡은 뒤 HALTED와 감사를 쓴다.
- `SessionRuntime.close()`는 이 작업을 기다리지 않았다. 세션 정리 → Guard 닫기 → Store 닫기 순서로 바로 갔다.
- 정지가 아직 lock을 기다리거나 쓰는 중이면 둘 중 하나가 된다.
  - 닫힌 Store가 쓰기를 거부한다.
  - Guard 닫기의 전역 정지가 기록을 거부한다.
- 어느 쪽이든 정지는 `SystemControlError`로 끝나고 Task 행은 RUNNING으로 남는다.

## 한 일

- Guard 기록 작업(세션 정지, `request_user_input` 감사)을 정리 작업과 다른 `BackgroundWork`에 모은다. 종료가 그것만 기다릴 수 있다.
- `BackgroundWork.wait(timeout_s)`: 기다리는 동안 새로 시작된 작업까지 기다린다. 시간 안에 다 끝나지 않으면 False다.
- 종료 순서(`SessionRuntime.close()` → `_close_guard()`)
  1. Codex 세션과 Task 자원 정리(기존)
  2. transport 닫기. 예약된 호출이 정리되고 그 Task lock이 풀린다.
  3. 정지 기록을 기다린다. 상한은 `CleanupLimits.stop_record_s`(5초)다. Guard는 아직 기록을 받는다.
  4. Guard 닫기(전역 정지)
  5. 남은 기록을 한 번 더 기다린다(`session_s`). 상한을 넘긴 정지는 정지된 Guard에 거부되어 끝난다. Store가 닫히기 전에 끝나게 하려는 것이다.
  6. Store 닫기
- 3이나 5가 시간 안에 끝나지 않으면 Store는 그대로 닫고 `close()`가 `SESSION_STOP_UNCONFIRMED`를 올린다. 다시 불러도 같은 코드다. bridge는 이것을 정리 미확인 종료로 받는다(A110).
- `AssembledGuard.aclose(settle=...)`: transport 닫기와 Guard 닫기 사이에 `settle`을 부른다. transport 닫기가 실패하거나 취소되어도 `settle`과 Guard 닫기는 실행된다.
- 종료가 바깥에서 취소되어도 Guard 닫기와 Store 닫기는 `finally`에서 실행된다.

## 검증

- `tests/unit/test_session_runtime.py`
  - 정지 작업이 lock을 기다리는 동안 종료를 시작한다. Store는 열린 채로 기다리고, 정지가 쓴 뒤에 닫힌다. 순서는 `stop-recorded` → `store-closed`이고 `close()`는 오류가 없다.
  - 정지가 상한 안에 끝나지 않으면 `SESSION_STOP_UNCONFIRMED`이고 Store는 닫힌다. 두 번째 `close()`도 같은 코드다. 늦게 온 쓰기는 닫힌 Store에 거부된다.
  - 옛 종료 순서로 바꿔 돌리면 두 테스트 모두 실패한다. 첫 테스트는 정지보다 Store가 먼저 닫히고, 둘째 테스트는 미확인을 알리지 않는다.
- `tests/unit/test_product_assembly.py`: `aclose(settle=...)`에서 `settle`은 transport가 닫힌 뒤, Guard 닫기 전에 불린다.
- `tests/unit/test_session_collaborators.py`: `BackgroundWork.wait`가 대기 중에 시작된 작업까지 기다리고, 시간 초과면 False, 작업이 없으면 바로 True다.
- B가 말한 흐름(`run_one_turn` + L3 위반, 실제 B Guard·SQLite)을 30번 돌렸다. 옛 순서와 새 순서 모두 미확인 정지 0건이었다. 이 브랜치의 Store에서는 경합이 드러나지 않았다. 그래서 결정적 재현은 위 단위 테스트(정지가 lock을 기다리는 상황)로 했다.
- 기존 세션 런타임·정지·Codex 제품 흐름 테스트가 그대로 통과한다.
- 전체 결과는 A08·A132·A133과 같은 실행이다(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`).
  - 단위 5,202개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(238개 파일)·mypy(102개 소스 파일) 통과

## 남은 것

- **#58 writer로 확인한다.** B 말로는 #58 writer에서 잘 재현된다. #58이 이 브랜치에 들어오면 `1d1575c`의 기다림 없이 그 테스트를 반복해서 `SystemControlError`가 없어졌는지 본다.
- 제품 bridge의 종료 상한은 3초(`shutdown_timeout` 기본값)다. 세션 정리 상한(최대 12초+5초)과 이 대기(최대 5.5초)보다 짧다. 상한을 넘기면 bridge가 미확인으로 보고 종료한다(A110). 이 관계는 바꾸지 않았다.
- 진행 중인 Guard submit(예약 뒤 결과 커밋)은 따로 기다리지 않는다. 같은 Task의 정지는 그 submit이 lock을 놓은 뒤에 기록되므로 3단계 대기에 들어간다. 정지가 없는 Task의 submit 커밋이 Store 닫기 전에 끝나는지는 확인하지 않았다.
- PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
