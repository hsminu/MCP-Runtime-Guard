# 공통 계약 체크리스트

> 2026-10-13 반영: SQLite·영속 순번·감사 저장·시작 복구는 2026-10-12 사용자 결정에 따라 B6 소유, C는 독립 검증 담당이다. 이번 리뷰 수정·임시 앱 계약은 [리뷰 반영 기록](tasks/a-review-fixes.md)을 따른다. 기존 설계 코드 블록은 참고이며 구현 증거가 아니다.
담당은 B, 사용자는 A·C다. `domain/*`와 `ports.py` 변경은 3인 합의 대상이다. 이 문서는 [개발문서](technical-design.md) 0.5·3·7–12장의 계약 요구를 정리한 **미확정 체크리스트**다. 실제 코드의 모든 메서드·반환형을 이미 정의했다는 뜻은 아니다.

## 합의할 계약

| 계약 | 정의 담당 / 구현 담당 | 필수 의미 | 근거 |
| --- | --- | --- | --- |
| domain 모델 | B / B | 신뢰하지 않는 `ProposedCall`과 신뢰 해석 `ResolvedRequest` 분리, frozen·strict·extra forbid | 3장 |
| Guard 입구와 결과 | B / B | 일반 도구 실행은 `submit`; 결과 `Denied`, `Pending`, `Released`, `Withheld`, `OutcomeUnknown` | 0.5·7장 |
| Transport | B / A | 서버별 세션 소유 worker, 명령 future의 성공·예외 확정, 원시 결과 격리, 쓰기 자동 재전송 금지 | 9장 |
| AccessChecker | B / A | 요청·신뢰 맥락 입력, `AccessResult` 반환, 거절·오류는 DENY, 형식 불명·시간초과는 `error=True` | 10.3절 |
| Scanner | B / C | 정의 metadata만 입력, 필수·완료·skip·failed 분석기와 지문 집합을 명시 | 10.2절 |
| Store | B / B | 상태·감사·예약·승인 조회와 아래 세 원자적 연산 | 8.3·11장 |
| ModelClient | B / A | 대화와 ACTIVE 등록 ToolView 입력, ModelTurn 반환, 새 Task에서 대화·캐시 분리 | 6.5·10.4절 |
| Registry 조회 | B와 C 합의 / C | status·fingerprint는 저장된 등록 레코드가 근거, 변경 시 PENDING·승인 무효 | 10.1절 |
| UI 명령·이벤트 | B와 A·C 합의 / A | 불변 메시지, 실행·제어 큐 분리, 상태 확정은 백엔드, 원시 결과 공개 금지 | 8.5·12장 |
| 계수와 fixture | A·B·C 합의 / C 주도 | 승인 소비·host dispatch·wire tools/call·receiver effect를 요청과 연결 | 9.4·14장 |

## 저장소 원자성

- [ ] `commit_observation`: READ 등급 병합, security_version, 승격 근거, 감사, 요청 상태가 한 트랜잭션에서 확정된다.
- [ ] `commit_send_outcome`: SEND 요청 상태와 감사만 갱신하며 READ 등급 병합을 호출하지 않는다.
- [ ] `consume_and_reserve`: 승인 CONSUMED, 요청 DISPATCHING, Task RUNNING 전이가 한 트랜잭션이다. 해시·만료·상태·security_version과 각 변경 행 수를 검사한다.
- [ ] 연결은 백엔드 단일 writer가 소유하며 WAL·foreign_keys·synchronous FULL·명시적 트랜잭션을 사용한다.
- [ ] 저장소 오류·상태 충돌·감사 기록 실패가 도메인 결과로 바뀌는 경계를 합의한다. 원문을 오류 메시지로 공개하지 않는다.

## 상태·승인·응답

- [ ] `policy_version`, `security_version`, `row_revision`, 도구 `fingerprint`의 용도를 구분한다.
- [ ] 새 Task는 보안 상태뿐 아니라 모델 대화·캐시·클라이언트 세션 상태도 분리한다.
- [ ] 승인 사본은 요청·인자 해시·등급·버전·승인자·만료를 고정한다. 원문은 메모리에만 둔다.
- [ ] 승인 재평가의 ASK·해시 일치와 원자적 소비를 검사한다. 재평가부터 응답 커밋까지 동일 Task lock을 유지한다.
- [ ] 승인 대기 중 일반 submit을 거부하지만 제어 큐는 처리한다.
- [ ] 결과 없음인 READ 전송 실패와, 수신·변환 후 예상하지 못한 예외를 구분한다. 후자는 HALTED·미공개다.
- [ ] SEND 결과 불명은 UNKNOWN_OUTCOME으로 잠그고 새로운 모델 호출 ID로도 재전송하지 않는다.
- [ ] READ 출처 불명·resource_id 불일치·지원하지 않는 형식·커밋 실패 시 미공개 처리한다.
- [ ] monotonic 만료와 DB UTC 만료를 함께 확인하고 재시작 때 승인을 복원하지 않는다.

## 착수 때 확정할 항목

- [ ] `ports.py`의 전체 메서드·인자·반환형·오류 타입과 Guard 결과 모델을 B가 제안하고 A·C가 확인한다.
- [ ] UI 명령·이벤트 payload, 제어 주체, reason_code 표시와 모델에 공개할 고정 응답을 확정한다.
- [ ] 설정 파일·Profile 검증 및 로딩의 편집 담당을 배정한다. 정책·경계 의미는 B, 위험 입력 검증은 C, 연결 필드는 A와 검토한다.
- [ ] `pyproject.toml`, lock 생성, 모델 제공자·모드 설정의 편집 담당을 정한다.
- [ ] Python 3.12에서 동일 MCP SDK로 stdio·HTTP 왕복을 검증하고 AGT·Scanner 호환성을 기록한다.
- [ ] fixture의 `wire_tools_call`과 문서의 `wire_post`가 같은 tools/call 계수를 뜻함을 계약 테스트에서 확인한다. 프로토콜 메시지는 별도 계수다.
- [ ] 위 계약을 실제·Fake 어댑터에 동일하게 적용할 C의 계약 테스트 인수 기준을 확정한다.

## B1 구현 초안

2026-10-11 B 브랜치에 도메인 모델·enum·reason code·전용 오류, 결과 DTO와 포트 Protocol의
검토용 구현 및 단위 테스트를 추가했다. 세부 구현과 추가 시그니처 선택은
[B1 도메인·포트 계약 구현 초안](b1-contract-draft.md)에 있다.
코드 작성과 단위 검증은 A·C의 합의·계약 동결을 뜻하지 않는다.
위 체크리스트의 미정 항목과 실제 저장소·전송·UI 인수 조건은 계속 검토 대상이다.

## B4·B5 연동 제안

해시·예외·Guard·응답·승인 서비스의 구현과 미확정 연결 조건은
[B4·B5 구현 문서](b4-b5-execution.md)에 기록했다. 기존 domain·ports 코드는 유지했다.
실제 A·C 연결 위치, 서비스 구성 예시와 인수 순서는 [B 인수 안내](b-handoff.md)에 있다.
다음 항목은 구현자가 제안한 서비스 구성 선택이며 A·C 합의 기록이 아니다.

- ID·기존 Task의 순번·예약 전 예외 감사는 구성 callback으로 주입한다.
  실제 C 공급자의 충돌 방지·내구성·재시작 의미를 확정해야 한다.
- Task WAITING 전이와 승인 생성은 현재 포트에서 별도 연산이다. 중간 실패는
  실행 없이 global halt로 처리하며, 원자 묶음 API는 C와 확정해야 한다.
- WAITING↔RUNNING의 security_version 의미는 미정이다. WAITING 반환 상태로
  사본을 고정하고 소비 후 버전 유지·1 증가를 지원하되 다른 보안 맥락은 같아야 한다.
- consume_and_reserve는 정확한 bool ACK, 실제 트랜잭션 시점의 UTC 만료,
  취소·내구성 커밋 ACK 의미를 보장해야 한다. Guard는 승인 소비 완료 후에도
  전송 직전 양시계 만료를 검사한다.
- HumanControl은 A가 인증된 사람 제어 큐에만 연결해야 한다. 모델 도구로 노출하지 않는다.

## 합의 기록

현재 합의 기록은 없다. 아래 표는 실제 검토 후에만 채운다.

| 날짜 | 변경 대상 / 커밋 | 결정과 영향 | A 확인 | B 확인 | C 확인 |
| --- | --- | --- | --- | --- | --- |

해결되지 않은 사항은 미확정 상태로 남기고, 구현 중 발견한 설계 충돌은 근거 절과 영향을 적어 합의한다. 제공받은 원본 사본을 조용히 고쳐 합의된 것으로 만들지 않는다.

## 2026-10-13 A 리뷰 수정 연결

- B 기준 `f22968c`: `GuardService.submit(..., expected_epoch=runner.epoch)`를 연결한다.
- 사람용 `HumanControl.review(approval_id)`는 같은 메모리 승인 사본만 반환한다.
  운영자·상태·만료·해시·Task 맥락을 검증하며 일반 모델/API에 노출하지 않는다.
- Guard와 ApprovalService 내부 stop 협력 함수는 잠금 이전 요청 기록과 예약 전 재검사를 연결한다.
- A bridge 내부 TaskProjection·ApprovalReview는 불변이고 command/Task/epoch로 오래된 응답을 배제한다.
  domain·ports 공통 DTO를 동결하거나 C 조회 API를 대신한 것이 아니다.
- SQLite·영속 감사는 B6 소유다. `agt_mode`와 rule version의 영속 저장은 B6 계약/스키마 후속이다.
- AGT 입력 최소화·경계·규칙 담당·timeout의 제안은 [리뷰 반영 기록](tasks/a-review-fixes.md#agt-경계와-남은-결정)을 따른다.
- 사용자 요청으로 A 개발 명세·lock을 추가했다. 실제 AGT/Scanner까지 검증한 제품 lock은 아니다.

이 절은 이번 사용자 수정 요청과 구현 연결을 기록한다. A·B·C의 별도 합의 서명은 여전히 미확정이다.
