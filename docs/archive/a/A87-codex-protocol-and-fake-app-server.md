# A87 · Codex 0.162.1 프로토콜 순수 함수와 대본형 가짜 app-server

2026-10-18. 세션 관리자(A88)의 기반이다. 입출력 없는 프로토콜 모듈과 테스트용 가짜 app-server를 만들었다.

## 근거

공식 `openai/codex` `rust-v0.162.1` commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`의 app-server-protocol v2 소스와 `thread_processor.rs`를 별도 조사 에이전트가 원문으로 확인했다. 확인하지 못한 항목은 미확인으로 남겼다. 실제 codex.exe는 실행하지 않았다.

## 한 일

- **`adapters/codex_protocol.py`**
  - 요청 생성: `initialize`(`capabilities.experimentalApi=true`, 없으면 experimental 필드 거부), `thread/start`, `turn/start`, `turn/interrupt`.
    - `thread/start`는 `model`, `approvalsReviewer:"user"`, `ephemeral:true`, `environments:[]`, `baseInstructions`, namespace 형식 `dynamicTools`를 보낸다.
    - `turn/start`의 text는 1,048,576자까지다.
  - **`dynamicTools` 이름 규칙.** 이름은 `^[A-Za-z0-9_-]+$`이고, 도구는 128자, namespace는 64자까지다. `mcp`·`mcp__*`와 예약 namespace 14개는 쓸 수 없다.
    - contracts 10-17의 "도구 이름 제한 없음"과 충돌한다. 그래서 규칙을 어기는 도구는 모델 목록에서 빼고 `excluded`로 보고한다. `thread/start` 자체가 실패하는 것을 막기 위해서다. B·C에는 A92에서 알린다.
  - **`item/tool/call` 엄격 파싱.** 허용 필드는 `threadId`·`turnId`·`callId`·`namespace?`·`tool`·`arguments`뿐이다. 모델이 끼워 넣은 `task_id`·`label` 같은 필드는 거부한다(I7).
  - **응답 생성.** `{contentItems:[inputText…], success}`를 만든다. Released는 text 뒤에 `structured_content`를 JSON 문자열로 붙인다(사용자 결정).
- **`tests/unit/fake_codex_app_server.py`**
  - LF JSON-RPC를 쓰고, `jsonrpc` 필드는 없고, 알림에 `emittedAtMs`가 붙는다.
  - turn마다 대본이 동작한다: 답변 delta·완료, 도구 호출, 승인 요청, `requestUserInput`, 금지 item, 오류, interrupt, `account/*`.
  - 받은 모든 메시지를 기록해 계수로 검증한다.
  - C의 L2 가짜 모델 서버를 대신하는 것은 아니다.

## 검증

`test_codex_protocol.py` 26개: 이름·길이·예약어 경계, namespace 묶기와 중복, 엄격 파싱, Released 변환, turn 길이 상한.

## 미확인

- `functionCallOutput` item이 실제로 나오는 조건은 확인하지 못했다. 그래서 L3 허용 목록에서 빼 두었고, 나오면 위반으로 처리된다.
- `item/permissions/requestApproval`·`mcpServer/elicitation/request`의 필드도 확인하지 못했다. 둘 다 위반으로 처리한다.

관련: I7·I11, T22(L3 부분).
