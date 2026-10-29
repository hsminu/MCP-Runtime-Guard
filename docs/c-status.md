# C 현황

갱신: 2026-10-29 · main `b14c181` · 이전 상세: [2026-10-15까지의 C 현황](archive/status/c-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| Store 계약 회신(B6-01–05) | B·A | C1.8, C3.5 |
| 경고 표시 화면(A28–A32·승인 창) | A | C4.13 |
| API 키 입력 화면 | A | C2.10 |
| 설치판 리소스 위치(패키징) | A | C6.20 |
| 호출·승인 조회 화면 연결(B6.16) | A | C3.8 |

## C1 · 계약과 관측 기반

- [x] C1.1 Store 계약 요구 초안(세 원자 연산·U1–U8) · [기록](archive/tasks/c-store-requirements.md)
- [ ] C1.2 Scanner·Registry 요구 초안(입력 범위·분석기 완료/실패·지문·사용자 활성화·승인 무효화)
- [ ] C1.3 관측 요구 초안(approval_consumed·host_dispatch·wire tools/call·receiver_effect)
- [ ] C1.4 환경·인수 요구 초안. Cisco Scanner 조사 부분은 완료 · [기록](archive/tasks/c-cisco-scanner-spike.md)
- [x] C1.5 Store 계약 처리 제안 v1 · [기록](archive/tasks/c-store-proposal.md)
- [x] C1.6 B 승인 코드 검토, C-B-01 발견과 수정 확인 · [기록](archive/tasks/c-b-review.md)
- [x] C1.7 공통 의존성 조합 고정(main `pyproject.toml`·`uv.lock`, Scanner 포함) · #4 · #6
- [ ] C1.8 B Store·Scanner·Registry·상태·감사 계약 검토와 합의. B6-06·SCAN-01·DESC-01은 #32에서 합의 — 막힘: B6-01–05
- [ ] C1.9 A와 fixture 실행 방법·host_dispatch 관측·지문 변경 알림 맞추기

## C2 · 위험 연산과 정적 강제

- [x] C2.1 Scanner 비밀값 로딩(`secrets.py`)·SDK 원문 로그 제한(`redact.py`) · [기록](archive/tasks/c-cisco-luna-connection.md)
- [x] C2.2 SDK import의 외부 tokenizer 다운로드 차단(C-CISCO-05) · #6 · [대장](weakness_register.md#발견-항목과-조치)
- [x] C2.3 safe_path(`security/paths.py`): 빈 값·NUL·제어 문자·역슬래시·`:`(드라이브·ADS)·절대·상위·장치 이름·끝 점 거부, 루트 아래 symlink·junction·하드링크 거부, ID는 디스크 실제 이름(대소문자·8.3)의 root 기준 POSIX. B Catalog용 `file_resource_resolver` · [결정](contracts.md#사용자-결정) · [기록](archive/c/C2.3-safe-path.md)
- [x] C2.4 검사-열기 TOCTOU: 파일 서버가 `safe_path` 뒤 링크 미추적(`OPEN_REPARSE_POINT`) 핸들로 열고 핸들의 실제 경로·reparse·하드링크를 다시 대조(`FILE_CHANGED`). 상위 폴더 junction 바꿔치기 실측 · [기록](archive/c/C2.11-file-server.md)
- [x] C2.5 URL 검증(URL-01, `security/endpoints.py`): 등록 화면은 `normalize_endpoint`로 정규형을 만들고, 설정 로더(`ServerSettings.url`)와 transport는 `canonical_endpoint`로 정규형만 받는다. https 원칙, http는 루프백 IP 리터럴만, 사용자 정보·쿼리·fragment·끝 점·`%`·숫자 IP 변형·특수 주소 거부, IDNA 2008 · [기록](archive/c/C2.5-endpoint-validation.md)
- [x] C2.6 stdio MCP 서버 실행 입구(EXE-01, `security/launcher.py`): `check_stdio_values`(command·args·env_allow 검사), `pin_executable`(등록 지문), `launch_mcp_server`(연결마다 재검사·지문 대조, 지문 없으면 `LAUNCH_EXECUTABLE_UNPINNED`, 서버별 폴더 `AppFolders.server_folder`·연결별 stderr·기본 환경 5개와 서버 폴더 TEMP·`env_allow`만). A 화면·연결과 B 지문 필드(#48)는 각 담당 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C2.6-mcp-server-launch.md)
- [x] C2.7 ids(`security/ids.py`): `new_id`·`new_suffix`·`new_token`·`token_matches`, `secrets`만 사용. C 파일 6개의 직접 생성을 옮기고 L2 가짜 모델 인증 비교를 `compare_digest`로. `src`의 `random` import와 C 모듈의 직접 생성을 `test_ids.py`가 막음(Semgrep은 C2.9). 비밀값 누락은 기존 고정 코드(`SCAN_LLM_KEY_MISSING_OR_INVALID`, 대체값 없음). A 모듈 전환은 A 요청 · [기록](archive/c/C2.7-ids.md)
- [x] C2.8 로그 마스킹 마지막 방어(`redact.install_log_redaction`): 레코드 생성 때 `guard_app` 로그는 인증정보 모양을 가리고 500자로 자름, 그 밖의 로거는 원문 제거, 예외는 형식 이름만, 처리되지 않은 예외·warnings도 같은 규칙. 감사 `detail`은 고정 키(ID·개수·코드·예외 형식)만(코드 확인, 실측은 C3.7). A 앱 시작 연결은 남음 · [기록](archive/c/C2.8-log-redaction.md)
- [x] C2.9 단일 구현 지점 정적 강제: import-linter 계약 7개(core 순수·MCP SDK는 transport만·계층·ui는 bridge로·sqlite3는 storage만·subprocess는 launcher만·HTTP 클라이언트는 adapters만, `tools/ci/importlinter.ini`)와 Semgrep 규칙 10개(13.4절, `tools/ci/semgrep.yml`, CI Linux 작업). 예외 4곳(SG-01–04, B 3곳은 주석만), 설계 경계 6곳은 파일 제외 없이 줄 단위 표시(A 1·B 5, 주석만) · [기록](archive/c/C2.9-ci-static-checks.md)
- [x] C2.10 Scanner API 키를 Windows 자격증명 관리자에 저장·로딩, 제품 경로에서 `.env` 미사용(`secrets.store_scanner_key`·`save_scanner_key`(A `KeySaver`)·`load_scanner_key`·`scanner_key_tail`·`delete_scanner_key`·`scanner_key_environ`). A 화면 연결과 #59 뒤 검사기 연결은 남음 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C2.10-scanner-key-store.md)
- [x] C2.12 codex.exe 실행 launcher(`security/launcher.py`): 쓰기 금지 핸들로 실행마다 SHA-256 대조, 허용 목록 환경, `-c sqlite_home='<Task 폴더>'`, 멈춘 채 생성 후 Job Object 할당, stdin/stdout은 A `CodexPipes` 형식, stderr는 Task 폴더 격리 파일, `close()`로 종료 확인(#21 2번). stdio MCP 서버도 같은 `launch_process`를 쓴다(#21 5번) · [기록](archive/c/C2.12-codex-launcher.md)
- [x] C2.13 앱 폴더 경로·권한(`security/paths.py`): `%LOCALAPPDATA%\MCPRuntimeGuard` 아래 codex 설치·CODEX_HOME·Task 폴더·config에 사용자·SYSTEM만의 보호된 DACL, 단일 실행 잠금, Task 폴더 생성·삭제와 앱 시작 때 남은 폴더 삭제(#21 6번) · [기록](archive/c/C2.13-app-folders.md)
- [x] C2.11 제품용 읽기 전용 파일 MCP 서버(`guard_app.servers.file_server`, stdio, 표준 라이브러리 JSON-RPC): `list_root`·`list_folder`·`read_file`(모두 READ), `structuredContent.resource_id`(C2.3), UTF-8 텍스트 192KiB, 목록 1,000개, 루트는 레코드 `args`의 `--root`(`file_server_args`·`file_server_root`). A 등록 화면·조립은 남음 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C2.11-file-server.md)
- [x] C2.14 설정 파일 연산(CFG-01, #33, `security/settings_io.py`): `config\settings.json`을 링크 없이 열어 일반 파일·하드링크 1·사용자·SYSTEM 권한 확인, 상한+1바이트 읽기, 세대(크기·SHA-256)가 같을 때만 같은 폴더 임시 파일·fsync·`MoveFileExW(WRITE_THROUGH)` 교체, 실패와 결과 불명 구분, 앱 시작 때 남은 임시 파일 삭제. 서버 실행 파일·URL 검증은 C2.6·C2.5로 분리 · [기록](archive/c/C2.14-settings-file-io.md)
- [x] C2.15 로그아웃 정리 함수 `clear_codex_credentials`(`security/codex_home.py`): `secrets/` 암호문과 자격증명 관리자 항목 두 개 삭제, 모르는 항목이 있으면 아무것도 지우지 않음. A가 `account/logout` 성공 뒤 부름 · [기록](archive/c/C2.15-codex-credential-cleanup.md)
- [x] C2.16 앱 폴더 권한 확인의 SID 비교를 전체 SID로(C2.13 후속): SDDL 약어(`LA` 등) 때문에 CI 러너(내장 Administrator)에서 거부되던 문제 수정, 읽지 못한 ACE는 거부(#35 B 리뷰 3번) · [기록](archive/c/C2.16-sid-alias-compare.md)
- [x] C2.17 제품 DB 위치 `data\guard.sqlite3`와 백업 폴더 `data\backups`(`AppPaths.db_file`·`backups`). `app.lock`과 앱 폴더 권한으로 DB 단일 소유(REC-01, #35 A·B 합의) · [기록](archive/c/C2.17-product-db-path.md)
- [x] C2.18 #35 리뷰 후속: 재개 전 실행 이미지와 검사한 파일의 경로·파일 ID 대조(B launcher 1번), 이미 있는 루트의 DACL 재적용을 잠금 뒤로(B paths 2번, 결과 권한은 같음) · [기록](archive/c/C2.18-image-check-and-lock-order.md)
- [x] C2.19 DB 입구(#37 REC-01·SEQ-03): `AppFolders.db_path()`(DB와 옆 파일이 링크·하드링크·넓은 권한이 아닌지 확인), `begin_backup(label)`→`BackupTarget.publish()`·`discard()`, 앱 시작 때 남은 `.partial` 삭제 · [기록](archive/c/C2.19-db-backup-entry.md)
- [x] C2.20 저장한 Scanner 키를 검사에 연결: `cisco_connector()` 기본값이 검사마다 자격증명 관리자(`scanner_key_environ`)에서 키를 읽음(화면에서 바꾼 키가 다음 검사부터), `.env`·프로세스 환경은 읽지 않음. A 조립은 `ScanService(registry, cisco_connector())` · [기록](archive/c/C2.20-scanner-key-wiring.md)
- [x] C2.21 Codex 브라우저 로그인 URL 검증·기본 브라우저 열기(`security/login_browser.py`, A109 C 입구): 고정 Codex 0.162.1이 주는 `authUrl` 모양만 받음(호스트·경로·`client_id`·`redirect_uri` 루프백 콜백·`scope`·`S256` 고정, 문자 집합·길이 제한), `ShellExecuteExW`(셸 해석·`cmd`·`os.startfile` 없음), 결과는 True/False뿐이고 예외는 `CancelledError`만, 원문 비기록. A가 `account_access(open_browser=...)`로 연결 · [기록](archive/c/C2.21-login-browser.md)
- [x] C2.22 B3.14 지문 필드에서 실행 입구까지(EXE-01): 엄격 모드로 읽은 설정 레코드의 `command_sha256`·`command_size`로 실행·`tools/list`, 필드 없으면 `LAUNCH_EXECUTABLE_UNPINNED`, 같은 크기로 바뀐 파일은 `LAUNCH_EXECUTABLE_MISMATCH`(실행 전). 파일 서버 시험도 레코드의 지문을 씀 · [기록](archive/c/C2.22-settings-pin-launch.md)

## C3 · B SQLite 리뷰와 독립 검증

- [ ] C3.1 단일 writer·WAL·foreign_keys·synchronous FULL·명시적 트랜잭션 리뷰
- [ ] C3.2 바인딩 SQL·FK·UNIQUE·관계·epoch·revision 독립 계약 검증
- [ ] C3.3 READ 관측 원자성과 커밋 전 공개 0
- [ ] C3.4 SEND 성공에서 Task 보안 상태 불변
- [ ] C3.5 승인 생성·종결·소비 원자성과 중복 소비 — 막힘: B6-01
- [ ] C3.6 쓰기·감사·잠금·COMMIT/ROLLBACK 실패와 중단 결함 주입
- [ ] C3.7 원문 비기록·재시작 승인 미복원·SEND 재전송 0
- [ ] C3.8 (Store 조회 `list_calls`·`get_call`·`list_approvals`와 요청별 계수 대조는 정식 시험으로 완료(stdio·HTTP, T01·T02·T03·응답 유실), [C3.8-1](archive/c/C3.8-1-query-counts.md)) A 화면 연결 뒤 화면 쪽 대조 — 막힘: A 화면 연결
- [ ] C3.9 DB를 직접 고친 비정상 행(관계 깨짐·모르는 상태값·등급 하향)을 시작 점검이 잡고 멈추는지 검증 · [결정](contracts.md#사용자-결정)
- [ ] C3.10 시작 복구가 감사 기록과 맞지 않는 등급·미분류 표시를 복구하지 않는지 검증 — 막힘: B6.8

## C4 · Registry와 Scanner

- [x] C4.1 Luna 확장 평가와 모델 선택(Luna low) · [기록](archive/tasks/c-luna-scanner-evaluation.md)
- [x] C4.2 도구 정의 전체를 보존하는 입력 어댑터(C-CISCO-03) · [기록](archive/tasks/c-cisco-metadata-implementation.md)
- [x] C4.3 Luna low 요청 연결 · [기록](archive/tasks/c-cisco-luna-connection.md)
- [x] C4.4 실제 MCP metadata 조사(설정 7개·189건) · [기록](archive/tasks/c-real-mcp-metadata-survey.md)
- [x] C4.5 LLM·YARA 내부 병행 검사·집계 · [기록](archive/tasks/c-cisco-internal-aggregation.md)
- [x] C4.6 운영 설정 추천안과 실제 정의 144개 검증 · [추천안](archive/tasks/c-scanner-operating-proposal.md) · [검증](archive/tasks/c-scanner-operating-validation.md)
- [x] C4.7 C 브랜치 main 병합 · #6
- [x] C4.8 Scanner 준비가 이벤트 루프를 막지 않게 수정(C-CISCO-06) · #8
- [x] C4.9 Scanner 결과를 B ScanReport로 번역(`cisco_aggregation.scan_report`): 필수 LLM·YARA, 모든 도구에서 끝난 분석기만 완료, 준비 실패·도구별 실패 진단, 등록 지문 집합, SDK severity 그대로. `ServerRegistry.record_scan`으로 저장. 번역 규칙은 B 동의 대기 · [기록](archive/c/C4.9-scan-report-translation.md)
- [x] C4.10 Registry 등록·지문 영속화와 변경 시 승인 무효화(`services/registry.ServerRegistry`): B `RegistryStore` 위 동기 조회 캐시, 시작 때 복원, 연결 직후 `observe`가 정의 변경을 기록해 ACTIVE→PENDING과 바뀐 도구별 승인 무효화, 기록 실패 서버는 이번 실행 PENDING. A 연결(`assemble_guard`의 `observe`·`load`)과 설정에서 도구 종류를 바꾸는 경로는 A·B와 정함 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C4.10-server-registry.md)
- [x] C4.11 신규 서버 PENDING(`register`: 설정 레코드 + 실제 목록, 설정에 없는 도구는 `OTHER`), 사용자 확인 후 ACTIVE(`activate`: 캐시의 등록 지문으로) · [기록](archive/c/C4.10-server-registry.md)
- [x] C4.12 정의 전체로 도구 지문 계산(`services/registry.listing_fingerprints`): FP-01 입력(A transport의 SDK 재구성 dict)에 B `tool_fingerprint`(JCS), 미완료·이름 없음·중복·JCS 실패면 지문 없음. 실제 SDK 파싱 경로로 예시 정의 7개의 지문을 고정해 SDK·변환·JCS 변화를 잡음 · [기록](archive/c/C4.12-tool-fingerprints.md)
- [ ] C4.13 경고 데이터를 등록 화면과 도구 호출 승인 창에 제공 · [결정](contracts.md#사용자-결정) — 막힘: A 화면
- [x] C4.14 Scanner 검사 예산 구현(`services/scanner.ScanService`): 서버 검사 1건·대기 8건(`SCAN_QUEUE_FULL`), 작업 10분(끝난 결과 보존·`SCAN_INCOMPLETE` 보고서), LLM 동시 4개(도구 병렬), YARA 전용 작업자 1개·native 5초(SDK `_rules` 감쌈), 취소는 기록 없음, 제품 키는 넘긴 값만(C2.10 전 YARA만) · [결정](contracts.md#사용자-결정) · [기록](archive/c/C4.14-scan-service-budget.md)
- [x] C4.15 LLM 자체 평가(`overall_risk`)를 참고 필드로 보존. 판정·정렬에 쓰지 않음. SDK가 버리는 값을 `raw_response`에서 꺼내 64자로 무해화 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C4.9-scan-report-translation.md)
- [ ] C4.16 LLM 경고의 반복 실행 변동 측정(144개 × 여러 회)
- [x] C4.17 모델 설명(`threat_summary`) 보존: SDK 원문 비복사 원칙의 예외로 이 문장만, 300자·일반 텍스트·로그 미기록(`defang_scanner_text`) · [기록](archive/c/C4.9-scan-report-translation.md)
- [x] C4.18 등록 때 서버 ID의 `__`·끝 `_` 거부: B `valid_server_id`를 그대로 쓰고 등록 화면은 bridge가 넘기는 같은 검사 결과를 씀. Registry `register`·`observe`가 설정 검증과 별도로 다시 검사 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C4.10-server-registry.md)
- [x] C4.19 Registry 쓰기 결과를 모를 때 닫기(#46 B 리뷰): 제어 쓰기가 실패하거나 커밋 뒤 취소되면 그 서버의 ACTIVE를 PENDING으로 보고, 다음 제어 연산·`refresh`가 DB 행을 다시 읽어 맞춤. `observe` 취소는 기록 실패와 같이 이번 실행 PENDING. 실제 writer 재현은 C4.20 · [기록](archive/c/C4.19-registry-unknown-write.md)
- [x] C4.20 C4.19 실제 writer 재현: B6.13 writer가 COMMIT을 실행한 직후 취소해도 `register`·`activate`·`record_scan`·`request_review`·`save_reviewed_descriptions`는 다시 읽기 전까지 ACTIVE로 보이지 않고 `refresh`가 DB와 맞춤, `observe`는 이번 실행 PENDING·활성화 거부. `block`·시작 전 취소·COMMIT 실패는 #58 B 시험 · [기록](archive/c/C4.20-registry-real-writer.md)
- [x] C4.21 설정에서 바꾼 도구 종류·인자를 등록에 반영(`ServerRegistry.apply_settings`, #46 B·C 합의): 저장된 정의·지문에 새 설정의 종류·인자만 얹어 B `record_settings_change`(revision CAS)로 넘기고, 반환 사본으로 캐시 교체 뒤 바뀐 도구마다 `invalidate_tool`. 경계 변경은 쓰기 전 `REG_SETTINGS_MISMATCH`, 결과 불명은 C4.19처럼 다시 읽기 전까지 PENDING. 실제 Guard·서버로 승인 대기 중 종류 변경 → 승인 닫힘·계수 0(T10 변형). A155 이관·B 시작 목록 제외는 각 담당 · [기록](archive/c/C4.21-registry-apply-settings.md)

## C5 · fixture와 계수

- [x] C5.1 Scanner 목록 수신 경계 합성 fixture(stdio·HTTP 22개 시나리오) · [검증](archive/tasks/c-scanner-operating-validation.md)
- [ ] C5.2 C2.11 서버를 합성 문서(public·personal·private, TEST_SECRET)로 띄우는 fixture
- [ ] C5.3 Remote Send HTTP 서버(127.0.0.1, tools/call과 프로토콜 메시지 구분 계수)
- [x] C5.4 approval_consumed·host_dispatch·wire tools/call·receiver_effect를 요청별로 연결: 공용 하니스 `tests/unit/c_counting.py`(승인 소비·dispatch는 `request_id`, wire·효과는 인자 표지) · [기록](archive/c/C5.4-approval-counts.md)
- [ ] C5.5 wire_tools_call과 wire_post 대응, 카운터는 테스트 임시 파일
- [ ] C5.6 공격 fixture: 응답 유실·307/308·재연결·정의 변조·stderr/progress 유출. 응답 유실(응답 없음·늦은 응답·프로세스 종료·500)·307/308·재연결은 완료(`c_counting` 서버 동작) · [기록](archive/c/C5.6-write-loss-counts.md). 정의 변조·stderr/progress 유출 남음
- [ ] C5.7 관찰 불가 경로를 검증 불가로 기록
- [ ] C5.9 실제 MCP 서버가 연결 뒤 `notifications/tools/list_changed`를 보내는지와 시점 관찰(LIST-01, #30). 연결 직후 습관적으로 보내는 서버가 많으면 3안(재조회·재대조) 재검토
- [ ] C5.8 `tools/list` 바이트 상한(1MiB·8MiB) 집행 검증: `attack_fixtures/scanner_limits`로 stdio·HTTP 초과 시 파싱 전 중단·미완료 경고 확인(#21 5번) — 막힘: A 바이트 상한 층

## C6 · 검증·실험·CI

- [x] C6.1 CI 1단계(Windows, Ruff·포맷·mypy·단위) · #4 · [통합 기록](archive/integration-a-b.md)
- [x] C6.2 contract 테스트 Windows 수정(socket 차단·SDK import 경로·deadline) · #7
- [ ] C6.3 CI 2단계(import-linter·Bandit·Semgrep·pip-audit, pip-audit 예외 없음). import-linter·Bandit(`-ll`)·Semgrep은 CI에 넣음(C2.9). pip-audit은 의존성 정리(C6.24) 뒤 · [기록](archive/c/C2.9-ci-static-checks.md)
- [x] C6.4 contract 테스트 CI 편입: Windows 작업에서 `tests/contract` 45개(외부 연결 차단) · [기록](archive/c/C2.9-ci-static-checks.md)
- [ ] C6.5 실험 스크립트 Windows 수정(socket 차단·`PYTHONPATH`·인코딩)과 MCP 정의 수집기 저장소 반영
- [ ] C6.6 Windows Cisco 실험(2026-10-14~15) 완료 기록 작성
- [ ] C6.7 Fake·실제 Transport·Store·AccessChecker·Scanner에 같은 계약 테스트
- [ ] C6.8 C 담당 T/S 구현: T01–T04·T07–T12·T14–T17·T19–T24·T26·T28–T30·T33–T37·T39–T46·S01·S02·S04·S05. 41개 중 완료 33·부분 3·미실행 5([현황](archive/c/C6.25-2-verification-summary.md)). Guard 경로 T01·T09·T10·T11(재시작)·T26·T30 계수와 T28 지연 측정 완료 · [기록](archive/c/C6.8-1-guard-path-counts.md). 실제 codex.exe(대본 가짜 모델)를 거친 T01–T03·T21·T41·T42·T43·T45·T46 완료 · [기록](archive/c/C6.8-2-codex-flow.md). Guard 경로 T04·T29·T33·T34·T35·S05 완료 · [기록](archive/c/C6.8-3-guard-path-more.md)
- [x] C6.9 자체 접근 규칙의 거절·오류·누락(활성화 때 사용자가 끈 도구)에서 전송 0: 제품 조립의 실제 `AccessRulesChecker`·C `ServerRegistry`와 실제 stdio(C2.6)·HTTP 서버로 승인 소비·host_dispatch·wire `tools/call`·수신 효과 4개 계수 확인, 대조군 포함 28개 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C6.9-access-rule-counts.md)
- [ ] C6.10 정책·승인·응답 게이트 공격
- [ ] C6.11 accumulate·last_read·none replay(사전 정답 고정, 모드별 10회, 일치율·과잉 제한·완료율·p50/p95)
- [x] C6.12 공격 fixture를 제품 스캔에서 제외하고 예외를 대장에 기록: Bandit·Semgrep은 `src`만 봐서 빠지고 배포물에도 없음(AF-01), Semgrep 제외 경로를 루트 기준 `/src/...`로 고정 · [기록](archive/c/C6.12-attack-fixture-scan-scope.md)
- [x] C6.13 Codex 0.162.1 L1 설정·모델 정보 파일·고정값(`config/codex/`)·설치 스크립트, CODEX_HOME에 쓰고 해시를 돌려주는 함수(#21 4번) · [기록](archive/c/C6.13-codex-l1.md)
- [x] C6.14 L2 시작 시험(T22·T39·T40, `security/codex_l2.py`): 같은 codex.exe·CODEX_HOME·환경에 가짜 모델만 바꿔 requirements·설정 층·적용 값·원격 제어·도구 목록·프롬프트 블록·금지 호출 30종 거부·Guard 호출 순차·종료를 판정, `run_l2`가 A 입구에 줄 결과를 돌려줌. 진행 중 종료 0.05–0.11초 측정 · [기록](archive/c/C6.14-codex-l2.md)
- [ ] C6.15 (허용 밖 항목 정지는 실제 codex.exe로 확인, [C6.8-2](archive/c/C6.8-2-codex-flow.md)) L3 허용 항목 밖 항목·승인 요청에서 interrupt·Task 정지, Runner 감시 상한 초과 interrupt, `request_user_input` 빈 응답, 원격 제어 상태가 `disabled`가 아닐 때 정지(C-CODEX-07) 검증 · [결정](contracts.md#사용자-결정)
- [ ] C6.16 (DB 다섯 개의 Task 폴더 위치·CODEX_HOME 남는 항목·동시 실행 정리는 확인, [C6.8-2](archive/c/C6.8-2-codex-flow.md). TEMP 쓰기·stderr 크기 상한 남음) Codex `sqlite_home` Task별 정리 검증(C-CODEX-01): logs·state·goals·memories·queue DB가 모두 Task 폴더로 가는지, CODEX_HOME에 남는 것(`installation_id`·`.sandbox_migration`·`tmp\arg0` 보조 파일·인증 파일(`secrets/*.age`는 C6.21에서 확인), T43·#21 8번)과 동시 실행 때 경합(T46) 확인. Codex가 TEMP에 쓰는 것을 보고 TEMP·TMP를 Task 폴더 아래로 둘지, `codex-stderr.log` 크기 상한(#35 B launcher 2·3번) · [대장](weakness_register.md#발견-항목과-조치)
- [ ] C6.17 (후속·선택) codex.exe AppContainer 실행 실험(C-CODEX-04)
- [x] C6.18 최종 제출 전 C-CODEX-05 약관 재확인(#19 8번): Sign in with ChatGPT 약관(자기 앱 이름, 사칭·제휴 암시 금지)과 오픈소스 토큰 공유 경로 확인. 위험 수준 그대로(위험 수용 유지), 제출·시연은 API 키 로그인 기본·OpenAI 제휴 암시 금지 권고 · [결정](contracts.md#사용자-결정) · [기록](archive/c/C6.18-codex-terms-recheck.md)
- [x] C6.19 CODEX_HOME 최상위 허용 항목 확인: 지시문 파일(AGENTS.md 등)·허용 밖 항목이 있으면 쓰기·Task 시작 거부(C-CODEX-06) · [기록](archive/c/C6.19-codex-home-allowlist.md)
- [ ] C6.20 설치판에서 `config/codex/` 위치: 지금 기본값은 소스 checkout 기준. A 패키징과 맞춰 설치 패키지에서도 찾게 함 — 막힘: A 패키징
- [x] C6.21 CODEX_HOME `secrets/` 허용(알려진 암호문·임시 파일만, 해시 대조 제외, 앱 시작 때 유지)과 L1 `include_permissions_instructions`·`include_environment_context` = false, `codex.lock.json` 갱신(C-CODEX-08) · [기록](archive/c/C6.21-codex-secrets-and-prompt-blocks.md)
- [ ] C6.22 CI에서 실제 L2: 워크플로에서 `tools/install_codex.py`로 codex.exe를 받고 `GUARD_TEST_CODEX_EXE`로 `test_real_codex_passes_l2`를 켬(C6.14 후속, 공용 CI 파일 변경이라 따로 제안)
- [x] C6.23 #41 A 리뷰 후속(L2): 전체 시간 상한 `deadline_s`(기본 30초)와 `L2_DEADLINE_EXCEEDED`, 최악 시간·취소·예외 경로 설명, 가짜 모델 서버 밖 통신은 관찰하지 않는다는 범위 명시, L2·L3 허용 항목 같음 테스트 · [기록](archive/c/C6.23-l2-a-review-followup.md)
- [ ] C6.24 의존성 취약점 정리(pip-audit 16건, C-DEP-01–03): certifi·cryptography·filelock lock 갱신 완료(C-DEP-03), litellm 1.93.2 두 ID는 예외 PA-01(Scanner 고정, 2026-10-29 사용자 결정). 남은 것: mcp 1.30.0(C-DEP-01, A·B 합의·A transport 재검증) 뒤 pip-audit을 실패로 막는 단계로 · [기록](archive/c/C2.9-ci-static-checks.md)
- [x] C6.25 C 독립 검증 현황 요약(인수 기준 5 일부): C 담당 T/S 41개의 완료·부분·미실행, 완료 근거, 다른 담당 작업의 C 리뷰 프로브, 미실행·검증 불가 항목, 측정 원자료와 재현 명령 · [기록](archive/c/C6.25-verification-summary.md) · [갱신(main `b14c181`)](archive/c/C6.25-2-verification-summary.md)
- [x] C6.26 L2 실패 코드 상수 `codex_l2.L2_FAILURE_CODES`(28개): 모듈이 실제로 쓰는 코드와 AST로 대조하는 시험, A151 사본과 같음 확인. A 사본 교체는 A · [기록](archive/c/C6.26-l2-failure-codes.md)

## 인수 기준

- [x] DENY와 승인 전·거절·만료 ASK에서 dispatch·wire·부작용 0: Guard 입구·실제 stdio·HTTP 서버 기준, 작업 취소 포함(C5.4). Codex 경유는 C6.8 · [기록](archive/c/C5.4-approval-counts.md)
- [x] 승인 1회와 더블클릭에서 consumed·dispatch·wire·부작용이 각각 1: 연속 2회·동시 2·5회 승인 모두 각 1(C5.4). 화면 쪽 Enter 반복은 A `test_runtime` · [기록](archive/c/C5.4-approval-counts.md)
- [x] 쓰기 응답 유실 후 UNKNOWN_OUTCOME, 새 호출 ID·재연결로도 추가 전송 0: stdio·HTTP 응답 없음·늦은 응답·종료·500, HTTP 307/308 미추적. Guard 입구·실제 서버 기준(Codex 경유는 C6.8) · [기록](archive/c/C5.6-write-loss-counts.md)
- [ ] READ 커밋 실패·미분류·원시 로그 공격에서 결과·기밀 표지 미공개
- [ ] 측정 원자료·미실행·검증 불가 항목·약점 조치와 재현 방법 기록. 2026-10-29 현황 요약 · [C6.25-2](archive/c/C6.25-2-verification-summary.md)
