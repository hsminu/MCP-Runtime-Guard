# 공통 계약 체크리스트

> 2026-10-13 반영: SQLite·영속 순번·감사 저장·시작 복구는 2026-10-12 사용자 결정에 따라 B6 소유, C는 독립 검증 담당이다. 이번 리뷰 수정·임시 앱 계약은 [리뷰 반영 기록](tasks/a-review-fixes.md)을 따른다. 기존 설계 코드 블록은 참고이며 구현 증거가 아니다.
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
  안전 ID는 C, 영속 순번·감사 저장은 B6가 맡으며 충돌 방지·내구성·재시작 의미를 확정해야 한다.
- Task WAITING 전이와 승인 생성은 현재 포트에서 별도 연산이다. 중간 실패는
  실행 없이 global halt로 처리하며, B6 원자 묶음 API는 A·C와 검토·확정해야 한다.
- WAITING↔RUNNING의 security_version 의미는 미정이다. WAITING 반환 상태로
  사본을 고정하고 소비 후 버전 유지·1 증가를 지원하되 다른 보안 맥락은 같아야 한다.
- consume_and_reserve는 정확한 bool ACK, 실제 트랜잭션 시점의 UTC 만료,
  취소·내구성 커밋 ACK 의미를 보장해야 한다. Guard는 승인 소비 완료 후에도
  전송 직전 양시계 만료를 검사한다.
- HumanControl은 A가 인증된 사람 제어 큐에만 연결해야 한다. 모델 도구로 노출하지 않는다.

## 2026-10-13 B 검토안과 구현 상태

[B6 계약 변경안](b6-contract-proposal.md)에 현재 Store 12개 연산과 C U1–U8,
A의 상태·제어·승인 상세 요청을 대조했다. 원자 승인 API·DTO·오류·버전·복구는
공동 검토용 제안이며 아래 합의 표의 동의를 대신하지 않는다.

같은 날 사용자는 **B6는 A·C 검토를 받은 뒤 확정**하도록 결정했다.
새 API·DTO·오류·버전·복구 규칙은 [변경안 10절](b6-contract-proposal.md)의 회신 후
실제 합의 근거를 기록한다. 검토 자료 작성과 다른 clone에 전달된 상태는 구분한다.

회신할 항목·목적·담당별 질문·답변 형식은 [A·C 공동 계약 검토 요청](ac-contract-review-request.md)에
모았다. 기존 도메인·Store 12개 연산의 의미를 유지하는 독립 SQLite 준비·구현은 먼저 진행할 수
있으며, 이 순서를 새 공유 API/DTO/오류/상태 규칙을 미리 확정하는 근거로 사용하지 않는다.

정규식은 사용자의 기술설계·GitHub 문서 기준 판단 요청에 따라 B가 **기능 유지와
전체 JSON Schema 검증의 종료 가능한 프로세스 실행**을 선택했다.
[검증 연결안](b-schema-validation-proposal.md)에 A 모델 입력·B Catalog/Guard/승인·C launcher의
연결 범위와 리뷰 질문을 제공했다. 공동 API·실행 예산·실제 수정/인수는 검토 대기다.

| 피드백 | B 상태 | 다음 인수 |
| --- | --- | --- |
| 일반 submit의 호출자 기대 epoch | 서비스에 선택적 `expected_epoch`를 추가하고 Task lock 내부에서 ID 확인 직후 검사. A `c9f6e1a` Runner의 전달 코드 확인. domain·ports 변경 없음 | 최신 B 보완 동기화 후 A 재검증, C 실제 교체 경합·전송 0 검증 |
| 권위 상태·revision·제어 적용 결과 | B6 변경안의 승인/요청/Task 조회·접수/적용 분리 제안 | A·C 계약 검토, 실제 저장소와 UI 연결 |
| 동일 메모리 승인 사본 상세 | 현재 `HumanControl.review(approval_id)` 구현. strict 승인 모델·ID, Task ID/epoch·PENDING·hash·현재 등록·양시계 만료와 메모리 사본을 검사 | A 표시·접근 수명 인수. B6의 revision 지정 조회는 별도 공동 제안 |
| 긴급 차단·늦은 AGT 정리 | 인증 후 lock 전에 정지 요청 기록, 소비·예약·전송 직전 재검사. 실제 AGT 작업 완료 전 추적·실행 슬롯 유지 | A 실제 어댑터 취소·종료, C 소비·dispatch·wire 계수 검증. 같은 Task ID/epoch 복구 규칙 미정 |
| 승인 생성·종결 원자성 | open/finish 및 반환 관계 DTO 제안 | 공통 API 합의 후 B6 구현·C 독립 검증 |
| 등록 스키마 정규식 | B 처리 방향은 기능 유지·전체 검증 프로세스 실행. 연결안 제공, 제품 소스는 유지 | A 직접 검증·C launcher·공동 비동기/실패 계약 검토 후 구현·인수 |
| 실제 AGT 규칙·버전·감사 | A `c9f6e1a`의 로컬 판정 연결 확인. 제품 규칙·policy_version/승인 결속·agt_mode/규칙 버전 영속 감사는 추가 검토안 | AGT-01–03 회신, B6 저장·마이그레이션과 C 독립 계수 인수 |
| 공통 설치 조합 | A jsonschema 4.25.1, B 4.26.0 차이 확인. A의 AGT·HTTP·Qt 조합과 B 검사 도구를 대조 | ENV-01 회신, 통합 버전·lock 담당과 Scanner 포함 인수 환경 결정 |

submit의 생략 기본값은 기존 호출과 호환되는 `None`이며, 기대 epoch가 다른 호출은
`Denied(TASK_NOT_RUNNING)`을 반환한다. 새 epoch의 Task를 정지·수정하거나 요청을 만들지 않는다.
신뢰 epoch의 bool·음수·float·문자열은 `Denied(STATE_MISSING)`이다. 모델 DTO에는 epoch를 추가하지 않는다.
A `c9f6e1a`에서는 Runner가 `expected_epoch=self.epoch`를 전달한다. 코드 연결 확인과
실제 epoch 교체 경합·C 독립 T21 인수는 구분하며, 이번 문서 갱신을 인수 통과로 표시하지 않는다.
2026-10-13 B에서는 관련 회귀 22개와 기존 테스트를 포함한 886개 단위·성질 테스트,
Ruff·포맷·mypy·pip check를 통과했다. 실제 MCP·SQLite·wire·수신 부작용 검증은 별도다.

이후 사용자의 반영 지시로 B `f22968c`에 A `655aef5`의 긴급 차단·승인 상세를 선별 반영한
B 브랜치 변경을 준비했다. 상세는 마지막 await 뒤 UTC·monotonic 만료와 사본 현재성을 재확인하며,
요청한 승인 ID와 Store 반환 ID를 대조한다. 조회는 메모리 원문 사본만 반환하며 승인 소비·
Store 상태 쓰기를 하지 않는다. 만료·정지·조회 취소는 `None`, 깨진 Store·모델·Task 관계는
원문 없이 `None`과 global halt로 봉쇄한다.

승인 조회·전이 결과의 ID는 승인·거절·만료·무효화·소비 실패 재조회·오류 정리에서도 검사한다.
다른 ID를 반환하면 추가 승인/Task 행 변경·소비·전송 없이 global halt하고 해당 Task의
메모리 사본만 폐기한다. 정상 전이가 이미 커밋된 뒤 잘못된 DTO가 반환되면 전이를 되돌리지
않고 실행을 차단한다. 관계없는 승인 행과 다른 Task의 메모리 사본은 변경하지 않는다.

취소 후 끝나지 않은 AGT가 있으면 새 검사를 생성하지 않고 접근 오류로 거부하며 실제 완료 후
슬롯을 회수한다. 기본 동시 검사 상한은 `max_access_checks=4`, 정리 대기는
`access_cleanup_timeout_seconds=0.05`다. 측정으로 최적화한 값이 아니며 A 어댑터와 검토해야 한다.
정지 latch는 Task ID에 남으므로 재시작에는 새 Task ID를 사용한다. 같은 ID의 epoch 재사용은
B6 복구 계약에서 아직 미정이다. 이번 반영은 B6·정규식 새 공동 계약의 동의를 뜻하지 않는다.

이번 변경 검증은 관련 **250개(1.36초)**, 전체 **971개(4.21초, 기존 886개+85개)** 통과다.
세 반례의 회귀를 포함했고 Ruff·45개 Python 파일 포맷·24개 소스 strict mypy·pip check·diff 검사도
통과했다. `domain/*`·`ports.py`·최종 기술설계·의존성 lock은 유지했다.
사용자는 이번 B 브랜치 변경의 commit·push를 승인했다.
B 최신 코드·SQLite·C 서비스가 함께 연결된 제품과 C 독립 인수는 미완료다.

## 2026-10-13 추가 요청: 최신 A와 AGT 검토

검토 기준은 A `c9f6e1a`, B `29025e0`, C/main `4179f98`다. A는 실제 로컬 AGT와
HTTP loopback을 구현했지만 B `29025e0`의 승인 ID·상세 만료·늦은 AGT 추적 보완은
A 사본에 아직 없다. 최신 B 동기화 후 AGT real/mock × stdio/HTTP의 재검증을 요청한다.
A의 1,282개 테스트·HTTP 앱 각 모드 8경로는 A 문서의 실행 기록이며 이번 B 재현 결과가 아니다.

A가 요청한 AGT mode/규칙 버전의 영속 감사와 규칙 변경/승인 결속은
[B6 변경안 11절](b6-contract-proposal.md)의 검토 제안이다. 현재 불변 합성 규칙을 제품 정책
인수로 확대하거나 A의 NOT NULL 감사 필드 제안을 공동 합의로 간주하지 않는다.
기존 불명 감사 기록을 real/mock으로 추정하지 않으며 정확한 migration·필드·실패 처리를 검토한다.

아래 표는 **검토 요청 관리**이고 합의 기록이 아니다. 실제 회신이 오면 항목별 상태를 갱신하고,
확정한 선택·영향·검토자·날짜·문서/커밋만 아래 합의 기록에 남긴다.

| 요청 ID | 검토 대상 | A 회신 근거 | C 회신 근거 | 현재 상태 |
| --- | --- | --- | --- | --- |
| B6-01 | 승인 생성·종결 원자 API/DTO | 미기록 | 미기록 | 요청 초안 |
| B6-02 | 상태·보안 버전·객체 revision·요청 관계 | 미기록 | 미기록 | 요청 초안 |
| B6-03 | 내구성 ACK·충돌·커밋 불명·만료·취소 | 미기록 | 미기록 | 요청 초안 |
| B6-04 | SEND 실패 상태·시작 정리·recovery epoch | 미기록 | 미기록 | 요청 초안 |
| B6-05 | 권위 조회·사람 상세·명령 접수/적용 결과 | 미기록 | 미기록 | 요청 초안 |
| B6-06 | Registry 등록·지문 영속화·단일 writer | 미기록 | 미기록 | 요청 초안 |
| AGT-01 | 제품 규칙·버전·Task/승인 결속 | 미기록 | 미기록 | 요청 초안 |
| AGT-02 | mode/규칙 버전 감사·migration·실패 | 미기록 | 미기록 | 요청 초안 |
| AGT-03 | 상한·취소·늦은 SDK 작업·종료 미확인 | 미기록 | 미기록 | 요청 초안 |
| SCHEMA-01 | 전체 스키마 검증과 C launcher 연결 | 미기록 | 미기록 | 요청 초안 |
| ENV-01 | 통합 버전·lock·검사 도구·Scanner 조합 | 미기록 | 미기록 | 요청 초안 |

답변 형식은 [중앙 요청 6절](ac-contract-review-request.md)을 따른다. 이번 작업은 요청 문서
준비이며 실제 전달·A/C 동의·새 공유 계약 구현·SQLite 착수/제품 검증 완료를 뜻하지 않는다.

## 합의 기록

2026-10-13 A는 B `3bf414c`의 코드·검토 요청을 병합했다.
[A 동기화 기록](tasks/a-b-sync-2026-10-13.md)은 현재 코드·환경·검증과 AGT 기준 차이를 기록한다.
동기화는 위 요청 ID의 동의가 아니며 공통 API·상태·환경 합의 표는 미확정으로 유지한다.

API 세부 사항에 대한 A·B·C 합의 기록은 없다. SQLite 담당에 대한 사용자 결정은 위 담당 변경 기록에 남겼으며, 아래 표의 동의를 대신 채우지 않는다. 아래 표는 실제 검토 후에만 채운다.

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
