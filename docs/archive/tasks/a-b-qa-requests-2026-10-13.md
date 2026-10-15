# A → B · 2026-10-13 전체 QA의 B 소유 결함 요청

기준: A `cd75bf9`에서 수행한 전체 QA. A 수정은 `feat/a-application`의 `8ad4cdb..a9e3cac`에 반영했다.
B 소유 파일(`services/guard.py`, `services/approvals.py`, `services/response_gate.py`)은 수정하지 않았다.
A 반영 내용과 검증은 [QA 2차 반영 기록](a-qa-fixes-2026-10-13.md)을 따른다. 이 문서는 B 검토 요청이며
B·C 동의나 공동 계약 합의를 대신하지 않는다.

> **임시 요청 문서.** B는 각 항목을 해결하거나 기각하면 이 문서에서 처리 결과를 표시한다.
> 모든 항목이 정리되면 이 문서를 삭제하고, [QA 2차 반영 기록](a-qa-fixes-2026-10-13.md)의 B 요청 표와
> 이 문서로 가는 링크를 해결 커밋 기준으로 갱신한다.

우회·데이터 유출·이중 전송은 재현되지 않았다. 아래 항목은 모두 차단 쪽으로 닫히지만 정지 범위가
지나치게 넓거나 Guard·Store·UI 상태가 어긋나는 결함이다.

## 우선 검토

### B-QA-01 · 시계 역행 시 Guard 전역 정지

- 위치: `approvals.py:557-568`, `domain/contracts.py:131`
- 관측: 시스템 시계가 2초 뒤로 가면 승인·거절 시 `decided_at < created_at` 검증이 실패하고
  containment가 전역 정지를 건다. 관련 없는 Task의 submit도 거부된다. Store 대역 3종에서 같다.
- 영향: NTP 보정·VM 재개에서 발생 가능하다. 설계 8.4의 "시스템 시간 변경과 무관" 원칙과 어긋난다.
- 요청: 결정 시각을 생성 시각 이상으로 고정하거나 단조 시계 기준으로 검증한다.

### B-QA-03 · 전역 정지가 메모리에만 있음 (B-QA-10 포함)

- 위치: `guard.py:151, 469-470`
- 관측: 전역 정지 뒤에도 Store는 RUNNING이다. UI·Runner는 정지를 알 수 없고 재시작 전에는 해제할 수 없다.
- B-QA-10: Withheld 뒤 두 containment 단계가 모두 실패하면 Store는 RUNNING, Guard만 정지한다.
  Runner가 모델을 한 번 더 호출한 뒤 다음 submit에서 정지한다.
- A 임시 대응: Runner가 `INTERNAL_ERROR` 계열 Denied를 받으면 `guard_error`로 정지한다.
- 요청: 전역 정지 조회 API 또는 Store 반영. 해제 조건도 정한다.

### B-QA-02 · 없는 task id로 전역 정지

- 위치: `guard.py:195-199, 472-474`, `approvals.py:506-531`
- 관측: 없는 task id의 submit·cancel·긴급 차단이 `STATE_MISSING`을 저장소 손상으로 보고 전역 정지한다.
  빈 id(`guard.py:189`)와 지난 epoch(`:233`)는 전역 정지하지 않는다.
- 도달성: 현재 앱 경로에서는 도달하지 않는다. API 직접 호출이나 일시적 StoreError에서 발생한다.
- 요청: 없는 Task는 호출 단위 거부로 처리한다.

## 계약 결정

### B-QA-04 · 전송 전 폐기된 SEND의 결과 분류

- A 변경: `McpTransport.discard_queued(task_id)`가 cancel·긴급 차단 직전에 그 Task의 대기 명령을
  `DispatchError(DispatchStopped)`로 확정한다(Q01, 취소 수락 뒤 대기 SEND가 전송되던 결함).
- 현재 Guard 처리: 예약 후 실패로 보고 `UNKNOWN_OUTCOME`을 기록한다. 보수적이지만 실제로 나가지 않은
  SEND도 결과 불명으로 남는다.
- 요청: 전송 전 폐기를 "미전송"으로 구분할지 Store 계약과 함께 결정한다.

## 그 외 (P3)

| ID | 결함 | 위치 | 요청 |
| --- | --- | --- | --- |
| B-QA-05 | 스냅샷 제거와 store 쓰기 순서 때문에 Task lock 없이 승인 기록을 쓰는 창. Store가 await에서 양보할 때만 발생 | `approvals.py:209-210, 221, 280-289, 496-497, 542` | 제거·쓰기 순서 정리 |
| B-QA-06 | AGT 동시 검사 상한(4) 포화 중 승인 재검사가 `AGT_ERROR`로 사람 승인을 무효화 | `guard.py:326`, `approvals.py:387-398` | 일시 포화를 무효화 사유에서 제외 |
| B-QA-07 | 사후 실패 commit 전에 감사를 기다림. 감사가 no-op라 현재는 잠재 | `guard.py:487-507` | 실패 commit 후 감사 |
| B-QA-08 | Store 반환값 검사 불일치. `_finish(resume)`가 반환 TaskState 무시, `_contain_post_dispatch`가 taint 보존 미확인 | `approvals.py:549-555`, `guard.py:498-514` | 반환값 검증 일관화 |
| B-QA-09 | Task별 `_locks`·`_stop_requests`가 제거되지 않음. Task당 약 1.2KB | `guard.py:205, 211, 329` | 종결 Task 정리. stop Event는 HALTED commit 뒤에만 제거 |

## B가 알아야 할 A 변경

- transport는 MCP SDK의 서버 `outputSchema` 검증을 건너뛴다. 서버 `$ref`로 Guard 프로세스가 외부 URL을
  조회하고 루프가 멈추는 문제 때문이다. ResponseGate에는 검증하지 않은 `structuredContent`가 그대로 온다.
  원래 신뢰하지 않는 입력이므로 의미는 같다.
- Runner는 요청 ID 없는 정지 사유(`INTERNAL_ERROR`, `STATE_MISSING`, `STATE_COMMIT_FAILED`,
  `TASK_NOT_RUNNING` 등)를 받으면 정지한다. Withheld 뒤에는 Store 상태를 조회해 RUNNING이면 계속한다.
- 각 항목의 재현 probe는 A 로컬에 있다. 필요하면 별도로 전달한다.

## B 처리 결과 · 2026-10-13

A `ab82919`에서 읽은 요청을 B `9eb0560` 이후 작업에 반영했다.
아래는 B 구현 결과이며 A·C 공동 계약 합의나 독립 인수 기록은 아니다.
상세 변경·검증·재시작 한계는 [B QA 반영 기록](../b-qa-fixes-2026-10-13.md)을 따른다.

| ID | 처리 상태 | 결과 / 남은 사항 |
| --- | --- | --- |
| B-QA-01 | B 수정 | 사건 기록 시각 하한 보정. UTC/monotonic 만료와 TTL 유지 |
| B-QA-02 | B 수정 | 정상적인 최초 Task 부재는 호출 단위 거부. 저장 무결성 오류는 전역 차단 |
| B-QA-05 | B 수정 | 원문 없는 종결 중 관계와 inflight를 유지해 같은 Task lock으로 직렬화. 소유 미상 승인은 read-only 실행 거부 |
| B-QA-07 | B 수정 | 실패 저장·반환값 검증 뒤 추가 감사. 저장 실패 시 추가 감사 0 |
| B-QA-08 | B 수정 | 상태·승인·응답 반환의 식별자/맥락/등급/버전/이력 검사 보강 |
| B-QA-03/10 | 공동 결정 필요 | 전역 정지의 Store·Runner·UI 가시성과 복구/해제 조건 |
| B-QA-04 | 공동 결정 필요 | 확정 미전송 증거와 저장/표시 계약 |
| B-QA-06 | 공동 결정 필요 | AGT busy와 오류 구분·대기 정책·TTL·Runner 처리 |
| B-QA-09 | B 내부 수명 보완 | [lock 참조·늦은 AGT·종결 확인 후 안전 정리](../b-qa09-task-lifecycle.md). 미확정/없는 Task의 설정된 stop과 실패 승인 관계의 해제·복구는 보류 |

미결 요청이 남아 있으므로 임시 요청 문서는 유지한다. 이후 2026-10-13 사용자가 B QA 묶음의
commit·push를 지시했다. 원격 공개와 A·C 리뷰·독립 인수 완료는 구분한다.
