# B · 보안 코어 작업 지침과 체크리스트

작업 브랜치는 `feat/b-security-core`다. **호출을 보내도 되는지 결정하고, 승인·결과 공개·Task 상태를 일관되게 집행하는 코어**를 맡는다. 현재는 문서 준비 단계다. 후속 구현 지시 전에는 기본 뼈대 소스를 만들지 않는다.

## 담당 범위

제품 경로는 향후 `src/guard_app/` 기준이다.

| 파일 / 영역 | 구현 책임 | 리뷰 |
| --- | --- | --- |
| `domain/*`, `ports.py` | 불변 모델, enum, reason_code, 전체 포트 계약 | A·C, 변경은 3인 합의 |
| `core/*` | 정책·결정표·등급 병합·승인 snapshot 구성 | C |
| `services/guard.py`, `services/catalog.py` | 유일 실행 입구·직렬화, 신뢰 설정 기반 요청 해석 | A |
| `services/approvals.py`, `services/response_gate.py` | 재평가·원자적 소비 연계, 격리·커밋 후 공개 | C |
| `security/failclosed.py`, `security/digest.py` | 예약 전후 예외 의미, 바이트 직렬화·SHA-256 | C |
| 담당 단위·성질 테스트 | 정책·모델·Catalog·승인 의미 검증 | 관련 리뷰 담당 |
| `docs/contracts.md` | 계약 초안·미확정 항목·3인 합의 기록 | A·C |

A의 transport·AGT 번역·Runner·GUI, C의 SQLite·Registry·Scanner·위험 연산을 구현하지 않는다. 필요한 연산을 `ports.py`로 계약하고 담당 구현을 사용한다. core의 외부 의존성을 추가하지 않는다.

## 먼저 읽을 설계

개발문서 0–8장 전체, 9.2·9.4절(실패·계수), 10.3절(AGT 분담), 11장(Store 트랜잭션), 13장(예외·해시·정적 규칙), 14.3·14.4절·부록 B를 읽는다.

## 작업 순서

### B1 · 공통 모델과 계약

- [ ] `ProposedCall`과 `ResolvedRequest`를 분리하고 모델을 frozen·strict·extra forbid로 정의한다. 내부 dict·list의 실제 변경 가능성도 검토한다.
- [ ] 모델·도구 출력에 task_id·등급·경계·승인자 결정권을 주지 않는다. Guard가 ID·epoch·call_seq를 관리한다.
- [ ] Label·Decision·TrustTier·ToolKind·등록·Task·승인 상태와 reason_code 의미를 확정한다.
- [ ] Guard 결과 타입, Transport·AccessChecker·Scanner·Store·ModelClient 전체 계약과 오류 타입을 초안으로 작성한다.
- [ ] A·C와 명령·이벤트 payload, 원시 결과 격리·공개 결과 구분, 계수 연결을 확정한다.
- [ ] policy_version·security_version·row_revision·fingerprint의 역할을 분리하고 `docs/contracts.md`에 3인 합의 근거를 기록한다.

### B2 · 순수 정책과 등급 누적

- [ ] 모든 경계에서 max_label을 우선 검사하고 기본 행렬을 적용한다. 외부 max_label은 INTERNAL 이하만 로드한다.
- [ ] READ에도 서버 경계를 적용하고 SEND에는 목적지 경계도 적용한다.
- [ ] 서버·목적지·AGT의 가장 엄격한 결과를 합성한다. AGT 허용은 정책을 완화하지 않으며 거절·오류는 DENY다.
- [ ] 등록·지문·Task 상태·미분류·정책 버전·목적지 전제조건을 다른 판정보다 먼저 검사한다. submit과 approval_recheck의 허용 상태를 구분한다.
- [ ] `accumulate`를 기본으로 하고 last_read·none은 비교 모드로 둔다. 모드 차이는 effective_label 계산에만 둔다.
- [ ] high-watermark 단조성과 Unclassified의 해제 금지를 구현한다. 보안 의미 변화에서만 security_version을 올린다.
- [ ] 같은 입력의 결정성, 등급·경계 단조성, 거부 흡수, 전처리 우선 성질을 hypothesis로 검증한다.

### B3 · Catalog와 요청 해석

- [ ] `server_id__tool_name` 네임스페이스와 고정 JSON Schema로 검증한다. ToolKind를 서버 annotation에서 신뢰하지 않는다.
- [ ] 미등록 서버·도구·목적지·지원하지 않는 OTHER는 DENY로 처리한다.
- [ ] 자원형 READ는 C의 안전 경로 함수를 사용해 expected_resource_id를 계산한다. 결과의 실제 resource_id와 대조한다.
- [ ] 출처는 서버·도구·경로 세그먼트 접두어로 매칭하고 중복 규칙은 최고 등급을 택한다. 미매칭은 PUBLIC이 아니라 Unclassified다.
- [ ] 도구 단위 READ는 자원 경로 없이 `prefix: []` 규칙만 적용한다. 규칙 없으면 Unclassified다.
- [ ] 목적지는 등록 목록의 정확한 일치로 해석한다. 이름·IP·DNS·stdio 여부를 신뢰 근거로 쓰지 않는다.

### B4 · Guard와 응답 게이트

- [ ] 일반 도구 실행은 `GuardService.submit()` 하나로 받는다. transport는 Guard 외부에 노출하지 않는다.
- [ ] Task별로 판정부터 실행·응답 커밋까지 직렬화한다. 이전 READ 커밋 전에 다음 SEND를 판정하지 않는다.
- [ ] ASK 대기 중 lock을 유지하지 않는다. WAITING_APPROVAL 상태의 일반 submit은 거부하며 제어 큐가 처리될 수 있게 한다.
- [ ] DISPATCHING 예약 전에는 fail_closed를 적용한다. 예약 후 예상하지 못한 예외는 READ HALTED·미공개, SEND UNKNOWN_OUTCOME으로 처리한다.
- [ ] 결과 불명·정지 상태를 기존 Task의 RUNNING으로 복구하지 않는다. 후속 호출은 차단하고 새 Task로만 재시작한다.
- [ ] 격리·형식/크기 검사·출처 분류·원자적 커밋·공개 순서를 지킨다. 미분류·커밋 실패·미지원 형식은 미공개다.
- [ ] 오류에 본문이 섞인 READ도 분류한다. 바이트가 없는 알려진 전송 실패와 예기치 않은 결과 처리 오류를 구분한다.
- [ ] SEND 응답은 commit_send_outcome으로 요청·감사만 저장하고 등급 병합을 호출하지 않는다.
- [ ] 정책·Profile 변경은 Task 정지와 승인 무효로 처리한다. 상태 기록 실패까지 겹치면 global halt로 이후 판정을 차단한다.

### B5 · 승인 프로토콜

- [ ] 요청·서버/도구/지문·경계/목적지·인자 해시·유효 등급·버전·승인자·만료를 snapshot에 고정한다.
- [ ] 정규화 JSON과 SHA-256을 사용한다. 유니코드 NFC 변환을 하지 않고 float·NaN·허용하지 않는 값 타입을 거부한다.
- [ ] 사본 원문은 메모리에만 두고 DB에는 해시만 저장한다. 재시작 때 승인 원문·실행 권한을 복원하지 않는다.
- [ ] 사람 전용 제어 채널만 승인·거절·취소·긴급 차단 API를 호출하게 한다. AGT 허용은 사람 승인을 대체하지 않는다.
- [ ] 승인 시 현재 등록·지문·목적지·상태로 재해석하고 재평가한다. ASK와 snapshot 해시가 모두 일치해야 소비한다.
- [ ] C의 consume_and_reserve로 승인 소비·실행 예약·Task RUNNING을 한 트랜잭션에 묶는다. 더블클릭·재전달에도 1회만 실행한다.
- [ ] 재평가부터 소비·dispatch·응답 커밋까지 submit과 같은 Task lock을 유지한다.
- [ ] monotonic deadline과 DB UTC 만료를 함께 검사한다. 만료 시간은 설정값으로 관리한다.

## 검증과 인수

| 분류 | B가 작성할 검증 | C의 독립 검증 |
| --- | --- | --- |
| 모델·Catalog·정책 | T04–T06, T13, T25–T27, T30–T32, T38의 단위 부분과 정책 성질 | 해당 통합 시나리오·계약 검증 |
| 승인 | 해시·만료·상태·버전 의미의 단위 테스트 | T02, T09–T12, T37의 실제 소비·dispatch 계수 |
| 응답·예외 | READ/SEND 분리, 예약 전후 결과 의미 | T07, T19, T33, T35–T37, S05의 결함·유출 검증 |
| 누적·비교 모드 | high-watermark 단조성·미분류 고정 | T23·T24·T28·T34의 동일 조건 replay |

- [ ] A의 Guard·Catalog 리뷰와 C의 정책·승인·응답·예외·해시 리뷰를 받는다.
- [ ] C의 저장소·보안 모듈·Registry·Scanner·CI 및 C 소유 테스트를 리뷰한다.
- [ ] A·C에게 확정 계약·reason_code·상태 전이·예외 주입 경계·검증 결과를 전달한다.
- [ ] 구현자가 만든 Fake 테스트만으로 집행을 완료 처리하지 않는다. C의 실제 수신 계수 검증을 확인한다.

이 역할은 I1–I10 전체의 판정·상태 의미를 책임진다. 상세 기대 결과는 개발문서 14.3절을 기준으로 한다.
