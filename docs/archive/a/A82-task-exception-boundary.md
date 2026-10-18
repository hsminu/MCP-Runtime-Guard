# A82 · `Task.exception()` 고정 코드 변환의 경계 해석

2026-10-18. A79에서 보류한 "넓은 예외 경계의 사실상 사용"을 사용자 결정으로 정리했다. 코드는 바꾸지 않았다.

## 결정

사용자 결정(2026-10-18): 완료 콜백이나 대기 함수에서 `Task.exception()`으로 예외를 꺼내 고정 코드로 바꾸는 패턴을 허용한다. `except Exception`·`BaseException`·bare except를 새로 쓰는 것과는 다른 패턴으로 본다.

- **목적.** 주입된 콜백이나 채널이 낸 예외 원문이 asyncio 기본 예외 처리기(로그)로 나가지 않게 막는다(I9). 결과는 항상 실패 쪽(fail-closed) 고정 코드다.
- **범위.** 이 패턴을 쓰는 A 위치는 다음과 같다.
  - `services/session_runtime.py`의 `_prepared`·`_bounded_cleanup`·`_consume_cleanup`
  - `services/task_tool_calls.py`의 완료 콜백
  - `adapters/jsonrpc_client.py`의 `_server_task_done`·`_reader_done`
  - `adapters/codex_stream.py`의 완료 콜백
  - 같은 패턴이 A 소유 `services/bridge.py`·`adapters/mcp_transport.py`와 B 소유 `services/guard.py`에도 있다.
- **조건.** 꺼낸 예외의 원문·타입명·인자를 로그·화면·모델에 싣지 않는다. 취소(`CancelledError`)는 삼키지 않는다. 새 위치에 쓸 때는 리뷰에서 이 기록을 근거로 든다.

## 남은 일

AGENTS.md의 "넓은 예외 처리는 세 경계로 제한"과 개발문서 13.5절에 이 해석을 반영하는 일은 공통 문서라 사용자가 한다. A 범위에서는 이 기록과 A→main PR 본문으로 근거를 남긴다.

관련: I6·I8·I9, A79.
