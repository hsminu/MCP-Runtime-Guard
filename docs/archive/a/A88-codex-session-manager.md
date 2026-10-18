# A88 · Codex 세션 관리자 A 계층: L3·Guard 중계·FIFO·감시 상한·제품 연결

2026-10-18. A19·A20·A21·A22·A50·A68·A80 중 B·C 없이 할 수 있는 부분을 구현했다. 실제 codex.exe와 C launcher는 실행하지 않았다.

## 한 일

- **`services/codex_session.py`: `CodexAppServerSession`.** 기존 `CodexSession` 계약(send·interrupt·aclose)을 지킨다. A55·A58·A67·A73의 JSON-RPC·프레이밍과 A68 FIFO를 재사용한다.
  - **start.** `initialize` → `initialized` → `thread/start` 순서다. thread에는 Guard 도구만 등록한다(I11).
  - **send.** `turn/start` → `turn/completed`까지 기다리고 agentMessage를 모아 돌려준다.
    - 최종 텍스트는 `item/completed`의 agentMessage에서 가져온다. 없으면 delta를 합친다.
    - 65,536자를 넘으면 정지한다.
    - turn 응답보다 먼저 온 delta는 turn ID를 알게 된 뒤 순서대로 흘려보낸다.
  - **L3.**
    - 허용 item은 `userMessage`·`agentMessage`·`reasoning`·`contextCompaction`·`dynamicToolCall`뿐이다. 다른 item이 오면 `CODEX_L3_VIOLATION`으로 정지한다.
    - `commandExecution`·`fileChange` 승인 요청에는 `decline`으로 답한 뒤 정지한다. 그 밖의 서버 요청은 바로 정지한다.
    - `requestUserInput`에는 `{answers:{}}`로 답하고 건수만 센다.
  - **인증·한도.** `unauthorized`면 기존 인증 실패 경로로 그 Task만 정지한다. `usageLimitExceeded`·`rateLimitExceeded`와 `rateLimitReachedType` 알림은 `CODEX_USAGE_LIMIT`로 정지한다. 오류 원문은 싣지 않는다.
  - **도구 호출.**
    - Task별 FIFO로 하나씩 처리한다. 한 turn에 호출이 두 개 와도 순서대로다.
    - 감시 상한 = `approval_ttl_seconds` + MCP 호출 상한 + 30초(A80). 넘으면 "작업이 정지되었습니다"로 답하고 `CODEX_WATCHDOG_TIMEOUT`으로 정지한다. 재전송은 없다(I8).
  - **답한 뒤 정지.** 도구 호출을 처리하다 정지를 결정해도, 답이 실제로 나간 뒤에 정지한다. 이를 위해 `JsonRpcClient`에 `on_reply_sent` 훅을 추가했다.
  - **연결 종료.** stdout이 닫히면 기다리던 `send`가 멈춰 있지 않고 실패한다. 이를 위해 `JsonRpcClient.wait_closed`를 추가했다.
- **`services/guard_relay.py`.**
  - `namespace__tool`과 `callId`로 `ProposedCall`을 만든다. 도메인 모델로 검증한다.
  - Task·epoch는 세션 관리자가 들고 있는 값만 쓴다(I7). namespace가 없으면 Guard를 부르지 않는다.
  - 답 문구는 B `core/model_messages`의 고정 문구표를 쓴다. Withheld·OutcomeUnknown이면 정지 문구로 답한 뒤 정지한다.
  - **Pending.** A 내부 `ApprovalOutcomes`로 기다린다. 사람이 승인·거절하면 bridge가 그 결과를 넘겨 준다(`BackendBindings.approval_resolved`). NOTIFY-01 전까지 쓰는 임시 연결이다. 만료는 Guard 타이머가 결정할 일이라 A는 `expire`를 부르지 않고, 응답이 오지 않으면 감시 상한에서 끝난다.
- **`services/codex_composition.py`·`bootstrap_product`.**
  - Task마다 C launcher가 넘긴 `TaskResources.handle`(read·write·drain)로 세션을 만든다. 형식이 다르면 시작을 거부한다.
  - 시작이 실패하면 세션을 닫고 `CODEX_START_FAILED`를 낸다.
  - 도구가 있으면 `tools_available`를 켠다.
- **bridge.** Guard가 붙은 제품 경로에서 취소·긴급 차단을 하면 Guard Task 정지 뒤에 모델 세션도 interrupt하고 닫는다(`stop_session`). 이전에는 Guard만 멈추고 Codex 세션은 남았다.
- **Runner.** `CodexSessionError`에 고정 코드를 실었다. 화면에는 정지 사유별 안내가 나온다: L3, 한도, 도구 정지, 감시 상한 등.
- **`services/codex_prompt.py`.** `baseInstructions` 문구를 넣었다(A90). 보안 장치는 아니다(I7).

## 검증

- **`test_codex_session.py` 28개(가짜 app-server).** 도구 등록 범위, 답변·스트리밍, Released·거부 공개 범위, Withheld·결과 불명 정지, Pending 승인·거절, FIFO, L3 4종, 승인 요청 거절, 알 수 없는 요청, `requestUserInput`, 인증·한도 4종, 감시 상한(재전송 0), 끼워 넣은 필드, stdout 종료를 다룬다.
- **`test_codex_product_flow.py` 4개.** 제품 bootstrap → Codex 세션 → 실제 GuardService → MCP 경로를 검증했다.
  - 허용 호출은 수신 1회 후 답변이 돌아온다.
  - 거부 호출은 수신 0회다.
  - L3 위반이면 그 Task가 HALTED가 되고 interrupt 1회가 나간다.
  - 지문이 다르면 도구가 0개로 등록된다.
- **`test_bridge_conversation.py` 4개.** 승인 결과 전달, 취소·긴급 차단 시 세션 정지.
- 전체 단위 테스트 **3,399개 통과, 1 skip, subtest 20**. ruff·format·mypy 79소스 통과.

## 남은 것

- **C 대기.** C launcher(C2.12)·L2(C6.14)가 있어야 실제 codex.exe로 연결할 수 있다.
- **B 대기.** NOTIFY-01이 나오면 `ApprovalOutcomes`를 교체한다. STOP-01이 나오면 정지 경로를 교체한다.
- **스트리밍 화면 연결.** 답변 스트리밍(delta)은 세션에서 `on_partial`로 받지만, 대화 화면 연결은 하지 않았다. 부분 메시지를 바꿔 끼우는 UI 이벤트가 필요하다.

관련: I1·I6·I7·I8·I9·I11, T21·T22(L3 부분)·T39–T45의 A 부분. C 독립 인수가 아니다.
