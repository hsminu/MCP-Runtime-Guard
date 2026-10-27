# A114 · 리팩터링 3단계: Codex 계층 정리

2026-10-27. 동작은 바꾸지 않았다. 대상은 Codex 세션 관리자와 그 아래 JSON-RPC·Runner다. `codex_stream`·`guard_relay`·`task_tool_calls`는 이미 기준 안이라 손대지 않았다. B·C와 맞물리는 곳(`CodexPipes`, `TaskResources`, Guard relay, `CodexSession` 프로토콜)은 그대로다.

## 한 일

| 대상 | 바꾼 것 |
| --- | --- |
| 새 `services/codex_turns.py`의 `TurnAnswers` | 세션이 들고 있던 turn별 사전 8개를 한 객체로 모았다. 모은 것: 끝난 메시지, final_answer, 스트리밍 조각, 조각 사이 줄바꿈, 이미 보낸 조각 수, 답 글자 수, turn 종료 상태. 답 크기 한도(`MAX_ANSWER_CHARS`)와 turn 종료 결과(인증 실패·사용 한도·중단·실패 코드)도 여기서 정한다. |
| `CodexAppServerSession` (`services/codex_session.py`) | `_notification`은 종류별로 나누고, 항목 이벤트의 L3 검사는 `_item_event`, rate limit 판정은 `_rate_limit_reached`로 뺐다. `start`·`_send`의 결과 ID 검사는 `_result_id` 하나로 합쳤다. 한 줄짜리 `_wake`는 없앴다. |
| `CodexTaskRunner.advance` (`services/runner.py`) | 보내기와 응답 검사를 `_send`로 뺐다. 인증 실패와 세션 오류의 `except` 두 개는 하나로 합쳤다. `CodexAuthenticationError`는 `CodexSessionError("CODEX_AUTH_FAILED")`라 실패 코드가 같다. |
| `JsonRpcClient._dispatch` (`adapters/jsonrpc_client.py`) | 서버 요청 처리를 `_dispatch_request`로 뺐다. |

- 확인·거부 순서, 고정 실패 코드, 정지 시점(답장을 보낸 뒤 정지)은 그대로다.
- 바꾼 테스트는 두 곳뿐이다. `test_codex_session`은 `MAX_ANSWER_CHARS`를 `codex_turns`에서 가져오고, A 규칙 테스트는 새 모듈을 A 모듈로 분류한다.

## 검증

- 전체 테스트 4,656개가 통과했다(skip 1). Ruff, format, mypy(strict)도 통과했다.
- 회귀 18개를 일부러 넣었다.
  - **테스트가 실패해서 잡은 것(13개):** 스트리밍 답 크기 한도, 조각 사이 줄바꿈, final_answer 우선, 인증 실패·사용 한도 매핑, L3 항목 검사, rate limit, remote control 상태, 스트리밍 초과 시 정지, Runner 응답 크기, 실패 코드 유지, 진행 중인 서버 요청 ID 재사용, 서버 요청 수 한도
  - **테스트가 멈춰서 드러난 것(1개):** turn 종료가 `send()`를 깨우지 않으면 테스트가 끝나지 않는다. CI에서는 시간 초과로 드러나지만 실패 메시지가 없다.
  - **못 잡은 것(4개):** 다른 turn의 조각을 보여 줌, 끝난 답의 크기 한도, 중단된 turn을 `CODEX_TURN_FAILED`로 보고함, 빈 turn ID를 받아들임
- 못 잡은 4개와 멈춘 1개는 A114 전 코드에 같은 회귀를 넣어도 같았다. 이번 변경 때문이 아니라 원래 있던 테스트 빈틈이다.
- 측정 방법은 A111과 같다. [근거](../evidence/a114-codex-layer-2026-10-27.json)

| 항목 | A113·A118 뒤 | A114 |
| --- | --- | --- |
| `CodexAppServerSession` | 340줄·메서드 22개 | 278줄·메서드 20개(클래스 기준 안) |
| Codex 계층의 기준 초과 함수 | 5개: `_notification` 분기 28, `_send` 13, `_dispatch` 14, `advance` 14, 세션 `__init__` 62줄 | 1개: 세션 `__init__` 53줄(분기 2) |
| A 전체 기준 초과 함수 | 50개 | 46개 |
| A 모듈 | 53개 | 54개 |

## 남은 것

- **세션 `__init__`:** 53줄이라 줄 기준(50)을 조금 넘는다. 분기 없는 초기화 목록이라 나누지 않았다.
- **쓰이지 않는 속성:** `CodexAppServerSession._task`는 대입만 되고 읽히지 않는다. 원래 있던 것이라 지우지 않았다.
- **테스트 빈틈 4개와 멈춤 1개:** 위에 적은 것들이다. 멈춤 1개는 시간 제한이 있는 테스트로 바꾸면 실패로 드러난다.

관련: I6·I9·I11, 개발문서 10.4, A90. PR은 아직 없다(A 브랜치 작업).
