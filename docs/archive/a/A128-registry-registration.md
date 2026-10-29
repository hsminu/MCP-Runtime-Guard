# A128 · 실제 Registry 등록과 EXE-01 지문 확인

2026-10-29. 작업 위치는 `.cache/a-work/wt-A128`, 브랜치는 `wip/a128`이다. 원래 checkout의 src·tests·docs는 읽거나 고치지 않았다. 공통 문서·a-status와 B·C 소유 파일은 고치지 않았다.

## 한 일

- `ServerAddForm` → bridge 제어 명령 → `RegistrationCommands` → C `ServerRegistry.register`를 연결했다. B `ServerSettings.model_validate_json(..., strict=True)`와 C 검증을 쓴다. 실제 저장 ACK 뒤에만 PENDING 등록 완료를 표시한다. 화면 목록은 B 저장소 조회 결과로 갱신하며 재시작 후 복원한다.
- URL은 bridge의 순수 힌트 함수와 백엔드 명령에서 모두 C `normalize_endpoint`를 쓴다. 기존 A URL 판정 복제는 제거했다. 붙여넣기와 직접 입력이 같은 경로를 지나고, IDNA·기본 포트 정규형을 확인 화면과 설정 파일에 사용한다. C의 12개 거부 코드에 고정 문구를 대응했다. `URL_NOT_CANONICAL`은 정규화 입구가 직접 내지 않지만 문구는 준비했다.
- stdio는 C `check_stdio_values`·`pin_executable`로 검증·계산한다. 경로·크기·SHA-256을 일반 텍스트로 표시하고 사람이 확인해야 실행 파일을 띄워 목록을 수집한다. 확인 토큰은 백엔드의 불변 설정 사본 하나에 결속하고 한 번만 쓴다. 확인 후 지문이 바뀌면 수집·등록 없이 새 지문·새 토큰으로 다시 확인받는다. 입력 수정과 오래된 응답은 확인 상태를 폐기한다.
- 제품 목록 수집은 `product_assembly.collect_registration_listing`에서 C launcher 또는 기존 HTTP stream을 쓰며 `ToolListLimits()`를 켠다. 목록만 수집하고 `tools/call`은 하지 않는다. transport는 finally에서 닫는다. 등록 명령 전체와 직렬화 잠금 대기는 90초 상한을 둔다.
- 설정 파일의 서버 레코드에 `command_size`·`command_sha256`을 저장한다. Registry 등록과 설정 파일은 별도 저장이므로 원자적이라고 주장하지 않는다. DB 결과 불명은 같은 실행에서 재등록을 막는다. 설정 저장 실패·취소·미확인 뒤에는 재시작을 요구하고 Guard가 있으면 닫는다. 부분적으로 PENDING 행이 남을 수 있음을 안내한다.
- 설정이 없는 첫 등록은 B가 검증한 빈 선택 Profile(`local`, 선택 서버 없음)과 등록 서버 설정을 C 설정 파일 교체로 저장한다. 등록으로 도구 접근이나 실행 권한을 부여하지 않는다. 기존 설정이 잘못되어 Profile 로딩이 거부됐으면 새 등록 저장기를 연결하지 않아 그 파일을 덮어쓰지 않는다.
- 기존 stdio 서버 ID로 지문 재확인을 요청하면 백엔드에 저장된 실행 값으로 다시 계산한다. ACTIVE이면 C 재검토 전이로 내리고, 설정 저장·B 정책 적용·Task 종료를 같은 정책 잠금에서 처리한다. 실제 지문 변경으로 정책 버전과 진행 중 Task 상태가 바뀌는 시험을 추가했다. 재확인 후에는 기존 프로세스를 재사용하지 않도록 새 세션을 거부하고 재시작을 안내한다.
- 목록 조회 때도 현재 실행 파일 지문을 대조한다. 일치하면 ‘저장 지문과 일치’, 변경·미고정·검증 실패면 ‘지문 재확인 필요’다. 등록 상태와 실행 파일 확인 상태를 분리한다. 설정 저장 후 화면의 첫 실행 상태 문구도 갱신한다.

## 검증

공통 실행 환경: worktree에서 Git Bash, 원래 checkout의 `.venv/Scripts/python.exe`, `PYTHONUTF8=1`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`. 최초 `import guard_app; print(guard_app.__file__)`가 worktree의 src를 가리킴을 확인했다. TMPDIR·TEMP·TMP는 worktree의 `.cache/a-work/A128-tmp-1`이다.

최종 시험 명령(아래 `PY`는 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`):

```bash
PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A128-tmp-43 tests/unit/test_bridge_registration.py tests/unit/test_server_add.py tests/unit/test_a_architecture.py tests/unit/test_bridge.py tests/unit/test_product_settings.py tests/unit/test_bridge_policy_access.py tests/unit/test_product_assembly.py tests/unit/test_session_runtime.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_responsive_detail.py tests/unit/test_ui_sessions.py tests/unit/test_a138_product_startup.py tests/unit/test_ui_overview.py tests/unit/test_ui_policy.py
```

결과: **266 passed, 144 subtests passed, 19.33초**. 새 시험은 11개다. 실제 C 실행 파일 해시, B SQLite, C 설정 파일 저장·읽기, 제품 bootstrap·Qt bridge·목록·재시작, 정책 변경에 따른 실제 저장 Task 정지를 확인했다. 목록 수집은 SDK의 합성 바이트 peer로 확인했고 peer의 `tools/call` 수신은 0이었다. 등록 전 확인 단계의 수집 횟수 0, 완료 후 1, 중복 요청 후 1을 확인했다.

- `PY -m ruff check <변경 Python 16개>` 통과.
- `PY -m ruff format --check <변경 Python 16개>` 통과(16 files already formatted).
- `PYTHONPATH=src PY -m mypy` 통과(118 source files).
- `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini` 통과(7 kept, 0 broken).
- `git diff --check` 통과. 제거한 URL 보조 이름을 tests 전체에서 검색했고 직접 의존한 시험은 없었다.
- Qt 420×720에서 지문 확인 영역·버튼을 캡처해 확인했다. 가로 스크롤 최대값 0. offscreen의 기본 폰트가 네모로 나와 캡처 프로세스에만 Windows `malgun.ttf`를 명시 로드했다. 제품 폰트 설정은 바꾸지 않았다. 캡처는 worktree 임시 경로 `.cache/a-work/A128-tmp-1/pin-confirmation.png`다.

## 회귀 대조

각 주입 뒤 해당 시험을 따로 실행했고, 매번 파일 원래 바이트를 finally에서 복구한 뒤 SHA-256 일치를 확인했다. 11개 시험에 12개 회귀를 주입해 모두 탐지했다. SyntaxError나 수집 실패를 탐지 성공으로 세지 않았다.

| 시험 (`test_bridge_registration.py`) | 주입한 회귀 | 결과 |
| --- | --- | --- |
| `test_registration_requires_confirmation_and_persists_canonical_settings` | 확인 토큰 일치 검사 제거 | 해당 시험 실패로 탐지 |
| `test_validation_rejection_has_no_registration_or_collection` | 사람이 선택한 외부 경계를 internal로 바꿈 | 해당 시험 실패로 탐지 |
| `test_real_executable_pin_is_displayed_and_saved` | 저장할 실행 파일 지문·크기 제거 | 해당 시험 실패로 탐지 |
| `test_changed_pin_requires_new_confirmation_and_recheck_stops_admission` | 등록 직전 변경 지문 대조 제거 | 해당 시험 실패로 탐지 |
| `test_store_failure_is_not_retried_or_reported_as_registered` | 저장 결과 불명 뒤 재요청 차단 제거 | 해당 시험 실패로 탐지 |
| `test_settings_failure_leaves_pending_and_requires_restart` | 설정 저장 False를 성공 취급 | 해당 시험 실패로 탐지 |
| `test_url_code_messages_and_paste_use_c_normalization` | C URL 검증·정규화 우회 | 해당 시험 실패로 탐지 |
| `test_form_confirmation_ignores_stale_reply_and_clears_on_edit` | 오래된 화면 응답의 command_id 검사 제거 | 해당 시험 실패로 탐지 |
| `test_product_bridge_registration_updates_window_and_survives_restart` | 등록 결과의 창 목록 연결 제거 | 해당 시험 실패로 탐지 |
| `test_registration_collector_lists_without_tool_calls` | tools/list 수집 옵션 제거 | 해당 시험 실패로 탐지 |
| `test_repin_changes_policy_version_and_stops_persisted_task` | 등록 정책 적용·Task 정지 생략 | 해당 시험 실패로 탐지 |
| `test_changed_pin_requires_new_confirmation_and_recheck_stops_admission` 추가 대조 | 목록의 현재 지문 대조를 항상 일치로 바꿈 | 해당 시험 실패로 탐지 |

## 한계와 인계

- A47 Scanner 경고·검토된 설명·SCAN-01은 계속 막힘이다. Scanner를 호출하거나 활성화하지 않았다. 신규 도구의 종류는 설정되지 않았으므로 C 규칙대로 OTHER이고, Profile 선택·접근 허용에도 넣지 않는다. A04 전체 활성화·A81·A105의 활성화 정책 잠금 연결은 별도 후속이다.
- 지문은 실행 파일만 덮는다. 인터프리터가 실행할 스크립트·패키지를 고정하지 않는다. C launcher의 실제 외부 서버 실행·네트워크·OS Job 전체 인수와 Scanner·공격 시험은 미실행이다. 합성 peer의 0회 수신을 외부 wire·부작용 전체의 0으로 일반화하지 않는다.
- 설정 파일과 Registry의 두 저장 중 하나가 실패하면 PENDING만 남을 수 있다. 자동 삭제·재시도·새 활성화는 없다. 부분 등록 복구 UX는 B·C 리뷰 대상으로 남긴다.
- 표시 이름은 B 서버 설정·Registry 저장 필드가 아니므로 영속 목록은 server_id를 쓴다. 새 저장 계약은 임의로 추가하지 않았다.
- C `request_review`는 ACTIVE/BLOCKED→PENDING만 받는다. 이번 재확인은 ACTIVE만 PENDING으로 내리며 이미 PENDING이면 그대로, BLOCKED이면 차단 상태를 유지한다.
- #52 C 리뷰 원문은 GitHub connector 404와 gh 네트워크 거부로 직접 확인하지 못했다. worktree A08·A130·A135 기록의 리뷰 요약과 현행 C 코드를 근거로 연결했다. A31 기록은 `archive/integration-notes.md`의 독립 등록 폼 절에서 확인했다.
- B: 설정 저장·정책 적용·부분 저장 처리 리뷰. C: 실제 Registry/launcher 연결·지문 재확인·URL 문구·목록 수집 리뷰와 독립 T/S 인수 요청. B·C 구현이나 공통 계약 수정은 요구하지 않는다.

관련: I1·I6·I7·I8·I9·I10, T10·T18·T29 및 S02의 A 단위 경계, 설계 0.4·0.5·2·10.1·12장, EXE-01·URL-01·CFG-01·FP-01, A04·A08·A31·A47·A81·A105·A128·A129·A138.

PR: 미정.
