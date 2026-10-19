# A68 · NOTIFY-01·STOP-01로 A 임시 연결 교체

2026-10-19. PR #28(B5.9·B5.10)이 main에 병합된 뒤, A가 임시로 쓰던 승인 결과 전달과 세션 정지 경로를 B 계약으로 바꿨다. PR #29 B 리뷰 5번도 함께 반영했다.

## 한 일

- **승인 결과(NOTIFY-01), `services/guard_relay.py`.**
  - Pending의 모델 답은 `guard.pending_result(request_id).wait()`의 `PendingFinal`에서만 나온다. 사람이 approve/reject한 반환값을 모델에 넘기던 bridge의 `approval_resolved`는 지웠다.
  - `executed`는 결과 그대로 쓰고, `rejected`·`expired`·`cancelled`·`invalidated`는 고정 승인 문구로 바꾼다.
  - 다음 경우는 정지 문구와 함께 Task를 멈춘다: `failed`·`shutdown`, 다른 Task·epoch의 결과, 알 수 없는 요청.
  - 매핑은 B가 #28에서 동의한 표다. B 3차 PR의 `final_model_message(PendingFinal)`가 들어오면 그걸로 바꾼다.
  - `ApprovalOutcomes`는 이제 어느 Task가 어느 승인을 기다리는지만 기록한다(화면의 승인 버튼용). 감시 상한이 걸리면 대기만 취소하고 Guard 결과는 건드리지 않는다.
- **세션 실패 정지(STOP-01), `services/session_runtime.py`.**
  - `guard.system_control(store).stop_task(TaskIdentity, reason)`를 백그라운드로 부르고, A는 HALTED를 직접 쓰지 않는다. 사유는 #28 리뷰에서 합의한 매핑(`stop_reason`)이다.
  - Guard가 잠금을 넘겨받아 HALTED를 기록하기 전까지 Store는 RUNNING일 수 있다. 그동안에도 세션은 이미 쓸 수 없고 도구 실행도 막혀 있다. 정지가 확정되면 화면을 다시 그린다.
  - `stop_task`가 실패하면 `SESSION_STOP_UNCONFIRMED`로 표시한다. 정상 정지로 보이지 않게 한다.
  - `requestUserInput`마다 `record_request_user_input`으로 감사를 남긴다. 질문 원문은 넘기지 않는다.
- **사람 정지, `stop_session`(B 리뷰 5).**
  - bridge가 먼저 Guard 사람 채널(`cancel`/`emergency_halt`)로 정지를 건다. Guard가 있으면 `stop_session`은 Codex 세션만 닫고, Store에 쓰지 않는다.
  - 사람 정지는 감사에 운영자가 남도록 사람 채널을 유지했다(`stop_task`로 바꾸지 않음).
  - Guard가 없는 구성(시연·단위 테스트)만 A가 HALTED를 쓴다.
- **앱 종료.** `AssembledGuard.aclose`는 transport를 먼저 닫고 그다음 `Guard.aclose`를 부른다(B 안내 순서).

## 검증

- 실제 Guard·SQLiteStore·가짜 app-server로 확인했다(`test_b28_integration.py`, `test_codex_product_flow.py`, `test_codex_session.py`, `test_session_runtime.py`).
  - SQLite 감사 행: `SESSION_STOPPED/SESSION_L3_VIOLATION`, `SESSION_REQUEST_USER_INPUT`
  - 승인 결과 원인별 문구, 다른 Task·epoch·알 수 없는 결과는 정지
  - 감시 상한 취소가 Guard 결과를 남기는지
  - 정지 실패 표시, 사람 정지 때 Store 직접 쓰기 없음, 종료 순서
- 바뀐 동작에 맞춰 고친 기존 단언: L3 정지 직후 Store 상태(Guard 확정 전 RUNNING), 정지 시 화면 갱신 알림 두 번, 사람 승인 반환값 미전달.
- 전체 결과는 이번 병합 커밋 기준으로 PR #29 설명에 적었다.

## 남은 것

- `final_model_message`로 교체(B 3차 PR).
- 실제 codex.exe로 확인하는 건 C launcher를 기다린다.

관련: I6·I8·I9·I11, T21·T45.
