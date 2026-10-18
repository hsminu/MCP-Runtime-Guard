# A86 · Profile 로딩·접근 규칙·연결 직후 지문 대조를 Guard 조립에 연결

2026-10-18. main에 들어온 B 함수들을 B·C 소유 파일 변경 없이 A 조립 루트에 연결했다.

- B: `load_profile_settings`·`GuardSettings`·`evaluate_access`·`tool_fingerprint`·`GuardService`
- 관련 A 항목: A24–A27·A51·A79·A15·A16

## 한 일

- **`adapters/access_rules_checker.py`: `AccessRulesChecker`.**
  - 지금의 async `AccessChecker` 포트를 구현하고 B의 순수 함수 `evaluate_access`를 감싼다.
  - 정확한 (server_id, tool_name)만 판정한다. 거부가 우선이고 기본은 거부다.
  - 요청의 Task·epoch가 신뢰 상태와 다르면 `error=True`(I7).
  - 무효한 규칙으로는 만들 수 없다.
  - B2.9가 동기 포트로 확정되면 `async`만 뗀다.
- **`services/product_assembly.py`: `assemble_guard(store, ProductInputs)`.**
  - 입력은 조립 루트가 준다(주입): 파싱된 Profile 문서(B3.11·C 경로 검증), C Registry, C launcher의 서버별 스트림, `next_call_seq`(B-SQL-04), `failure_audit`.
  - 처리 순서는 다음과 같다.
    1. `load_profile_settings`로 Profile을 읽는다.
    2. `profile.server_ids`에 든 서버만 `McpTransport`로 띄운다. A76·A77의 바이트 상한도 적용한다.
    3. 연결 직후 지문을 대조한다.
    4. `GuardSettings`(profile·policy_version·TTL·taint)로 `GuardService`를 만든다.
  - Profile 서버의 스트림이 없거나 Profile이 무효하면 `PROFILE_SERVER_UNAVAILABLE`·`PROFILE_INVALID`로 시작을 거부한다.
- **연결 직후 지문 대조(A15·A16).**
  - `tools/list`로 받은 정의를 `tool_fingerprint`로 계산해 Registry 등록 지문과 비교한다.
  - 다르거나, 정의가 없거나, 목록이 미완료인 도구가 있으면 `LiveCheckedRegistry`가 이번 실행 동안 그 도구의 지문을 "다름"으로 돌려준다. 그 결과 Catalog의 `tool_views`에서 빠지고, Guard는 `TOOL_FINGERPRINT_CHANGED`로 거부한다.
  - 대조가 끝나기 전에는 지문 조회 자체가 실패한다(fail-closed).
  - Registry를 PENDING으로 돌리는 것은 C 몫이다(I10).
- **`SessionRuntime`·`bootstrap_product`.**
  - 새 Task 행은 `GuardSettings`의 profile·policy_version을 쓴다. `codex-pending` 임시값은 지웠다(A79).
  - Profile이 없으면 생성 전에 `GUARD_PROFILE_UNAVAILABLE`로 거부한다. `creation_blocker`도 같은 순서로 미리 알린다.
  - `bootstrap_product(product=...)`가 있으면 `human_control`·`discard_queued`를 bridge에 연결하고, 종료할 때 transport를 닫는다.
  - 조립이 실패하면 Store를 닫는다.
  - 현재 `__main__`은 `product`를 넘기지 않는다. 설정 파일 읽기(B3.11)와 C launcher·Registry가 없기 때문이다.

## 검증

- 새 `test_product_assembly.py` 8개. 실제 `GuardService`·Profile 로딩·메모리 MCP 파이프·가짜 Registry로 확인했다.
  - 허용된 READ는 Released이고 수신 tools/call 1회
  - 규칙 누락은 Denied이고 수신 0회
  - 정의 변경·누락은 Denied(`TOOL_FINGERPRINT_CHANGED`)이고 도구 목록에서 빠지며 수신 0회
  - 무효 Profile·스트림 누락은 거부
  - 접근 규칙 판정과 Task 결박
  - 제품 bootstrap 연결·실패 시 Store 해제
- `test_session_runtime.py` 2개: 새 Task 행이 Profile 값을 쓰는지, Profile이 없으면 행이 생기기 전에 거부하는지.
- 기존 테스트 2곳을 고쳤다.
  - 준비 테스트에 `GuardSettings`를 넣었다.
  - `test_bridge_open_failure_is_fixed_startup_failure`의 경합을 고쳤다. 스레드 종료만 기다리고 Qt 큐로 늦게 오는 이벤트를 기다리지 않던 문제다.
- 전체 단위 테스트 **3,332개 통과, 1 skip, subtest 20**. ruff·format·mypy 73소스 통과.

## 남은 한계

- **지문 계산 기준.** SDK가 다시 만든 dict(`model_dump(exclude_unset, by_alias)`)로 계산한다. 이름·설명·inputSchema만 있는 정의에서는 원문 dict와 지문이 같았다. annotations·기본값이 있는 정의도 같은지는 C의 등록 지문 계산 방식과 맞춰야 한다(#21 확인 요청, A92).
- **실제 실행 미검증.** 실제 C launcher·Registry·`next_call_seq` 영속 공급은 없어서 제품 실행 경로로는 확인하지 못했다.
- **resource READ 응답 형식.** resource READ는 서버가 `structuredContent.resource_id`를 돌려줘야 분류된다(B ResponseGate). MCP 서버 쪽 요구사항으로 C fixture에 반영돼야 한다.

관련: I1·I2·I6·I7·I10, T10·T14(부분).
