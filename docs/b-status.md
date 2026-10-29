# B 현황

갱신: 2026-10-29 · main `db901d7` · 이전 상세: [2026-10-15까지의 B 현황](archive/status/b-status-2026-10-15.md)

`B6-01`처럼 하이픈이 붙은 ID는 [공통 계약](contracts.md#미결-요청)의 요청이고, `B6.1`처럼 점이 붙은 ID는 이 체크리스트의 항목이다.

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| AGT-04 표시 문구 리뷰·contract 테스트 | A·C | B2.10 |
| AGT-02 감사 범위 합의 | A·B·C | B6.15 |
| A66 세션 관리자 계약 합의(legacy 시연이 ModelClient 사용) | A·B·C | B1.7 |
| B6-01·B6-05 결정, B6-02–04 닫기 제안(#76) | A·B·C | B1.5, B1.6, B6.9–B6.12, B6.16 |
| 자원 ID 판정기의 제품 조립 연결 | A | B3.3 |
| 지문 변경·예약/승인 소비·전송 경쟁(S03) 원자 조건 합의 | A·B·C | B6.19 |
| 저장소 리뷰·독립 검증 | C | B7.6, B7.8 |
| NOTIFY-01 DTO·포트·종료 의미 합의와 A/C 연결·독립 검증 | A·B·C | B5.13 |
| STOP-01 TaskIdentity·사유·감사 포트 합의와 A 세션 관리자 연결·T41/T42 독립 검증 | A·B·C | B5.14 |
| SCAN-01 필드·고정 오류 코드 합의와 C Scanner 번역·A 경고 표시 | A·B·C | B1.9 |
| DESC-01 검토 입력·원본 분리·Registry 저장·설명 변경 적용 의미 | A·B·C | B3.13 |
| 설정 파일 안전 읽기·원자적 교체(C2.14)·A 시작/저장/Task 생성 직렬화와 거부·누락 전송 0 검증 | A·C | B3.12 |

## B1 · 공통 모델과 계약

- [x] B1.1 `ProposedCall`·`ResolvedRequest` 분리, frozen·strict·extra forbid · [기록](archive/b1-contract-draft.md)
- [x] B1.2 모델·도구 출력에 ID·등급·경계·승인 결정권을 주지 않음. ID·epoch·call_seq는 Guard 관리
- [x] B1.3 Label·Decision·TrustTier·ToolKind·상태 enum과 reason_code 초안
- [x] B1.4 Guard 결과 타입과 Transport·AccessChecker·Scanner·Store·ModelClient 계약 초안
- [ ] B1.5 명령·이벤트 payload, 원시 결과 격리·공개 결과 구분, 계수 연결 확정 — 막힘: A·C 합의
- [ ] B1.6 policy_version·security_version·row_revision·fingerprint 역할 분리와 3인 합의 기록 — 막힘: B6-02
- [ ] B1.7 ModelClient 제거·세션 관리자 DTO(TaskIdentity 등, #20 5번) — 막힘: A66 세션 관리자 계약 합의(legacy 시연이 ModelClient 사용)
- [x] B1.8 SCAN-01 DTO 확장·표시 검사 오탐/명시적 무해화 보완·합의 전 제안 · [기록](archive/b/B1.8-scan-report.md)
- [ ] B1.9 SCAN-01 필드·고정 코드/무해화 합의·C Scanner 번역·A 경고 표시 — 막힘: 3인 합의·A/C 연결

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
- [ ] B2.10 AGT-04 접근 사유 코드 교체·과거 저장값 판독 B 단위 구현 완료 · A 표시 문구 리뷰·C contract 테스트 대기 · [기록](archive/b/B2.10-agt04-access-reasons.md)
- [x] B2.11 기존 AGT 코드 제거 · #47 병합 · [기록](archive/b/B2.9-B2.11-agt-removal.md)

## B3 · Catalog와 요청 해석

- [x] B3.1 `server_id__tool_name` 네임스페이스·고정 JSON Schema 검증, ToolKind는 annotation을 신뢰하지 않음 · [기록](archive/b3-catalog.md)
- [x] B3.2 미등록 서버·도구·목적지·OTHER는 DENY
- [x] B3.3 자원형 READ의 expected_resource_id 계산·대조 B 구현 완료. C2.3(#63) `file_resource_resolver`가 Catalog `ResourceResolver`와 연결됨 · A `ProductInputs.resource_resolver` 제품 조립 연결 대기
- [x] B3.4 출처 접두어 매칭·중복은 최고 등급·미매칭은 Unclassified
- [x] B3.5 도구 단위 READ는 `prefix: []` 규칙만, 없으면 Unclassified
- [x] B3.6 목적지는 등록 목록 정확 일치, 이름·IP·DNS·stdio를 신뢰 근거로 쓰지 않음
- [x] B3.7 FP-01 JCS 지문 함수 · [기록](archive/b/B3.7-jcs-fingerprint.md)
- [x] B3.8 DESC-01 검토된 설명 필드·Catalog 연결·합의 전 제안 · [기록](archive/b/B3.8-reviewed-description.md)
- [x] B3.9 파싱된 설정·Profile 로딩 1단계와 policy_version 계산(10-18 결정) · [기록](archive/b/B3.9-profile-loading.md)
- [x] B3.10 정책 변경 준비·적용·Task 정지·승인 무효화(10-18 결정) · [기록](archive/b/B3.10-policy-change.md)
- [x] B3.11 CFG-01 B 변환 함수 완료, C 파일 연산(C2.14)·A 시작/저장 연결(B3.12) 대기 · [기록](archive/b/B3.11-settings-file.md)
- [ ] B3.12 A81 허용 체크·CFG-01 시작/저장/적용·Task 생성 직렬화·C 거부/누락 전송 0 검증 — 막힘: C2.14 파일 연산·A 연결
- [ ] B3.13 DESC-01 A 검토 입력·C Registry 원본 분리/저장·설명 변경 적용 의미 합의 — 막힘: 3인 합의·A/C 연결
- [x] B3.14 EXE-01 실행 파일 고정 설정 필드 B 구현 · A 등록 화면·launcher 연결 대기 · [기록](archive/b/B3.14-exe01-settings-fields.md)
- [x] B3.15 URL-01 HTTP 서버 URL을 서버별 검사 · A 연결·표시 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B3.16 설정과 Registry가 다르면 해당 서버만 숨김 · A 재확인 화면 연결 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)

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
- [ ] B5.13 NOTIFY-01 DTO·포트·종료 의미 합의·A 대기/종료·C 지문 변경 연결·T45 독립 검증 — 막힘: 3인 합의·A/C 연결
- [ ] B5.14 STOP-01 TaskIdentity·정지 사유·SessionAudit 합의·A 세션 관리자 연결·C T41/T42 계수 검증 — 막힘: 3인 합의·A/C 연결

## B6 · SQLite 저장소

- [x] B6.1 Store 12개 연산과 C 요구·제안 대조, 계약 변경안과 회신 항목 정리 · [기록](archive/b6-contract-proposal.md)
- [x] B6.2 연결·v1 스키마(7개 테이블·STRICT·FK·UNIQUE)·v0→v1 마이그레이션, WAL·FULL·BEGIN IMMEDIATE · [기록](archive/b6-sqlite-foundation.md)
- [x] B6.3 Task 생성/조회·상태 전이·판정 감사·ALLOW 예약 · [기록](archive/b6-sqlite-decisions.md)
- [x] B6.4 승인 저장/조회/전이/소비·READ/SEND/실패 저장, 현행 12/12 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.5 UTC 만료·쓰기 중 만료·ACK 유실, 원문·인자·비밀값 비기록과 고정 오류 코드 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.6 영속 순번·시작 복구 설계와 검증 시나리오 50개 · [설계](archive/b6-sequence-recovery-design.md) · [시나리오](archive/b6-sequence-recovery-validation.md)
- [x] B6.7 읽기 전용 시작 사전 점검기 · [기록](archive/b6-startup-preflight.md)
- [ ] B6.8 영속 call_seq·시작 복구·허용된 실패 감사·재시작 정리 — 1단계·2a·2b B 단위 구현 완료, #69 `b796ff0` 병합 · A 제품 연결·C 독립 인수 대기 · [1단계](archive/b/B6.8-1-call-seq-v3.md) · [2a 기록](archive/b/B6.8-2a-prepare-schema.md) · [2b 기록](archive/b/B6.8-2b-recover-and-activate.md) · [병합 뒤 수정](archive/b/B6.8-3-post-merge-fixes.md)
  - #65(C2.7) 후속으로 명시 모드 기본 감사 ID 출처를 `security.ids`로 옮기고 B 모듈의 직접 생성 금지 정적 검사를 추가했다.
- [ ] B6.9 승인 생성·종결 원자화 B 단위 제안 구현 · A·C 확인(#76) 대기 · [기록](archive/b/B6.9-atomic-approvals.md)
- [ ] B6.10 요청 epoch/kind/revision 확장·승인/요청/Task 결속 — 막힘: B6-02
- [ ] B6.11 U8 제어/응답 경쟁, 조건 충돌·rollback·확정 불가 구분 — 막힘: B6-03·B6-04
- [ ] B6.12 DB 대기·양시계 만료·취소·커밋 ACK 규칙과 예산(잠금 대기 1000ms는 미측정 제안값) — 막힘: B6-03
- [ ] B6.13 SQLite 대기 응답성·Store 정지 가시성(B-QA-03/10) — B 단위 구현·C 독립 검증 및 측정 완료, A 연결·#55와 C 기록 PR 대기 · [기록](archive/b/B6.13-sqlite-writer.md) · [C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6468999539)
- [x] B6.14 RegistryStore 계약·SQLite v2·빈 v1 이관 B 구현·합의 전 제안 · [기록](archive/b/B6.14-registry-store.md)
- [ ] B6.15 규칙 버전 감사(AGT-02) — 막힘: 판정·감사 버전 추적 범위와 직접 저장·간접 조회 합의 · [조사](archive/b/B2.9-B2.11-agt-removal.md#b615agt-04와-공통-계약-문안)
- [ ] B6.16 화면용 권위 조회 B 단위 제안 구현 · C 계수 대조 완료(#86 C 리뷰), A 연결 대기 · #76 최종 합의 대기 · [기록](archive/b/B6.16-store-queries.md)
- [x] B6.17 SEQ·REC B 결정안·A·C 동의(#37), 사용자 contracts 반영 완료 · 구현 B6.8·B6.13 대기 · [기록](archive/b/B6.17-seq-rec-decisions.md)
- [x] B6.18 RegistryStore 계약·C4.10(#46) 사본 교체/승인 무효화와 C4.19(#53) 연결 완료 · [기록](archive/b/B6.14-registry-store.md)
- [ ] B6.19 지문 변경과 예약/승인 소비·전송 경쟁(S03) 원자 조건·독립 검증 — 막힘: 3인 합의·A/C 연결
- [x] B6.20 `record_settings_change` CAS·도구 설정 변경·감사 B 구현 · C Registry 연결 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B6.21 검사 보고서 저장 전 현재 등록 지문 집합 대조 · C 리뷰·독립 검증 대기 · [기록](archive/b/B3.15-B3.16-server-settings.md)
- [x] B6.22 요청 실패 감사의 예외 형식 이름 상한 64자 · [기록](archive/b/B3.15-B3.16-server-settings.md)

## B7 · 검증과 인수

- [x] B7.1 A의 B 서비스 병합과 32경로 검증 보고 대조
- [ ] B7.2 A의 Guard·Catalog 리뷰, C의 정책·승인·응답·예외·해시 리뷰 받기 — 막힘: A·C
- [x] B7.3 10-15 AGT 제거 결정으로 대체 · [기록](archive/b/B7.3-agt-decision-superseded.md)
- [x] B7.4 AGT-03 폐기 · [기록](archive/b/B7.4-agt-worker-retired.md)
- [ ] B7.5 정규식 전체 스키마 검증을 종료 가능한 프로세스에서 실행(SCHEMA-01) · [연결안](archive/b-schema-validation-proposal.md)
- [ ] B7.6 B6 저장소의 C 리뷰·독립 검증 받기 — 막힘: C
- [ ] B7.7 C 보안 모듈·Registry·Scanner·CI·테스트 리뷰(PR #6·#8의 `adapters/cisco*.py`, `security/secrets.py`·`redact.py` 포함)
- [ ] B7.8 C 독립 통합 인수 결과와 대상 커밋 기록 — 막힘: C
- [ ] B7.9 확정 계약·reason_code·상태 전이·예외 주입 경계·검증 결과를 A·C에 전달
- [x] B7.10 공통 문서 미결 상태 갱신 제안 · [PR #18](https://github.com/hsminu/MCP-Runtime-Guard/pull/18) · [기록](archive/b/B7.10-contract-status-proposal.md)
- [x] B7.11 A 리뷰 후속 최종 알림 고정 문구·실행 본문 A 경로 위임·예약 전 취소 Task 격리 · [기록](archive/b/B7.11-a-review-followups.md)
- [x] B7.12 C 리뷰 후속 미소비 공개 본문 폐기·예약 뒤 취소 최종 알림 · [기록](archive/b/B7.12-c-review-followups.md)
- [x] B7.13 PR #32 C 리뷰 후속 취소 콜백 실패 격리·무변경 지문 관측 쓰기 제거 · [기록](archive/b/B7.13-c-review-followups.md)
