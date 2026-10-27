# C 현황

갱신: 2026-10-26 · main `58afdd4` · 이전 상세: [2026-10-15까지의 C 현황](archive/status/c-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| Store 계약 회신(B6-01–05) | B·A | C1.8, C3.5 |
| AGT 제거(구현) | A·B | C2.9, C6.3, C6.4, C6.9 |
| 경고 표시 화면(A28–A32·승인 창) | A | C4.13 |
| API 키 입력 화면 | A | C2.10 |
| 설치판 리소스 위치(패키징) | A | C6.20 |

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
- [ ] C2.3 safe_path: 빈 값·NUL·역슬래시·절대·상위·symlink·드라이브·UNC·junction 거부
- [ ] C2.4 검사-열기 TOCTOU 한계와 no-follow 옵션 확인
- [ ] C2.5 URL 검증: scheme·userinfo·IDNA·끝 점·기본 포트·인코딩 경로
- [ ] C2.6 launcher: 등록된 절대 경로·인자 리스트·최소 환경, shell·동적 실행 금지. 실행 핵심은 C2.12 `launch_process`. 남은 것은 `servers.yaml` 등록 값 검증과 MCP 서버 실행 파일 해시의 출처
- [ ] C2.7 ids: 암호학적 난수, 비밀값 누락은 전용 예외
- [ ] C2.8 구조화 감사에서 원문·인자·인증정보 제외, 마스킹 필터
- [ ] C2.9 단일 구현 지점을 Semgrep·import-linter로 강제 — 막힘: CI 2단계(C6.3)
- [ ] C2.10 Scanner API 키를 Windows 자격증명 관리자에 저장·로딩, 제품 경로에서 `.env` 미사용 · [결정](contracts.md#사용자-결정) — 막힘: A 입력 화면
- [x] C2.12 codex.exe 실행 launcher(`security/launcher.py`): 쓰기 금지 핸들로 실행마다 SHA-256 대조, 허용 목록 환경, `-c sqlite_home='<Task 폴더>'`, 멈춘 채 생성 후 Job Object 할당, stdin/stdout은 A `CodexPipes` 형식, stderr는 Task 폴더 격리 파일, `close()`로 종료 확인(#21 2번). stdio MCP 서버도 같은 `launch_process`를 쓴다(#21 5번) · [기록](archive/c/C2.12-codex-launcher.md)
- [x] C2.13 앱 폴더 경로·권한(`security/paths.py`): `%LOCALAPPDATA%\MCPRuntimeGuard` 아래 codex 설치·CODEX_HOME·Task 폴더·config에 사용자·SYSTEM만의 보호된 DACL, 단일 실행 잠금, Task 폴더 생성·삭제와 앱 시작 때 남은 폴더 삭제(#21 6번) · [기록](archive/c/C2.13-app-folders.md)
- [ ] C2.11 제품용 읽기 전용 파일 MCP 서버(stdio): 폴더 목록·파일 읽기만, `safe_path`(C2.3) 사용, `structuredContent.resource_id` 보고, UTF-8 텍스트만 · [결정](contracts.md#사용자-결정)
- [ ] C2.14 설정 파일·Profile 로딩의 경로·실행 파일 검증(B가 읽어 Catalog에 넣고 A가 앱 시작에 연결, #19 1번). CFG-01: `%LOCALAPPDATA%\MCPRuntimeGuard\config\settings.json` 경로·사용자 전용 권한, 1MiB 제한 읽기, 같은 폴더 임시 파일·fsync·교체(#33) · [결정](contracts.md#사용자-결정)

## C3 · B SQLite 리뷰와 독립 검증

- [ ] C3.1 단일 writer·WAL·foreign_keys·synchronous FULL·명시적 트랜잭션 리뷰
- [ ] C3.2 바인딩 SQL·FK·UNIQUE·관계·epoch·revision 독립 계약 검증
- [ ] C3.3 READ 관측 원자성과 커밋 전 공개 0
- [ ] C3.4 SEND 성공에서 Task 보안 상태 불변
- [ ] C3.5 승인 생성·종결·소비 원자성과 중복 소비 — 막힘: B6-01
- [ ] C3.6 쓰기·감사·잠금·COMMIT/ROLLBACK 실패와 중단 결함 주입
- [ ] C3.7 원문 비기록·재시작 승인 미복원·SEND 재전송 0
- [ ] C3.8 A 연결 후 요청별 계수 대조 — 막힘: C5
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
- [ ] C4.9 Scanner 결과를 B ScanReport로 번역(필수 분석기·완료/생략/실패·지문·severity). B SCAN-01 형식(#32), 모델 문장은 `defang_scanner_text`로 무해화
- [ ] C4.10 Registry 등록·지문 영속화와 변경 시 승인 무효화. DB의 ACTIVE만으로 실행하지 않고 연결마다 실제 지문과 대조 · [결정](contracts.md#사용자-결정). B `RegistryStore`(#32)로 저장. 지문 계산 입력은 #21 A 질문(FP-01)에 회신 필요
- [ ] C4.11 신규 서버 PENDING, 사용자 확인 후 ACTIVE
- [ ] C4.12 정의 전체로 도구 지문 계산(B digest, float 표기 포함) · [결정](contracts.md#사용자-결정)
- [ ] C4.13 경고 데이터를 등록 화면과 도구 호출 승인 창에 제공 · [결정](contracts.md#사용자-결정) — 막힘: A 화면
- [ ] C4.14 Scanner 검사 예산 구현(동시성·작업 10분·YARA native 5초·대기열 8건) · [결정](contracts.md#사용자-결정)
- [ ] C4.15 LLM 자체 평가(`overall_risk`)를 참고 필드로 보존. 판정·정렬에 쓰지 않음 · [결정](contracts.md#사용자-결정) — 필드 형식은 C4.9
- [ ] C4.16 LLM 경고의 반복 실행 변동 측정(144개 × 여러 회)
- [ ] C4.17 모델 설명(`threat_summary`) 보존: SDK 원문 비복사 원칙의 예외로 이 문장만, 300자·일반 텍스트·로그 미기록
- [ ] C4.18 등록 때 서버 ID의 `__`·끝 `_` 거부: B `valid_server_id`를 그대로 쓰고 등록 화면은 bridge가 넘기는 같은 검사 결과를 씀 · [결정](contracts.md#사용자-결정)

## C5 · fixture와 계수

- [x] C5.1 Scanner 목록 수신 경계 합성 fixture(stdio·HTTP 22개 시나리오) · [검증](archive/tasks/c-scanner-operating-validation.md)
- [ ] C5.2 C2.11 서버를 합성 문서(public·personal·private, TEST_SECRET)로 띄우는 fixture
- [ ] C5.3 Remote Send HTTP 서버(127.0.0.1, tools/call과 프로토콜 메시지 구분 계수)
- [ ] C5.4 approval_consumed·host_dispatch·wire tools/call·receiver_effect를 요청별로 연결
- [ ] C5.5 wire_tools_call과 wire_post 대응, 카운터는 테스트 임시 파일
- [ ] C5.6 공격 fixture: 응답 유실·307/308·재연결·정의 변조·stderr/progress 유출
- [ ] C5.7 관찰 불가 경로를 검증 불가로 기록
- [ ] C5.9 실제 MCP 서버가 연결 뒤 `notifications/tools/list_changed`를 보내는지와 시점 관찰(LIST-01, #30). 연결 직후 습관적으로 보내는 서버가 많으면 3안(재조회·재대조) 재검토
- [ ] C5.8 `tools/list` 바이트 상한(1MiB·8MiB) 집행 검증: `attack_fixtures/scanner_limits`로 stdio·HTTP 초과 시 파싱 전 중단·미완료 경고 확인(#21 5번) — 막힘: A 바이트 상한 층

## C6 · 검증·실험·CI

- [x] C6.1 CI 1단계(Windows, Ruff·포맷·mypy·단위) · #4 · [통합 기록](archive/integration-a-b.md)
- [x] C6.2 contract 테스트 Windows 수정(socket 차단·SDK import 경로·deadline) · #7
- [ ] C6.3 CI 2단계(import-linter·Bandit·Semgrep·pip-audit, pip-audit 예외 없음) — 막힘: AGT 제거
- [ ] C6.4 contract 테스트 CI 편입(C6.3과 함께) — 막힘: AGT 제거
- [ ] C6.5 실험 스크립트 Windows 수정(socket 차단·`PYTHONPATH`·인코딩)과 MCP 정의 수집기 저장소 반영
- [ ] C6.6 Windows Cisco 실험(2026-10-14~15) 완료 기록 작성
- [ ] C6.7 Fake·실제 Transport·Store·AccessChecker·Scanner에 같은 계약 테스트
- [ ] C6.8 C 담당 T/S 구현: T01–T04·T07–T12·T14–T17·T19–T24·T26·T28–T30·T33–T37·T39–T46·S01·S02·S04·S05
- [ ] C6.9 자체 접근 규칙의 거절·오류·누락(활성화 때 사용자가 끈 도구)에서 host_dispatch 0 · [결정](contracts.md#사용자-결정) — 막힘: AGT 제거(B 판정·A 연결)
- [ ] C6.10 정책·승인·응답 게이트 공격
- [ ] C6.11 accumulate·last_read·none replay(사전 정답 고정, 모드별 10회, 일치율·과잉 제한·완료율·p50/p95)
- [ ] C6.12 공격 fixture를 제품 스캔에서 제외하고 예외를 대장에 기록
- [x] C6.13 Codex 0.162.1 L1 설정·모델 정보 파일·고정값(`config/codex/`)·설치 스크립트, CODEX_HOME에 쓰고 해시를 돌려주는 함수(#21 4번) · [기록](archive/c/C6.13-codex-l1.md)
- [ ] C6.14 L2 시작 시험(T22): `configRequirements/read`·`config/read` 대조, 가짜 모델 서버로 도구 목록 정확 대조·금지 도구 7종 거부·호출 순차·요청 프롬프트 확인, `config/read` 대조에서 Task별 `-c sqlite_home` 하나만 허용, 원격 제어 상태 `disabled` 확인(C-CODEX-07), 실패 시 시작 거부, 앱 시작·버전 변경·CI 실행. 스크래치 실험 스크립트를 저장소로. C 구현은 A가 여는 주입형 L2 결과 포트에 채움(#21 3번). A `SessionPreparation`(#29)에 C2.12·C2.13과 함께 연결 · [결정](contracts.md#사용자-결정)
- [ ] C6.15 L3 허용 항목 밖 항목·승인 요청에서 interrupt·Task 정지, Runner 감시 상한 초과 interrupt, `request_user_input` 빈 응답, 원격 제어 상태가 `disabled`가 아닐 때 정지(C-CODEX-07) 검증 · [결정](contracts.md#사용자-결정)
- [ ] C6.16 Codex `sqlite_home` Task별 정리 검증(C-CODEX-01): logs·state·goals·memories·queue DB가 모두 Task 폴더로 가는지, CODEX_HOME에 남는 것(`installation_id`·`.sandbox_migration`·`tmp\arg0` 보조 파일·인증 파일 여부, T43·#21 8번)과 동시 실행 때 경합(T46) 확인 · [대장](weakness_register.md#발견-항목과-조치)
- [ ] C6.17 (후속·선택) codex.exe AppContainer 실행 실험(C-CODEX-04)
- [ ] C6.18 최종 제출 전 C-CODEX-05 약관 재확인(#19 8번) · [결정](contracts.md#사용자-결정)
- [x] C6.19 CODEX_HOME 최상위 허용 항목 확인: 지시문 파일(AGENTS.md 등)·허용 밖 항목이 있으면 쓰기·Task 시작 거부(C-CODEX-06) · [기록](archive/c/C6.19-codex-home-allowlist.md)
- [ ] C6.20 설치판에서 `config/codex/` 위치: 지금 기본값은 소스 checkout 기준. A 패키징과 맞춰 설치 패키지에서도 찾게 함 — 막힘: A 패키징

## 인수 기준

- [ ] DENY와 승인 전·거절·만료 ASK에서 dispatch·wire·부작용 0
- [ ] 승인 1회와 더블클릭에서 consumed·dispatch·wire·부작용이 각각 1
- [ ] 쓰기 응답 유실 후 UNKNOWN_OUTCOME, 새 호출 ID·재연결로도 추가 전송 0
- [ ] READ 커밋 실패·미분류·원시 로그 공격에서 결과·기밀 표지 미공개
- [ ] 측정 원자료·미실행·검증 불가 항목·약점 조치와 재현 방법 기록
