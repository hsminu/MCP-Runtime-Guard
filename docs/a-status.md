# A 현황

갱신: 2026-10-29 · 이전 상세: [2026-10-15까지의 A 현황](archive/status/a-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| fixture·로그 정책·독립 인수 | C | A07–A12, A14–A16, A23, A36–A40 |
| 시작 복구·영속 call_seq·조회/감사 API (B-SQL-01–06) | B | A13, A24–A27, A33–A35 |
| domain/ports·UI 조회 DTO 합의 | A·B·C | A01·A03, A05, A28–A32 |
| `ModelClient` 제거·A66 세션 관리자 제안의 공통 계약 정의·합의 ([제안](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6246972714)) | A·B·C | A19–A22, A50 |
| 검토된 설명(DESC-01)·SCAN-01 필드 3인 합의. 합의 전 제안은 [PR #32](https://github.com/hsminu/MCP-Runtime-Guard/pull/32)로 main 반영 ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20)) | A·B·C | A47 |
| T43/T46·수신 상한 독립 인수(C5.8) ([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21)) | C | A09·A10, A52 |
| 제품 Guard 조립용 영속 `next_call_seq`·실패 감사(B6.8). 설정 파일 읽기·저장과 Registry 로드는 A129로 연결됨 | B | A24–A27, A81, A129 |
| CI 2단계(C6.3)·C 환경 재측정 | C | A41, A46 |
| bridge 및 목록 알림 변경 리뷰 | C·B | A115, A140 |
| 실제 writer의 종료·정지 경합 재현 조건 ([#58 회신](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6473123051)) | B | A134 |

**A가 막고 있는 것**: C4.13 경고 표시 화면(A47·A128), C2.3·C2.11 파일 서버 등록·출처 폴더 연결(A49, #63·#64·#67 병합됨), C2.10 키 입력 화면 연결(#60, A 브랜치에 병합됨), C5.8 launcher 파이프 연결(A09·A10, EXE-01 B 필드는 #58로 병합됨), EXE-01 지문 확인 화면(A128), C6.20 설치판 리소스 위치(패키징).

## 계약·환경

- [x] A01·A03 B ref·테스트 동기화, Runner epoch 전달
- [ ] A01·A03 domain/ports 합의 — 막힘: 3인 합의
- [x] A02·A06 AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현
- [x] A02·A06 AGT 제거 뒤 lock·새 환경 설치 재확인 · [기록](archive/a/A02-A06-lock-recheck-after-agt-removal.md)
- [ ] A02·A06 Scanner 포함 공통 제품 조합 인수 — 막힘: C 조합 인수(main lock에는 Scanner 포함, #6)
- [ ] A04 C Registry 제품 연결 — A 차례: Registry 구현 병합, A128 등록·활성화 연결
- [x] A05 앱 내부 Task projection·사람 review 연결
- [ ] A05 공통 조회 DTO — 막힘: B·C 계약 검토

## transport·worker

- [x] A07·A11 stdio 실제 SDK·Guard 왕복 스파이크
- [ ] A07·A11 정식 C 수신 fixture로 인수 — 막힘: C fixture
- [x] A08·A12 Streamable HTTP adapter·loopback 왕복·JSON/SSE·재전송 차단 · [기록](archive/tasks/a-http-integration.md)
- [x] A08 HTTP 연결 직전 endpoint를 C `canonical_endpoint`(C2.5, #50)로 다시 검사 · [기록](archive/a/A08-canonical-endpoint.md)
- [ ] A08 등록 화면·A128 붙여넣기의 URL 확인을 C `normalize_endpoint`·거부 코드별 문구로 — A 차례(#52 C 리뷰)
- [ ] A08·A12 독립 fixture 인수·인증 확장 — 막힘: C
- [x] A09·A10 worker 구현·회귀 검증
- [x] A09·A10 `tools/list` 수집과 도구 수·페이지·깊이·시간 상한 · [기록](archive/a/A09-A10-tools-list-collection.md)
- [x] A57 수집 시간 기준(연결 포함·초기화 시작부터)·반복 cursor·중복 이름·수집 중 종료 보정 · [기록](archive/a/A57-tools-list-timing-and-loop-rules.md)
- [x] A76 `tools/list` 파싱 전 페이지 1MiB·조회 누적 8MiB와 주입형 MCP 파이프 연결 · [기록](archive/a/A76-mcp-listing-byte-ingress.md)
- [x] A77 HTTP JSON·SSE 압축 해제 후 바이트 제한·종료 확인과 실패 격리 · [기록](archive/a/A77-http-listing-byte-ingress.md)
- [ ] A09·A10 C launcher 파이프 실제 연결·수신 상한 독립 인수 — A 차례(EXE-01 B 필드 #58·C2.6 #54 병합됨). 독립 인수는 C5.8([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247463861))
- [x] A13 미종결 거부·정리 미확인 재시작 차단, SQLite 같은 스레드 종료 순서(W1)
- [ ] A13 이전 Task 종결·시작 복구 — 막힘: B-SQL-02·C 인수
- [x] A14 errlog 필수·SDK·합성 HTTP 서버 로그 격리
- [ ] A14 공개 handler 인수 — 막힘: C 로그 정책(A39와 함께)
- [x] A15·A16 등록 요구·단위 구현
- [x] A86 연결 직후 지문 대조·불일치 도구 차단(A15·A16, 주입형 Registry) · [기록](archive/a/A86-profile-guard-assembly.md)
- [x] A96 등록에 없는 도구가 보이면 서버 차단(PR #29 B 리뷰 3) · [기록](archive/a/A96-live-listing-added-tools.md)
- [x] A97 MCP 도구 오류(`is_error`)를 Codex에 실패로 전달 · [기록](archive/a/A97-released-tool-error.md)
- [x] A100 대조 뒤 `tools/list_changed`를 받으면 그 서버를 이번 실행 동안 PENDING 취급, 새 세션 도구 목록에서 제외(LIST-01, PR #34 결정) · [기록](archive/a/A100-tools-list-changed.md)
- [x] A140 A100 후속: 서버별 첫 목록 응답 뒤 알림은 대조 전에도 이번 실행 차단 · [기록](archive/a/A140-list-change-before-seal.md)
- [ ] A140 수신 순서·서버 격리 리뷰 — 막힘: [#70](https://github.com/hsminu/MCP-Runtime-Guard/pull/70) B·C 리뷰, 실제 알림 시점 관찰은 C5.9
- [x] A132 HTTP 세션이 요청 오류(`httpx.HTTPError`)만으로 끝나면 종료를 완료로 봄(#61 A 후속 2) · [기록](archive/a/A132-http-teardown-completed.md)
- [x] A141 A132의 HTTP 종료 기대값 C 수정(#68)을 A 브랜치에 통합·전체 재검사 · [기록](archive/a/A141-c68-integration-check.md)
- [x] A133 세션이 끊긴 서버를 이번 실행 동안 PENDING으로 보고 다음 호출을 예약 전 `REG_NOT_ACTIVE`로 거부(#61 A 후속 3) · [기록](archive/a/A133-failed-server-pending.md)
- [x] A15·A16 연결 직후 목록을 C `ServerRegistry.observe`로 기록(정의 변경은 PENDING으로 저장, 기록 실패는 이번 실행 차단) · [기록](archive/a/A129-settings-file-registry-connection.md)
- [ ] A15·A16 실제 launcher 연결 — A 차례(EXE-01 B 필드 #58·C2.6 #54 병합됨)

## 접근 규칙·모델·Runner

- [x] A17 AGT core 5.0.0 선정, Windows 판정 스파이크 8경로 · [기록](archive/tasks/a-agt-selection.md)
- [ ] A17 Scanner와 함께 쓰는 공통 조합 검증 — 폐기: AGT 제거(2026-10-15 결정)
- [x] A18 실제 로컬 판정·real/mock·timeout/취소, 동시 16·worker 교체·종료 확인 · [기록](archive/tasks/a-agt-integration.md)
- [ ] A18 AGT 역할·규칙 관리 — 폐기: AGT 제거(2026-10-15 결정, A51로 대체)
- [ ] A18 영속 agt_mode 감사 — 폐기: AGT 제거(mode 감사 불필요)
- [ ] A18 dispatch 0 독립 인수 — 폐기: AGT 제거(자체 규칙은 C6.9로 검증)
- [x] A51 자체 접근 규칙을 `AccessChecker`로 Guard에 연결·규칙 설정 로딩(A86, 현재 async 포트) · [기록](archive/a/A86-profile-guard-assembly.md)
- [x] A51·A53 동기 포트 전환과 AGT 제거에서 바뀐 A 소유 파일 리뷰(#47 A 승인) · [기록](archive/a/A51-A53-agt-removal-review.md)
- [x] A87 Codex 0.162.1 프로토콜 순수 함수·대본형 가짜 app-server · [기록](archive/a/A87-codex-protocol-and-fake-app-server.md)
- [x] A88 Codex 세션 관리자 A 계층(L3·Guard 중계·FIFO·감시 상한·제품 연결·취소 시 세션 정지) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A101 C launcher(C2.12)·앱 폴더(C2.13)·CODEX_HOME을 세션 준비에 연결, 로컬 codex.exe 지정(`--codex-exe`), 준비 실패는 앱이 아니라 세션만 차단 · [기록](archive/a/A101-codex-launcher-preparation.md)
- [x] A102 단일 실행 잠금을 DB 열기 전으로 옮기고, 잠금을 못 잡으면 앱을 시작하지 않음(REC-01의 C2.13 몫, #35 질문) · [기록](archive/a/A102-app-lock-before-db.md)
- [x] A103 Task 폴더 삭제를 작업 스레드로 옮기고 삭제 상한을 따로 둠(PR #35 B 리뷰) · [기록](archive/a/A103-task-folder-removal-thread.md)
- [x] A110 앱을 닫을 때 정리가 확인되지 않으면 단일 실행 잠금을 쥔 채 프로세스 종료(B6.17 8.1, #37) · [기록](archive/a/A110-unconfirmed-exit-keeps-lock.md)
- [x] A134 앱 종료가 세션 정지 기록(STOP-01)을 기다린 뒤 Guard·Store를 닫음(#58 B 답변의 `SystemControlError`) · [기록](archive/a/A134-shutdown-waits-for-stop-record.md)
- [ ] A134 #58 writer로 경합 재현·해소 확인 — 막힘: B 재현 조건([사후 확인·요청](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6473123051))
- [x] A136 #58 병합: Store 닫기 뒤 `wait_closed(5.0)`으로 연결 종료 확인, 미확인이면 `CLEANUP_UNCONFIRMED`·잠금 유지(시작 실패·시연 포함) · [기록](archive/a/A136-store-close-confirmed.md)
- [ ] A19·A20 실제 codex.exe 세션 — C 쪽 막힘은 풀림(L2 C6.14 #41, 설정 파일 CFG-01 #44·A129, CODEX_HOME `secrets` 허용 #35). 남은 것: A `run_l2` 연결, 세션 관리자 공통 계약 합의(막힌 것 표)
- [x] A107 실제 codex.exe 0.162.1·가짜 모델로 Guard 도구 호출 turn 스파이크(ALLOW·ASK 승인·DENY, 제품 경로 밖) · [기록](archive/a/A107-codex-real-exe-fake-model-spike.md)
- [x] A108 실제 로그인·실제 모델로 같은 turn(ALLOW·ASK 승인), 로그인 뒤 CODEX_HOME `secrets` 거부 발견 · [기록](archive/a/A108-codex-real-login-turn.md)
- [ ] A109 기기 코드 로그인이 막힌 계정 처리(화면 안내 또는 브라우저 로그인 지원) — 막힘: 사용자 결정(A108 발견 2)
- [x] A50 Codex용 JSON-RPC 클라이언트 뼈대(채널 주입, 프로세스 미실행) · [기록](archive/a/A50-jsonrpc-client-skeleton.md)
- [x] A55 JSON-RPC 클라이언트를 Codex 0.159 형식(`jsonrpc` 필드 없음)에 대응·알림 처리기 실패 구분 · [기록](archive/a/A55-jsonrpc-codex-envelope.md)
- [x] A58 JSON-RPC 전송 마감·처리기 안 close 교착·종료 사유 보정 · [기록](archive/a/A58-jsonrpc-send-and-close.md)
- [x] A67 JSON-RPC 진행 중 ID 중복 차단·Codex 0.162.1 공식 envelope/메서드 schema 대조 · [기록](archive/a/A67-jsonrpc-duplicate-and-codex-schema.md)
- [ ] A50 Codex 0.162.1 실제 프로세스·메서드 연결 — A19·A20과 같음; C launcher·L1/L2 구현은 병합됨([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21))
- [x] A68 Task별 도구 호출 FIFO·callId 중복 거부·취소/종료 격리의 A 내부 연결부 · [기록](archive/a/A68-task-tool-call-fifo.md)
- [x] A68 Guard 호출·FIFO 세션 연결(A 내부 Pending 대기) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A68 NOTIFY-01·STOP-01로 교체, 사람 정지의 Store 직접 쓰기 제거(PR #29 B 리뷰 5) · [기록](archive/a/A68-notify-stop-integration.md)
- [x] A68 승인 결과 문구를 `final_model_message`로 교체(#32 로컬 병합 위) · [기록](archive/a/A68-final-model-message.md)
- [x] A80 세션 관리자 승인 대기 감시 상한을 Profile의 `approval_ttl_seconds`에서 계산 · [기록](archive/a/A88-codex-session-manager.md)
- [x] A88 L3 감시·`request_user_input` 빈 응답·고정 문구표 연결·인증/한도 오류 정지(토큰 갱신 충돌은 `unauthorized` 경로로 그 Task만 정지) · [기록](archive/a/A88-codex-session-manager.md)
- [x] A87 `dynamicTools` 이름 규칙 위반 도구 제외·보고 · [기록](archive/a/A87-codex-protocol-and-fake-app-server.md)
- [x] A95 Codex에서 빠진 도구를 이유와 함께 정책 화면에 표시(2026-10-18 결정, PR #31) · [기록](archive/a/A95-codex-excluded-tools.md)
- [x] A92 contracts 10-17 "도구 이름 제한 없음"과 Codex 이름 규칙 충돌 알림([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6251209139) 2절, 사용자 결정 대기) · [기록](archive/a/A92-contract-replies-and-b72-review.md)
- [x] A120 대화 입력창에서 고른 추론 수준을 그 턴부터 Codex `turn/start`에 전달(`ultra` 제외) · [기록](archive/a/A120-reasoning-effort.md)
- [ ] A120 실제 codex.exe에서 추론 수준 반영 확인 — A19·A20과 같음
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
- [ ] A21·A22 실제 codex.exe로 T21 — A19·A20과 같음. launcher 연결은 A101
- [x] A23 B 리뷰: AGT 지연 중 긴급 차단 수정·회귀 · [기록](archive/tasks/a-b-review.md)
- [ ] A23 독립 wire 검증 — 막힘: C

## bridge·화면

- [x] A24–A27 프로세스 시작 게이트(W1)·옛 Task 실행/제어 거부(W2)·`--store sqlite` 연결·시작 실패 처리
- [x] A24–A27 제품 SQLite 기본 시작·새 Task 연결부 · [기록](archive/a/A65-codex-only-concurrent-tasks.md)
- [x] A24–A27 Profile 로딩·Guard 조립을 제품 시작에 연결(A86, 주입형 설정 문서·Registry) · [기록](archive/a/A86-profile-guard-assembly.md)
- [x] A129 시작 때 설정 파일 읽기(C2.14·B3.11)·Registry 로드(C4.10)·Profile 확인, 미설정·오류 상태 표시와 새 Task 거부, 정책 저장을 파일 교체에 연결(결과 불명은 재시작) · [기록](archive/a/A129-settings-file-registry-connection.md)
- [x] A130 #52 B·C 리뷰 후속: 저장 중 취소도 Guard를 닫음, 앱 밖에서 바뀐 설정 파일은 저장하지 않고 재시작 안내, `turn/start` 키 집합 고정 테스트 · [기록](archive/a/A130-pr52-review-followups.md)
- [x] A131 승인 대기 강조: 대화 화면 승인 안내 띠(사본 확인 버튼), 활동 패널 승인 구역 warn 강조, 승인 사본 한 필드 한 줄, 사이드바 승인 대기 행 글자색 · [기록](archive/a/A131-approval-emphasis.md)
- [x] A135 실행 컨트롤 안내의 내부 코드를 접힌 줄로 내림(#52 C 리뷰 UI 3. UI 1·2는 A131) · [기록](archive/a/A135-internal-code-fold.md)
- [ ] A135 설정 파일이 없을 때 화면에서 첫 파일 만들기(#52 C 리뷰 참고) — 등록 흐름(A81·A91)에서. 막힘: B6.8
- [ ] A24–A27 제품 Guard 조립·시작 복구 — 막힘: B6.8(영속 call_seq·실패 감사); C2.6·C2.5는 병합됨
- [ ] A138 제품 Guard 조립 때 #58 연결: `on_global_halt=store.halt_execution`, 새 turn·도구 응답·새 Task 전 `execution_state()` 확인과 `state_revision` 비교([#58](https://github.com/hsminu/MCP-Runtime-Guard/pull/58) A 요청) — A24–A27과 같이
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
- [ ] A48 자격증명 관리자 저장 연결 — A 차례: C2.10 [#60](https://github.com/hsminu/MCP-Runtime-Guard/pull/60) 병합됨
- [x] A49 로컬 파일 서버 루트·하위 폴더 등급 입력 폼 · [기록](archive/a/A49-local-folder-labels-form.md)
- [x] A59 폴더 규칙 중복을 대소문자 무시로 검사 · [기록](archive/a/A59-folder-rule-case.md)
- [ ] A49 등록·출처 규칙 연결 — A 차례: 폴더 선택 UI·`file_resource_resolver`·파일 서버 등록 값·상위 폴더 등급 안내([#63 수용](https://github.com/hsminu/MCP-Runtime-Guard/pull/63#issuecomment-6472888055), [#64 수용](https://github.com/hsminu/MCP-Runtime-Guard/pull/64#issuecomment-6472888675))
- [x] A89 Codex 로그인 화면·`account/read`·기기 코드 로그인/취소/로그아웃 · [기록](archive/a/A89-codex-login-panel.md)
- [x] A89 API 키 로그인 입력란(2026-10-18 결정, PR #31) · [기록](archive/a/A89-api-key-field.md)
- [x] A52 로그인 화면 전용 app-server, 로그아웃 뒤 자격증명 정리, 세션 중 로그아웃 막기([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21)) · [기록](archive/a/A52-account-app-server-logout.md)
- [ ] A52 실제 codex.exe로 로그인·로그아웃 정리 확인 — C와 함께(T43)
- [x] A54 등록 화면 서버 ID 즉시 안내·bridge에서 B 검사 결과 전달 · [기록](archive/a/A54-server-id-bridge-validation.md)
- [x] A81 서버 활성화 단계의 도구별 허용 체크박스·Task 정지 안내(정책 화면) · [기록](archive/a/A91-policy-screen.md)
- [x] A91 정책 화면(Profile·정책 버전·TTL·taint·접근 규칙 읽기 전용) · [기록](archive/a/A91-policy-screen.md)
- [x] A81 규칙 저장을 B3.10에 연결(prepare → 주입형 저장 → apply, 정지 세션 종료) · [기록](archive/a/A81-policy-change-save.md)
- [x] A104 저장 뒤 적용 실패·적용 전 거부면 Guard를 닫고 새 세션·재저장을 막아 재시작 안내(#33 B 답변) · [기록](archive/a/A104-policy-apply-failure-restart.md)
- [x] A105 새 Task 행을 정책 잠금 안에서 그때의 policy_version으로 씀(B3.12의 Task 생성 직렬화) · [기록](archive/a/A105-task-creation-policy-lock.md)
- [ ] A105 서버 활성화도 같은 잠금으로 묶음 — A 차례: C Registry 활성화(C4.10) 병합됨
- [x] A117 겹친 서버 접근 변경이 앞 변경이 저장한 문서에서 시작함(정책 잠금 안에서 문서를 읽음, A112 발견) · [기록](archive/a/A117-overlapping-policy-changes.md)
- [x] A106 제품 DB를 앱 폴더 고정 경로(`data\guard.sqlite3`, C2.17)로 옮기고 `--db-path`는 시연 전용으로, `--demo`는 앱 폴더 안을 열지 못하게 함(#35 B 리뷰 1번) · [기록](archive/a/A106-product-db-path.md)
- [x] A106 C `AppFolders.db_path()`(DB·`-wal`·`-shm` 링크·권한 확인)로 교체, 거부되면 창을 열기 전에 시작하지 않음 · [기록](archive/a/A106-db-path-check.md)
- [ ] A81·A91 제품 화면에서 Profile 저장·서버 활성화 — 막힘: 제품 Guard 조립(B6.8); 저장은 A129, 서버 실행 입구는 C2.6으로 제공됨
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
- [x] A121 화면 개편: Orca·Paradev 기준 라이트·다크 색, 세션 작업 공간·사이드바, 색·간격·문구 감사 테스트 · [기록](archive/a/A121-gui-redesign.md)
- [x] A122 결과 · 실행 활동을 대화 옆 패널(대화 머리 "활동")과 전체 화면(사이드바)으로 나눔 · [기록](archive/a/A122-results-dock.md)
- [x] A123 화면의 AGT 표시 제거(#47과 같은 변경) · [기록](archive/a/A123-agt-display-removal.md)
- [x] A123 `RuntimeIdentity.agt_mode`와 그 생성 코드 정리(`src`는 #47, 남은 `examples/ui_demo.py` 호환 코드 제거) · [기록](archive/a/A123-ui-demo-agt-option.md)
- [x] A124 가짜 조회 데이터로 전 화면을 눌러 보는 시연(`examples/ui_demo.py`) · [기록](archive/a/A124-ui-demo.md)
- [x] A125 앱 문구를 하세요체(안내)·음슴체(상태)로 통일 · [기록](archive/a/A125-ui-copy-style.md)
- [x] A126 정책 화면 표가 폭을 다 쓰고 Codex 제외 도구 이름·이유가 잘리지 않음 · [기록](archive/a/A126-policy-table-width.md)
- [x] A127 현황 화면을 줄 목록·막대그래프로 줄임(시연 기준 5075px → 1673px) · [기록](archive/a/A127-overview-rows.md)
- [x] A128 MCP 서버 추가: '초안' 표기 제거, 설정 붙여넣기로 칸 채우기, 표시 이름·ID 추천·찾아보기·명령줄 인자, `.exe`만 통과(EXE-01) · [기록](archive/a/A128-server-add-paste.md)
- [ ] A128 실제 Registry 등록·EXE-01 지문 확인 화면 — A 차례: EXE-01 B 필드(B3.14, #58)·C2.6(#54)·Scanner(C2.10 #60, C4.14 #59)가 A 브랜치에 병합됨
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
- [x] A93 A 브랜치 main 병합 PR([#29](https://github.com/hsminu/MCP-Runtime-Guard/pull/29), 병합됨) · [기록](archive/a/A93-main-merge-pr.md)
- [x] A71 A 단위 테스트 포맷 제외 대상 정리·AST 동일 검증 · [기록](archive/a/A71-a-unit-format-baseline.md)
- [x] A43 공통 포맷 제외 목록·타입/린트 예외 축소(A93 PR에서 공통 설정 변경으로 표시) · [기록](archive/a/A43-lint-type-baseline-reduction.md)
- [x] A44 시연 앱의 `tests.unit.test_guard` 대역 import 제거(메모리 모드도 임시 파일의 B SQLiteStore, 고정 합성 Registry) · [기록](archive/a/A44-demo-without-test-doubles.md)
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
  - B 리뷰 받음([#40](https://github.com/hsminu/MCP-Runtime-Guard/issues/40), 고칠 점 없음). 의견대로 `ResultTooLarge`의 N818 이유 주석에 감사 값 일치 이유를 더함
  - 개발문서·team-workflow의 파일 경로는 [#39](https://github.com/hsminu/MCP-Runtime-Guard/pull/39)로 main에 반영됨
- [x] A118 A113에서 찾은 mcp_transport 테스트 빈틈 9개 중 7개를 테스트로 메움(나머지 2개는 관찰 불가) · [기록](archive/a/A118-mcp-transport-test-gaps.md)
- [x] A114 3단계: Codex 계층(`codex_session`·`jsonrpc_client`·`codex_stream`·`guard_relay`) 정리 · [기록](archive/a/A114-codex-layer-cleanup.md)
- [x] A119 A114에서 찾은 Codex 세션 테스트 빈틈 4개와 멈춤 1개를 메움 · [기록](archive/a/A119-codex-session-test-gaps.md)
- [x] A115 4단계: `BackendBridge` 안쪽 분리 · [기록](archive/a/A115-backend-bridge-split.md)
- [ ] A115 bridge 분리 독립 리뷰 — 막힘: [#70](https://github.com/hsminu/MCP-Runtime-Guard/pull/70) C 리뷰
- [ ] A137 A115에서 찾은 bridge 테스트 빈틈 11개와 멈춤 1개 메우기 — A 차례
- [ ] A116 5단계: UI 화면 조립·`__main__` 정리(C 리뷰) — A 차례(B2.11 #47 병합)
- [ ] A139 A 모듈 ID 생성을 `new_id`·`new_suffix`로 통합(`bridge_ingress.bridge_id` 포함), B6.8 복구에 `new_id("run")` 연결 — 막힘: [#65](https://github.com/hsminu/MCP-Runtime-Guard/pull/65) 병합([수용](https://github.com/hsminu/MCP-Runtime-Guard/pull/65#issuecomment-6472889394))
