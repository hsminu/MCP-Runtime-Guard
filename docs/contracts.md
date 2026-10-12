# 공통 계약 체크리스트

담당은 B, 사용자는 A·C다. `domain/*`와 `ports.py` 변경은 3인 합의 대상이다. 이 문서는 [개발문서](technical-design.md) 0.5·3·7–12장의 계약 요구를 정리한 **미확정 체크리스트**다. 실제 코드의 모든 메서드·반환형을 이미 정의했다는 뜻은 아니다.

## 담당 변경 기록

| 날짜 | 근거 | 확정한 배정 | 상태와 남은 항목 |
| --- | --- | --- | --- |
| 2026-10-12 | 사용자 명시적 결정: "sqlite b로 넘기자" | `storage/*`, SQLite·스키마·마이그레이션·시작 복구·영속 순번·감사 저장·저장소 단위 테스트는 B. 저장소 리뷰·독립 계약·통합·공격·결함 주입·실험은 C | 담당 확정, 이 작업 디렉터리 문서 반영. 원격 공유·B 착수 확인·API 세부 합의·실제 구현·인수는 별도 |

현행 소유권은 [협업 규칙](team-workflow.md), B의 작업은 [B6 · SQLite 인수](tasks/b-sqlite-store.md)를 따른다. C의 Registry·Scanner·위험 연산·정적 강제 규칙과 A의 시작 실행 게이트 배정은 해당 소유권 표를 따른다. 사용자 결정은 SQLite 담당 변경에 적용하며, 아래 API·DTO·상태·오류 제안 전체의 동의로 확대하지 않는다.

## 합의할 계약

| 계약 | 정의 담당 / 구현 담당 | 필수 의미 | 근거 |
| --- | --- | --- | --- |
| domain 모델 | B / B | 신뢰하지 않는 `ProposedCall`과 신뢰 해석 `ResolvedRequest` 분리, frozen·strict·extra forbid | 3장 |
| Guard 입구와 결과 | B / B | 일반 도구 실행은 `submit`; 결과 `Denied`, `Pending`, `Released`, `Withheld`, `OutcomeUnknown` | 0.5·7장 |
| Transport | B / A | 서버별 세션 소유 worker, 명령 future의 성공·예외 확정, 원시 결과 격리, 쓰기 자동 재전송 금지 | 9장 |
| AccessChecker | B / A | 요청·신뢰 맥락 입력, `AccessResult` 반환, 거절·오류는 DENY, 형식 불명·시간초과는 `error=True` | 10.3절 |
| Scanner | B / C | 정의 metadata만 입력, 필수·완료·skip·failed 분석기와 지문 집합을 명시 | 10.2절 |
| Store | B / B, C 독립 검증 | 상태·감사·예약·승인 조회와 아래 세 원자적 연산 | 8.3·11장 및 담당 변경 기록 |
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

C가 작성한 [Store 계약 요구 초안](tasks/c-store-requirements.md)은 세 연산의 입력 의미·관계 검사·rollback·실패 전달과 후속 검증 기준을 제안한다. B 검토·A·B·C 합의는 아직 없으며, 특히 상태 전이의 `security_version`, 소비 감사 원자성, SEND 결과·커밋 실패의 처리, 재시작·제어 경쟁은 초안 U1–U8의 미확정 항목이다. 이 링크 추가는 계약 확정이나 위 체크리스트 통과를 뜻하지 않는다.

C의 [Store 계약 처리 제안 v1](tasks/c-store-proposal.md)은 정상 승인 상태 전이의 보안 버전 유지, 승인 생성·종결 원자 연산, 커밋 불명 오류·취소, DB 처리 시점의 만료·시작 정리 규칙을 구체적으로 제안한다. 새 API·DTO·오류 이름과 시계·스키마 변경은 B 검토·A·B·C 합의 대상이다. 제안 작성과 실제 구현·검증·합의를 구분한다.

SQLite 이관 후에도 C 요구·제안 문서는 B의 설계 검토와 C의 독립 인수 기준으로 사용한다. Registry 상태·지문 영속화, 변경 시 승인 무효화와 단일 writer 사용 인터페이스는 B·C가 구체화하고 공통 계약 변경을 기록한다.

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

## 합의 기록

API 세부 사항에 대한 A·B·C 합의 기록은 없다. SQLite 담당에 대한 사용자 결정은 위 담당 변경 기록에 남겼으며, 아래 표의 동의를 대신 채우지 않는다. 아래 표는 실제 검토 후에만 채운다.

| 날짜 | 변경 대상 / 커밋 | 결정과 영향 | A 확인 | B 확인 | C 확인 |
| --- | --- | --- | --- | --- | --- |

해결되지 않은 사항은 미확정 상태로 남기고, 구현 중 발견한 설계 충돌은 근거 절과 영향을 적어 합의한다. 제공받은 원본 사본을 조용히 고쳐 합의된 것으로 만들지 않는다.
