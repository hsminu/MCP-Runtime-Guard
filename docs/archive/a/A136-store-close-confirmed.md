# A136 · Store를 닫은 뒤 연결 종료까지 확인(#58 병합)

2026-10-28. A 브랜치에 main(`6b6c75f`, #58·#59·#60·#62)을 합쳤다(`232cfe8`). 그 사이 병합된 #61(`8484473`), #63(`dc0982f`), #64·#67(`673cf5b`)도 이어서 합쳤다(`118e43e`·`d4e028e`·`8e33e3b`). 글자 충돌은 없었다. 대신 #58(B6.13) 때문에 A의 종료 동작이 어긋났다.

- #58 뒤 `SQLiteStore.close()`는 writer에게 종료를 요청만 하고 바로 돌아온다. 연결이 닫혔는지는 `wait_closed(timeout)`이 알려 준다(`ports.StoreLifecycle`).
- A는 `close()`만 불렀다. 그래서 병합한 그대로면 DB 연결이 아직 열려 있어도 정리 완료로 보고 단일 실행 잠금(`app.lock`)을 풀 수 있었다. REC-01(잠금 해제 전 Store 종료 확인)과 #58 B의 종료 순서 요청에 어긋난다.

## 한 일

- **세션 런타임 종료**
  - `SessionRuntime`이 닫을 Store를 `owned_store`(`StoreLifecycle`)로 받는다. 전에는 `close_store` 콜백을 받았다.
  - `close()` 순서는 #58 B 요청 그대로다.
    1. 세션 정지 기록이 끝나기를 기다린다(A134).
    2. Store 종료를 요청한다.
    3. `wait_closed(5.0)`으로 연결 종료를 확인한다.
  - 확인하지 못하면 `STORE_CLOSE_UNCONFIRMED`를 낸다. 다시 `close()`해도 같은 코드다. bridge는 이것을 `CLEANUP_UNCONFIRMED`로 보고, 앱은 잠금을 쥔 채 종료 코드 3으로 끝난다(A110).
  - 5초는 `CleanupLimits.store_close_s`다.
  - 아직 도는 세션 생성자가 Store를 닫는 경우(종료가 생성자를 기다리다 포기한 경우)는 기다리지 않는다. 그때 종료는 이미 `CODEX_CLEANUP_UNCONFIRMED`로 보고했다.
  - 미확인 정지·Store 보고를 `_raise_if_closing_unconfirmed` 하나로 모았다. `close()`의 분기 수가 A111 기준(12) 안에 남는다.
- **시작 실패:** `bootstrap_product`가 설정 파일 읽기나 Guard 조립에 실패해 Store를 직접 닫을 때도 연결 종료를 기다린다. 확인하지 못하면 `BackendCleanupError`를 내고, bridge는 `STARTUP_CLEANUP_UNCONFIRMED`로 끝난다.
- **시연(`examples/synthetic_app.py`):** Store 닫기를 비동기 정리로 바꿔 임시 폴더를 지우기 전에 연결 종료를 기다린다. 확인하지 못하면 `BackendCleanupError`다. `close()`는 예외를 내지 않으므로(포트 설명) `StoreError`를 잡던 부분은 지웠다.

## 검증

- **테스트**
  - 새로 넣은 것
    - `test_session_runtime.py`: 종료가 끝난 직후 DB 파일을 지운다. Windows는 열린 DB 파일을 지우지 못하므로 연결이 닫혔는지가 드러난다.
    - `test_session_runtime.py`: `wait_closed`가 False인 Store는 `STORE_CLOSE_UNCONFIRMED`이고 다시 닫아도 같다. 기다린 시간은 5.0초 한 번이다.
    - `test_product_assembly.py`: 시작 실패 때 연결 종료를 확인하지 못하면 `BackendCleanupError`다.
  - 기존 것 강화: 잘못된 Profile로 시작이 실패한 뒤 DB 파일을 지운다(`test_product_bootstrap_with_invalid_profile_closes_the_store`).
  - 계약에 맞춰 바꾼 것: `test_sqlite_app.py::test_bridge_reports_store_cleanup_failure`는 `close()`가 `StoreError`를 낸다고 가정했다. 이제는 `wait_closed`가 False인 경우로 본다.
- **회귀를 일부러 넣었다.** 다섯 개 모두 테스트가 실패해서 잡았다.

| ID | 회귀 | 결과 |
| --- | --- | --- |
| K1 | 종료가 연결 종료를 기다리지 않음 | 잡음(2개 실패, DB 파일 지우기 포함) |
| K2 | 다시 닫을 때 미확인을 알리지 않음 | 잡음(2개 실패) |
| K3 | 시작 실패 때 연결 종료를 기다리지 않음 | 잡음(1개 실패) |
| K4 | 시연이 연결 종료를 기다리지 않음 | 잡음(2개 실패) |
| K5 | 5초 대신 0초 기다림 | 잡음(36개 실패) |

- K5에서 보듯 writer는 `close()` 직후에는 아직 닫혀 있지 않다. 기다리지 않으면 실제로 열린 연결을 두고 끝난다.
- **A134를 #58 writer로 다시 봤지만 재현하지 못했다.**
  - B 흐름(`run_one_turn`, L3 위반으로 HALTED)을 `wait_for_status` 없이 turn 직후 바로 닫았다. 닫은 뒤 DB를 다시 열어 Task가 HALTED로 기록됐는지 봤다.
  - 30번 모두 기록됐고 `close()` 오류도 없었다.
  - 그런데 A134의 대기를 뺀 옛 순서로 바꿔도 30번 모두 같았다. 이 흐름에서는 정지가 종료 전에 이미 기록된다.
  - 그래서 #58 writer에서 해소됐는지는 확인하지 못했다. 근거는 여전히 A134의 단위 테스트(정지가 lock을 기다리는 상황)다.
- 전체 결과(Windows, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`, `232cfe8`에 A115·A136 작업 트리)
  - 단위 5,346개 통과, 4개 skip, subtest 20개, 실패 없음. contract 45개 통과. ruff check·ruff format --check(257개 파일)·mypy(110개 소스 파일) 통과
- #61·#63을 합친 뒤(`d4e028e`에 A115·A136 작업 트리)
  - 단위 5,429개 통과, 4개 실패, 5개 skip, subtest 20개. contract 45개 통과. ruff check·ruff format --check(263개 파일)·mypy(110개 소스 파일) 통과
  - 실패 4개는 C 소유 `test_write_loss_counts.py`의 HTTP 닫기 실패 기대값이다(`[] == ['SessionCloseFailed']`). A132 때문이고 이번 변경과는 무관하다. 자세한 내용은 [A115 기록](A115-backend-bridge-split.md)에 있다.
- #64·#67을 합친 뒤(`8e33e3b`): A 규칙 테스트와 새 파일 서버 테스트만 돌렸다(241개 통과). 전체 테스트는 돌리지 않았다.

## 남은 것

- **A134의 #58 writer 재현은 B 방법이 필요하다.** B는 #58 writer에서 잘 재현된다고 했다. A 흐름으로는 옛 순서도 재현되지 않았다.
- **5초 안에 못 닫는 writer는 만들지 못했다.** 그 경우는 `wait_closed`가 False를 돌려주도록 바꿔서 확인했다.
- **bridge 종료 상한(3초)이 이 대기(5초)보다 짧다.** 연결 종료가 3초를 넘기면 bridge가 먼저 미확인으로 끝낸다. 결과는 같은 미확인 종료(종료 코드 3, 잠금 유지)다. 상한 관계는 바꾸지 않았다.
- **#58의 나머지 A 요청은 아직이다.** 제품 모드가 Guard를 조립하는 단계(B6.8 뒤)에서 한다.
  - `GuardService(..., on_global_halt=store.halt_execution)`
  - 새 turn 전, 도구 응답을 돌려주기 전, 새 Task·세션 전에 `execution_state().execution_enabled` 확인과 `state_revision` 비교
- **정상 종료 뒤 Store 상태:** `guard.aclose()`도 전역 정지라 Store는 `FAULTED`로 끝난다. A 화면은 종료 뒤 Store 상태를 보여 주지 않으므로 오류로 표시되는 곳은 없다.
- **C 하니스(#61):** 같은 순서(`close()` → `wait_closed(5.0)` → 앱 폴더 닫기)는 B가 C에 요청했다. C 소유라 A가 고치지 않았다.

관련: REC-01·REC-05, I6, A110·A134, [#58](https://github.com/hsminu/MCP-Runtime-Guard/pull/58). PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
