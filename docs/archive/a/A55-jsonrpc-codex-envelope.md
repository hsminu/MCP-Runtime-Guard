# A55 · JSON-RPC 클라이언트를 Codex 0.159 형식에 대응, 알림 처리기 실패를 따로 구분

A50(JSON-RPC 클라이언트 뼈대)을 다시 손본 작업이다. A50 기록은 고치지 않고 이 기록을 새로 쓴다.

## 한 일

- 로컬에 설치된 Codex 0.159.0으로 `codex app-server generate-json-schema --out <임시 폴더>`를 실행해 프로토콜 스키마 39개 파일을 만들었다. 저장소에는 넣지 않았다.
  - 요청·응답·알림·오류 형식(`JSONRPCRequest`·`JSONRPCResponse`·`JSONRPCNotification`·`JSONRPCError`)과 전체 스키마 어디에도 `"jsonrpc"` 필드가 없다.
  - A50은 받는 메시지에 `"jsonrpc": "2.0"`이 있어야 한다고 가정했다. 그대로 두면 Codex 메시지가 모두 형식 오류로 거부되는 결함이었다.
  - `RequestId`는 문자열 또는 int64 정수다.
  - 참고로 확인한 내용:
    - 서버가 보내는 요청 메서드에 `item/tool/call`, `item/tool/requestUserInput`, 각종 승인 요청이 있다.
    - `DynamicToolCallParams`의 필수 필드는 `arguments`·`callId`·`threadId`·`tool`·`turnId`이고 `namespace`는 선택이다.
    - `DynamicToolCallResponse`의 필수 필드는 `contentItems`·`success`다.
    - 이 내용은 contracts 2026-10-15·10-17 하네스 결정과 어긋나지 않는다.
- `JsonRpcClient`에 `version_field` 옵션(기본 `True`)을 추가했다.
  - `False`이면 보내는 메시지에 `"jsonrpc"` 필드를 넣지 않는다.
  - 받는 메시지에 이 필드가 있으면 형식 오류로 닫는다. 스키마와 다른 메시지가 오면 하네스가 바뀐 것으로 보고 fail-closed로 처리한다.
  - 기본값은 표준 JSON-RPC 2.0 그대로다.
- 알림 처리기에서 예외가 나면 `CHANNEL_FAILED`가 아니라 `HANDLER_FAILED`로 닫히게 했다. 원인 구분용이다. 넓은 except를 쓰지 않고, 처리기를 부르는 동안 표시해 두는 방식이다.

## 실제 검증

- `tests/unit/test_jsonrpc_client.py` 27개 통과(A50의 25개에 2개 추가).
  - Codex 형식: 보낼 때 `"jsonrpc"`가 없고, 이 필드가 없는 응답·알림을 받으며, 이 필드가 섞인 메시지는 `PROTOCOL_ERROR`로 닫는다. 잘못된 옵션 값은 거부한다.
  - 알림 처리기 예외는 `HANDLER_FAILED`이고 원문을 노출하지 않는다. 채널 수신 오류는 `CHANNEL_FAILED`다.
  - 처리기 실패 구분 테스트는 수정 전 코드에서 실패함을 확인했다.
- `PytestUnraisableExceptionWarning`을 오류로 올려 돌려도 통과했다.
- `ruff check`·`ruff format --check`·`mypy` 통과. 전체 `pytest -q` 결과는 이 기록과 같은 커밋 메시지에 남긴다.

## 남은 한계

- 고정 버전이 아니다. 기준 버전(#21 1번, C 실험 기록은 0.162.1)에서 다시 확인해야 한다.
- 메시지 구분 방식(줄 단위 등)은 스키마에 없다. 실제 app-server를 띄워 확인하지 않았다. 사용자 `~/.codex` 설정과 로그 DB를 건드릴 수 있어서다(C-CODEX-01).
- 우리가 보내는 메시지에 모르는 필드가 있을 때 Codex가 받아주는지는 확인하지 않았다. 그래서 `version_field=False`에서는 아예 보내지 않는다.

## 관련

- I6(형식 불명은 닫기), I11 기반, contracts 2026-10-15 하네스 결정, GitHub #21 1번.
- 커밋: 이 기록과 같은 커밋. PR 없음.
