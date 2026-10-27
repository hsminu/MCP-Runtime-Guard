# A107 · 실제 codex.exe 0.162.1로 Guard 도구 호출 turn 스파이크(가짜 모델)

2026-10-27. 실제 codex.exe가 A 세션 관리자와 Guard를 거쳐 MCP까지 가는 turn을 처음으로 끝까지 돌렸다. 모델은 가짜 서버로 대신했다. [근거](../evidence/a107-codex-real-exe-fake-model-2026-10-27.json)

## 방법

- `tools/install_codex.py`로 고정 codex.exe 0.162.1을 설치했다. SHA-256이 고정값과 같다.
- 실제로 돈 것: `bootstrap_product`부터 아래 전부다.
  - SessionRuntime, `CodexAppServerSession`, `GuardToolRelay`, GuardService, SQLiteStore, McpTransport
  - `CodexProcessPreparation`: C2.13 앱 폴더(임시 루트), C2.12 launcher(실행마다 해시 대조, Job Object), `write_codex_home`
- 대역으로 둔 것
  - 모델: 127.0.0.1의 `/v1/responses` SSE 서버. Codex 0.162.1 Python SDK 테스트 하네스와 같은 이벤트 형식이다. 정해진 순서로 함수 호출과 답을 낸다.
  - MCP 서버(단위 테스트의 메모리 Peer), Registry, Profile 문서
- 스파이크에만 둔 우회 두 개(제품 코드에는 넣지 않았다)
  - L2(C6.14)가 없어서, 같은 구성으로 L2를 통과했다는 영수증을 직접 넣었다.
  - codex.exe에 가짜 모델용 `-c model_provider…` 플래그를 더 넘겼다.
- 스크립트는 저장소에 넣지 않았다.

## 결과

| 시나리오 | 결과 |
| --- | --- |
| PUBLIC 읽기(ALLOW) | MCP `tools/call` 1회, 결과가 모델로 가서 답변으로 끝남. Task RUNNING·PUBLIC |
| INTERNAL 읽기 → 외부 전송(ASK) → 승인 | 승인 전 MCP 호출은 읽기 1회뿐. 기다리는 동안 Codex는 모델에 새 요청을 보내지 않음. 승인하면 `Released`, 전송 1회, 같은 turn이 답변으로 끝남. Task INTERNAL |
| 접근 규칙 없음(DENY) | MCP 호출 0. 모델은 B 고정 문구 "이 요청은 정책에 의해 차단되었습니다"만 받음 |

- 세 경우 모두 callId마다 응답 1회, interrupt 0, 정지 코드 없음이다.
- 실제 Codex 이벤트가 L3 허용 목록을 통과했다. 잘못된 정지는 없었다.
- 세션을 닫은 뒤 Task 폴더는 남지 않았다(A103 스레드 삭제 경로).

## 확인한 실제 프로토콜

- 모델이 낸 `function_call`의 `name`(도구)·`namespace`(서버)가 `item/tool/call`로 왔다. A가 대역으로 가정한 형식과 같다.
- 모델 요청은 `tool_choice: auto`, `parallel_tool_calls: false`, 추론 `low`다. 도구 목록은 `functions.request_user_input`과 `docs` 네임스페이스(read·send)뿐이다.
- 읽기 결과는 `function_call_output`에 두 조각으로 들어간다: 본문 텍스트, 그리고 `structuredContent`의 JSON(`resource_id`). 모델은 읽은 경로를 본다.

## 발견: Codex가 덧붙이는 블록

- 우리 `baseInstructions`(770자) 외에 Codex가 세 블록을 더 넣는다. 앞의 두 블록은 developer 메시지 하나(4,860자)에 함께 들어간다.
  - `<permissions instructions>`(3,939자): 셸 명령, 샌드박스 권한 상승(`require_escalated`), prefix_rule 안내. 우리 설정에서는 쓸 수 없는 기능이다.
  - `<collaboration_mode>`(920자): `request_user_input` 사용 규칙
  - `<environment_context>`(user 메시지, 120자): 현재 날짜와 사용자 PC의 시간대
- 실제 모델이 permissions 안내를 보고 없는 셸 도구를 부르거나 권한 상승 승인을 요청하면, L3가 그 Task를 정지시킨다. 안전 문제는 아니지만 Task가 괜히 멈출 수 있다. 확인하지 않은 가정이다.
- Codex 0.162.1에는 `include_permissions_instructions`, `include_environment_context`, `include_collaboration_mode_instructions` 설정이 있다(`codex-rs/config/src/config_toml.rs`).
  - 앞의 두 개를 `false`로 넘겨 다시 돌렸다. 요청 크기가 10,060바이트에서 5,674바이트로 줄었고 turn은 그대로 끝났다.
  - L1 설정(`config/codex/config.toml`)은 C 소유라서 이 값을 넣을지는 C가 L2와 함께 정할 일이다.

## 실제 모델 비용 추정에 쓸 값

- 요청 하나가 약 10–11KB, 블록을 줄이면 약 6KB다. JSON 바이트로 잰 값이라 토큰 수는 추정이다.
- ASK 시나리오는 모델 요청 3번이었다.

## 남은 한계

- 모델이 가짜라서, 실제 모델이 우리 프롬프트와 도구 설명을 보고 Guard 도구를 고르는지는 확인하지 않았다.
- 서버가 내려주는 모델 정보의 영향(C-CODEX-03)도 확인하지 않았다. 실제 로그인으로 따로 돌린다.
- MCP 서버·Registry·Profile은 대역이다. 실제 MCP 서버를 launcher로 띄우는 일은 C2.6 뒤다.
- L2는 우회했다. 제품에서는 C6.14 전까지 세션이 거부된다.
- 임시 폴더를 루트로 써서 codex.exe가 PATH 별칭 경고 1줄을 남겼다. 앱 폴더 루트에서는 나지 않는 경고다(C2.13 기록과 같은 현상).

관련: I1·I2·I3·I5·I11, T01·T02·T03 A 부분(독립 계수 인수는 C). PR은 아직 없다(A 브랜치 작업).
