# A115 · 리팩터링 4단계: BackendBridge 안쪽 분리

2026-10-28. 동작은 바꾸지 않았다. 바뀌지 않은 것은 세 가지다.
- 공개 API: `BackendBridge`의 생성자·시그널·`request_*`·`start`·`close`·속성. `services.bridge`가 내보내는 이름도 그대로다(UI·예제·테스트가 쓰는 것).
- 고정 코드와 그 순서: 명령 단계(ACCEPTED → PROCESSING → BACKEND_RETURNED/REJECTED/UNCONFIRMED), 시작·종료 상태 코드, 입력 검사 오류 문구.
- B·C와 맞물리는 곳: `BackendBindings`, `HumanControl`, `TaskExecution`, `_PROCESS_START_GATE`(테스트가 바꿔 끼우는 시작 게이트).

## 한 일

- `BackendBridge`(1,160줄·메서드 49개)를 맡는 일 하나씩 가진 협력 객체로 나눴다. `bridge.py`에는 Qt 쪽 입구(시그널, `request_*`, `close`), 명령 받아들이기, backend 스레드의 시작·종료만 남았다.

| 모듈 | 객체 | 맡는 일 |
| --- | --- | --- |
| `bridge_types` | 공개 값과 고정 상수 | `BridgeState`·`CommandEvent`·`BackendBindings`·`TaskProjection`·`SessionOptions`·`CODEX_*` 등. Qt를 쓰지 않는다. |
| `bridge_ingress` | `BridgeCommand`, 명령 만드는 함수 6개, `CommandLedger`, `CommandIngress` | `request_*`의 입력 검사(검사 순서·오류 문구 그대로). 명령마다 마지막 단계를 기억하는 장부(재생·ID 충돌 판단, 끝난 명령은 `max_command_ids`개까지). Qt 스레드에서 backend loop로 넘기는 줄별 입구(긴급 → 재생 → control → run 순서, 줄별 한도). |
| `bridge_lanes` | `CommandLanes` | backend loop의 asyncio 큐와 작업자. run은 작업자 1개와 Task별 lock, 긴급 명령은 예약 작업자 2개. |
| `bridge_bindings` | `BindingsOwner`, `usable` | bootstrap 결과를 받아 들고 있다가 시간 제한 안에 한 번 닫는다. 시작이 포기한 뒤에 늦게 성공한 결과도 정리한다. |
| `bridge_streams` | `AnswerStreams` | 실행 중인 답 조각을 모아 0.1초마다 다시 보낸다(A90). |
| `bridge_commands` | `TaskViews`, `AccountCommands`, `CommandExecutor` | Task 화면 갱신과 새 세션 가능 여부, Codex 로그인·서버 접근 변경, 명령 종류별 실행(새 세션, 실행·메시지, 검토, 정지, 승인·거부). |

- `_serve`(133줄·분기 50)를 단계별 메서드로 나눴다: `_start_backend`(bootstrap 확인), `_first_view`(첫 Task 화면), `_end_startup`(시작 실패 코드), `_announce_ready`, `_shut_down`. `_thread_main`의 남은 task 정리는 `_cancel_leftovers`로 뺐다.
- Qt가 필요 없는 서비스는 `bridge_types`를 가져다 쓴다(`codex_composition`·`session_admission`·`session_runtime`). 이제 A 서비스 중 Qt를 쓰는 곳은 `bridge`뿐이다.
- 공개 이름 변화:
  - **이동:**
    - `_review_fields`·`_review_header`는 `bridge_commands.review_fields`·`review_header`가 됐다.
    - `DEVICE_LOGIN_TIMEOUT_S`는 `bridge_commands`로 옮겼다. `bridge`에서 다시 내보내지 않는다. 옛 이름을 바꿔 끼우는 테스트가 아무 효과 없이 통과하지 않게 하려는 것이다.
  - **그대로 내보냄:** `PARTIAL_FLUSH_S`·`MAX_PARTIAL_CHARS`와 `bridge_types`의 이름 전부.
- 정리하면서 함께 바꾼 것. 모두 동작 차이는 없다.
  - 장부를 읽는 곳을 모두 장부 lock 안으로 넣었다. 전에는 backend loop에서 lock 없이 읽는 곳이 3곳 있었다. run 명령 수 세기, Task별 run lock 정리, 종료 때 남은 명령 찾기다. 장부를 쓰는 곳은 backend loop뿐이라 결과는 같다. lock 순서(입구 → 장부, 상태 → 장부)도 그대로다.
  - 시작 단계에서 "종료 요청이면 CLOSED, 아니면 FAILED"를 정하는 분기 3곳을 `_end_startup` 하나로 합쳤다.
  - 첫 대기 결과의 `stop in done`을 `stop.done()`으로 바꿨다. stop task가 끝났다면 종료 요청이 있었다는 뜻이다. 그래서 바로 앞의 `_stop_requested` 검사와 결과가 같다.
  - 입구 drain이 다시 예약하는 `call_soon`을 입구 lock 밖으로 옮겼다. 그동안 `scheduled`가 True로 남아 있어, 다른 스레드가 같은 drain을 또 예약하지 않는다.
- 테스트:
  - 비공개 속성은 새 객체의 메서드로 읽게 바꿨다.
    - `test_bridge`: `_ingress.waiting(...)`·`_ingress.scheduled`·`_bindings.current`·`_ledger.unsettled()`·`_lanes.running()`
    - `test_bridge_conversation`: `_ledger.settled()`
    - `test_bridge_streaming`: `_streams.active()`
    - `test_codex_account`: `bridge_commands.DEVICE_LOGIN_TIMEOUT_S`
    - `test_bridge`·`test_ui_approval_t18`: `review_fields`·`review_header`를 `bridge_commands`에서 가져온다.
  - `_admit`·`_replay_or_conflict`를 바꿔 끼우는 테스트 3개는 그대로 돈다. drain이 매번 `self._admit`을 찾아 부르기 때문이다.
  - A 규칙 테스트(A111): 새 모듈 6개를 A 모듈로 분류했다. 새 규칙 `test_only_the_bridge_facade_brings_qt_into_a_services`도 더했다. ui와 `__main__` 밖의 A 모듈 중 PySide6를 쓰는 곳은 `services.bridge` 하나뿐이어야 하고, `services.bridge`를 가져다 쓰는 곳은 없어야 한다.

## 검증

- **전체 테스트**(Windows, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`)
  - main을 합치기 전(`85808a5`에 A115): 단위 5,221개 통과, 4개 skip, subtest 20개. contract 42개 통과
  - main을 합친 뒤(`232cfe8`에 A115·A136 작업 트리): 단위 5,346개 통과, 4개 skip, subtest 20개, 실패 없음. contract 45개 통과. ruff check·ruff format --check(257개 파일)·mypy(110개 소스 파일) 통과
  - #61·#63을 합친 뒤(`d4e028e`에 A115·A136 작업 트리): 단위 5,429개 통과, 4개 실패, 5개 skip, subtest 20개. contract 45개 통과. ruff check·ruff format --check(263개 파일)·mypy(110개 소스 파일) 통과
    - 늘어난 skip 하나는 #63의 C 테스트(`test_safe_path.py:152`, 심볼릭 링크를 만들 권한 없음)다.
    - 실패 4개는 C 소유 `test_write_loss_counts.py`다(`test_lost_write_response_is_unknown_and_never_resent[http-hang]`·`[http-error500]`, `test_redirect_is_not_followed_and_the_write_is_not_resent[307]`·`[308]`). 모두 `[] == ['SessionCloseFailed']`다. A132로 HTTP 닫기 실패가 사라졌기 때문이고, #61에서 예고한 그대로다. C5.6 기록대로 A132가 main에 들어가면 C가 기대값을 바꾼다. A는 고치지 않았다.
  - #64·#67(`673cf5b`, C2.11 파일 서버)을 합친 뒤(`8e33e3b`): A 규칙 테스트와 새 파일 서버 테스트만 돌렸다(`test_a_architecture.py`·`test_file_server.py`, 241개 통과). 이 상태의 전체 테스트·정적 검사는 돌리지 않았다.
- **새 A 규칙 테스트**는 일부러 어겨서 확인했다. `session_runtime`이 `services.bridge`를 다시 가져오게 하거나 `bridge_streams`가 PySide6를 가져오게 하면 이 테스트가 실패한다.
- **회귀를 일부러 32개 넣었다**(main을 합치기 전 A115 트리, bridge 관련 테스트 8개 파일, `-x`, 420초 제한).
  - **테스트가 실패해서 잡은 것(20개):** 입구 꺼내는 순서·줄 한도·재생 줄·같은 ID 충돌, 메시지 추론 수준 검사, run 명령 수 한도, 재생 실패가 원래 처리 기록을 덮음, 긴급 줄 예약 작업자, 같은 Task의 run lock, 종료 때 남은 명령 `SHUTDOWN_UNCONFIRMED`, 닫은 뒤 바인딩을 계속 들고 있음, 시작 실패 코드가 정리 결과를 무시함, 다른 epoch의 조각·메시지, 끝난 Task의 Runner 닫기, 막힘 코드 없을 때 새 세션 막음, 실행·승인 뒤 Task 화면 다시 읽기, 대기열 비우기 실패 때 정지 명령, Guard 정지 뒤 Codex 세션 닫기
  - **테스트가 멈춰서 드러난 것(1개):** 첫 Task 화면이 실패해도 시작을 계속하면(M20) 테스트가 끝나지 않는다. 실패 메시지 없이 시간 초과로만 드러난다. A115 전 코드에 같은 회귀를 넣어도 똑같이 멈췄다(420초 제한).
  - **못 잡은 것(11개):** 아래 표다. A115 전 코드(`85808a5`)에 같은 회귀를 넣어도 11개 모두 못 잡았다(같은 테스트 254개 통과). 이번 분리 때문이 아니라 원래 있던 테스트 빈틈이다.

| ID | 못 잡은 회귀 |
| --- | --- |
| M05 | 닫힌 입구가 새 명령을 받음 |
| M06 | 장부가 미확인 명령을 기억하지 않음 |
| M07 | 장부가 끝난 명령을 한도 없이 쌓음 |
| M09 | API 키가 아닌 계정 명령에도 비밀값을 실음 |
| M14 | 종료 때 대기 중인 명령을 `SHUTTING_DOWN`으로 거부하지 않음(뒤의 `SHUTDOWN_UNCONFIRMED` 정리가 대신 정리한다) |
| M16 | 종료 때 기기 로그인 대기를 취소하지 않음 |
| M18 | 늦게 끝난 bootstrap을 정리 확인 없이 완료로 봄 |
| M19 | bootstrap 결과에 `HumanControl`이 아닌 값을 받아들임 |
| M22 | 스트리밍 답 크기 한도를 지움 |
| M24 | 종료 요청 뒤에도 답 조각을 내보냄 |
| M30 | 로그아웃 뒤 새 세션 가능 여부를 다시 알리지 않음 |

- **측정:** 방법은 A111·A114와 같다. 뒤 값은 main을 합친 트리에서 A136 변경을 뺀 것이다. main 병합은 A 모듈을 바꾸지 않았다. [근거](../evidence/a115-backend-bridge-split-2026-10-28.json)

| 항목 | A115 전(`85808a5`) | A115 |
| --- | --- | --- |
| `BackendBridge` | 1,160줄·메서드 49개 | 518줄·메서드 36개 |
| `services.bridge` 모듈 | 1,500줄 | 663줄 |
| bridge의 기준 초과 함수 | 7개: `_serve` 133줄·분기 50, `_execute` 126줄·분기 37, `__init__` 86줄·분기 14, `_drain_ingress` 58줄·분기 15, `_thread_main` 분기 17, `_admit` 분기 14, `_submit` 분기 16 | 1개: `__init__` 65줄(분기 5). 새 모듈 6개에는 기준 초과 함수·클래스가 없다 |
| A 전체 기준 초과 함수 | 51개 | 45개 |
| A 모듈·줄 | 56개·17,120줄 | 62개·17,538줄 |
| 테스트의 비공개 속성 접근 | 274곳 | 274곳 |

## 남은 것

- **`BackendBridge` 크기:** 518줄·메서드 36개로, 클래스 기준(300줄·20개)을 아직 넘는다.
  - 공개 API가 14개다(속성 5, `start`·`close`·`wait_done`, `request_*` 6).
  - 나머지는 명령 받아들이기 9개와 backend 수명 9개다. 둘 다 `_set_state`와 같은 상태 lock을 쓴다. 명령은 READY인지 그 lock 안에서 보고 큐에 넣고, 시작·종료는 그 lock으로 상태를 바꾼다. 더 나누려면 상태 기계를 따로 떼야 해서 이번에는 하지 않았다.
- **`__init__`:** 65줄로 줄 기준(50)을 넘는다. 분기 5개인 협력 객체 연결 목록이라 나누지 않았다.
- **테스트 빈틈 11개와 멈춤 1개:** 위 표다. A114 뒤의 A119처럼 A137로 따로 메운다.
- **테스트의 비공개 속성 접근:** 줄이지 않았다. 위 테스트들은 여전히 bridge 안쪽 객체를 직접 본다. A111의 세는 방법은 다시 만들지 못했다. 그래서 직접 정한 규칙으로 전후를 셌고, 274곳 → 274곳이다. 규칙은 A 테스트 파일(A115 전 54개, 뒤 55개. 늘어난 하나는 main에서 온 `test_scan_service`)에서 `self`가 아닌 객체의 `_`로 시작하는 속성(`__` 제외)을 세는 것이다.
- **C 리뷰:** 이 단계는 C 리뷰 대상이다. 아직 요청하지 않았다.
- 실제 codex.exe로는 다시 돌리지 않았다.

관련: I6·I9·I11, 개발문서 10.4, A52·A89·A90·A110. PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
