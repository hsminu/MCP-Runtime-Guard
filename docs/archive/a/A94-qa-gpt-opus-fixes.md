# A94 · GPT·Opus QA와 B7.2 리뷰에서 나온 A 결함 수정

2026-10-18. 오늘 구현(A43·A82–A91, `f333dc5..8f01f20`)을 두 리뷰어가 같은 지침으로 독립 QA했다.

- GPT: Codex CLI 0.162.1, `gpt-6-astra` high, read-only sandbox
- Opus: high

B 소유 `guard.py`·`catalog*.py`의 Guard 우회 관점 리뷰(B7.2)에서 나온 A 쪽 후속 조치도 함께 고쳤다. B·C 소유 파일은 바꾸지 않았다.

## 고친 것

| 출처 | 결함 | 수정 |
| --- | --- | --- |
| GPT High·Opus H1 | 제품 Codex Task가 Pending이면 사람이 승인할 경로가 없음. projection에 `approval_id`가 없고, turn이 끝날 때까지 화면 갱신이 없고, 실행 중 명령이 승인 버튼을 막음 | `ApprovalOutcomes`가 기다리는 Task를 기록하고 `snapshot.approval_id`에 싣는다. `BackendBindings.task_events`로 명령 밖에서도 Task를 다시 게시한다. 결과 화면의 검토·승인·거절 버튼은 사람 제어 명령이 진행 중일 때만 막는다 |
| GPT High | 지문이 바뀐 서버의 다른 도구가 계속 실행됨 | 불일치하거나 목록이 미완료인 서버는 이번 실행 동안 PENDING으로 보이게 한다(REG_NOT_ACTIVE). 정상 서버에는 실제로 받은 정의의 지문 값을 Guard에 넘긴다(B7.2) |
| Opus H2 | 알 수 없는 서버 요청·stdout 종료·느린 interrupt 응답 하나로 앱 전체가 정리 미확인이 되고, 이후 새 세션이 모두 거부됨 | 이미 닫힌 client에는 interrupt를 보내지 않는다. 정리 완료 판정에서 interrupt 응답을 뺐다. 프로세스 종료 확인(C `confirm_exit`)과 정리가 기준이다 |
| GPT M·Opus 관련 | `send` 밖에서 생긴 L3 위반이 Task를 멈추지 않음 | 세션 정지 handler가 runtime의 `_fail_session`을 백그라운드로 실행하고 화면을 다시 게시한다 |
| GPT M | 다른 RPC의 답이 "도구 답을 보낸 뒤 정지"를 앞당김 | 미뤄 둔 정지를 메서드별로 보관한다 |
| GPT M | 스트리밍 중 답변 크기 상한이 없음 | turn별 delta 합계가 65,536자를 넘으면 정지하고 전달을 멈춘다 |
| GPT M·Opus L6 | 기기 코드 로그인 대기가 control 작업자를 무기한 점유함 | 로그인 대기를 bridge 백그라운드 작업으로 옮겼다. 15분이 지나면 미완료로 처리하고, 종료할 때 취소한다 |
| Opus M1 | 감시 상한에 FIFO 대기 시간이 포함됨 | 호출 처리 구간만 잰다(A80) |
| Opus M2·B7.2 | 세션 정지가 진행 중인 `Guard.submit`을 취소함. 예약 전이면 Guard 전역 정지 | Guard 호출을 `asyncio.shield`로 감쌌다. 세션 정지는 Store에 HALTED를 쓰기 전에 `HumanControl.cancel`로 Guard 정지 의사를 먼저 남긴다(STOP-01 전 임시, 감사 주체는 설정된 운영자). 진행 중이던 호출은 `TASK_NOT_RUNNING`으로 거부되고 다른 Task는 계속 실행된다 |
| Opus L1 | 한 turn의 여러 agentMessage를 구분 없이 이어 붙임 | `phase=final_answer`를 우선 쓰고, 없으면 빈 줄로 구분한다 |
| Opus L2 | 정지 뒤에도 큐에 남은 호출이 Guard로 감 | 정지 뒤에는 Guard에 넘기지 않고 정지 문구로 답한다 |
| Opus L3 | 이미 처리된 승인을 거절하면 무조건 "거절됨"으로 답함 | Denied의 사유가 STALE·RACE·CONTEXT_LOST면 무효화 문구로 답한다(NOTIFY-01 전 임시) |
| Opus L4 | 모델에 넘긴 도구가 0개여도 `tools_available`이 참 | Codex 이름 규칙으로 빼고 남은 도구 기준으로 판단한다 |
| Opus L5 | `arguments`가 객체가 아니면 Task 전체가 정지함 | 잘못된 인자(INVALID_ARGUMENTS)로 답하고 Guard는 부르지 않는다 |
| Opus L7 | Profile 승인 유효 시간이 승인 목록에 반영되지 않음 | 정책 view의 TTL을 승인 목록에 넘긴다 |
| B7.2 | Codex에 등록되지 않은 도구 호출도 Guard로 감 | relay가 Codex에 실제 등록한 도구 목록만 받는다 |
| B7.2 | `tool_views` 오류 원문이 그대로 나감 | `PROFILE_TOOL_INVALID`로 바꾼다 |
| #28 확인 | B3.10은 OTHER 도구 선택을 받지 않음 | 허용 대화상자에서 OTHER는 비활성으로 보이고 제출에서 뺀다 |

## B·C에 넘긴 것

- Windows 경로 별칭 등급 우회(B `catalog_sources`)
- 스키마 정규식이 이벤트 루프를 막는 문제(SCHEMA-01)
- 예약 전 취소를 전역 정지로 둘지
- 지문 계산 기준(C)
- dynamicTools 이름 규칙 충돌(contracts 10-17)

이 항목들은 #20·#21·PR #28 댓글로 남긴다(A92).

## 검증

- 전체 단위 테스트(`QT_QPA_PLATFORM=offscreen`): 3,492 통과, 1 skip
- ruff check·ruff format --check·mypy(`src/guard_app`, 81파일): 통과
- 세션 정지 중 전역 정지: 예약 전에 멈춘 submit을 둔 채 세션을 정지하고 다시 진행시켰다. 다른 Task의 READ가 실행되고(tools/call 1) `_halt_globally` 호출은 0이었다(`test_codex_product_flow.py`). 이전의 strict xfail은 뗐다.
- 실행하지 않은 것: 실제 codex.exe·C launcher·L2. 검증 불가(C 대기)로 둔다.
