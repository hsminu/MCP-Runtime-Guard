# A 현황

갱신: 2026-10-27 · main `58afdd4` · 이전 상세: [2026-10-15까지의 A 현황](archive/status/a-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| Registry·endpoint 검증·fixture·독립 인수 | C | A04, A07–A12, A14–A16, A23, A36–A40 |
| 시작 복구·영속 call_seq·조회/감사 API (B-SQL-01–06) | B | A13, A24–A27, A33–A35 |
| domain/ports·UI 조회 DTO 합의 | A·B·C | A01·A03, A05, A28–A32 |
| `ModelClient` 제거·A66 세션 관리자 제안의 공통 계약 정의·합의 ([제안](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6246972714)) | A·B·C | A19–A22 |
| 검토된 설명(DESC-01)·SCAN-01 필드 3인 합의. 합의 전 제안은 [PR #32](https://github.com/hsminu/MCP-Runtime-Guard/pull/32)로 main 반영 ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20)) | A·B·C | A47 |
| 동기 `AccessChecker` 포트(ACCESS-01, B2.9)·AGT 코드 제거(B2.11)·계약 합의 | B·A·C | A02·A06, A41, A44, A53 |
| L2(C6.14)·T43/T46·수신 상한 독립 인수(C5.8)·MCP 서버 실행 파일 해시 출처(C2.6)·ScanReport·Scanner 자격증명(C2.10). launcher·앱 폴더(C2.12·C2.13)는 [PR #35](https://github.com/hsminu/MCP-Runtime-Guard/pull/35) 병합 대기 ([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21)) | C | A09·A10, A19–A22, A47, A48, A50, A52 |
| CFG-01 설정 파일: B 변환 함수([PR #36](https://github.com/hsminu/MCP-Runtime-Guard/pull/36) 병합 대기)·C 파일 연산(C2.14)·경로·실행 파일 검증 ([#33](https://github.com/hsminu/MCP-Runtime-Guard/issues/33)) | B·C | A24–A27, A49 |
| Profile 저장 연결(C2.14)·Registry 활성화(C4.10). B `activate_server`는 #32로 main 반영 | C | A81 |
| `main` 병합 순서: A 브랜치에 C 브랜치(#35)가 병합돼 있음 | C·사용자 | A68 main 반영, A98–A105 |
| CI 2단계(C6.3)·C 환경 재측정 | C | A41, A46 |
| B의 AGT 제거 PR(같은 파일) | B | A44 |

**A가 막고 있는 것**: 없음. B1.7·B2.11·C2.10·C5.8이 기다리던 A 브랜치는 [PR #29](https://github.com/hsminu/MCP-Runtime-Guard/pull/29)로 main에 병합됐다. #36 A 리뷰와 #37 A 회신은 게시했다.

## 계약·환경

- [x] A01·A03 B ref·테스트 동기화, Runner epoch 전달
- [ ] A01·A03 domain/ports 합의 — 막힘: 3인 합의
- [x] A02·A06 AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현
- [ ] A02·A06 AGT 제거 뒤 lock·새 환경 설치 재확인 — 막힘: B AGT 코드 제거
- [ ] A02·A06 Scanner 포함 공통 제품 조합 인수 — 막힘: C 조합 인수(main lock에는 Scanner 포함, #6)
- [ ] A04 C 연결: 요구사항은 전달 — 막힘: C Registry
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
- [x] A76 `tools/list` 파싱 전 페이지 1MiB·조회 누적 8MiB와 주입형 MCP 파이프 연결 · [기록](archive/a/A76-mcp-listing-byte-ingress.md)
- [x] A77 HTTP JSON·SSE 압축 해제 후 바이트 제한·종료 확인과 실패 격리 · [기록](archive/a/A77-http-listing-byte-ingress.md)
- [ ] A09·A10 C launcher 파이프 실제 연결·수신 상한 독립 인수 — 막힘: C2.6 MCP 서버 실행 파일 해시 출처·C5.8([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247463861))
- [x] A13 미종결 거부·정리 미확인 재시작 차단, SQLite 같은 스레드 종료 순서(W1)
- [ ] A13 이전 Task 종결·시작 복구 — 막힘: B-SQL-02·C 인수
- [x] A14 errlog 필수·SDK·합성 HTTP 서버 로그 격리
- [ ] A14 공개 handler 인수 — 막힘: C 로그 정책(A39와 함께)
- [x] A15·A16 등록 요구·단위 구현
- [x] A86 연결 직후 지문 대조·불일치 도구 차단(A15·A16, 주입형 Registry) · [기록](archive/a/A86-profile-guard-assembly.md)
- [x] A96 등록에 없는 도구가 보이면 서버 차단(PR #29 B 리뷰 3) · [기록](archive/a/A96-live-listing-added-tools.md)
- [x] A97 MCP 도구 오류(`is_error`)를 Codex에 실패로 전달 · [기록](archive/a/A97-released-tool-error.md)
- [x] A100 대조 뒤 `tools/list_changed`를 받으면 그 서버를 이번 실행 동안 PENDING 취급, 새 세션 도구 목록에서 제외(LIST-01, PR #34 결정) · [기록](archive/a/A100-tools-list-changed.md)
- [ ] A100 서버별 목록 수신 뒤·대조 전 알림 처리 — 막힘: 사용자 결정(현재는 결정 문구대로 차단하지 않음)
- [ ] A15·A16 실제 Registry·launcher 연결 — 막힘: C Registry. 지문 계산 기준(SDK 재구성 dict) C 확인 필요

## 접근 규칙·모델·Runner

- [x] A17 AGT core 5.0.0 선정, Windows 판정 스파이크 8경로 · [기록](archive/tasks/a-agt-selection.md)
- [ ] A17 Scanner와 함께 쓰는 공통 조합 검증 — 폐기: AGT 제거(2026-10-15 결정)
- [x] A18 실제 로컬 판정·real/mock·timeout/취소, 동시 16·worker 교체·종료 확인 · [기록](archive/tasks/a-agt-integration.md)
- [ ] A18 AGT 역할·규칙 관리 — 폐기: AGT 제거(2026-10-15 결정, A51로 대체)
- [ ] A18 영속 agt_mode 감사 — 폐기: AGT 제거(mode 감사 불필요)
- [ ] A18 dispatch 0 독립 인수 — 폐기: AGT 제거(자체 규칙은 C6.9로 검증)
- [x] A51 자체 접근 규칙을 `AccessChecker`로 Guard에 연결·규칙 설정 로딩(A86, 현재 async 포트) · [기록](archive/a/A86-profile-guard-assembly.md)
- [ ] A51 동기 포트 전환 — 막힘: B2.9(ACCESS-01)
- [ ] A53 B의 AGT 코드 제거 중 A 소유 파일 리뷰 — 막힘: B PR
- [x] A87 Codex 0.162.1 프로토콜 순수 함수·대본형 가짜 app-server · [기록](archive/a/A87-codex-protocol-and-fake-app-server.md)
- [x] A88 Codex 세션 관리자 A 계층(L3·Guard 중계·FIFO·감시 상한·제품 연결·취소 시 세션 정지) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A101 C launcher(C2.12)·앱 폴더(C2.13)·CODEX_HOME을 세션 준비에 연결, 로컬 codex.exe 지정(`--codex-exe`), 준비 실패는 앱이 아니라 세션만 차단 · [기록](archive/a/A101-codex-launcher-preparation.md)
- [x] A102 단일 실행 잠금을 DB 열기 전으로 옮기고, 잠금을 못 잡으면 앱을 시작하지 않음(REC-01의 C2.13 몫, #35 질문) · [기록](archive/a/A102-app-lock-before-db.md)
- [x] A103 Task 폴더 삭제를 작업 스레드로 옮기고 삭제 상한을 따로 둠(PR #35 B 리뷰) · [기록](archive/a/A103-task-folder-removal-thread.md)
- [x] A110 앱을 닫을 때 정리가 확인되지 않으면 단일 실행 잠금을 쥔 채 프로세스 종료(B6.17 8.1, #37) · [기록](archive/a/A110-unconfirmed-exit-keeps-lock.md)
- [ ] A19·A20 실제 codex.exe 세션 — 막힘: L2(C6.14)·Profile 설정 파일(CFG-01, B 변환·C 파일 연산)·로그인 뒤 CODEX_HOME `secrets` 허용(C6.19, A108 발견 1)
- [x] A107 실제 codex.exe 0.162.1·가짜 모델로 Guard 도구 호출 turn 스파이크(ALLOW·ASK 승인·DENY, 제품 경로 밖) · [기록](archive/a/A107-codex-real-exe-fake-model-spike.md)
- [x] A108 실제 로그인·실제 모델로 같은 turn(ALLOW·ASK 승인), 로그인 뒤 CODEX_HOME `secrets` 거부 발견 · [기록](archive/a/A108-codex-real-login-turn.md)
- [ ] A109 기기 코드 로그인이 막힌 계정 처리(화면 안내 또는 브라우저 로그인 지원) — 막힘: 사용자 결정(A108 발견 2)
- [x] A50 Codex용 JSON-RPC 클라이언트 뼈대(채널 주입, 프로세스 미실행) · [기록](archive/a/A50-jsonrpc-client-skeleton.md)
- [x] A55 JSON-RPC 클라이언트를 Codex 0.159 형식(`jsonrpc` 필드 없음)에 대응·알림 처리기 실패 구분 · [기록](archive/a/A55-jsonrpc-codex-envelope.md)
- [x] A58 JSON-RPC 전송 마감·처리기 안 close 교착·종료 사유 보정 · [기록](archive/a/A58-jsonrpc-send-and-close.md)
- [x] A67 JSON-RPC 진행 중 ID 중복 차단·Codex 0.162.1 공식 envelope/메서드 schema 대조 · [기록](archive/a/A67-jsonrpc-duplicate-and-codex-schema.md)
- [ ] A50 Codex 0.162.1 실제 프로세스·메서드 연결 — 막힘: B 세션 계약·C launcher·L1/L2 구현([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21))
- [x] A68 Task별 도구 호출 FIFO·callId 중복 거부·취소/종료 격리의 A 내부 연결부 · [기록](archive/a/A68-task-tool-call-fifo.md)
- [x] A68 Guard 호출·FIFO 세션 연결(A 내부 Pending 대기) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A68 NOTIFY-01·STOP-01로 교체, 사람 정지의 Store 직접 쓰기 제거(PR #29 B 리뷰 5) · [기록](archive/a/A68-notify-stop-integration.md)
- [x] A68 승인 결과 문구를 `final_model_message`로 교체(#32 로컬 병합 위) · [기록](archive/a/A68-final-model-message.md)
- [ ] A68 교체분 main 반영(A 브랜치에는 푸시함) — 막힘: #35 병합 뒤 A main PR
- [x] A80 세션 관리자 승인 대기 감시 상한을 Profile의 `approval_ttl_seconds`에서 계산 · [기록](archive/a/A88-codex-session-manager.md)
- [x] A88 L3 감시·`request_user_input` 빈 응답·고정 문구표 연결·인증/한도 오류 정지(토큰 갱신 충돌은 `unauthorized` 경로로 그 Task만 정지) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A87 `dynamicTools` 이름 규칙 위반 도구 제외·보고 · [기록](archive/a/A87-codex-protocol-and-fake-app-server.md)
- [x] A95 Codex에서 빠진 도구를 이유와 함께 정책 화면에 표시(2026-10-18 결정, PR #31) · [기록](archive/a/A95-codex-excluded-tools.md)
- [x] A92 contracts 10-17 "도구 이름 제한 없음"과 Codex 이름 규칙 충돌 알림([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6251209139) 2절, 사용자 결정 대기) · [기록](archive/a/A92-contract-replies-and-b72-review.md)
- [x] A90 `baseInstructions` 프롬프트 문구·C1.9 관측 지점(`SessionObservations`) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A90 답변 스트리밍의 대화 화면 연결(bridge `conversation_partial`, 부분 답 교체·중단 표시) · [기록](archive/a/A90-answer-streaming.md)
- [x] A74 Task 폴더 정리 실제 연결(A101, C2.13 `remove_task_folder`) · [기록](archive/a/A101-codex-launcher-preparation.md)
- [x] A72 앱 시작 준비 1회·Task별 구성 지문/L2 대조 연결부 · [기록](archive/a/A72-per-task-configuration-gate.md)
- [x] A73 Codex stdin/stdout 바이트 프레이밍 어댑터·상한/종료 검증 · [기록](archive/a/A73-codex-stream-framing.md)
- [x] A74 주입형 Task 자원 준비·종료 확인·정리 순서와 미확인 재시작 차단 · [기록](archive/a/A74-task-resource-lifecycle.md)
- [x] A98 종료 확인(`confirm_exit`) 상한을 정리 상한과 분리, C2.12 `close()` 대기 수용(PR #35 A 리뷰) · [기록](archive/a/A98-confirm-exit-bound.md)
- [x] A99 Codex 원격 제어가 `disabled`가 아니면 Task 정지·로그인 연결 종료(C-CODEX-07, PR #35 A 리뷰) · [기록](archive/a/A99-remote-control-stop.md)
- [x] A21·A22 Runner expected_epoch·승인 resume 연결
- [x] A21·A22 Task별 새 세션·thread 격리(가짜 app-server) · [기록](archive/a/A88-codex-session-manager.md)
- [ ] A21·A22 실제 codex.exe로 T21 — 막힘: L2(C6.14)·Profile(CFG-01). launcher 연결은 A101
- [x] A23 B 리뷰: AGT 지연 중 긴급 차단 수정·회귀 · [기록](archive/tasks/a-b-review.md)
- [ ] A23 독립 wire 검증 — 막힘: C

## bridge·화면

- [x] A24–A27 프로세스 시작 게이트(W1)·옛 Task 실행/제어 거부(W2)·`--store sqlite` 연결·시작 실패 처리
- [x] A24–A27 제품 SQLite 기본 시작·새 Task 연결부 · [기록](archive/a/A65-codex-only-concurrent-tasks.md)
- [x] A24–A27 Profile 로딩·Guard 조립을 제품 시작에 연결(A86, 주입형 설정 문서·Registry) · [기록](archive/a/A86-profile-guard-assembly.md)
- [ ] A24–A27 설정 파일 읽기·실제 Registry·시작 복구 — 막힘: #36 병합·C2.14·C Registry·B6.8
- [ ] A28–A32 Registry·Scanner 조회 화면 — 막힘: 조회 계약 합의
- [x] A33·A34 승인 T18 필드·위젯·4스킴(W3), SQLite 승인 표시·중복 승인·재시작 권한 미복원
- [ ] A33·A34 과거 승인·제품 목록 통합 — 막힘: B-SQL-02/03
- [x] A35 로그 enum 매핑
- [ ] A35 영속 감사 조회 — 막힘: B6 조회
- [x] A36·A37 확정/요청 상태 분리·한글 표시
- [ ] A36·A37 전체 GUI 인수 — 막힘: C
- [x] A47 신뢰하지 않는 텍스트 표시 위젯(모델 답변·서버 설명·threat_summary) · [기록](archive/a/A47-untrusted-text-view.md)
- [x] A47 모델 대화의 비신뢰 텍스트·일반 텍스트 표시 연결 · [기록](archive/a/A63-session-workspace.md)
- [ ] A47 등록 화면·Scanner 경고에 연결 — 막힘: UI 이벤트 계약(B1.5)·검토된 설명(#20 3번)·SCAN-01
- [x] A48 Scanner API 키 입력 화면(가림·끝 4자리·주입형 저장) · [기록](archive/a/A48-scanner-key-form.md)
- [ ] A48 자격증명 관리자 저장 연결 — 막힘: C2.10([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21) 8번)
- [x] A49 로컬 파일 서버 루트·하위 폴더 등급 입력 폼 · [기록](archive/a/A49-local-folder-labels-form.md)
- [x] A59 폴더 규칙 중복을 대소문자 무시로 검사 · [기록](archive/a/A59-folder-rule-case.md)
- [ ] A49 등록·출처 규칙 연결 — 막힘: C Registry·safe_path·설정 로딩 담당(#19)
- [x] A89 Codex 로그인 화면·`account/read`·기기 코드 로그인/취소/로그아웃 · [기록](archive/a/A89-codex-login-panel.md)
- [x] A89 API 키 로그인 입력란(2026-10-18 결정, PR #31) · [기록](archive/a/A89-api-key-field.md)
- [ ] A52 실제 app-server로 로그인 — 막힘: C launcher·T43 검증(#21)
- [x] A54 등록 화면 서버 ID 즉시 안내·bridge에서 B 검사 결과 전달 · [기록](archive/a/A54-server-id-bridge-validation.md)
- [x] A81 서버 활성화 단계의 도구별 허용 체크박스·Task 정지 안내(정책 화면) · [기록](archive/a/A91-policy-screen.md)
- [x] A91 정책 화면(Profile·정책 버전·TTL·taint·접근 규칙 읽기 전용) · [기록](archive/a/A91-policy-screen.md)
- [x] A81 규칙 저장을 B3.10에 연결(prepare → 주입형 저장 → apply, 정지 세션 종료) · [기록](archive/a/A81-policy-change-save.md)
- [x] A104 저장 뒤 적용 실패·적용 전 거부면 Guard를 닫고 새 세션·재저장을 막아 재시작 안내(#33 B 답변) · [기록](archive/a/A104-policy-apply-failure-restart.md)
- [x] A105 새 Task 행을 정책 잠금 안에서 그때의 policy_version으로 씀(B3.12의 Task 생성 직렬화) · [기록](archive/a/A105-task-creation-policy-lock.md)
- [ ] A105 서버 활성화도 같은 잠금으로 묶음 — 막힘: C Registry 활성화(C4.10)
- [x] A117 겹친 서버 접근 변경이 앞 변경이 저장한 문서에서 시작함(정책 잠금 안에서 문서를 읽음, A112 발견) · [기록](archive/a/A117-overlapping-policy-changes.md)
- [ ] A106 제품 DB를 앱 폴더 고정 경로로 옮기고 `--demo`가 그 경로를 열지 못하게 함 — 막힘: C `AppPaths` DB 경로(#35 B 리뷰 1번)
- [ ] A81·A91 실제 Profile 저장·서버 목록 활성화 흐름 — 막힘: #36 병합·C2.14·C Registry 활성화(C4.10)
- [x] A83 조회 화면 미연결 표시·세션 시작 가능 여부·모델 이름 상수·창 제목·실행 옵션 정리 · [기록](archive/a/A83-ui-unconnected-and-product-cli.md)
- [x] A85 Task 등급·미분류 표시 · [기록](archive/a/A85-task-label-display.md)
- [x] A56 입력·수집 규칙 다섯 개 사용자 승인 확인 · [기록](archive/a/A56-input-rules-approved.md)
- [x] A61 Qt 시안 컴포넌트·실행/승인/중지 카드·아이콘·작은 화면 배치 · [기록](archive/a/A61-ui-components.md)
- [x] A63 세션 중심 대화·결과 화면·기존 기능 진입·Task/epoch 메시지 연결·원문 폐기·독립 GUI QA · [기록](archive/a/A63-session-workspace.md)
- [x] A64 SQLite 새 Task·세션 격리 연결부 — 직접 모델 API 어댑터·키 설정 주입은 A65에서 제거(2026-10-18 결정: 모델 경로는 Codex 하네스만) · [기록](archive/a/A64-api-session-connections.md)
- [x] A65 직접 모델 API 제거·Codex 전용 주입부·L2 시작 게이트·Task별 동시 실행과 정지 격리 · [기록](archive/a/A65-codex-only-concurrent-tasks.md)
- [x] A66 세션 관리자 인터페이스 제안 v1·담당 경계 명확화 · [기록](archive/a/A66-session-interface-proposal.md)
- [x] A69 개인 사용자 문구·Withheld 상태 안내·SQLite/대화 연결 현황 보정 · [기록](archive/a/A69-ui-wording-and-status.md)
- [x] A75 구성·준비 실패와 정리 미확인 안내·Task별 화면 격리 · [기록](archive/a/A75-session-preparation-notices.md)
- [x] A78 QA High·Medium 수정: 수집 전 예측 응답의 목록 완료 차단, JSON-RPC 취소 시 종료, 끝난 Task 상태 표시, 대화창 화자 위조 차단 · [기록](archive/a/A78-qa-runtime-ui-fixes.md)
- [x] A79 QA Low 5건 수정: Scanner 키 예외 문구·Withheld 문구·결과 화면 None·거부 코드 안내·정리 겹침 미확인 · [기록](archive/a/A79-qa-low-fixes.md)
- [x] A82 넓은 예외 경계의 `Task.exception()` 고정 코드 변환을 허용 패턴으로 정리(2026-10-18 사용자 결정) · [기록](archive/a/A82-task-exception-boundary.md)
- [x] A79 `codex-pending` profile·policy_version을 `GuardSettings` 값으로 교체 · [기록](archive/a/A86-profile-guard-assembly.md)

## 통합·패키징

- [x] A38–A40 합성 경로·SQLite/mock/stdio·HTTP ASK·ALLOW·DENY 계수, 재시작·시작 실패 게이트
- [ ] A38–A40 전체 복구·launcher/endpoint·독립 인수 — 막힘: C T/S
- [x] A41 lock·설치·단위 검증, CI 1단계(#4)
- [ ] A41 보안 스캔(CI 2단계) — 막힘: AGT 제거 뒤 C CI 2단계(C6.3)
- [ ] A42 인수: 리뷰 결과와 검증 인계
- [x] A84 제품 `CodexTaskRunner`·제품 시작 경로·icons/style 테스트 보강 · [기록](archive/a/A84-product-runner-and-startup-tests.md)
- [x] A94 GPT·Opus QA와 B7.2 리뷰의 A 결함 수정(승인 경로·서버 단위 지문 차단·세션 정지 전역 정지 방지 등) · [기록](archive/a/A94-qa-gpt-opus-fixes.md)
- [x] A92 B6-01–06·ACCESS-01·AGT-04 회신, B7.2 Guard·Catalog 리뷰, #21 C1.9 관측 지점 조율, PR #28 리뷰 · [기록](archive/a/A92-contract-replies-and-b72-review.md)
- [x] A93 A 브랜치 main 병합 PR([#29](https://github.com/hsminu/MCP-Runtime-Guard/pull/29), B·C 리뷰·사용자 병합 대기) · [기록](archive/a/A93-main-merge-pr.md)
- [x] A71 A 단위 테스트 포맷 제외 대상 정리·AST 동일 검증 · [기록](archive/a/A71-a-unit-format-baseline.md)
- [x] A43 공통 포맷 제외 목록·타입/린트 예외 축소(A93 PR에서 공통 설정 변경으로 표시) · [기록](archive/a/A43-lint-type-baseline-reduction.md)
- [ ] A44 시연 앱의 `tests.unit.test_guard` 대역 import 제거 — 막힘: B AGT 제거 PR과 같은 파일(`examples`)
- [x] A45 제품 wheel 외부 설치·콘솔·offscreen 시작/종료·C 미제공 시작 거부 검증 · [기록](archive/a/A45-installed-wheel-product-smoke.md)
- [x] A46 HTTP transport TLS 문맥을 이벤트 루프 밖에서 생성(반복 연결 정지 해소) · [기록](archive/a/A46-tls-off-loop.md)
- [ ] A60 SDK HTTP 전체 경고 해소·B 간헐 실패 재현 — SDK 1.30.0 SSE 재현은 해소, 별도 소켓 경고와 B 실패 원인 미확정·공통 버전 검토 대기
- [x] A70 SDK 수정 버전 공식 소스 조사·격리 비교 검증 · [기록](archive/a/A70-sdk-http-upgrade-validation.md)
- [x] A62 HTTP ResourceWarning 재현·스트림 생성 위치 추적 · [기록](archive/a/A62-http-stream-warning-investigation.md)
- [x] A46 첫 연결의 `httpcore` 최초 import 정지를 모듈 import로 이동 · [기록](archive/a/A46-httpcore-preload.md)
- [ ] A46 이전 a-status의 "C 측정 210–280ms" 재측정 — 막힘: C 환경(출처 기록 미확인)

## 리팩터링(동작 유지)

- [x] A111 0단계: A 모듈 계층·시큐어 코딩 규칙 테스트와 기준선 측정 · [기록](archive/a/A111-refactor-baseline-and-rules.md)
- [x] A112 1단계: `SessionRuntime` 책임 분리(생성·정리·정책 변경·시작 게이트) · [기록](archive/a/A112-session-runtime-split.md)
- [x] A113 2단계: `mcp_transport`를 같은 이름의 패키지로 나누고 긴 함수 정리 · [기록](archive/a/A113-mcp-transport-package.md)
  - 남은 것: B 리뷰, 개발문서·team-workflow의 파일 경로 갱신(main PR 제안)
- [x] A118 A113에서 찾은 mcp_transport 테스트 빈틈 9개 중 7개를 테스트로 메움(나머지 2개는 관찰 불가) · [기록](archive/a/A118-mcp-transport-test-gaps.md)
- [ ] A114 3단계: Codex 계층(`codex_session`·`jsonrpc_client`·`codex_stream`·`guard_relay`) 정리
- [ ] A115 4단계: `BackendBridge` 안쪽 분리(C 리뷰) — 막힘: B AGT 제거(B2.11, 같은 파일)
- [ ] A116 5단계: UI 화면 조립·`__main__` 정리(C 리뷰) — 막힘: B AGT 제거(B2.11, 같은 파일)
