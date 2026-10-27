# A100 · 대조 뒤 `tools/list_changed`를 받으면 그 서버를 이번 실행 동안 PENDING 취급(LIST-01)

2026-10-26. LIST-01(#30 2안)의 A 구현이다. 결정 문구는 PR #34의 `docs/contracts.md`와 개발문서 10.1절을 따랐다. PR #34는 아직 main에 병합되지 않았다.

## 한 일

- `adapters/mcp_transport.py`
  - SDK 세션의 `message_handler`는 `notifications/tools/list_changed`일 때만 콜백을 부른다. params는 넘기지 않는다. 다른 서버 알림은 전처럼 버린다.
  - `McpTransport(on_tools_changed=...)`는 서버 ID만 받는다. callable이 아니면 `TypeError`다.
  - 쓰지 않게 된 `_discard_message`를 지웠다.
- `services/product_assembly.py`
  - `LiveCheckedRegistry.tools_changed(server_id)`
    - 대조(`seal`) 전에 온 알림은 차단하지 않고 건수(`early_list_changes`)만 센다.
    - 대조 뒤에 온 알림은 그 서버를 이번 실행 동안 PENDING으로 돌려준다(`status`). `blocked_servers`에 포함되고 `listing_changed`로 확인한다.
  - 지문은 live 값을 그대로 둔다. 그래서 Guard 사유는 기존 `REG_NOT_ACTIVE` 하나다. 새 Reason은 없다.
  - C의 Registry에는 쓰지 않는다. 다음 연결 때 다시 대조한다.
  - `assemble_guard`가 transport 콜백을 이 Registry 보기에 연결한다.
  - `AssembledGuard.offers_tool`·`offered_tool_views`: 바뀐 서버의 도구를 뺀 목록이다.
- `services/codex_composition.py`: Codex 세션마다 `offered_tool_views()`로 `dynamicTools`와 중계 허용 목록을 만든다. 전에는 조립 때 한 번 만들었다.
- `services/session_runtime.py`: Task 화면의 `tools_available`을 snapshot마다 계산해서 바뀐 서버 도구를 뺀다.

결과는 다음과 같다.

- 새 호출: Catalog 해석에서 `REG_NOT_ACTIVE`로 DENY된다. dispatch는 0이다. `failure_audit`에 `before_dispatch`·`REG_NOT_ACTIVE`가 남고 Task는 RUNNING을 유지한다.
- 대기 승인: 사람이 승인하면 재평가에서 `REG_NOT_ACTIVE`로 INVALIDATED된다. 그 Task는 HALTED가 되고 SEND는 보내지 않는다.

## 검증

- `test_mcp_ingress.py`
  - 실제 SDK 세션에서 resources·prompts 목록 변경 알림은 콜백에 닿지 않는다. tools 알림 두 번만 인자 없이 닿는다.
  - callable이 아닌 콜백은 거부한다.
- `test_product_assembly.py`
  - 대조 뒤 알림: 새 READ는 `REG_NOT_ACTIVE`로 DENY된다.
    - MCP `tools/call`은 1회 그대로다.
    - 감사 1건이 남고 Task는 RUNNING이다.
    - 지문 불일치 없음, `offered_tool_views()`는 빈 목록이다.
    - C Registry 상태는 ACTIVE를 유지한다.
    - 알림 params의 문자열은 로그(DEBUG 수준 caplog)에 없다.
  - 대조 전 알림(`tools/list` 응답 앞)은 차단하지 않는다. `early_list_changes`는 1이다.
  - 대기 SEND 승인: 승인하면 `Denied(REG_NOT_ACTIVE)`, 승인 기록 INVALIDATED, Task HALTED, SEND 0회다.
- `test_codex_product_flow.py`
  - 알림 전에 시작한 Codex 세션은 `read`를 계속 나열한다. 호출은 실패 응답이고 MCP 호출은 0회다.
  - 그 Task의 `tools_available`은 False로 바뀐다.
  - 알림 뒤 새 세션의 `dynamicTools`는 빈 목록이다.
- 수정 전 소스(HEAD `fca4506`)로 돌리면 새 테스트 6개가 모두 실패했다. 대조 전 알림 테스트는 동작(차단 안 함)이 전과 같고, 새 속성이 없어서 실패한 것이다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15)
  - 단위 4,208개 통과·1개 생략, contract 41개 통과
  - Ruff·포맷·mypy(82개 파일) 통과
  - 경고 5개는 기존 AGT 패키지 사용 중단 경고(`test_agt_real.py`)다.

## 남은 한계

- Codex는 실행 중인 thread의 `dynamicTools`를 바꾸지 못한다. 알림 전에 시작한 세션은 그 도구를 계속 나열하고, 호출만 거부된다.
- 화면에는 "도구 목록이 바뀌어 이번 실행 동안 차단" 표시가 없다. 지금은 `tools_available`과 호출 실패로만 드러난다.
- "고정 코드만 남긴다"는 알림 내용을 전송 계층 밖으로 내보내지 않고 서버 ID라는 사실만 남기는 것으로 구현했다. 알림 수신 자체를 따로 기록하는 영속 감사 이벤트는 없다. 거부된 호출에는 `REG_NOT_ACTIVE` 감사가 남는다.
- 대조 기준은 결정 문구대로 "모든 서버의 연결 직후 대조(`seal`)"다. 서버가 여럿이면, 한 서버의 `tools/list`를 받은 뒤 다른 서버 목록을 기다리는 동안 그 서버가 보낸 알림도 "대조 전"으로 세어 차단하지 않는다. 이 경우에는 결정의 전제("그 대조가 최신 목록을 본다")가 맞지 않는다. 서버별로 목록 수신 이후 알림을 차단할지는 사용자 결정이 필요하다. C5.9 관찰에서 연결 직후 알림의 시점을 함께 보면 판단 근거가 된다.
- 실제 MCP 서버의 알림 시점은 관찰하지 않았다(C5.9).

관련: I10, T10, LIST-01, #30, PR #34. PR은 아직 없다(A 브랜치 작업).
