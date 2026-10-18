# B 현황

갱신: 2026-10-18 · main `a2698b2` · 이전 상세: [2026-10-15까지의 B 현황](archive/status/b-status-2026-10-15.md)

`B6-01`처럼 하이픈이 붙은 ID는 [공통 계약](contracts.md#미결-요청)의 요청이고, `B6.1`처럼 점이 붙은 ID는 이 체크리스트의 항목이다.

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| ACCESS-01·AGT-04의 ports/domain 3인 합의 | A·B·C | B2.9, B2.10 |
| A 브랜치 병합(A 소유 파일의 AGT·ModelClient 사용) | A | B1.7, B2.11 |
| SEQ·REC 계약 회신 | C | B6.8 |
| B6-01–06 계약 회신 | A·C | B1.5, B1.6, B6.9–B6.12, B6.16 |
| safe_path | C | B3.3 |
| Registry 영속화 계약(B6-06) | C | B6.14 |
| 저장소 리뷰·독립 검증 | C | B7.6, B7.8 |

## B1 · 공통 모델과 계약

- [x] B1.1 `ProposedCall`·`ResolvedRequest` 분리, frozen·strict·extra forbid · [기록](archive/b1-contract-draft.md)
- [x] B1.2 모델·도구 출력에 ID·등급·경계·승인 결정권을 주지 않음. ID·epoch·call_seq는 Guard 관리
- [x] B1.3 Label·Decision·TrustTier·ToolKind·상태 enum과 reason_code 초안
- [x] B1.4 Guard 결과 타입과 Transport·AccessChecker·Scanner·Store·ModelClient 계약 초안
- [ ] B1.5 명령·이벤트 payload, 원시 결과 격리·공개 결과 구분, 계수 연결 확정 — 막힘: A·C 합의
- [ ] B1.6 policy_version·security_version·row_revision·fingerprint 역할 분리와 3인 합의 기록 — 막힘: B6-02
- [ ] B1.7 ModelClient 제거·세션 관리자 DTO(TaskIdentity 등, #20 5번) — 막힘: A 제안 v1 검토·3인 합의, A 병합
- [ ] B1.8 SCAN-01 ScanReport 확장(#21 7번 필드: overall_risk 참고·threat_summary 300자)

## B2 · 순수 정책과 등급 누적

- [x] B2.1 모든 경계에서 max_label 우선 검사·기본 행렬, 외부 max_label은 INTERNAL 이하만 · [기록](archive/b2-policy-and-taint.md)
- [x] B2.2 READ에 서버 경계, SEND에 목적지 경계도 적용
- [x] B2.3 서버·목적지·접근 규칙 중 가장 엄격한 결과 합성, 접근 규칙 거절·오류는 DENY
- [x] B2.4 등록·지문·Task 상태·미분류·정책 버전·목적지 전제조건을 먼저 검사
- [x] B2.5 `accumulate` 기본, last_read·none은 비교 모드
- [x] B2.6 high-watermark 단조성·Unclassified 해제 금지, 보안 의미 변화에만 security_version 증가
- [x] B2.7 결정성·단조성·거부 흡수·전처리 우선 성질 hypothesis 검증
- [x] B2.8 접근 규칙 판정 순수 함수(ACCESS-01 1단계) · [기록](archive/b/B2.8-access-rules.md)
- [ ] B2.9 AccessChecker 동기 포트·Guard의 AGT용 대기 장치 제거(ACCESS-01) — 막힘: 3인 합의, A 연결
- [ ] B2.10 AGT-04 reason code·AccessResult 정리 — 막힘: 3인 합의
- [ ] B2.11 기존 AGT 코드 제거(B 범위, A 소유 파일은 A 리뷰) — 막힘: A 병합 순서

## B3 · Catalog와 요청 해석

- [x] B3.1 `server_id__tool_name` 네임스페이스·고정 JSON Schema 검증, ToolKind는 annotation을 신뢰하지 않음 · [기록](archive/b3-catalog.md)
- [x] B3.2 미등록 서버·도구·목적지·OTHER는 DENY
- [ ] B3.3 자원형 READ의 expected_resource_id를 C safe_path로 계산하고 실제 resource_id와 대조 — 막힘: C safe_path
- [x] B3.4 출처 접두어 매칭·중복은 최고 등급·미매칭은 Unclassified
- [x] B3.5 도구 단위 READ는 `prefix: []` 규칙만, 없으면 Unclassified
- [x] B3.6 목적지는 등록 목록 정확 일치, 이름·IP·DNS·stdio를 신뢰 근거로 쓰지 않음
- [x] B3.7 FP-01 JCS 지문 함수 · [기록](archive/b/B3.7-jcs-fingerprint.md)
- [ ] B3.8 DESC-01 검토된 설명 필드
- [ ] B3.9 설정·Profile 로딩과 policy_version 계산(10-18 결정)
- [ ] B3.10 정책 변경 명령·Task 정지 처리(10-18 결정)

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
- [x] B5.4 사람 전용 제어 채널만 승인·거절·취소·긴급 차단, AGT 허용은 사람 승인을 대체하지 않음
- [x] B5.5 승인 시 재해석·재평가, ASK와 snapshot 해시가 모두 일치해야 소비
- [x] B5.6 consume_and_reserve의 정확한 True ACK 후에만 실행, 중복 클릭·재전달 실행 1회
- [x] B5.7 재평가부터 응답 커밋까지 같은 Task lock 유지
- [x] B5.8 monotonic deadline·UTC 만료 동시 검사, 소비 후 전송 직전에도 검사
- [ ] B5.9 NOTIFY-01 만료 타이머·최종 결과 알림
- [ ] B5.10 STOP-01 시스템 정지 입구
- [ ] B5.11 모델에 돌려줄 고정 문구표(A 검토)

## B6 · SQLite 저장소

- [x] B6.1 Store 12개 연산과 C 요구·제안 대조, 계약 변경안과 회신 항목 정리 · [기록](archive/b6-contract-proposal.md)
- [x] B6.2 연결·v1 스키마(7개 테이블·STRICT·FK·UNIQUE)·v0→v1 마이그레이션, WAL·FULL·BEGIN IMMEDIATE · [기록](archive/b6-sqlite-foundation.md)
- [x] B6.3 Task 생성/조회·상태 전이·판정 감사·ALLOW 예약 · [기록](archive/b6-sqlite-decisions.md)
- [x] B6.4 승인 저장/조회/전이/소비·READ/SEND/실패 저장, 현행 12/12 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.5 UTC 만료·쓰기 중 만료·ACK 유실, 원문·인자·비밀값 비기록과 고정 오류 코드 · [기록](archive/b6-sqlite-lifecycle.md)
- [x] B6.6 영속 순번·시작 복구 설계와 검증 시나리오 50개 · [설계](archive/b6-sequence-recovery-design.md) · [시나리오](archive/b6-sequence-recovery-validation.md)
- [x] B6.7 읽기 전용 시작 사전 점검기 · [기록](archive/b6-startup-preflight.md)
- [ ] B6.8 영속 call_seq·시작 복구·허용된 실패 감사·재시작 정리 구현 — 막힘: SEQ-01–03·REC-01–05
- [ ] B6.9 승인 생성·종결 원자화(현재 두 커밋 간격) — 막힘: B6-01
- [ ] B6.10 요청 epoch/kind/revision 확장·승인/요청/Task 결속 — 막힘: B6-02
- [ ] B6.11 U8 제어/응답 경쟁, 조건 충돌·rollback·확정 불가 구분 — 막힘: B6-03·B6-04
- [ ] B6.12 DB 대기·양시계 만료·취소·커밋 ACK 규칙과 예산(잠금 대기 1000ms는 미측정 제안값) — 막힘: B6-03
- [ ] B6.13 SQLite 잠금 대기 중 이벤트 루프 정지·전역 정지(B-QA-03)
- [ ] B6.14 Registry 등록·지문 영속화·변경 시 승인 무효화 — 막힘: B6-06·C
- [ ] B6.15 규칙 버전 감사(AGT-02)
- [ ] B6.16 A 시작 게이트·조회 요구와 C 인수 기준을 대조해 API 합의 기록 — 막힘: A·C 회신
- [ ] B6.17 SEQ·REC B 결정안(A 회신의 질문 답변)

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
