# B 현황

갱신: 2026-10-30 · 확인한 main `f0c2643`(#103·#104·#109 병합) · 완료 89/95, 남음 6 · [#106 결정 기록](archive/b/B-106-agreement-closure.md) · 이전 상세: [2026-10-15까지의 B 현황](archive/status/b-status-2026-10-15.md)

`B6-01`처럼 하이픈이 붙은 ID는 [공통 계약](contracts.md#미결-요청)의 요청이고, `B6.1`처럼 점이 붙은 ID는 이 체크리스트의 항목이다.

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| 자원 ID 판정기의 제품 조립 연결 | A | B3.3 |
| 지문 변경·예약/승인 소비·전송 경쟁(S03) 원자 조건 후속(제출 범위 밖) | A·B·C | B6.19 |
| 독립 통합 인수 | C | B7.8 |
| 설정 파일 안전 읽기·원자적 교체(C2.14)·A 시작/저장/Task 생성 직렬화와 거부·누락 전송 0 검증 | A·C | B3.12 |

## B1 · 공통 모델과 계약

- [x] B1.1 `ProposedCall`·`ResolvedRequest` 분리, frozen·strict·extra forbid · [기록](archive/b1-contract-draft.md)
- [x] B1.2 모델·도구 출력에 ID·등급·경계·승인 결정권을 주지 않음. ID·epoch·call_seq는 Guard 관리
- [x] B1.3 Label·Decision·TrustTier·ToolKind·상태 enum과 reason_code 초안
- [x] B1.4 Guard 결과 타입과 Transport·AccessChecker·Scanner·Store·ModelClient 계약 초안
- [x] B1.5 명령·이벤트 payload·원시 결과 격리: A 로컬 frozen bridge DTO를 v1으로 확정([#106](https://github.com/hsminu/MCP-Runtime-Guard/issues/106)) · C 계수 지점을 worker `_execute` 진입으로 정렬 완료([#108](https://github.com/hsminu/MCP-Runtime-Guard/pull/108))
- [x] B1.6 policy_version·security_version·row_revision·fingerprint 역할 분리와 3인 합의 대조 · [기록](archive/b/B6.10-B6.12-unblocked.md)
- [x] B1.7 ModelClient 공통 포트 제거·Protocol의 examples 이동 `9dae11b` · [#106 A·C 동의](https://github.com/hsminu/MCP-Runtime-Guard/issues/106) · [기록](archive/b/B1.7-B1.9-code-s.md)
- [x] B1.8 SCAN-01 DTO 확장·표시 검사 오탐/명시적 무해화 보완 · [#32 DTO 형식 합의](https://github.com/hsminu/MCP-Runtime-Guard/pull/32#pullrequestreview-5649334182) · [기록](archive/b/B1.8-scan-report.md) · Hypothesis 반복 수 1,000 → 200(#100 `17df1ce`, 나머지 두 시험은 300 → 200), C가 4개 시험을 5,000회로 1회 확인해 반례 없음([#100 C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/100#issuecomment-6503859925))
- [x] B1.9 SCAN-01 B `Finding.code`가 C 번역 집합과 일치 `e4c08d4` · [#106 C 조건부 동의](https://github.com/hsminu/MCP-Runtime-Guard/issues/106#issuecomment-6505180007), [#57 사례 C 확인 완료](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6506140831) · 후속: A 경고 화면(A47·A128) · [기록](archive/b/B1.7-B1.9-code-s.md)

## B2 · 순수 정책과 등급 누적

- [x] B2.1 모든 경계에서 max_label 우선 검사·기본 행렬, 외부 max_label은 INTERNAL 이하만 · [기록](archive/b2-policy-and-taint.md)
- [x] B2.2 READ에 서버 경계, SEND에 목적지 경계도 적용
- [x] B2.3 서버·목적지·접근 규칙 중 가장 엄격한 결과 합성, 접근 규칙 거절·오류는 DENY
- [x] B2.4 등록·지문·Task 상태·미분류·정책 버전·목적지 전제조건을 먼저 검사
- [x] B2.5 `accumulate` 기본, last_read·none은 비교 모드
- [x] B2.6 high-watermark 단조성·Unclassified 해제 금지, 보안 의미 변화에만 security_version 증가
- [x] B2.7 결정성·단조성·거부 흡수·전처리 우선 성질 hypothesis 검증 · 순수 함수 속성 검사의 시간 제한 해제 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B2.8 접근 규칙 판정 순수 함수(ACCESS-01 1단계) · [기록](archive/b/B2.8-access-rules.md)
- [x] B2.9 AccessChecker 동기 포트·Guard 대기 장치 제거(ACCESS-01 결정 이행) · #47 병합 · [기록](archive/b/B2.9-B2.11-agt-removal.md)
- [x] B2.10 AGT-04 접근 사유 코드 교체·과거 저장값 판독 · C contract 확인(#81·#84), [A 표시 문구 확인](https://github.com/hsminu/MCP-Runtime-Guard/pull/81#issuecomment-6489991832) · [기록](archive/b/B2.10-agt04-access-reasons.md)
- [x] B2.11 기존 AGT 코드 제거 · #47 병합 · [기록](archive/b/B2.9-B2.11-agt-removal.md)

## B3 · Catalog와 요청 해석

- [x] B3.1 `server_id__tool_name` 네임스페이스·고정 JSON Schema 검증, ToolKind는 annotation을 신뢰하지 않음 · [기록](archive/b3-catalog.md)
- [x] B3.2 미등록 서버·도구·목적지·OTHER는 DENY
- [x] B3.3 자원형 READ의 expected_resource_id 계산·대조 B 구현 완료. C2.3(#63) `file_resource_resolver`가 Catalog `ResourceResolver`와 연결됨 · A `ProductInputs.resource_resolver` 제품 조립 연결 대기
- [x] B3.4 출처 접두어 매칭·중복은 최고 등급·미매칭은 Unclassified
- [x] B3.5 도구 단위 READ는 `prefix: []` 규칙만, 없으면 Unclassified
  - 자원형 출처 규칙(prefix ≠ ())은 Guard가 기대 자원 ID를 계산하고 응답 ID를 신뢰 코드가 만드는 서버(현재 C 파일 서버)에만 적용한다. 그 밖의 서버는 도구 단위 READ만 쓴다. 도구 단위 READ 등급은 운영자가 그 도구의 모든 결과에 붙이는 선언이며 응답 `resource_id`는 거부에만 사용한다([#94 B 답](https://github.com/hsminu/MCP-Runtime-Guard/issues/94#issuecomment-6505157878)). 파일 서버 인정 조건의 실행 파일 확인은 A·C 결정 대기다.
- [x] B3.6 목적지는 등록 목록 정확 일치, 이름·IP·DNS·stdio를 신뢰 근거로 쓰지 않음
- [x] B3.7 FP-01 JCS 지문 함수 · [기록](archive/b/B3.7-jcs-fingerprint.md)
- [x] B3.8 DESC-01 검토된 설명 필드·Catalog 연결·합의 전 제안 · [기록](archive/b/B3.8-reviewed-description.md)
- [x] B3.9 파싱된 설정·Profile 로딩 1단계와 policy_version 계산(10-18 결정) · [기록](archive/b/B3.9-profile-loading.md)
- [x] B3.10 정책 변경 준비·적용·Task 정지·승인 무효화(10-18 결정) · [기록](archive/b/B3.10-policy-change.md)
- [x] B3.11 CFG-01 B 변환 함수 완료, C 파일 연산(C2.14)·A 시작/저장 연결 병합([#44](https://github.com/hsminu/MCP-Runtime-Guard/pull/44)·[#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)); B3.12 인수 범위는 별도 대조 · [기록](archive/b/B3.11-settings-file.md)
- [ ] B3.12 A81 허용 체크·CFG-01 시작/저장/적용·Task 생성 직렬화·C 거부/누락 전송 0 검증 — 막힘: C2.14 파일 연산·A 연결
- [x] B3.13 DESC-01 검토 설명은 PENDING 저장→재활성화→재시작 후 새 세션에 적용, 원격 설명 별도 영속화 없음·화면 전 빈 설명([#106](https://github.com/hsminu/MCP-Runtime-Guard/issues/106)) · 후속: A 검토 입력 화면
- [x] B3.14 EXE-01 실행 파일 고정 설정 필드 B 구현 · A 등록 화면·launcher 연결 대기 · [기록](archive/b/B3.14-exe01-settings-fields.md)
- [x] B3.15 URL-01 HTTP 서버 URL을 서버별 검사 · #86 병합 · A 연결·표시 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B3.16 설정과 Registry가 다르면 해당 서버를 숨기고 시작에서 제외 · [기록](archive/b/B3.15-B3.16-server-settings.md) · [시작 제외](archive/b/B3.17-startable-settings-mismatch.md)

## B4 · Guard와 응답 게이트

- [x] B4.1 일반 도구 실행은 `GuardService.submit()` 하나, transport 비노출 · [기록](archive/b4-b5-execution.md)
- [x] B4.2 Task별 판정→실행→응답 커밋 직렬화
- [x] B4.3 ASK 대기 중 lock 미보유, WAITING_APPROVAL의 일반 submit 거부·제어 큐 처리
- [x] B4.4 DISPATCHING 예약 전 fail_closed, 예약 후 READ HALTED·미공개, SEND UNKNOWN_OUTCOME
- [x] B4.5 결과 불명·정지 Task를 RUNNING으로 복구하지 않음, 새 Task로만 재시작
- [x] B4.6 격리→형식/크기 검사→분류→원자 커밋→공개 순서, 미분류·커밋 실패·미지원 형식은 미공개
- [x] B4.7 본문이 섞인 오류 READ도 분류, 알려진 전송 실패와 예기치 않은 처리 오류 구분
- [x] B4.8 SEND 응답은 commit_send_outcome으로 요청·감사만 저장
- [x] B4.9 정책·Profile 불일치는 Task 정지·승인 무효, 기록 실패가 겹치면 global halt
- [x] B4.10 Guard 정상 종료 사유 GUARD_CLOSED B 구현·합의 전 제안 · A 문구·3인 합의 대기 · [기록](archive/b/B4.10-guard-close-reason.md)

## B5 · 승인 프로토콜

- [x] B5.1 요청·서버/도구/지문·경계·인자 해시·등급·버전·승인자·만료를 snapshot에 고정 · [기록](archive/b4-b5-execution.md)
- [x] B5.2 정규화 JSON·SHA-256, NFC 변환 없음, float·NaN·불허 타입 거부
- [x] B5.3 원문은 메모리에만, Store에는 해시·메타데이터, 재시작 때 승인 원문·권한 미복원
- [x] B5.4 사람 전용 제어 채널만 승인·거절·취소·긴급 차단, 접근 규칙 허용은 사람 승인을 대체하지 않음
- [x] B5.5 승인 시 재해석·재평가, ASK와 snapshot 해시가 모두 일치해야 소비
- [x] B5.6 consume_and_reserve의 정확한 True ACK 후에만 실행, 중복 클릭·재전달 실행 1회
- [x] B5.7 재평가부터 응답 커밋까지 같은 Task lock 유지
- [x] B5.8 monotonic deadline·UTC 만료 동시 검사, 소비 후 전송 직전에도 검사
- [x] B5.9 NOTIFY-01 만료 타이머·최종 결과 알림 B 구현·합의 전 제안 · [기록](archive/b/B5.9-pending-notifications.md)
- [x] B5.10 STOP-01 시스템 정지·사실 감사 B 구현·합의 전 제안 · [기록](archive/b/B5.10-system-stop.md)
- [x] B5.11 모델에 돌려줄 고정 문구표 · [기록](archive/b/B5.11-model-messages.md)
- [x] B5.12 모델 고정 문구표 A 검토·세션 관리자 연결 · [#20 A 검토 댓글(2026-10-28)](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6464059746)
- [x] B5.13 NOTIFY-01 계약·연결 [#28](https://github.com/hsminu/MCP-Runtime-Guard/pull/28)·[#106](https://github.com/hsminu/MCP-Runtime-Guard/issues/106): 완료 전 콜백 실패는 알림 0·전역 정지·재발행 0 · 후속: C6.10 독립 주입 검증
- [x] B5.14 STOP-01 계약·A 연결·T41 확인([#106](https://github.com/hsminu/MCP-Runtime-Guard/issues/106)) · 후속: 실제 Codex T42 승인 대기·A watchdog 경로 확인

## B6 · SQLite 저장소

- [x] B6.1 Store 12개 연산과 C 요구·제안 대조, 계약 변경안과 회신 항목 정리 · [기록](archive/b6-contract-proposal.md)
- [x] B6.2 연결·v1 스키마(7개 테이블·STRICT·FK·UNIQUE)·v0→v1 마이그레이션, WAL·FULL·BEGIN IMMEDIATE · [기록](archive/b6-sqlite-foundation.md)
- [x] B6.3 Task 생성/조회·상태 전이·판정 감사·ALLOW 예약 · [기록](archive/b6-sqlite-decisions.md)
- [x] B6.4 승인 저장/조회/전이/소비·READ/SEND/실패 저장, 현행 12/12 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.5 UTC 만료·쓰기 중 만료·ACK 유실, 원문·인자·비밀값 비기록과 고정 오류 코드 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.6 영속 순번·시작 복구 설계와 검증 시나리오 50개 · [설계](archive/b6-sequence-recovery-design.md) · [시나리오](archive/b6-sequence-recovery-validation.md)
- [x] B6.7 읽기 전용 시작 사전 점검기 · [기록](archive/b6-startup-preflight.md)
- [ ] B6.8 영속 call_seq·시작 복구·허용된 실패 감사·재시작 정리 — B 구현·A 제품 연결 [#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83), C 재시작·변조 [#103](https://github.com/hsminu/MCP-Runtime-Guard/pull/103)·강제 종료 60회 복구 [#104](https://github.com/hsminu/MCP-Runtime-Guard/pull/104) 확인 · 후속: C의 영속 순번 배정·허용 실패 감사 연산 자체 독립 확인 · [1단계](archive/b/B6.8-1-call-seq-v3.md) · [2a 기록](archive/b/B6.8-2a-prepare-schema.md) · [2b 기록](archive/b/B6.8-2b-recover-and-activate.md) · [병합 뒤 수정](archive/b/B6.8-3-post-merge-fixes.md)
  - #65(C2.7) 후속으로 명시 모드 기본 감사 ID 출처를 `security.ids`로 옮기고 B 모듈의 직접 생성 금지 정적 검사를 추가했다.
- [x] B6.9 승인 생성·종결 원자화 · #89 병합, A·C 동의(C 독립 결함 주입 인수는 B7.6·B7.8) · [기록](archive/b/B6.9-atomic-approvals.md)
- [x] B6.10 요청 epoch/kind/revision 확장·승인/요청/Task 결속 · [기록](archive/b/B6.10-B6.12-unblocked.md)
- [x] B6.11 U8 제어/응답 경쟁, 조건 충돌·rollback·확정 불가 구분 · [기록](archive/b/B6.10-B6.12-unblocked.md)
- [x] B6.12 DB 대기·양시계 만료·취소·커밋 ACK 규칙 구현·시험 완료, 잠금 대기 1000ms 확정, C 실측 중앙 1.17초(이 PC 한 대, [#104](https://github.com/hsminu/MCP-Runtime-Guard/pull/104)) · [기록](archive/b/B6.10-B6.12-unblocked.md)
- [x] B6.13 SQLite 대기 응답성·Store 정지 가시성(B-QA-03/10) — B 단위 구현·C 독립 검증 및 측정 완료, [A 연결](https://github.com/hsminu/MCP-Runtime-Guard/pull/83) · [#55 종료](https://github.com/hsminu/MCP-Runtime-Guard/issues/55) · C 기록 #79·[#93](https://github.com/hsminu/MCP-Runtime-Guard/pull/93) 병합 · 늦은 결과 전달 [#97](https://github.com/hsminu/MCP-Runtime-Guard/pull/97) 병합 · [기록](archive/b/B6.13-sqlite-writer.md) · [늦은 결과 전달](archive/b/B6.13-2-late-publish.md) · [C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6468999539)
- [x] B6.14 RegistryStore 계약·SQLite v2·빈 v1 이관 B 구현·합의 전 제안 · [기록](archive/b/B6.14-registry-store.md)
- [x] B6.15 규칙 버전 감사(AGT-02) · Task 연결 사건은 불변 `tasks.policy_version`으로 간접 추적, Task 없는 비판정 사건은 범위 밖, 직접 저장·과거 보정 없음([#106](https://github.com/hsminu/MCP-Runtime-Guard/issues/106)) · [조사](archive/b/B2.9-B2.11-agt-removal.md#b615agt-04와-공통-계약-문안)
- [x] B6.16 화면용 권위 조회 · #76 A·C 합의(10-29), #86 병합 · A 승인 목록·호출 기록 연결 [#92](https://github.com/hsminu/MCP-Runtime-Guard/pull/92) · [C 계수 대조](https://github.com/hsminu/MCP-Runtime-Guard/pull/86#issuecomment-6494025432) · [기록](archive/b/B6.16-store-queries.md)
- [x] B6.17 SEQ·REC B 결정안·A·C 동의(#37), 사용자 contracts 반영 완료 · B6.8 영속 순번·실패 감사 독립 확인은 후속 · [기록](archive/b/B6.17-seq-rec-decisions.md)
- [x] B6.18 RegistryStore 계약·C4.10(#46) 사본 교체/승인 무효화와 C4.19(#53) 연결 완료 · [기록](archive/b/B6.14-registry-store.md)
- [ ] B6.19 지문 변경과 예약/승인 소비·전송 경쟁(S03) · 후속(제출 범위 밖, [#106 8번](https://github.com/hsminu/MCP-Runtime-Guard/issues/106)): 정의 전용 generation·예약/소비 TX 대조·worker 전송 직전 확인. 제출 시연은 고정 등록 목록·변경 시 정지/재시작. I10 동시 경쟁 해결 주장 안 함
- [x] B6.20 `record_settings_change` CAS·도구 설정 변경·감사 B 구현 · #86 병합 · C Registry 연결 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B6.21 검사 보고서 저장 전 현재 등록 지문 집합 대조 · #86 병합 · C 리뷰·독립 검증 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B6.22 요청 실패 감사의 예외 형식 이름 상한 64자 · #86 병합 · [기록](archive/b/B3.15-B3.16-server-settings.md)

## B7 · 검증과 인수

- [x] B7.1 A의 B 서비스 병합과 32경로 검증 보고 대조
- [ ] B7.2 A의 [Guard](https://github.com/hsminu/MCP-Runtime-Guard/pull/47#pullrequestreview-5669225500)·[Catalog](https://github.com/hsminu/MCP-Runtime-Guard/pull/36#pullrequestreview-5653883833), C의 [승인·예외](https://github.com/hsminu/MCP-Runtime-Guard/pull/32#pullrequestreview-5649334182)·[해시](https://github.com/hsminu/MCP-Runtime-Guard/pull/89#issuecomment-6497627122) 리뷰 확인 · C `core/policy.py`·`services/response_gate.py` 직접 리뷰 근거 미확인
- [x] B7.3 10-15 AGT 제거 결정으로 대체 · [기록](archive/b/B7.3-agt-decision-superseded.md)
- [x] B7.4 AGT-03 폐기 · [기록](archive/b/B7.4-agt-worker-retired.md)
- [ ] B7.5 정규식 전체 스키마 검증(SCHEMA-01) · 2단계 B 구현 [#109](https://github.com/hsminu/MCP-Runtime-Guard/pull/109) 병합, [A 조립 연결](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6505998748)·[C worker launcher](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6506140831) 후속 · [기록](archive/b/B7.5-2-schema-boundary.md) · [1단계 기록](archive/b/B7.5-1-schema-jobs.md) · [연결안](archive/b-schema-validation-proposal.md)
- [x] B7.6 B6 저장소의 C 리뷰·독립 검증 [#103](https://github.com/hsminu/MCP-Runtime-Guard/pull/103)·[#104](https://github.com/hsminu/MCP-Runtime-Guard/pull/104) 병합 · B6.8의 영속 순번·실패 감사 후속은 별도
- [x] B7.7 C 보안 모듈·Registry·Scanner·CI·테스트 B 리뷰 완료 · [#102 종료](https://github.com/hsminu/MCP-Runtime-Guard/issues/102) · [#105 병합·B 재검증](https://github.com/hsminu/MCP-Runtime-Guard/pull/105#issuecomment-6505263530) 완료 · C 결함 2건은 C 후속
- [ ] B7.8 C 독립 통합 인수 결과와 대상 커밋 기록 — 막힘: C
- [x] B7.9 확정 계약·reason_code·상태 전이·예외 주입 경계·검증 결과를 A·C에 전달 · [기록](archive/b/B7.9-contract-handoff.md)
- [x] B7.10 공통 문서 미결 상태 갱신 제안 · [PR #18](https://github.com/hsminu/MCP-Runtime-Guard/pull/18) · [기록](archive/b/B7.10-contract-status-proposal.md)
- [x] B7.11 A 리뷰 후속 최종 알림 고정 문구·실행 본문 A 경로 위임·예약 전 취소 Task 격리 · [기록](archive/b/B7.11-a-review-followups.md)
- [x] B7.12 C 리뷰 후속 미소비 공개 본문 폐기·예약 뒤 취소 최종 알림 · [기록](archive/b/B7.12-c-review-followups.md)
- [x] B7.13 PR #32 C 리뷰 후속 취소 콜백 실패 격리·무변경 지문 관측 쓰기 제거 · [기록](archive/b/B7.13-c-review-followups.md)
