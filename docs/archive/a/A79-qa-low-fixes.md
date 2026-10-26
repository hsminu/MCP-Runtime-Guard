# A79 · A 브랜치 QA Low 지적 수정

2026-10-18. 기준 `a6bf141`. A78의 QA에서 나온 Low 7건 중 A가 혼자 고칠 수 있는 5건을 고쳤다. 나머지 2건은 근거와 함께 보류했다. PR은 만들지 않았고, B·C 소유 파일과 공통 계약은 바꾸지 않았다.

## 고친 것

1. **Scanner 키 저장 함수가 예외를 내면 이전 성공 표시가 남던 문제.**
   - 저장 함수를 부르기 전에 끝 4자리를 지우고, 상태를 "저장 결과를 확인하지 못했어"로 바꾼다.
   - 저장 함수가 예외를 내도 이전 키의 성공 문구가 남지 않는다.
   - 예외는 잡지 않는다. 넓은 예외 처리 경계 규칙에 맞추기 위해서다.
2. **`CONTROL_WITHHELD` 문구가 원인을 단정하던 문제.**
   - "분류할 수 없어"를 지우고 "공개 조건을 확인하지 못한 결과는 보여주지 않아"로 바꿨다.
   - 커밋 실패, resource_id 불일치, 지원하지 않는 형식도 모두 이 문구에 포함된다.
3. **결과 화면에 `None`이 그대로 나오던 문제.** dispatch·수신 부작용 계수가 없으면 "미제공"으로 보인다.
4. **거부 코드 안내가 틀리거나 없던 문제.**
   - bridge가 접수 전에 거부하는 `NOT_READY`·`CLOSING`·`INGRESS_FULL`·`QUEUE_FULL`·`COMMAND_ID_CONFLICT`는 초안과 기존 Task 모두 "요청을 접수하지 않았어"와 이유를 보인다. 이전에는 초안에서 "확인하지 못했어"로 보였고, 기존 Task에서는 안내가 없었다.
   - 기존 Task의 `RUNNER_UNAVAILABLE`·`RUNNER_UNCONFIRMED`는 실행 결과 문구를 대화 화면에도 보인다.
   - 앱 구성에 없는 Codex 구성요소(`CODEX_SESSION_UNAVAILABLE`·`CODEX_L2_UNAVAILABLE`·`CODEX_CONFIG_UNAVAILABLE`)는 "앱 설정을 확인하고 다시 시작해" 대신 "이 앱 구성에서는 아직 이 기능을 쓸 수 없어"로 안내한다.
5. **정리가 겹치면 정상 정리도 미확인으로 보고되던 문제.**
   - 진행 중인 정리가 있으면 `stop_session`·`close`가 그 결과를 기다려 따른다. 정리를 두 번 돌리지 않는다.
   - 이미 미확인으로 끝난 정리는 지금처럼 다시 시도하지 않고 미확인으로 보고한다.

## 보류한 것

- **넓은 예외 경계 사실상 사용.** QA가 지적한 A 위치는 `session_runtime._prepared`·`_bounded_cleanup`, `task_tool_calls` 완료 콜백, `jsonrpc_client`의 `_server_task_done`·`_reader_done`, `codex_stream` 완료 콜백이다. 같은 `Task.exception()` 조회 패턴이 `bridge.py`·`guard.py`·`mcp_transport.py`에도 있다.
  - 이 패턴은 콜백 예외를 고정 코드로 바꾸고, asyncio 기본 로그로 원문 예외가 나가지 않게 막는다(I9).
  - 예외 종류를 좁히면 원문이 로그로 샐 수 있다. 반대로 `fail_closed`로 옮기려면 "예약 전" 의미를 늘려 써야 한다.
  - 규칙을 어떻게 해석할지는 사용자 문서(AGENTS·개발문서 13.5·대장)의 결정 사항이라 코드는 바꾸지 않았다.
- **`codex-pending` 정책값 저장.** ACCESS-01 결정상 `policy_version`은 B가 계산한다. Profile 로딩은 #19 결정대로 B와 C의 몫이다. A가 쓸 값이 아직 없어서, B 함수가 생기면 바꾼다.

## 검증

- 새 회귀 테스트 17개를 추가했다.
  - Scanner 1개
  - 세션 UI 14개: 거부 코드 10, Runner 실패 2, 계수 표시 1, Withheld 문구 1
  - 정리 겹침 2개
  - 수정 전 코드로 되돌리면 17개가 모두 실패하고, 기존 기대 1개를 새 문구로 바꾼 테스트도 실패한다.
- 전체 단위 테스트: **2,734개·subtest 20개 통과**, 기존 경고 5개.
- 변경 파일의 Ruff·format, mypy 68소스 통과.

관련: I6·I9, T18. A 단위 회귀이며 GUI 전체 인수(C)가 아니다.
