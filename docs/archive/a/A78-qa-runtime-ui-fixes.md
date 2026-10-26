# A78 · A 브랜치 QA 지적 High·Medium 수정

2026-10-18. 기준 `3d443b3`. Opus 리뷰어 두 명이 A 변경(`a41f1d8..3d443b3`)을 읽기 전용으로 엄격히 QA했다. 하나는 런타임·전송·보안, 다른 하나는 UI·문서·테스트를 맡았다. 확정된 High 1건과 Medium 4건을 루트가 재현 스크립트로 다시 확인한 뒤 고쳤다. PR은 만들지 않았고 B·C 소유 파일과 공통 계약은 바꾸지 않았다.

## 고친 것

1. **[High] stdio `tools/list` 바이트 상한 우회.**
   - 원인: 게이트는 프레임을 꺼낼 때 판정하는데, SDK는 그보다 늦게 처리한다. 그 사이 대기 칸(버퍼 0)에 메시지 하나가 머물 수 있다. 피어가 다음 요청 ID를 맞혀 큰 페이지를 미리 보내고, 클라이언트 쓰기가 막힌 사이 수집이 시작되면 그 페이지가 차감 없이 목록을 완료했다. QA 재현에서는 기본 상한(1MiB/8MiB)에서 20MiB 정의가 완료 목록으로 받아들여졌다.
   - 수정: `IngressGate.check_delivery`를 추가하고, SDK에 넘기는 읽기 스트림을 `_DeliveryCheckedStream`으로 감쌌다. SDK가 응답을 꺼내는 순간 그 ID가 아직 `pending`이면, 수신 때 연결·차감되지 않은 응답이다. 이 경우 `TOOLS_LIST_RESPONSE_UNADMITTED`로 수집과 연결을 실패시킨다. 설치된 MCP 1.29의 `_receive_loop`는 이 스트림이 값을 돌려준 뒤 `_response_streams.pop`까지 await가 없다. 그래서 검사와 SDK의 ID 대응 사이에 끼어드는 등록은 없다.
   - 적용 범위: 래퍼가 `_sdk_session_factory`에 있어 게이트를 넘기는 stdio·HTTP 두 경로 모두에 적용된다.
2. **[Medium] JSON-RPC 호출자 취소 뒤 전송 지속.**
   - 원인: `_send`가 전송을 기다리는 중 호출자가 취소되면 `channel.send`는 계속 돌았다. 그런데도 잠금은 풀리고 클라이언트는 열려 있었다. 그래서 취소한 요청이 나중에 전송되고 다음 전송이 겹쳤다.
   - 수정: 취소되면 진행 중인 전송을 취소하고 클라이언트를 `SEND_CANCELLED`로 닫는다. 일부만 쓰였을 수 있는 프레임이 있으니 스트림은 다시 쓰지 않는다.
3. **[Medium] 끝난 Codex Task를 "연결 필요"로 표시.**
   - 원인: `model_ready`는 RUNNING일 때만 참이다. 그래서 HALTED·UNKNOWN_OUTCOME·CLOSED에서 세션 목록은 "연결 필요", 결과 화면은 "모델 연결 미완료"를 보였다.
   - 수정: `task_status.FINAL_STATUSES`를 추가했다. 끝난 상태에서는 상태 제목과 상태 안내(UNKNOWN_OUTCOME이면 "수신측을 확인해야 해")를 먼저 보인다. 실행 중이면서 모델이 없는 Task는 기존대로 연결 필요를 보인다.
4. **[Medium] 대화창 화자 위조.**
   - 원인: 모델 답변의 실제 줄바꿈 뒤에 "나"나 "Guard · …" 줄을 넣으면 진짜 화자 라벨과 구별되지 않았다.
   - 수정: 본문의 모든 줄 앞에 `│ `를 붙여, 줄 맨 앞에서 시작하는 것은 Guard가 넣은 라벨뿐이게 했다. `\n` 외의 줄 구분 문자는 기존 `untrusted_display_text`가 코드포인트로 보인다.
5. **[Medium] a-status A64 줄.**
   - 원인: 2026-10-18 결정(직접 모델 API 경로 없음)과 어긋나게 "API 사용 … C 구현 대기"로 남아 있었다.
   - 수정: A65에서 제거됐다는 사실로 고쳤다. 완료 기록 A64는 고치지 않았다.

## 검증

- 새 회귀 테스트 14개를 추가했다.
  - ingress 8개: `check_delivery` 단위 7개, 실제 SDK·파이프 경합 1개
  - JSON-RPC 1개
  - UI 5개
  - 수정 전 코드로 되돌려 실행하면 경합·JSON-RPC 테스트와 UI 4개가 실패하고, 대조 테스트 1개만 통과한다.
- QA 재현 스크립트 재실행: 20MiB 예측 페이지는 `TOOLS_LIST_RESPONSE_UNADMITTED`로 시작이 거부됐다. 취소 뒤 전송 프레임 0개, 동시 전송 1개였다.
- 전체 단위 테스트: **2,717개·subtest 20개 통과**, 기존 경고 5개.
- 변경 파일 Ruff·format 통과, mypy 68소스 통과.
- MCP·JSON-RPC 묶음을 `-X dev -W error::ResourceWarning`로 실행하면 `MemoryObjectReceiveStream` 미닫힘 오류 1건이 난다. 수정 전 HEAD에서도 같은 오류가 재현되는 기존 A60 미해결분이다.

## 남은 한계

- `check_delivery`는 MCP 1.29 수신 루프의 "반환 뒤 pop까지 await 없음"에 의존한다. SDK를 바꾸면 다시 확인해야 한다.
- 이미 OS 파이프로 넘어간 취소 프레임은 되돌릴 수 없다. 그래서 클라이언트를 닫고, 세션 관리자가 Codex 프로세스를 정리해야 한다(A19·A20 연결 때).
- 실제 OS 파이프·C launcher 환경에서의 경합 재현과 HTTP 경로의 같은 경합 재현은 하지 않았다.
- QA Low 지적은 이번 범위 밖이다: Scanner 키 저장 예외 시 이전 성공 문구, CONTROL_WITHHELD 문구, 결과 화면의 `None`, 거부 코드 안내, 정리가 겹칠 때의 미확인 보고, 넓은 예외 경계의 사실상 사용, `codex-pending` 정책값 저장.

관련: I6·I7·I8·I9, T10·T14·T18. C 독립 인수가 아닌 A 단위 회귀다.
