# A 현황

갱신: 2026-10-18 · main `1d34276` · 이전 상세: [2026-10-15까지의 A 현황](archive/status/a-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| launcher·Registry·endpoint 검증·fixture·독립 인수 | C | A04, A07–A12, A14–A16, A23, A36–A40 |
| 시작 복구·영속 call_seq·조회/감사 API (B-SQL-01–06) | B | A13, A24–A27, A33–A35 |
| domain/ports·UI 조회 DTO 합의 | A·B·C | A01·A03, A05, A28–A32 |
| `ModelClient` 포트 대체·`valid_server_id` 사용 방식 ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20) 5·6번) | A·B·C | A19–A22, A54 |
| Pending 최종 결과 알림·만료 주체(NOTIFY-01, [#18](https://github.com/hsminu/MCP-Runtime-Guard/pull/18) 제안)·세션 관리자용 Task 정지 입구·검토된 설명 ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20) 1–3번) | B | A19–A22, A47 |
| 접근 규칙 판정 함수·연결 형태(ACCESS-01, [#18](https://github.com/hsminu/MCP-Runtime-Guard/pull/18) 제안)·AGT 코드 제거 | B | A02·A06, A41, A44, A51, A53 |
| Codex 고정 버전·launcher·L2 입구·stream ingress·경로·ScanReport·자격증명 ([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21)) | C | A09·A10, A19–A22, A47, A48, A50, A52 |
| 설정 파일·Profile 로딩 담당 등 ([#19](https://github.com/hsminu/MCP-Runtime-Guard/issues/19)) | 사용자 | A24–A27, A45, A49 |
| A가 정한 입력·동작 규칙 확인 | 사용자 | A56 |
| CI 2단계(C6.3)·C 환경 재측정 | C | A41, A46 |
| B의 AGT 제거 PR(같은 파일) | B | A44 |

## 계약·환경

- [x] A01·A03 B ref·테스트 동기화, Runner epoch 전달
- [ ] A01·A03 domain/ports 합의 — 막힘: 3인 합의
- [x] A02·A06 AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현
- [ ] A02·A06 AGT 제거 뒤 lock·새 환경 설치 재확인 — 막힘: B AGT 코드 제거
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
- [x] A09·A10 `tools/list` 수집과 도구 수·페이지·깊이·시간 상한 · [기록](archive/a/A09-A10-tools-list-collection.md)
- [x] A57 수집 시간 기준(연결 포함·초기화 시작부터)·반복 cursor·중복 이름·수집 중 종료 보정 · [기록](archive/a/A57-tools-list-timing-and-loop-rules.md)
- [ ] A09·A10 수신 직전 wire-size 상한 — 막힘: C stream ingress
- [ ] A09·A10 `tools/list` 수신 상한 페이지 1MiB·조회 누적 8MiB · [결정](contracts.md#사용자-결정) — 막힘: C stream ingress([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21) 5번)
- [x] A13 미종결 거부·정리 미확인 재시작 차단, SQLite 같은 스레드 종료 순서(W1)
- [ ] A13 이전 Task 종결·시작 복구 — 막힘: B-SQL-02·C 인수
- [x] A14 errlog 필수·SDK·합성 HTTP 서버 로그 격리
- [ ] A14 공개 handler 인수 — 막힘: C 로그 정책(A39와 함께)
- [x] A15·A16 등록 요구·단위 구현
- [ ] A15·A16 연결 직후 지문 대조·Registry·launcher 연결 — 막힘: C Registry·FP-01

## 접근 규칙·모델·Runner

- [x] A17 AGT core 5.0.0 선정, Windows 판정 스파이크 8경로 · [기록](archive/tasks/a-agt-selection.md)
- [ ] A17 Scanner와 함께 쓰는 공통 조합 검증 — 폐기: AGT 제거(2026-10-15 결정)
- [x] A18 실제 로컬 판정·real/mock·timeout/취소, 동시 16·worker 교체·종료 확인 · [기록](archive/tasks/a-agt-integration.md)
- [ ] A18 AGT 역할·규칙 관리 — 폐기: AGT 제거(2026-10-15 결정, A51로 대체)
- [ ] A18 영속 agt_mode 감사 — 폐기: AGT 제거(mode 감사 불필요)
- [ ] A18 dispatch 0 독립 인수 — 폐기: AGT 제거(자체 규칙은 C6.9로 검증)
- [ ] A51 자체 접근 규칙을 `AccessChecker`로 Guard에 연결·규칙 설정 로딩 — 막힘: B 판정 함수·ACCESS-01
- [ ] A53 B의 AGT 코드 제거 중 A 소유 파일 리뷰 — 막힘: B PR
- [ ] A19·A20 Codex 세션 관리자(app-server 하네스, 10.4절) — 막힘: `ModelClient` 포트·NOTIFY-01·정지 입구(#20), 고정 버전·launcher·L2 입구(#21)
- [x] A50 Codex용 JSON-RPC 클라이언트 뼈대(채널 주입, 프로세스 미실행) · [기록](archive/a/A50-jsonrpc-client-skeleton.md)
- [x] A55 JSON-RPC 클라이언트를 Codex 0.159 형식(`jsonrpc` 필드 없음)에 대응·알림 처리기 실패 구분 · [기록](archive/a/A55-jsonrpc-codex-envelope.md)
- [x] A58 JSON-RPC 전송 마감·처리기 안 close 교착·종료 사유 보정 · [기록](archive/a/A58-jsonrpc-send-and-close.md)
- [ ] A50 Codex 프레이밍·메서드 연결 — 막힘: 고정 버전([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21) 1번, 형식은 0.159.0으로만 확인)
- [x] A21·A22 Runner expected_epoch·승인 resume 연결
- [ ] A21·A22 실제 모델 세션 격리(T21) — 막힘: A19·A20
- [x] A23 B 리뷰: AGT 지연 중 긴급 차단 수정·회귀 · [기록](archive/tasks/a-b-review.md)
- [ ] A23 독립 wire 검증 — 막힘: C

## bridge·화면

- [x] A24–A27 프로세스 시작 게이트(W1)·옛 Task 실행/제어 거부(W2)·`--store sqlite` 연결·시작 실패 처리
- [ ] A24–A27 제품 Store·Registry 구성(지금은 메모리 기본·B 대역) — 막힘: B-SQL-01–06·C·설정 로딩 담당(#19)
- [ ] A28–A32 Registry·Scanner 조회 화면 — 막힘: 조회 계약 합의
- [x] A33·A34 승인 T18 필드·위젯·4스킴(W3), SQLite 승인 표시·중복 승인·재시작 권한 미복원
- [ ] A33·A34 과거 승인·제품 목록 통합 — 막힘: B-SQL-02/03
- [x] A35 로그 enum 매핑
- [ ] A35 영속 감사 조회 — 막힘: B6 조회
- [x] A36·A37 확정/요청 상태 분리·한글 표시
- [ ] A36·A37 전체 GUI 인수 — 막힘: C
- [x] A47 신뢰하지 않는 텍스트 표시 위젯(모델 답변·서버 설명·threat_summary) · [기록](archive/a/A47-untrusted-text-view.md)
- [ ] A47 실행 탭·등록 화면·Scanner 경고에 연결 — 막힘: UI 이벤트 계약(B1.5)·검토된 설명(#20 3번)·SCAN-01
- [x] A48 Scanner API 키 입력 화면(가림·끝 4자리·주입형 저장) · [기록](archive/a/A48-scanner-key-form.md)
- [ ] A48 자격증명 관리자 저장 연결 — 막힘: C2.10([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21) 8번)
- [x] A49 로컬 파일 서버 루트·하위 폴더 등급 입력 폼 · [기록](archive/a/A49-local-folder-labels-form.md)
- [x] A59 폴더 규칙 중복을 대소문자 무시로 검사 · [기록](archive/a/A59-folder-rule-case.md)
- [ ] A49 등록·출처 규칙 연결 — 막힘: C Registry·safe_path·설정 로딩 담당(#19)
- [ ] A52 Codex 로그인 화면(ChatGPT 기기 코드·API 키) — 막힘: 고정 버전·자격증명 저장 담당(#21 1·8번)
- [ ] A54 등록 화면 서버 ID 즉시 안내(`valid_server_id`) — 막힘: 사용 방식([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20) 6번)
- [ ] A56 A가 정한 규칙 확인(키 앞뒤 공백 제거·4자 이하 키 끝자리 숨김·폴더 규칙 최소 1개·tools/list 반복 cursor·중복 이름 중단) — 막힘: 사용자 확인(contracts에 없음, 이슈 등록 필요)
- [x] A61 Qt 시안 컴포넌트·실행/승인/중지 카드·아이콘·작은 화면 배치 · [기록](archive/a/A61-ui-components.md)
- [x] A63 세션 중심 대화·결과 화면·기존 기능 진입·Task/epoch 메시지 연결·원문 폐기·독립 GUI QA · [기록](archive/a/A63-session-workspace.md)
- [x] A64 SQLite 새 Task·모델 API 어댑터·키 설정 주입·세션 격리 연결부 — 실제 키 저장·API 사용·CLI 실행은 사용자 선택에 따라 C 구현 대기 · [기록](archive/a/A64-api-session-connections.md)

## 통합·패키징

- [x] A38–A40 합성 경로·SQLite/mock/stdio·HTTP ASK·ALLOW·DENY 계수, 재시작·시작 실패 게이트
- [ ] A38–A40 전체 복구·launcher/endpoint·독립 인수 — 막힘: C T/S
- [x] A41 lock·설치·단위 검증, CI 1단계(#4)
- [ ] A41 보안 스캔(CI 2단계) — 막힘: AGT 제거 뒤 C CI 2단계(C6.3)
- [ ] A42 인수: 리뷰 결과와 검증 인계
- [ ] A43 기준선 축소: UI 린트 예외 제거·포맷 제외 22→12·RuntimePanel 타입 억제 제거, UI 밖 기준선 남음 · [기록](archive/a/A61-ui-components.md)
- [ ] A44 시연 앱의 `tests.unit.test_guard` 대역 import 제거 — 막힘: B AGT 제거 PR과 같은 파일(`examples`)
- [ ] A45 `guard-app` 콘솔 명령이 checkout 안에서도 실패(`__main__.py:40`이 패키지 밖 `examples`를 import) — 막힘: 제품 시작 구성(A24–A27)
- [x] A46 HTTP transport TLS 문맥을 이벤트 루프 밖에서 생성(반복 연결 정지 해소) · [기록](archive/a/A46-tls-off-loop.md)
- [ ] A60 전체 단위 테스트 간헐 실패(실행마다 다른 B 테스트 1개, 오늘 작업 전 HEAD에서도 재현)·SDK HTTP 스트림 미닫힘 ResourceWarning — 원인 미분석
- [x] A46 첫 연결의 `httpcore` 최초 import 정지를 모듈 import로 이동 · [기록](archive/a/A46-httpcore-preload.md)
- [ ] A46 이전 a-status의 "C 측정 210–280ms" 재측정 — 막힘: C 환경(출처 기록 미확인)
