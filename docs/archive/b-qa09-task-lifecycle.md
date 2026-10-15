# B-QA-09 · Task lock·stop 정보 수명 보완

2026-10-13 사용자 요청에 따라 A `ab82919`의 QA-09를 검토하고 B 내부에서
안전하게 가능한 정리를 구현했다. 시작 상태는 이전 QA-01·02·05·07·08 수정이
add된 B 브랜치, HEAD는 `9eb0560`이다. 최종 기준은 [기술설계서](../technical-design.md)의
Task 직렬화·종결 후 재개 금지·늦은 결과 폐기다. 공유 DTO·포트·DB 스키마·상태 규칙은
추가하지 않았다.

## 검토 결과와 구현

기존 `_locks`는 사용이 끝난 lock을 영구 보관했고, `_stop_requests`도
정상 AGT 검사 때 만든 미설정 Event까지 계속 보관했다. 하지만 종결 때 두 딕셔너리를
단순 삭제하면 대기자나 기존 lock 참조가 있는 동안 다른 lock을 만드는 문제가 생긴다.
정지 커밋 확인 전이나 늦은 AGT 작업이 남은 때 신호를 삭제하는 것도 안전하지 않다.

| 자원 | 정리 조건 / 보존 조건 |
| --- | --- |
| Task lock | weak cache를 사용한다. 소유자·acquire 대기자·기존 참조가 있는 동안 같은 lock이 살아 있고, 마지막 강한 참조가 끝나면 cache에서도 제거한다. `release()` 직후 강제로 삭제하지 않는다 |
| 미설정 stop 신호 | lock 참조·미완료 AGT·메모리 승인/종결 중/inflight 관계가 모두 없으면 정리한다 |
| 설정된 stop 신호 | 위 실행 관계가 모두 종료되고, 검증한 Store ACK 또는 후속 권위 조회에서 같은 Task의 HALTED/UNKNOWN_OUTCOME/CLOSED를 확인한 경우에만 정리한다 |
| 늦은 AGT | 내부 작업→Task ID 관계를 추적해 실제 완료 callback에서 정리를 재검토한다. 늦은 허용/예외는 dispatch 권한으로 쓰지 않는다 |
| 새 정지 명령 | 이전 명령의 종결 확인 표시를 지운 뒤 stop을 설정한다. 새 명령의 저장 실패를 이전 ACK로 성공처럼 처리하지 않는다 |

변경은 `services/guard.py` 안의 cache·신호·AGT 수명 메타데이터다.
추가 DB 조회나 상태 전이를 cleanup callback에서 수행하지 않는다.
기존 `_halt_task`/실패 저장의 검증 결과를 사용하며, 마지막 lock 참조 해제와
AGT 완료 callback에서 원문 없는 내부 정보를 정리한다.
HALTED·UNKNOWN_OUTCOME·CLOSED Task는 정리 후에도 기존 Store 상태 검사로 실행을 거부한다.
보안 버전·등급·승인 만료·SEND 재전송 규칙은 바뀌지 않는다.

## 보존하는 미확정 정보

- 저장 실패·불량 반환 등으로 종결을 확인하지 못한 **설정된 stop 신호**는 남긴다.
  잘못된 ACK 뒤에도 후속 정상 권위 조회가 종결을 확인하면, 실행 참조 종료 후 정리할 수 있다.
- **존재하지 않는 Task의 정지 신호**도 종결 근거가 없어서 보존한다. 최초 정상 부재는
  QA-02의 호출 단위 거부를 유지한다. 임의 삭제·TTL 해제·RUNNING 복구는 하지 않는다.
- live 승인 사본·실패 후 원문 없는 closing 관계가 있으면 신호 정리를 보류한다.
  승인 사본의 DB 복원이나 closing 관계의 추정 삭제를 구현하지 않았다.

따라서 정상 처리/확인된 종결의 누적 cache 잔류는 해결했지만,
모든 저장 불명·없는 Task·실패 승인 관계의 강제 정리를 완료했다는 뜻은 아니다.
이 정보의 해제·재시작 처리는 [B6 복구 계약](b6-contract-proposal.md)과 A 시작 게이트가 남아 있다.
QA-03/10·04·06의 공동 결정도 [QA 처리 표](tasks/a-b-qa-requests-2026-10-13.md)에 유지한다.

## 검증

관련 불변식은 I3/I5/I6/I8, T11/T20/T36/T37의 B 서비스 단위 부분이다.
새 `tests/unit/test_b_qa_task_lifecycle.py`는 다음을 검사한다.

- 정상 호출·취소·승인 대기 후 취소 각각 **80개 Task** 반복에서 idle lock/확인된 신호 잔류 0.
  메모리 Store와 실제 파일 SQLite를 각각 사용한다.
- 기존 lock 참조·대기 submit·취소/긴급 차단·정지 쓰기 대기 동안 같은 lock 유지,
  추가 dispatch 없음.
- 취소를 무시하는 늦은 AGT가 실제 완료되기 전에는 stop 보존,
  늦은 허용/예외 이후 추가 dispatch 0.
- 승인 review·승인·거절·만료·취소 후 안전한 정리와 소비/dispatch 횟수.
- HALTED/UNKNOWN_OUTCOME/CLOSED 정리 후에도 실행 재개 없음.
- 잘못된 ACK·실패 저장·취소된 lock 대기자·없는 Task에서 미확정 stop 보존.
  실제 SQLite 커밋 후 잘못된 ACK는 후속 정상 종결 조회까지 정리를 보류한다.
- 새 정지 명령이 이전 명령의 종결 ACK를 재사용하지 않음.

최초 25개 회귀는 수정 전 cache 잔류로 모두 실패했다. 이후 기존 긴급 제어·승인 lock
검사와 함께 **86 passed(12.01초)**를 확인했고, 추가 종결/ACK 회귀 10개를 포함해
새 파일은 **35개**다. 최종 전체 **1,614 passed(66.02초)**,
Ruff check·format 58개 파일·strict mypy 28개 소스·pip check를 통과했다.
이전 QA와 합친 결과는 [QA 반영 기록](b-qa-fixes-2026-10-13.md)을 따른다.

실제 SQLite의 B 단위 검증과 메모리 Transport dispatch 계수다. heap 바이트 단위의
메모리 사용량·실제 MCP wire·수신 부작용·C 독립 인수는 이 검사로 주장하지 않는다.
최초 사용자 요청대로 테스트 후 add까지 진행했다. 이후 2026-10-13 사용자의
commit·push 지시에 따라 이 검증된 QA 묶음을 B 브랜치에 공개한다.
