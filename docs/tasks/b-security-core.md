# B · 보안 코어 작업 지침과 체크리스트

작업 브랜치는 `feat/b-security-core`다. **호출을 보내도 되는지 결정하고, 승인·결과 공개·Task 상태를 일관되게 집행하는 코어**를 맡는다. B1 계약 초안과 B2–B5 담당 서비스·단위 테스트를 구현했다. 2026-10-12 사용자 결정으로 SQLite 구현을 B6로 인수했다. 2026-10-13 연결·초기 v1 스키마/마이그레이션과 현행 Store **12/12개 메서드·전체 1,395개 검사**를 완료했다. 분리 승인 생성/종결 간격·시작 복구·영속 순번·새 API 합의·제품 연결·독립 인수는 남아 있다. 아래 구현 체크는 B 소유 코드 범위이며 A·C 계약 합의·실제 연결·인수 검증은 별도다.

현재 문서 대조 기준은 **A `ab82919`, B 공개 `6115fa6`**이다.
후속 QA 요청 **01·02·05·07·08**의 B 내부 수정·검증·남은 항목은
[B QA 반영 기록](../b-qa-fixes-2026-10-13.md)을 따른다. 아래 1,395개는 SQLite 추가 당시 기록이다.
후속 [QA-09 자원 수명 보완](../b-qa09-task-lifecycle.md)까지 구현했고,
최신 전체 검사는 **1,614 passed**, Ruff·포맷·mypy·의존성 검사 통과다.
후속 로컬 B 내부 사전 점검기 69개 추가 후 전체 **1,683 passed**와 동일 코드 검사를 통과했다.
회신 입구는 [A·C 계약 검토 요청 초안](../ac-contract-review-request.md)이다.
최신 A/B 상태와 담당별 질문을 모았으며 실제 전달·동의·새 검증 완료 기록은 아니다.

## 담당 범위

제품 경로는 향후 `src/guard_app/` 기준이다.

| 파일 / 영역 | 구현 책임 | 리뷰 |
| --- | --- | --- |
| `domain/*`, `ports.py` | 불변 모델, enum, reason_code, 전체 포트 계약 | A·C, 변경은 3인 합의 |
| `core/*` | 정책·결정표·등급 병합·승인 snapshot 구성 | C |
| `services/guard.py`, `services/catalog.py` | 유일 실행 입구·직렬화, 신뢰 설정 기반 요청 해석 | A |
| `services/approvals.py`, `services/response_gate.py` | 재평가·원자적 소비 연계, 격리·커밋 후 공개 | C |
| `security/failclosed.py`, `security/digest.py` | 예약 전후 예외 의미, 바이트 직렬화·SHA-256 | C |
| `storage/*` | SQLite·스키마·마이그레이션·원자 연산·영속 순번·감사 저장·시작 복구 | C |
| 담당 단위·성질 테스트 | 정책·모델·Catalog·승인 의미 검증 | 관련 리뷰 담당 |
| `docs/contracts.md` | 계약 초안·미확정 항목·3인 합의 기록 | A·C |

A의 transport·AGT 번역·Runner·GUI, C의 Registry·Scanner·위험 연산을 구현하지 않는다. SQLite·저장소 단위 테스트는 B가 구현하고 C가 리뷰·독립 계약·통합·결함 주입 검증을 맡는다. 현행 소유권은 [협업 규칙](../team-workflow.md), 추가 작업은 [B6 · SQLite 인수](b-sqlite-store.md)를 따른다. 필요한 연산은 공통 계약으로 검토하고 담당 구현을 사용한다. core의 외부 의존성을 추가하지 않는다.

## 먼저 읽을 설계

개발문서 0–8장 전체, 9.2·9.4절(실패·계수), 10.3절(AGT 분담), 11장(Store 트랜잭션), 13장(예외·해시·정적 규칙), 14.3·14.4절·부록 B를 읽는다.

## 작업 순서

### B1 · 공통 모델과 계약

- [x] `ProposedCall`과 `ResolvedRequest`를 분리하고 모델을 frozen·strict·extra forbid로 정의한다. 내부 dict·list의 실제 변경 가능성도 검토한다.
- [x] 모델·도구 출력에 task_id·등급·경계·승인자 결정권을 주지 않는다. Guard가 ID·epoch·call_seq를 관리한다.
- [x] Label·Decision·TrustTier·ToolKind·등록·Task·승인 상태와 reason_code 초안을 정의한다.
- [x] Guard 결과 타입, Transport·AccessChecker·Scanner·Store·ModelClient 전체 계약과 오류 타입을 초안으로 작성한다.
- [ ] A·C와 명령·이벤트 payload, 원시 결과 격리·공개 결과 구분, 계수 연결을 확정한다.
- [ ] policy_version·security_version·row_revision·fingerprint의 역할을 분리하고 `docs/contracts.md`에 3인 합의 근거를 기록한다.

모델·포트·버전 역할의 구현 초안은 [B1 문서](../b1-contract-draft.md)에 있다.
위 완료 표시는 B 초안의 구현 상태이며 enum·reason 의미와 공통 계약의 A·C 합의는 남아 있다.
실제 연결 위치·합의 항목·인수 순서는 [B 인수 안내](../b-handoff.md)에 정리했다.

### B2 · 순수 정책과 등급 누적

- [x] 모든 경계에서 max_label을 우선 검사하고 기본 행렬을 적용한다. 외부 max_label은 INTERNAL 이하만 로드한다.
- [x] READ에도 서버 경계를 적용하고 SEND에는 목적지 경계도 적용한다.
- [x] 서버·목적지·AGT의 가장 엄격한 결과를 합성한다. AGT 허용은 정책을 완화하지 않으며 거절·오류는 DENY다.
- [x] 등록·지문·Task 상태·미분류·정책 버전·목적지 전제조건을 다른 판정보다 먼저 검사한다. submit과 approval_recheck의 허용 상태를 구분한다.
- [x] `accumulate`를 기본으로 하고 last_read·none은 비교 모드로 둔다. 모드 차이는 effective_label 계산에만 둔다.
- [x] high-watermark 단조성과 Unclassified의 해제 금지를 구현한다. 보안 의미 변화에서만 security_version을 올린다.
- [x] 같은 입력의 결정성, 등급·경계 단조성, 거부 흡수, 전처리 우선 성질을 hypothesis로 검증한다.

구현 의미와 실제 단위·성질 검증은 [B2 구현·검증 문서](../b2-policy-and-taint.md)에 기록했다.
이 체크는 순수 코어 구현 범위다. C 담당 리뷰와 실제 수신 계수·저장소 검증은 인수 단계에 남아 있다.

### B3 · Catalog와 요청 해석

- [x] `server_id__tool_name` 네임스페이스와 고정 JSON Schema로 검증한다. ToolKind를 서버 annotation에서 신뢰하지 않는다.
- [x] 미등록 서버·도구·목적지·지원하지 않는 OTHER는 DENY로 처리한다.
- [ ] 자원형 READ는 C의 안전 경로 함수를 사용해 expected_resource_id를 계산한다. 결과의 실제 resource_id와 대조한다.
- [x] 출처는 서버·도구·경로 세그먼트 접두어로 매칭하고 중복 규칙은 최고 등급을 택한다. 미매칭은 PUBLIC이 아니라 Unclassified다.
- [x] 도구 단위 READ는 자원 경로 없이 `prefix: []` 규칙만 적용한다. 규칙 없으면 Unclassified다.
- [x] 목적지는 등록 목록의 정확한 일치로 해석한다. 이름·IP·DNS·stdio 여부를 신뢰 근거로 쓰지 않는다.

독립 구현·단위 검증은 [B3 Catalog 문서](../b3-catalog.md)에 기록했다.
기대·실제 자원 ID 대조와 resolver 미연결 거부는 구현했다. 실제 C safe_path 연결·S01 검증은
C 브랜치에 경로 함수가 없어 위 자원형 READ 항목에 계속 남아 있다.

### B4 · Guard와 응답 게이트

- [x] 일반 도구 실행은 `GuardService.submit()` 하나로 받는다. transport는 Guard 외부에 노출하지 않는다.
- [x] Task별로 판정부터 실행·응답 커밋까지 직렬화한다. 이전 READ 커밋 전에 다음 SEND를 판정하지 않는다.
- [x] ASK 대기 중 lock을 유지하지 않는다. WAITING_APPROVAL 상태의 일반 submit은 거부하며 제어 큐가 처리될 수 있게 한다.
- [x] DISPATCHING 예약 전에는 fail_closed를 적용한다. 예약 후 예상하지 못한 예외는 READ HALTED·미공개, SEND UNKNOWN_OUTCOME으로 처리한다.
- [x] 결과 불명·정지 상태를 기존 Task의 RUNNING으로 복구하지 않는다. 후속 호출은 차단하고 새 Task로만 재시작한다.
- [x] 격리·형식/크기 검사·출처 분류·원자적 커밋·공개 순서를 지킨다. 미분류·커밋 실패·미지원 형식은 미공개다.
- [x] 오류에 본문이 섞인 READ도 분류한다. 바이트가 없는 알려진 전송 실패와 예기치 않은 결과 처리 오류를 구분한다.
- [x] SEND 응답은 commit_send_outcome으로 요청·감사만 저장하고 등급 병합을 호출하지 않는다.
- [x] 정책·Profile 불일치는 Task 정지와 살아 있는 승인 무효로 처리한다. 상태 기록 실패까지 겹치면 global halt로 이후 판정을 차단한다.

### B5 · 승인 프로토콜

- [x] 요청·서버/도구/지문·경계/목적지·인자 해시·유효 등급·버전·승인자·만료를 snapshot에 고정한다.
- [x] 정규화 JSON과 SHA-256을 사용한다. 유니코드 NFC 변환을 하지 않고 float·NaN·허용하지 않는 값 타입을 거부한다.
- [x] 사본 원문은 메모리에만 두고 Store에는 해시·메타데이터만 전달한다. 재시작 때 승인 원문·실행 권한을 복원하지 않는다.
- [x] 사람 전용 제어 채널만 승인·거절·취소·긴급 차단 API를 호출하게 한다. AGT 허용은 사람 승인을 대체하지 않는다.
- [x] 승인 시 현재 등록·지문·목적지·상태로 재해석하고 재평가한다. ASK와 snapshot 해시가 모두 일치해야 소비한다.
- [x] Store의 consume_and_reserve 초안을 연계하며 정확한 True ACK 후에만 실행한다. 더블클릭·재전달의 서비스 단위 실행은 1회다.
- [x] 재평가부터 소비·dispatch·응답 커밋까지 submit과 같은 Task lock을 유지한다.
- [x] monotonic deadline과 UTC 만료를 함께 검사한다. 소비 완료 후 전송 직전에도 검사하며 만료 시간은 설정값으로 관리한다.

해시·예외·B4·B5의 구현·검증·미확정 계약은 [B4·B5 문서](../b4-b5-execution.md)에 있다.
실제 B6 저장소의 승인 소비·실행 예약·Task RUNNING·감사 단일 트랜잭션과 DB 시점의 UTC 만료,
A의 인증된 제어 큐 연결, 실제 wire/부작용 계수는 인수 검증에 계속 남아 있다.

2026-10-12 `f3c2d2b`에서 승인 조회와 정지 경로의 Task ID·snapshot epoch 검증을 보강했다.
B의 회귀 테스트 24개 추가 후 전체 864개 통과를 확인했고, C도 해당 커밋의 864개·Ruff·mypy를
별도 환경에서 재현했다. 이는 당시 대역 단위 검증이며 SQLite 원자 소비/응답 커밋 인수는 미완료다.
근거는 [C의 B 검토·후속 수정 확인](c-b-review.md#후속-수정-f3c2d2b-확인)이다.

### B6 · SQLite 저장소

- [x] 현재 Store 12개 메서드와 [C Store 요구](c-store-requirements.md)·[처리 제안 v1](c-store-proposal.md)을 대조해 [B6 계약 변경안](../b6-contract-proposal.md)을 제공했다. API·DTO·상태·버전·오류·시간·감사 선택은 공동 합의 전 제안이다.
- [x] 사용자 결정인 A·C 검토 후 확정을 기록하고, 변경안 10절에 담당별 회신 항목을 정리했다. 실제 전달·동의는 미완료다.
- [x] 기존 Store 포트를 바꾸지 않고 SQLite 연결·초기 v1 스키마/마이그레이션·Task 생성/조회와 74개 SQLite 단위 검사를 구현한다. 전체 기존 971개와 합쳐 1,045개 통과. [첫 단계 기록](../b6-sqlite-foundation.md)을 따른다.
- [x] Task 상태 CAS/전이·판정/감사·ALLOW 예약을 추가하고 감사 실패 rollback·같은 인스턴스 결속을 검증한다. 148개 추가 후 전체 1,193개 통과. [후속 기록](../b6-sqlite-decisions.md)을 따르며 버전 구현 선택은 공동 합의와 구분한다.
- [x] 남은 승인 4개·응답 3개 연산을 구현하고 관계/CAS·소비/응답 원자성·만료·중복·부분 실패·ACK 불명·재개방 차단을 검증한다. 202개 추가 후 전체 1,395개 통과. [현행 12/12 기록](../b6-sqlite-lifecycle.md)을 따르며 제품 인수 완료와 구분한다.
- [ ] 영속 순번·시작 복구의 구현과 단위 검증을 진행한다. 제품 bootstrap 연결·새 open/finish·C 인수는 미완료다.
- [x] B `6115fa6` 기준으로 [순번·복구 상세 설계](../b6-sequence-recovery-design.md)와 [검증 계획 50개](../b6-sequence-recovery-validation.md)를 작성하고 SEQ-01–03·REC-01–05 합의 대상을 분리한다. 문서 준비이며 구현·시험·A/C 동의는 미완료다.
- [x] 후속 사용자 지시로 [B 내부 시작 사전 점검](../b6-startup-preflight.md)을 구현한다. v1 형식/FK·같은 읽기 snapshot·기록 MAX/미기록 종류를 관찰하는 private 기반이며 배정/복구/앱 개방은 아니다.
- [ ] A의 시작 실행 게이트·제어 큐·조회 요구와 C의 독립 인수 기준을 대조하고 실제 API 합의 근거를 공통 계약에 기록한다.
- [ ] 새 공유 API·DTO·상태/버전·오류 규칙은 회신·합의 기록 후 변경하고 SQLite·원자 연산·영속 순번·감사·시작 복구와 저장소 단위 테스트에 반영한다.
- [ ] C의 실제 Store/Fake 계약·결함 주입 검증을 받고, A 연결 후 요청별 소비·dispatch·wire·수신 부작용 계수를 확인한다.

세부 체크·설정·인수 기준은 [B6 작업 문서](b-sqlite-store.md)를 따른다.
C의 안전 ID·safe_path·Registry·Scanner 소유권은 유지하며 Registry 영속화는 B 저장소의
단일 writer 계약으로 연결한다. 이 담당 변경으로 C 제안의 API까지 확정하지 않는다.
기존 포트의 독립 구현·준비와 새 공유 계약의 확정은 구분한다. B6·정규식 검토 회신을 받은 뒤
공통 계약에 실제 합의 근거를 기록하고, 합의된 변경과 A/C 통합 인수를 진행한다.

A `5598009`의 transport·AGT mock·모델·Runner·bridge·Qt UI와 아래 `655aef5` 선별 반영은
당시 상태의 기록이다. 인증된 동일 메모리 승인 사본 조회의 UI 인수, 권위 있는
승인·Task 상태·revision 및 제어 적용 결과의 요청 근거는
[A 최초 계약 질문의 역사 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-port-review.md#7-b-head-f3c2d2b-연동-중-드러난-호출제어-계약-질문)에 있다. 현재 구현과 남은 계약은 아래 최신 대조를 따른다.

이전 검토 기준 A `cd75bf9fa2a96a76f84fc1ce17a746705265ae53`에는 실제 로컬 AGT core 5.0.0과
loopback HTTP MCP가 추가됐고 Runner가 신뢰 맥락의 `expected_epoch=self.epoch`를 Guard에 전달한다.
최신 QA 변경은 adapter 대기/실행 상한 16개·퇴역 worker 예산 2개·제한적 worker 교체·실제 thread
종료 확인 및 한국어 실행/제어 문구다. AGT 제품 역할과 규칙 관리의 새 A18-D 결정 요청은 미확정이다.
A `cd75bf9`는 B `3bf414c`를 병합했다. 당시 domain·ports·Guard·승인 서비스는 B와 동일했으며
B `29025e0`의 승인 ID 검증·마지막 조회 만료/현재성 확인·늦은 AGT 작업 추적 보완이 반영됐다.
A의 연결 검증 보고는 아래에 기록한다. C의 독립 경합·소비·dispatch·wire·부작용 인수와
공동 계약 합의는 필요하다. 제품 SQLite·C 서비스 연결은 미완료다.

2026-10-13 후속 사용자 지시로 B `f22968c`에 A `655aef5`의 긴급 차단·
`HumanControl.review(approval_id)`를 선별 반영한 B 브랜치 변경을 준비했다. 조회 도중 만료·
다른 승인 ID·취소 후 AGT 누적 세 지적을 보완했다. 상세는 strict 모델·승인/Task ID·epoch·
PENDING·hash·현재 ACTIVE/지문과 마지막 await 뒤 양시계·메모리 사본을 확인한다.
조회는 상태 쓰기·승인 소비 없이 메모리 사본 또는 `None`을 반환하며 깨진 Store 관계는 global halt다.
같은 승인 ID 검사도 승인·거절·만료·무효화·재조회·오류 정리 및 전이 반환에 공통 적용했다.
오연결 행은 추가 상태 변경·소비·전송 없이 봉쇄하며 다른 Task의 승인 행·사본을 보존한다.
긴급 차단은 인증 후 lock 전에 정지 요청을 기록하고 예약·소비·전송 직전에 확인한다.
AGT 실제 완료 전 추적·슬롯을 유지하고 미종료 취소 작업이 있으면 새 검사를 생성하지 않는다.
기본 상한 4개·정리 대기 0.05초는 실측 최적값이 아니며, 새 Task ID로 재시작한다.
같은 ID/epoch 복구·B6 revision 지정 상세 조회·정규식 프로세스 연결 계약은 아직 검토 대상이다.

관련 **250개(1.36초)**와 전체 **971개(4.21초, 기존 886개+85개)** 테스트,
Ruff·45개 파일 포맷·24개 소스 strict mypy·pip check·diff 검사 통과를 확인했다.
기존 840·864·886 결과는 해당 단계의 기록으로 보존한다. 최종 설계·domain·ports·lock·
B6 공유 API는 변경하지 않았다. 사용자는 당시 B 서비스 변경의 commit·push를 승인했다.
위 B 단독 시험 당시의 한계와 현재 A 합성 앱 연결을 구분한다. 실제 SQLite·C 서비스·
모델의 제품 연결과 C 독립 검증은 미완료다.

A의 [최신 동기화 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-b-sync-2026-10-13.md)은
**A lock 전체 1,371 passed(48.87초)**, **별도 B lock의 B 소유 테스트 971 passed(18.83초)**,
AGT real/mock × stdio/HTTP **4조합·총 32경로**와 Guard 4슬롯 결합 단위 시험을 보고했다.
이전 A `281cbae`의 1,298개·87개 × 30회 실패0·stdio 각 모드 8경로와 HTTP
1,282개는 당시 보고다. 모두 이번 B 재현·C 독립 인수가 아니다. 기존 B 971개 검증 기록도 유지한다.
A 실행 jsonschema 4.25.1과 B 별도 코어 검사 4.26.0, A의 config/b-core strict 설정 보존을
확인했다. 공통 제품 버전·Scanner 포함 환경은 ENV-01 검토 대기다.
이번 작업에서는 설치·테스트 실행·lock 변경을 하지 않는다.

## 검증과 인수

2026-10-13 정규식 후속은 기존 기능 유지·전체 검증 프로세스 실행을 B 방향으로 선택했다.
[B3 연결안](../b-schema-validation-proposal.md)을 제공했으며 A 모델 변환과 C launcher의
공동 비동기/실패 계약은 검토 대기다. 정규식 제품 수정·종료 검증은 미완료다.

| 분류 | B가 작성할 검증 | C의 독립 검증 |
| --- | --- | --- |
| 모델·Catalog·정책 | T04–T06, T13, T25–T27, T30–T32, T38의 단위 부분과 정책 성질 | 해당 통합 시나리오·계약 검증 |
| 승인 | 해시·만료·상태·버전 의미의 단위 테스트 | T02, T09–T12, T37의 실제 소비·dispatch 계수 |
| 응답·예외 | READ/SEND 분리, 예약 전후 결과 의미 | T07, T19, T33, T35–T37, S05의 결함·유출 검증 |
| 누적·비교 모드 | high-watermark 단조성·미분류 고정 | T23·T24·T28·T34의 동일 조건 replay |
| SQLite 저장소 | 스키마·제약·상태/버전·원자 연산·S03 바인딩 SQL의 단위 부분 | 실제 Store/Fake 계약·통합·공격·결함 주입·복구 검증 |

- [ ] A의 Guard·Catalog 리뷰와 C의 정책·승인·응답·예외·해시 리뷰를 받는다.
- [ ] A18-D 운영자 차단 목록/조건 규칙과 서명 정책 관리의 선택부터 AGT-01로 회신받는다. 신뢰 입력·기본 판정·규칙/버전·policy_version/승인 결속·agt_mode 감사는 실제 선택 후 확정한다.
- [ ] B 검사 상한 4개·정리 0.05초와 A 기본 대기/실행 16개·퇴역 예산 2개·미평가 요청 이관·대기 deadline·실제 종료 확인을 AGT-03으로 검토한다. 같은 실패 요청/MCP SEND 재시도와 구분하고 합의 후 제품 규칙을 변경한다.
- [ ] B6 저장소에 대한 C 리뷰·독립 검증을 받고, C의 보안 모듈·Registry·Scanner·CI 및 C 소유 테스트를 리뷰한다.
- [x] A `cd75bf9`의 B 서비스 병합과 A 자체 AGT real/mock × stdio/HTTP 32경로 검증 보고를 읽고 소스 동일성을 대조한다. 이번 B 시험 실행이나 공동 합의로 처리하지 않는다.
- [ ] C 독립 통합 인수의 실제 소비·dispatch·wire·부작용 결과와 대상 커밋을 기록한다. 후속 SQLite/공유 계약 변경 후 A 연결 시험도 갱신한다.
- [ ] A·C에게 확정 계약·reason_code·상태 전이·예외 주입 경계·검증 결과를 전달한다.
- [ ] 구현자가 만든 Fake 테스트만으로 집행을 완료 처리하지 않는다. C의 실제 수신 계수 검증을 확인한다.

이 역할은 I1–I10 전체의 판정·상태 의미를 책임진다. 상세 기대 결과는 개발문서 14.3절을 기준으로 한다.
