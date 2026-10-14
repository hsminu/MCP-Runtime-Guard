# A·C 공동 계약 검토 요청

> A 동기화 후속: 아래 B 요청의 A 기준은 `c9f6e1a`다. A `281cbae`의 AGT 대기열·worker 교체와
> 이번 B 병합 상태는 [A 동기화 기록](tasks/a-b-sync-2026-10-13.md)을 참고한다.
> 요청 문서를 A에 반영했으며 계약 검토 회신·동의는 아직 아니다. 아래 B 작성 당시 근거는 보존한다.

기준일: **2026-10-13**. 작성 B, 상태는 **요청 초안 작성·A/C 회신과 공동 합의 미기록**이다.
초기 요청 문서는 B `3bf414c`로 공개했다. 기존 A `cd75bf9` 검토 후 추가 기준은
A `ab82919`, B SQLite 공개 기준 `9eb0560`이다. 후속 QA 요청과 B 내부 보완은
[B QA 반영 기록](b-qa-fixes-2026-10-13.md)을 따른다.
이번 순번·복구 설계의 코드 기준은 공개된 B `6115fa6`이다.
[상세 설계](b6-sequence-recovery-design.md)·[검증 계획](b6-sequence-recovery-validation.md)의
추가 회신 항목은 아래 7절에 연결했다. 최초 설계 준비는 문서 작업이고,
후속 사용자 지시로 [B 내부 사전 점검/단위 검증](b6-startup-preflight.md)만 추가한다.
새 shared API·계수 schema·배정·상태 복구·제품 연결은 구현하지 않는다.
문서 작성으로 팀원에게 전달·동의·제품 연결·독립 검증 완료를 기록하지 않는다.

## 1. 검토 기준과 현재 연결 상태

| 담당 | 확인한 기준 커밋 | 이번 검토에서 확인한 상태 |
| --- | --- | --- |
| B | [6115fa6](https://github.com/hsminu/MCP-Runtime-Guard/commit/6115fa65236b58250e9ee9ca2e3d686130e35416) | 기존 SQLite 12/12·후속 QA 보완 공개·전체 1,614개 통과. 이번 순번/복구는 설계·50개 시나리오 준비이며 구현·공유 계약 동의·제품 배선은 별도 |
| A | [ab82919](https://github.com/hsminu/MCP-Runtime-Guard/commit/ab82919cbdc55492e1604a5aec41fec4fbedb33b) | B 문서 5f8a003 병합. 후속 Runner·transport·AGT·UI QA 수정과 B 소유 요청 10개 확인. SQLite 9eb0560과 이번 QA 보완은 아직 병합되지 않음 |
| C / main | [4179f98](https://github.com/hsminu/MCP-Runtime-Guard/commit/4179f98b476b41e806c4a87b879cbdf95dc92b54) | SQLite 구현 B 이관·C 독립 검증 배정과 기존 Store 요구/처리 제안 |

아래는 기존 `cd75bf9` 검토 당시 기록이다. 이번 QA 보완 후 Guard·승인 소스가 A와 동일하다는 뜻이 아니다.
당시 추가 대조는 이전 A 기준 이후의 `6452b13`(AGT), `91b966b`(UI), `281cbae`(QA/역할 문서),
`cd75bf9`(최신 B 병합과 연결 검증)다. 실제 로컬 AGT·HTTP·Runner expected_epoch 전달은 유지된다.
A `cd75bf9`에 B `3bf414c` 이력이 병합됐으며 B 서비스 `29025e0`의 승인 ID·상세 조회 마지막
만료/현재성·늦은 AGT 추적 보완도 반영됐다. 당시 A/B의 `domain/*`·`ports.py`·Guard·승인
서비스 소스가 동일함을 정적 대조했다. [A 동기화 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-b-sync-2026-10-13.md)도 확인했다.
당시 코드 동기화는 완료됐지만 새 공동 계약 동의와 C의 요청별 소비·host dispatch·wire·수신
부작용 독립 인수는 미완료다. 실제 SQLite·C 서비스·모델의 제품 연결도 별도다.

A 최신 동기화 기록은 **A lock 전체 1,371 passed(48.87초)**, **별도 B lock 환경의 B 소유
테스트 971 passed(18.83초)**와 **AGT real/mock × stdio/HTTP의 4조합·각 8경로·총 32경로**를 보고한다.
실제 SDK adapter와 대역 Store/transport를 사용하는 새 Guard 시험은 동시 4개 허용·5번째
AGT_ERROR/transport 0을 확인한다. 실제 wire·승인 소비 검증은 아니다.
이전 [AGT QA 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/281cbae8f34d773b854d1d7d46cd49af6f515a44/docs/tasks/a-agt-integration.md#2026-10-13-qa-지적-반영)의
1,298 passed·87개 × 30회 실패0·stdio 각 모드 8경로와 HTTP 1,282 passed는 해당 시점의 기록으로 보존한다.
이번 B 작업은 코드·테스트·문서 정적 대조이며 위 시험을 재현하거나 C 독립 인수로 확인하지 않았다.
상세 연결 위치와 기존 검증은 [B 인수 안내](b-handoff.md)를 따른다.

| 새 변경 | 확인한 구현 | 계약 요청에 미치는 영향 |
| --- | --- | --- |
| B 서비스 동기화 | A가 B 3bf414c를 병합했고 Guard·승인·domain·ports는 B와 동일 | 동기화 요청을 완료 상태로 수정. 새 API 합의와 C 독립 검증은 계속 필요 |
| AGT 동시성 | max_pending 기본 16, 현 worker가 순서 처리. 대기 취소는 슬롯 회수, 실행 중 취소는 실제 완료 또는 퇴역까지 별도 추적. A가 Guard 4슬롯 결합 단위 시험 추가 | AGT-03에서 대기 시간·취소/예산·실제 Store/wire를 C가 독립 검증 |
| AGT timeout/종료 | 멈춘 worker 퇴역·대기열 이관, 살아 있는 퇴역 worker 예산 기본 2. 소진/SDK 오류/교체 실패는 닫힘. close는 퇴역 스레드까지 실제 종료 확인 | 동일 실패 요청 재시도와 미평가 대기 요청 이관을 구분하고 예산 소진·종료 미확인을 인수 |
| UI 결과 표시 | 실행/승인 한국어 문구, RUNNING 입력 대기 안내, cancel/halt는 확정 HALTED/CLOSED 상태를 보고 표시. bridge 결과 코드 유지 | B6-05의 권위 조회·revision·접수/적용 결과 계약은 계속 필요 |
| AGT 역할 문서 | 운영자 차단 목록/조건 규칙 및 별도 서명 정책 관리 선택지. 구현·결정은 미확정 | AGT-01의 첫 회신을 역할 선택과 입력/관리 범위로 변경 |

## 2. 합의 범위와 SQLite 착수 경계

SQLite·스키마·마이그레이션·단일 writer·영속 순번·감사·복구·저장소 단위 테스트는 B,
리뷰와 독립 계약·통합·결함 주입·공격 검증은 C가 맡는 것으로 이미 결정됐다.
이 배정과 최종 기술설계의 SQL 바인딩·WAL·외래키·내구성 요구를 다시 투표하지 않는다.

기존 도메인·Store 12개 연산의 의미를 유지하는 SQLite 준비·구현·B 단위 검증은 먼저 진행할 수 있다.
새 공유 API·DTO·오류·상태/버전·복구 규칙을 확정하거나 호출부를 교체하는 단계는 A·C 검토 후다.
기존 두 커밋으로 조립한 승인 생성/종결을 새 원자 연산의 완료로 표시하지 않는다.
초기 요청 작성 때는 문서 정리만 진행했다. 이후 기존 포트 안의 SQLite 12/12 구현은
B `9eb0560`으로 공개했으며, 새 원자 승인 API·시작 복구·제품 배선·C 독립 인수는 남아 있다.

QA-03/10의 전역 정지 가시성은 B6-05·복구/해제 조건과 함께 검토한다.
QA-04의 확정 미전송 증거·결과 분류는 B6-03/04, QA-06의 일시 AGT 포화 구분·대기/TTL·
Runner 처리는 AGT-03에 추가해 A·C 회신을 받아야 한다.
이 연결은 검토 범위를 정리한 것이며 typed DTO·Reason·상태 규칙을 확정한 합의가 아니다.

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

현재 근거는 [A 실제 AGT·QA 연결](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-agt-integration.md)과
[AGT 역할·규칙 관리 결정 요청](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-agt-role-decision.md)이다.
이전 감사/담당 제안은 요청의 역사 근거이며 최신 역할 선택이나 공동 합의로 대신 사용하지 않는다.
현재 실제 SDK 입력은 server/tool/종류를 해시한 고정 키이며, 복사한 합성 규칙은 불변이다.
현재 동작은 등록 도구 사용 허용 목록이고 본문 기반 프롬프트 주입/유출 탐지 인수는 아니다.
아래는 제품 규칙·영속 저장을 연결할 때 결정할 항목이며 현재 합성 앱의 확정 취약점으로 기록하지 않는다.
상세 선택안은 [B6 변경안 11절](b6-contract-proposal.md)을 따른다.

| ID / 목적 | 회신할 결정 | A가 확인할 것 | C가 확인할 것 |
| --- | --- | --- | --- |
| AGT-01 · 역할·규칙과 Task/승인 결속 | A18-D의 운영자 차단 목록/조건 규칙 역할을 먼저 선택하고, 서명 정책 등 관리 방식은 별도로 결정. 기본 판정·신뢰 입력·버전/로딩 책임과 policy_version/승인 결속 | 선택지별 SDK 설정/입력·로더 조합과 현재 default deny/합성 규칙에서 필요한 실제 변경 제시 | 조건식/서명·안전 경로/키 관리 책임·설정 위조·규칙 변경/승인 경쟁·ASK/DENY 완화 및 이중 승인 방지 |
| AGT-02 · 실제/모의 판정의 영속 감사 | agt_mode·규칙 버전의 저장 위치/사건·전달 필드·누락/시작 실패·마이그레이션·감사 실패 처리. A의 NOT NULL 제안 수용 범위 결정 | 초기화 확인된 신뢰 실행 맥락의 mode/버전을 공급. 모델/도구 출력에서 추론하지 않음. mock 규칙 버전의 공급도 제시 | 새 사건의 필수 맥락·기존 불명 기록 구분·원자 감사 실패 rollback/격리·본문/전체 인자/SDK reason 비저장 |
| AGT-03 · 제한된 worker 교체와 실패 요청 격리 | Guard 4개/adapter 기본16개·퇴역 예산2개·대기 포함 deadline·늦은 결과 폐기·예산 소진/교체 실패·종료 미확인 처리 | 실패 요청을 재평가하지 않고 아직 미평가된 대기 요청만 이관. 현/퇴역 worker와 실제 SDK 수명을 추적하고 종료 확인 | timeout 대상 전송0·취소 대기 SDK 실행0·이관 요청 1회 평가·예산 상한·늦은 허용 폐기·전체 스레드 종료와 정상 제어 응답성 |

B의 기본 동시 검사 상한 4개·취소 정리 대기 0.05초와 A의 대기/실행 상한 16개·퇴역 worker
예산 2개는 서로 다른 경계다. A 합성 구성은 Guard 2초·adapter 1초를 사용한다. SDK 예외·교체
실패·퇴역 예산 소진 뒤에는 닫히며 허용/mock으로 전환하지 않는다. 처리 제한시간이 지난 같은
요청의 재시도와 아직 평가되지 않은 대기 요청의 이관을 구분한다. 제품 SLO·합의 값은 미정이고,
coroutine 종료를 SDK 스레드 강제 종료 성공으로 보고하지 않는다.

역할 결정은 AGT-01로 회신하되 A18-D의 3번(서명 정책 관리)은 1/2번의 역할과 함께 선택할
관리 방식이다. 운영자 차단 목록의 기본 허용이나 조건 규칙의 새 입력 필드는 현재 구현에
자동 적용하지 않는다. B가 정책 의미를, A가 SDK 번역/로더 조합을, C가 위험 설정 경로·서명/키
취급 인터페이스와 공격/계수 검증을 검토한다. 역할·입력 확장은 A/B/C와 사용자 결정 대상이다.
원격 AGT 입력·외부 수신 경계·추가 승인/최소화는 이번 로컬 계약과 별도 검토 대상이다.

## 5. 정규식 검증과 공통 환경

| ID / 목적 | 요청할 결정 | 담당별 회신 |
| --- | --- | --- |
| SCHEMA-01 · 검증 중 앱 정지 방지 | 전체 PREPARE/VALIDATE를 종료 가능한 프로세스에서 수행하는 API/DTO·IPC·입출력/동시성 상한·deadline·취소/종료 회수와 실패 상태 | A: 모델 인자 변환·ToolView 준비/검증 분리·비동기 주입. C: launcher/Registry·지원 OS 제한·종료/회수 API와 독립 계수. B: 검증 의미·Catalog/Guard/승인 연결 |
| ENV-01 · 같은 조합에서 연결 검증 | 현재 A 실행 lock(jsonschema 4.25.1)과 B 코어 검사 lock(4.26.0)은 분리. 공통 제품 버전·lock 관리·strict 검사 유지·AGT/MCP/Qt/Scanner 조합 결정 | A: 현재 분리 환경/config/b-core 보존안과 제품 재현안. C: Scanner 포함 공통/분리 환경·정식 fixture/CI 검증안. B: 코어 회귀/타입 검사와 의존성 영향 |

SCHEMA-01 상세는 [정규식 검증 연결안](b-schema-validation-proposal.md)을 따른다.
처리 방향 선택과 실제 launcher 연결/문제 해결을 구분한다. 예약 전 실패는 실행 권한을 만들지
않고, 예약/승인 소비 후 마지막 검증 실패는 READ Withheld/HALTED·SEND OutcomeUnknown으로
격리한다. 소비 1·실제 전송 0인 경우에도 이미 소비한 승인을 복원하거나 자동 재시도하지 않는다.

HTTP adapter의 재전송 제한은 A, endpoint/DNS/IP/Registry 검증·수신 상한과 독립 wire 인수는 C가
맡는다. B는 이를 복제하지 않고 판정·승인·영속 감사의 연결 계약을 제공한다.
현재 A는 B requirements.lock을 별도 검사 환경에 설치하고 B의 Ruff·strict mypy 설정을
config/b-core/pyproject.toml에 보존했다. A 실행 환경에 B lock을 덮어 설치하지 않는다.
공통 제품 버전·lock 변경은 ENV-01에서 검토하고 각 검증 근거와 담당 도구를 보존한다.
2026-10-14 `chore/integrate-a-b`가 ENV-01 제안으로 두 환경과 검사 설정을 루트 `pyproject.toml`·`uv.lock`으로 합쳤다([A+B 통합](integration-a-b.md)).

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

현재 기록 상태: **기존 요청 문서 공개, A18-D 새 결정 요청 확인, B 요청 ID별 A/C 회신·공동 동의 미기록**.
A18-D의 선택지 제시는 B6/AGT-01–03에 대한 동의 회신이 아니다. 실제 선택과 근거를 기록한 뒤 갱신한다.
사용자는 이번 최신 A 재검토 문서의 B 브랜치 commit·push를 승인했다.
문서 공유 승인은 A/C의 계약 동의나 SQLite·정규식 구현 승인으로 확대하지 않는다.

## 7. 영속 순번·재시작 복구 추가 회신

[상세 설계 8절](b6-sequence-recovery-design.md)에 A/C 질문과
상위 B6 계약 연결을 정리했다. [50개 검증 계획](b6-sequence-recovery-validation.md)은 미실행이다.
다음 **8개 항목의 동의/수정안/미결**을 6절 형식으로 남겨 줘.

| ID | 회신할 내용 | A 영향 | C 영향 |
| --- | --- | --- | --- |
| SEQ-01 | Task ID 생애 단위 계수·신규 초기값·공백/회수 금지 | 새 Task/호출 번호 표시 | UNIQUE·여러 epoch·승격 순번 |
| SEQ-02 | 콜백/Store 입구·expected_epoch 여부·오류/배정 ACK·할당 감사 | bootstrap 주입·실패 표시 | 반환·COMMIT 불명·대역 계약 |
| SEQ-03 | 독립 계수 구조·schema 이관·v1 보장 한계/과거 백업 | 지원 DB/교체 절차 | seed·누락/손상·삭제 이력·migration 원자성 |
| REC-01 | 이전 실행자 종료·단일 DB 인스턴스 소유·시작 barrier | 모델/제어 개방·Runner/worker/AGT 종료 | 안전 소유권·동시 앱·전송 계수 |
| REC-02 | 상태 표·split 중단 상태·v1 kind 유실·종결 U8 충돌 | 오래된 승인 제거·READ 미공개/SEND 불명 | 관계/상태·파손과 crash 구분·추가 전송 0 |
| REC-03 | epoch 증가와 새 Task 복구의 영속 관계 기록 | Task/대화/캐시 격리·옛 명령 | old epoch 보존·반복 복구·범위 |
| REC-04 | 버전·반복 적용·run 표식·감사/UTC·RecoveryReport | ACK와 화면 projection 구분 | 원자성·중복/누출·시간 역행 |
| REC-05 | 전체 복구 TX·불명 ACK·예산·백업/미확정 메모리 | 실패 시 계속 닫기·재초기화 안내 | 프로세스 중단·잠금·성능/오류·지원 경계 |

기술설계의 승인 복원/결과 불명 SEND 재전송 금지·등급 보존은 재투표 대상이 아니다.
구체적인 epoch·종류 유실·API/스키마 선택을 공동 결정하고 [contracts.md](contracts.md)에
실제 합의 근거를 기록한 뒤 기능을 구현한다. 후속 B 내부 사전 점검기는 사실 관찰만 하고,
이 SEQ/REC 계약을 확정/대체하지 않는다. 최초 문서·후속 코드는 add로 준비했고,
이후 2026-10-13 사용자가 설계/점검기 묶음의 commit·push를 지시했다. 자료 공개와 계약 동의는 구분한다.
