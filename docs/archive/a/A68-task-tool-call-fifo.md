# A68 · Task별 도구 호출 FIFO 연결부

2026-10-18. A 소유 `services/task_tool_calls.py`에 주입형 `TaskToolCallQueue`를 추가했다. 생성 시 신뢰한 Task ID를 전달하고, `enqueue(call_id, payload)`에서 수신 순서대로 접수한다. 한 Task의 이전 handler가 끝나야 다음 handler를 시작하며 다른 Task 큐는 동시에 진행한다. handler는 승인 대기를 포함한 호출 전체를 처리해야 한다.

Codex callId를 Task 큐 수명 전체에서 기억해 진행 중·대기 중·완료 후 중복을 거절한다. 결과를 재전달하거나 호출을 다시 실행하지 않는다. 대기 64개·누적 ID 4,096개가 기본 상한이며 초과 시 큐를 닫는다. 취소·종료 시 대기 future를 해결하고 늦은 결과를 버린다. 취소를 무시하는 handler의 정리가 제한 시간 안에 확인되지 않으면 `TOOL_CALL_CLEANUP_UNCONFIRMED`를 돌려주고 닫힌 상태를 유지한다. 원시 오류 대신 고정 코드만 노출한다.

검증: `python -m pytest -q tests/unit/test_task_tool_calls.py` **22 통과**, 별도 구현 담당의 읽기 전용 리뷰와 `-W error` 재실행도 **22 통과**. 취소 callback 전 신규 접수 경합, 실행 전 취소, late result/error, FIFO, Task 간 동일 callId·동시 실행·실패 격리, 수명/대기 상한을 확인했다. 새 두 파일 Ruff·format, 제품 모듈 mypy 통과.

관련 10.4절, I6·I7·I8, T21·T42의 의도를 합성 handler 계수로 검증했다. 실제 Guard dispatch·wire·수신 부작용은 측정하지 않았으며 T21/T42/T46 인수 완료가 아니다. payload 바이트 상한·전체 호출 감시 상한은 향후 parser/세션 관리자 연결 책임이다. 이미 시작한 부작용의 실행 여부나 Store 정지 상태를 큐가 판단하지 않는다.

실제 세션에는 아직 연결하지 않았다. 관리 대상 실행은 향후에도 Guard.submit만 사용하며 B Pending/STOP 계약과 C launcher/L2를 기다린다. domain·ports·Runner·C fixture·공통 설정은 변경하지 않았다. PR은 생성하지 않았다.
