# A·C 공동 계약 검토 요청

> A 동기화 후속: 아래 B 요청의 A 기준은 `c9f6e1a`다. A `281cbae`의 AGT 대기열·worker 교체와
> 이번 B 병합 상태는 [A 동기화 기록](tasks/a-b-sync-2026-10-13.md)을 참고한다.
> 요청 문서를 A에 반영했으며 계약 검토 회신·동의는 아직 아니다. 아래 B 작성 당시 근거는 보존한다.

기준일: **2026-10-13**. 작성 B, 상태는 **요청 초안 작성·A/C 회신과 공동 합의 미기록**이다.
사용자 요청으로 최신 A 구현을 대조하고 회신할 항목을 모았다. 사용자는 이번 요청 문서의 B 브랜치 commit·push를 승인했다.
문서 작성으로 팀원에게 전달·동의·SQLite 구현·제품 연결·독립 검증 완료를 기록하지 않는다.

## 1. 검토 기준과 현재 연결 상태

| 담당 | 확인한 기준 커밋 | 이번 검토에서 확인한 상태 |
| --- | --- | --- |
| B | [29025e0](https://github.com/hsminu/MCP-Runtime-Guard/commit/29025e038f7e4beb6cdcce15305f8cafc9550897) | 승인 ID·상세 조회 마지막 만료 검사·AGT 작업 추적 보완. 기존 B 단위·성질 테스트 971개 통과 기록 |
| A | [c9f6e1a](https://github.com/hsminu/MCP-Runtime-Guard/commit/c9f6e1ac37188ac1af9f9f70a9b43a9e4393c8c3) | 실제 로컬 AGT core 5.0.0, Streamable HTTP loopback, Runner의 trusted expected_epoch 전달 구현 |
| C / main | [4179f98](https://github.com/hsminu/MCP-Runtime-Guard/commit/4179f98b476b41e806c4a87b879cbdf95dc92b54) | SQLite 구현 B 이관·C 독립 검증 배정과 기존 Store 요구/처리 제안 |

A의 최신 두 변경은 AGT·HTTP 연결이며 B `29025e0`의 Guard·승인 보완은 아직 A 사본에 없다.
현재 A/B의 `domain/*`·`ports.py`는 동일하다. A는 B 보완을 동기화한 뒤 실제 AGT real/mock과
stdio/HTTP 조합을 재검증하고 C는 요청별 소비·host dispatch·wire·수신 부작용을 독립 확인해야 한다.
이는 새 코드가 연결 완료됐다는 기록이 아니라 인수 요청이다.

A의 **1,282 passed**, AGT real/mock HTTP 앱 각 8경로는
[A HTTP 연결 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/c9f6e1ac37188ac1af9f9f70a9b43a9e4393c8c3/docs/tasks/a-http-integration.md)에 있는 A 자체 실행 결과다.
B가 이번 문서 작업에서 재현했거나 C가 독립 인수한 결과로 사용하지 않는다.
상세 연결 위치와 기존 검증은 [B 인수 안내](b-handoff.md)를 따른다.

## 2. 합의 범위와 SQLite 착수 경계

SQLite·스키마·마이그레이션·단일 writer·영속 순번·감사·복구·저장소 단위 테스트는 B,
리뷰와 독립 계약·통합·결함 주입·공격 검증은 C가 맡는 것으로 이미 결정됐다.
이 배정과 최종 기술설계의 SQL 바인딩·WAL·외래키·내구성 요구를 다시 투표하지 않는다.

기존 도메인·Store 12개 연산의 의미를 유지하는 SQLite 준비·구현·B 단위 검증은 먼저 진행할 수 있다.
새 공유 API·DTO·오류·상태/버전·복구 규칙을 확정하거나 호출부를 교체하는 단계는 A·C 검토 후다.
기존 두 커밋으로 조립한 승인 생성/종결을 새 원자 연산의 완료로 표시하지 않는다.
이번 요청은 문서 정리이며 SQLite 소스를 구현한 작업은 아니다.

## 3. B6: 먼저 회신할 항목

아래 권장안은 [B6 계약 변경안](b6-contract-proposal.md)의 검토 제안이다.
API 이름·필드·오류·상태를 이번 표로 확정하지 않는다. 각 ID에 동의 또는 구체적인 수정안을 남겨 줘.

| ID / 목적 | B 제안과 결정할 내용 | A가 확인할 것 | C가 확인할 것 | 상세 근거 |
| --- | --- | --- | --- | --- |
| B6-01 · 부분 승인 저장 방지 | open_approval/finish_approval로 승인·요청·Task·감사를 함께 저장하고 ApprovalCommit의 관계를 검증. 현재 별도 연산을 교체할 API·반환 DTO 결정 | 승인 대기·거절·만료·취소 처리와 화면 갱신에 필요한 반환값 | U3/U4/U8, 중간 실패 rollback·행 수·ID/epoch/revision 검사·원문 비저장 | B6 2·3·5절 |
| B6-02 · 오래된 실행 권한 방지 | 정상 RUNNING↔WAITING에서는 보안 버전 유지, 실제 보안 변화는 트랜잭션당 +1 제안. 객체별 row_revision과 요청 epoch/kind/revision 결정 | Runner·화면이 참조할 버전과 오래된 명령/응답의 배제 | U2/U3, snapshot 일치·다른 Task/epoch·CAS 충돌·terminal 재개 금지 | B6 4절 |
| B6-03 · 저장 완료/불명과 만료 구분 | 정확한 bool/DTO ACK, 충돌·확인된 rollback·COMMIT/ROLLBACK 불명 오류 구분. fresh UTC·잠금 후/커밋 전 만료·취소 의미 결정 | 처리 완료를 언제 표시할지, 예약 불명과 READ 미공개/SEND 결과 불명의 표시·재제출 금지 | U1/U4/U6, 실제 감사 실패·잠금·만료 경합·취소·커밋 불명 검증, 오류 원문 비공개 | B6 6절 |
| B6-04 · 재시작 후 옛 승인/쓰기 재실행 방지 | 시작 정리 후 실행 개방. 잔존 승인 폐기·DISPATCHING READ 미공개/SEND 결과 불명 제안. 새 Task ID와 기존 ID epoch 증가의 기록 방식, SEND 실패 상태 쌍 결정 | 시작 게이트·새 Task 대화/캐시/세션 격리·정리 실패 표시 | U5/U7, 프로세스 중단 복구·누적 등급 보존·옛 요청 결속 금지·추가 전송 0 | B6 8절 |
| B6-05 · 화면과 실제 상태 일치 | 일관된 승인/요청/Task 조회, 객체별 revision, 명령 접수와 실제 적용 결과의 분리. 현재 HumanControl.review와 새 revision 지정 조회 제안을 구분 | 사용할 API/필드·사본 표시 수명·오래된 이벤트 처리·낙관적 완료 표시 방지 | 권위 상태와 DTO 관계·신원·원문 공개 경계·중복 제어·경합 검증 | B6 7절 |
| B6-06 · 등록 정보와 실행 권한 일치 | C Registry의 등록/지문/검사 메타데이터를 B 단일 writer로 저장하고 변경 시 PENDING·승인 무효화. 쓰기 인터페이스·트랜잭션 범위 결정 | 검증된 연결 설정과 ACTIVE 목록을 구성 루트/화면에 제공할 필드 | Registry 서비스가 별도 SQL 경로를 만들지 않는 연결, 지문 변경/승인 소비 경쟁·S03 강제 규칙 | contracts 저장소 원자성, C U1–U8 |

## 4. 최신 AGT: 추가 회신할 항목

근거는 [A 실제 AGT 연결](https://github.com/hsminu/MCP-Runtime-Guard/blob/c9f6e1ac37188ac1af9f9f70a9b43a9e4393c8c3/docs/tasks/a-agt-integration.md)과
[A의 AGT 감사/담당 제안](https://github.com/hsminu/MCP-Runtime-Guard/blob/c9f6e1ac37188ac1af9f9f70a9b43a9e4393c8c3/docs/tasks/a-review-fixes.md#agt-경계와-남은-결정)이다.
현재 실제 SDK 입력은 server/tool/종류를 해시한 고정 키이며, 복사한 합성 규칙은 불변이다.
현재 동작은 등록 도구 사용 허용 목록이고 본문 기반 프롬프트 주입/유출 탐지 인수는 아니다.
아래는 제품 규칙·영속 저장을 연결할 때 결정할 항목이며 현재 합성 앱의 확정 취약점으로 기록하지 않는다.
상세 선택안은 [B6 변경안 11절](b6-contract-proposal.md)을 따른다.

| ID / 목적 | 회신할 결정 | A가 확인할 것 | C가 확인할 것 |
| --- | --- | --- | --- |
| AGT-01 · 규칙과 Task/승인 결속 | 제품 규칙 의미·버전 발급/로딩 책임, AGT mode/규칙 변경을 policy_version에 포함할지 별도 결속할지. 변경 시 기존 Task·승인의 종결과 새 Task 개방 조건 | 신뢰 bootstrap에서 검증된 규칙/버전을 공급하고 SDK 번역. 현재 rule_version과 Task policy_version은 별도이므로 연결 방법 제시 | 규칙 로더/안전 경로 책임·위조된 버전/입력 배제·규칙 변경/승인 경쟁·ASK/DENY 완화 및 이중 승인 방지 |
| AGT-02 · 실제/모의 판정의 영속 감사 | agt_mode·규칙 버전의 저장 위치/사건·전달 필드·누락/시작 실패·마이그레이션·감사 실패 처리. A의 NOT NULL 제안 수용 범위 결정 | 초기화 확인된 신뢰 실행 맥락의 mode/버전을 공급. 모델/도구 출력에서 추론하지 않음. mock 규칙 버전의 공급도 제시 | 새 사건의 필수 맥락·기존 불명 기록 구분·원자 감사 실패 rollback/격리·본문/전체 인자/SDK reason 비저장 |
| AGT-03 · timeout/취소 뒤 추가 실행 방지 | Guard 전체 상한과 더 짧은 adapter 판정 상한, 동시/대기 상한·늦은 결과 폐기·종료 미확인 처리와 종료 순서 | adapter의 SDK 작업 완료 추적·busy/닫힘 반환·준비/종료 확인. 취소된 coroutine과 남은 SDK 스레드 수명을 구분 | 거절/오류/timeout/형식 불명·정리 미확인에서 소비/dispatch/wire/부작용 계수, 정상 제어 응답성 |

B의 기본 동시 검사 상한 4개·취소 정리 대기 0.05초와 A adapter의 미완료 판정 1개는 서로 다른
경계다. A 합성 구성은 Guard 2초·adapter 1초를 사용한다. 이 값을 제품 SLO나 측정 최적값으로
확정하지 않으며, coroutine 종료를 SDK 스레드 강제 종료 성공으로 보고하지 않는다.
원격 AGT 입력·외부 수신 경계·추가 승인/최소화는 이번 로컬 계약과 별도 검토 대상이다.

## 5. 정규식 검증과 공통 환경

| ID / 목적 | 요청할 결정 | 담당별 회신 |
| --- | --- | --- |
| SCHEMA-01 · 검증 중 앱 정지 방지 | 전체 PREPARE/VALIDATE를 종료 가능한 프로세스에서 수행하는 API/DTO·IPC·입출력/동시성 상한·deadline·취소/종료 회수와 실패 상태 | A: 모델 인자 변환·ToolView 준비/검증 분리·비동기 주입. C: launcher/Registry·지원 OS 제한·종료/회수 API와 독립 계수. B: 검증 의미·Catalog/Guard/승인 연결 |
| ENV-01 · 같은 조합에서 연결 검증 | A jsonschema 4.25.1과 B 4.26.0의 통합 버전, lock 관리·B strict 검사 도구 유지, AGT/MCP/Qt/Scanner 조합 | A: 앱 조합과 lock 재현안. C: Scanner 포함 공통/분리 환경·정식 fixture/CI 검증안. B: 코어 회귀/타입 검사와 의존성 영향 |

SCHEMA-01 상세는 [정규식 검증 연결안](b-schema-validation-proposal.md)을 따른다.
처리 방향 선택과 실제 launcher 연결/문제 해결을 구분한다. 예약 전 실패는 실행 권한을 만들지
않고, 예약/승인 소비 후 마지막 검증 실패는 READ Withheld/HALTED·SEND OutcomeUnknown으로
격리한다. 소비 1·실제 전송 0인 경우에도 이미 소비한 승인을 복원하거나 자동 재시도하지 않는다.

HTTP adapter의 재전송 제한은 A, endpoint/DNS/IP/Registry 검증·수신 상한과 독립 wire 인수는 C가
맡는다. B는 이를 복제하지 않고 판정·승인·영속 감사의 연결 계약을 제공한다.
통합 버전·lock 변경은 ENV-01에서 검토하고 각 브랜치의 기존 검증 근거와 담당 도구를 보존한다.

## 6. 회신 형식과 확정 절차

A·C는 관련 ID마다 아래 내용을 검토 문서·PR 또는 공통 계약에 남겨 줘.
부분 동의라면 동의한 범위와 미결 항목을 구분한다. 빈 칸·무응답은 동의로 처리하지 않는다.

- 항목 ID와 판단: 동의 / 수정 요청 / 미결
- 선택한 API·필드·상태/버전·오류/시계 규칙 또는 구체적인 대안
- 자기 코드에서 필요한 변경과 상대 담당에게 필요한 입력/반환값
- 독립 검증 조건·관련 I/T/S ID와 실행했을 경우의 결과/한계
- 검토자·날짜·기준 문서/커밋과 남은 질문

B는 회신을 대조해 제안을 갱신하고 실제 A·B·C 확인 근거만 [contracts.md](contracts.md)에 기록한다.
그 뒤 합의된 공유 모델·포트·호출부를 변경하고 SQLite 원자 연산과 B 단위 검증을 연결한다.
C 독립 계약/결함 검증과 A 제품 연결 후 실제 계수를 확인한 뒤 인수 상태를 갱신한다.

현재 기록 상태: **요청 초안 작성, A/C 검토 회신·동의·전달 완료 근거 없음**.
이번 문서의 B 브랜치 공유 승인은 A/C의 계약 동의와 구분한다. 실제 회신 근거에 따라 상태를 갱신한다.
