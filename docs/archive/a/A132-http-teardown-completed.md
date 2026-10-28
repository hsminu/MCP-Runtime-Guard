# A132 · HTTP 세션이 요청 오류로 끝나면 종료를 완료로 봄(#61 A 후속 2)

2026-10-28. [#61](https://github.com/hsminu/MCP-Runtime-Guard/pull/61) A 리뷰에서 찾은 것이다. HTTP MCP 서버가 `tools/call`에 500이나 307·308·401로 답하면 SDK가 세션을 끝낸다. 그 뒤 앱을 닫으면 worker가 `SessionCloseFailed`(종료 미확인)를 보고했다. 실제로는 정리가 끝난 상태였다.

## 원인

- mcp 1.29.0 `streamable_http_client`는 POST마다 자기 task group 안에서 `raise_for_status()`를 부른다.
- 상태 오류가 나면 group이 세션 본문을 취소한다. 모든 task가 끝난 뒤 `ExceptionGroup[HTTPStatusError]`를 다시 던진다.
- 그때는 SDK의 `finally`가 세션 DELETE를 이미 보냈다. httpx 클라이언트는 A가 SDK context 밖에서 닫는다.
- worker의 `_SessionExitTracker`는 예외로 끝난 context 종료를 모두 미확인으로 봤다.

## 한 일

- `streams.http_teardown_completed(error)`를 더했다. 예외 group 안이 `httpx.HTTPError`뿐이면 True다. 다른 예외가 하나라도 섞이거나 group이 아니면 False다.
- `_SessionExitTracker.__aexit__`가 그런 group을 받으면 종료를 완료로 기록하고 예외를 올리지 않는다.
  - 소유 task 자체가 취소되는 중(`cancelling()`)이면 그대로 올린다. 바깥에서 온 취소를 삼키지 않기 위해서다.
- worker 상태는 그대로 FAILED다. 이번 실행 동안 그 서버의 다음 호출은 `WorkerClosed`로 거부된다. 결과 불명 쓰기는 재시도하지 않는다.
- 404는 대상이 아니다. SDK가 기다리던 요청에 "session terminated"로 답하고 세션은 계속 산다.
- 수정 범위는 `adapters/mcp_transport/`(`streams.py`, `worker.py`)뿐이다.

## 검증

- `tests/unit/test_mcp_http.py`(실제 SDK, loopback 서버)
  - 307·308·401·500 각각: 호출 실패, worker FAILED, 두 번째 호출 `WorkerClosed`, `close()` 오류 없음.
  - dispatch 계수와 wire 계수가 모두 1이다. 리다이렉트 대상 서버에는 요청이 가지 않는다.
  - HTTP 오류가 아닌 예외로 끝난 세션은 여전히 `SessionCloseFailed`다. 가짜 httpx transport가 `RuntimeError`를 내는 경우로 확인했고, 예외 원문은 로그에 없다.
  - 분류 단위 테스트 7개
    - True: 상태 오류만, 상태 오류와 읽기 오류, 중첩 group 안의 읽기 오류
    - False: `OSError`가 섞인 group, `CancelledError`가 섞인 group, group이 아닌 상태 오류, `OSError`
  - 소유 task가 취소되는 중이면 HTTP 오류 group이어도 종료 미확인으로 남는다.
- 수정 전후 재현 스크립트(500·307): 수정 전에는 `close()`가 `SessionCloseFailed`였고, 수정 뒤에는 오류 없이 닫힌다. 두 경우 모두 worker는 FAILED이고 두 번째 호출은 `WorkerClosed`다.
- #61 head `bf828db`의 계수 테스트 3개 파일(58개)을 이 작업 트리(A133·A135 포함)에 얹어 임시 worktree에서 돌렸다. 54개 통과, 4개 실패다.
  - 실패는 모두 닫기 실패 기대값 줄 하나다. HTTP `hang-`·`error500-`과 리다이렉트 307·308에서 `close_errors`가 `["POST_DISPATCH_ERROR"]`가 아니라 `[]`다. 이 수정이 의도한 변화다.
  - 같은 테스트의 다른 단언(결과 불명 잠금, 재시도·재연결 거부, dispatch·wire·effect 계수)은 모두 그대로 통과한다.
- 전체 결과는 A08·A133·A134와 같은 실행이다(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`).
  - 단위 5,202개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(238개 파일)·mypy(102개 소스 파일) 통과

## 남은 것

- C의 인수 테스트(#61, C5.4·C5.6)에서 HTTP 쪽 `CLOSE_ERRORS` 기대값을 이 동작에 맞게 바꿔야 한다. 위 4개 경우의 결과와 함께 #61에 요청했다([댓글](https://github.com/hsminu/MCP-Runtime-Guard/pull/61#issuecomment-6470235928)). C 소유 테스트라 A는 고치지 않는다.
- 독립 인수(C)에서 실제 HTTP 서버로 같은 경우를 다시 본다.
- PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
