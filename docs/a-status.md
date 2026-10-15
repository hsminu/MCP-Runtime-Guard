# A 현황

갱신: 2026-10-15 · main `82563ce` · 이전 상세: [2026-10-15까지의 A 현황](archive/status/a-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| launcher·Registry·endpoint 검증·fixture·독립 인수 | C | A04, A07–A12, A14–A16, A23, A36–A40 |
| 시작 복구·영속 call_seq·조회/감사 API (B-SQL-01–06) | B | A13, A24–A27, A33–A35 |
| domain/ports·UI 조회 DTO 합의 | A·B·C | A01·A03, A05, A28–A32 |
| AGT 역할·규칙 관리 결정 (AGT-01) | 사용자·팀 | A18 |
| Codex CLI 연결 방식·승인 환경 (T22) | 사용자 | A19–A22 |

## 계약·환경

- [x] A01·A03 B ref·테스트 동기화, Runner epoch 전달
- [ ] A01·A03 domain/ports 합의 — 막힘: 3인 합의
- [x] A02·A06 AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현
- [ ] A02·A06 Scanner 포함 공통 제품 조합 인수 — 막힘: C 조합 인수(main lock에는 Scanner 포함, #6)
- [ ] A04 C 연결: 요구사항은 전달 — 막힘: C launcher·Registry
- [x] A05 앱 내부 Task projection·사람 review 연결
- [ ] A05 공통 조회 DTO — 막힘: B·C 계약 검토

## transport·worker

- [x] A07·A11 stdio 실제 SDK·Guard 왕복 스파이크
- [ ] A07·A11 정식 C 수신 fixture로 인수 — 막힘: C fixture
- [x] A08·A12 Streamable HTTP adapter·loopback 왕복·JSON/SSE·재전송 차단 · [기록](archive/tasks/a-http-integration.md)
- [ ] A08·A12 C endpoint 검증·독립 fixture 인수·인증 확장 — 막힘: C
- [x] A09·A10 worker 구현·회귀 검증
- [ ] A09·A10 수신 직전 wire-size 상한 — 막힘: C stream ingress
- [ ] A09·A10 `tools/list` 수신 상한 페이지 1MiB·조회 누적 8MiB · [결정](contracts.md#사용자-결정)
- [x] A13 미종결 거부·정리 미확인 재시작 차단, SQLite 같은 스레드 종료 순서(W1)
- [ ] A13 이전 Task 종결·시작 복구 — 막힘: B-SQL-02·C 인수
- [x] A14 errlog 필수·SDK·합성 HTTP 서버 로그 격리
- [ ] A14 공개 handler 인수 — 막힘: C 로그 정책(A39와 함께)
- [x] A15·A16 등록 요구·단위 구현
- [ ] A15·A16 지문·Registry·launcher 연결 — 막힘: C

## AGT·모델·Runner

- [x] A17 AGT core 5.0.0 선정, Windows 판정 스파이크 8경로 · [기록](archive/tasks/a-agt-selection.md)
- [ ] A17 Scanner와 함께 쓰는 공통 조합 검증 — 막힘: C 조합 인수
- [x] A18 실제 로컬 판정·real/mock·timeout/취소, 동시 16·worker 교체·종료 확인 · [기록](archive/tasks/a-agt-integration.md)
- [ ] A18 AGT 역할·규칙 관리 — 막힘: AGT-01 · [결정 요청](archive/tasks/a-agt-role-decision.md)
- [ ] A18 영속 agt_mode 감사 — 막힘: B6 스키마(AGT-02)
- [ ] A18 dispatch 0 독립 인수 — 막힘: C
- [ ] A19·A20 실제 모델 Codex CLI 연결(제공자는 결정, 미연결) — 막힘: 연결 방식·승인 환경(T22)
- [x] A21·A22 Runner expected_epoch·승인 resume 연결
- [ ] A21·A22 실제 모델 세션 격리(T21) — 막힘: A19·A20
- [x] A23 B 리뷰: AGT 지연 중 긴급 차단 수정·회귀 · [기록](archive/tasks/a-b-review.md)
- [ ] A23 독립 wire 검증 — 막힘: C

## bridge·화면

- [x] A24–A27 프로세스 시작 게이트(W1)·옛 Task 실행/제어 거부(W2)·`--store sqlite` 연결·시작 실패 처리
- [ ] A24–A27 제품 Store·Registry 구성(지금은 메모리 기본·B 대역) — 막힘: B-SQL-01–06·C
- [ ] A28–A32 Registry·Scanner 조회 화면 — 막힘: 조회 계약 합의
- [x] A33·A34 승인 T18 필드·위젯·4스킴(W3), SQLite 승인 표시·중복 승인·재시작 권한 미복원
- [ ] A33·A34 과거 승인·제품 목록 통합 — 막힘: B-SQL-02/03
- [x] A35 로그 enum 매핑
- [ ] A35 영속 감사 조회 — 막힘: B6 조회
- [x] A36·A37 확정/요청 상태 분리·한글 표시
- [ ] A36·A37 전체 GUI 인수 — 막힘: C

## 통합·패키징

- [x] A38–A40 합성 경로·SQLite/mock/stdio·HTTP ASK·ALLOW·DENY 계수, 재시작·시작 실패 게이트
- [ ] A38–A40 전체 복구·launcher/endpoint·독립 인수 — 막힘: C T/S
- [x] A41 lock·설치·단위 검증, CI 1단계(#4)
- [ ] A41 보안 스캔(CI 2단계) — 막힘: C CI 2단계(보류)
- [ ] A42 인수: 리뷰 결과와 검증 인계
- [ ] A43 Ruff·포맷·mypy 기준선 축소(포맷 제외 22개, Ruff 면제, mypy 완화) · [통합 기록](archive/integration-a-b.md)
- [ ] A44 시연 앱의 `tests.unit.test_guard` 대역 import 제거
- [ ] A45 `guard-app` 콘솔 명령이 checkout 밖에서 실패(`examples`가 패키지에 없음)
- [ ] A46 HTTP transport 시작 때 TLS 인증서 로딩이 이벤트 루프를 210–280ms 막음(C 측정)
