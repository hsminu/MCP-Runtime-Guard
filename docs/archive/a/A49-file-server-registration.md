# A49 · 로컬 파일 서버 등록과 출처 폴더 규칙 연결

2026-10-29. `wip/a49`, `.cache/a-work/wt-A49`에서 작업했다. 원래 checkout의 src·tests·docs는 읽거나 수정하지 않았다. 공통 문서·a-status·B/C 소유 구현과 시험은 수정하지 않았다.

## 구현과 판단

- 서버 추가의 연결 방식에 **로컬 파일 서버**를 추가했다. Qt `getExistingDirectory`로 루트·하위 폴더를 고르고 등급 선택 목록에서 루트, 루트 목록(`list_root`), 하위 폴더 등급을 정한다. 경로 칸은 읽기 전용이며 선택 문자열의 대소문자를 보존한다. 이 모드에서는 실행 명령·인자·환경변수·출처 prefix 자유 입력이 없고 설정 붙여넣기도 비활성이다. 입력 변경은 확인 토큰과 진행 중 화면 응답 ID를 폐기한다.
- 안내: “폴더 이름은 상위 폴더 등급으로 보임.” 루트 목록 등급을 따로 선택하며 하위 등급이 루트보다 낮아지지 않음을 안내한다. 사용자 지시대로 중복·중첩된 **하위 폴더 선택끼리**는 거부한다. 루트 기본 규칙과 하위 규칙의 겹침은 의도된 구조이며 B의 최고 등급 판정을 유지한다.
- 기준 worktree에는 `ui/server_add_folders.py`가 없고 옛 `LocalFolderRulesForm`이 `ui/server_add.py`에 있었다. 새 파일에 선택 전용 `FileServerFolders`를 만들었다. 제품 화면의 독립 초안 폼을 새 등록 경로로 교체했다. 옛 폼 API와 기존 단위 시험은 유지하며 제품에서는 사용하지 않는다. 기존 폼을 여는 화면 시험 두 곳을 새 경로에 맞췄다.
- UI는 bridge `RegistrationRequest(action="prepare_file")`만 전달한다. `RegistrationCommands`가 `sys.executable`, C `file_server_args(root)`, `FILE_SERVER_TOOL_SETTINGS`, `pin_executable(command)`로 등록 값을 만든다. 실행값은 화면 입력에서 가져오지 않는다. A128의 EXE-01 지문 확인·1회 토큰·지문 변경 재확인·목록 수집·C Registry 등록 경로를 재사용한다. 루트는 C가 푼 값으로 확인 사본에 고정하고, 확인 시 같은 등록 args의 루트인지 검사하며 폴더도 다시 검증한다.
- C `real_root`·`safe_path`가 실제 폴더, 루트 내부, 존재 여부, 링크 제한과 실제 이름을 검사한다. A의 `relative_to`는 선택 절대 경로를 C 상대 경로 인자로 바꾸는 문자열 연산뿐이다. 검사 실패 원문은 표시하지 않고 `REG_FOLDER_INVALID` 고정 문구를 쓴다.
- C 상수는 `list_root`, `list_folder`, `read_file` 모두 `kind=read`다. 뒤의 두 도구만 `resource_arg=path`다. annotations나 도구 응답에서 종류·등급을 추정하지 않는다. 따라서 A105 활성화의 세 도구 모두 허용 선택 가능하다.
- B 형식의 `profile.sources`에 `list_root/prefix=[]` 한 규칙과 `list_folder`·`read_file`의 루트/하위 prefix 규칙을 넣는다. B `_check_configuration`은 source 서버가 Profile에 선택되어 있어야 하므로 등록 저장 때 선택 서버 목록에도 추가한다. **Registry는 PENDING, 접근 허용은 추가하지 않음**을 유지한다. `TaskPolicy.save_registration`의 같은 정책 잠금 안에서 최신 문서에 서버·출처를 함께 추가하고 B 검증·설정 저장·정책 적용을 수행한다.
- `product_assembly`는 검증한 서버 설정의 `file_server_root(server.args)`만으로 C `file_resource_resolver`를 구성하여 `ProductInputs.resource_resolver`로 넘긴다. 별도 루트 설정을 만들지 않는다. 파일 서버가 없는 경우 기존 resolver를 유지하며, 혼합 구성에서는 파일 서버만 C resolver가 우선한다. 조립된 resolver를 보관해 접근 규칙 변경·등록 저장·활성화의 정책 재구성에도 유지한다.
- Registry와 설정 파일은 하나의 트랜잭션이 아니다. 부분 실패 때 PENDING 행이 남을 수 있다. 기존 A128/A105의 결과 불명 재시도 금지·Guard 종료·재시작 요구를 유지한다. 활성화 성공 뒤에도 기존 transport 재사용 대신 재시작한다.

## 검증

환경: Git Bash, 지정 worktree, `PYTHONPATH=src`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`. 최초 import 출력이 worktree의 `src/guard_app/__init__.py`임을 확인했다. 아래 `PY`는 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`다. TMPDIR/TEMP/TMP는 worktree `.cache/a-work/A49-tmp-1`이다.

```bash
PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A49-tmp-42 tests/unit/test_a49_registration.py tests/unit/test_server_add.py tests/unit/test_bridge_registration.py tests/unit/test_a105_activation.py tests/unit/test_product_assembly.py tests/unit/test_session_runtime.py tests/unit/test_a_architecture.py tests/unit/test_ui_sessions.py tests/unit/test_session_collaborators.py tests/unit/test_bridge_policy_access.py tests/unit/test_a138_product_startup.py tests/unit/test_bridge.py tests/unit/test_ui_policy.py tests/unit/test_product_settings.py tests/unit/test_b28_integration.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_responsive_detail.py tests/unit/test_ui_display_escaping.py tests/unit/test_ui_overview.py --tb=short
```

최종 **322 passed, 172 subtests passed, 30.13초**. 전체 단위 시험은 실행하지 않았다. 폴더 시험은 기준 worktree에서 `test_server_add.py`에 합쳐져 있었으며, 별도 `test_server_add_folders.py`는 없었다. grep으로 찾은 기존 A 폴더 시험과 변경된 화면 의존 시험을 포함했다. C 소유 `test_file_server.py`·`test_safe_path.py`는 변경·실행하지 않았다.

새 `test_a49_registration.py`는 5개 함수, 매개변수 확장 12개다. 실제 B SQLite·C Registry·C 설정 파일 저장과 실제 임시 NTFS 폴더를 사용한다. 도구 목록과 MCP peer는 합성이다. 확인한 내용:

- 폴더 대화상자 선택/취소, 대소문자 보존, 등급 전달, 읽기 전용, 상위 폴더 안내, 수정 시 확인 폐기, bridge action 접수.
- 등록 지문·고정 실행값·출처 설정·PENDING 저장, 세 READ 도구 허용 선택, 활성화와 재시작 요구.
- Registry를 DB에서 새로 로딩하고 Guard를 재조립해 `list_root` 공개 후 INTERNAL, `read_file` 공개 후 CONFIDENTIAL 반영. 합성 peer `tools/call` 수신은 합계 2회. 없는 파일과 허용하지 않은 `list_folder`는 Denied이고 수신은 2회 유지.
- 겹친/중복/없는/루트 밖 폴더, 파일을 폴더로 선택, 잘못된 등급을 수집·등록 전에 거부. 확인 중 하위 폴더가 사라지면 재검증 거부·토큰 재사용 거부.
- Registry 쓰기 실패, 설정 저장 실패, 설정 저장 결과 불명에서 성공 오표시·재시도 없이 기존 latch 유지.

변경 Python 11개에 `PY -m ruff check <변경 파일>`와 `PY -m ruff format --check <변경 파일>` 통과(11 files already formatted). `PYTHONPATH=src PY -m mypy`는 119 source files 통과. `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini`는 7 kept, 0 broken. `git diff --check` 통과.

### 회귀 대조

각 변형을 제품 코드에 하나씩 넣고 해당 시험을 실행한 뒤 finally에서 원래 바이트를 복구하고 SHA-256 동일성을 확인했다. 구문/수집 오류는 검출로 세지 않았다. 새 시험 함수 모두 포함한다. 설정 실패 시험은 첫 대조에서 latch 제거를 놓쳤다. 재시작 코드 직접 단언을 추가한 뒤 아래 최종 대조에서 검출했다.

| 변형 | 대상 시험 | 최종 결과 |
| --- | --- | --- |
| 폴더 경로 칸 자유 입력 허용 | picker_preserves_case_and_invalidates_confirmation | 1 실패 |
| 파일 서버 도구 설정 제거 | registration_activation_and_guard_use_registered_root | 1 실패 |
| resolver 루트를 등록 루트의 부모로 바꿈 | 같은 종단 시험 | 1 실패 |
| 출처 세그먼트 강제 소문자화 | 같은 종단 시험 | 1 실패 |
| 폴더 검증/규칙 생성 우회 | invalid_folders_are_rejected_before_collection | 6 실패 |
| 확인 시 폴더 재검증 생략 | folder_removed_after_confirmation_is_rechecked | 1 실패 |
| Registry 결과 불명 재시도 latch 무시 | file_registration_failure…[registry] | 1 실패 |
| 설정 실패 재시작 latch 제거 | file_registration_failure… | 설정 실패/불명 2 실패, 해당 없는 Registry 경우 1 통과 |

## 한계와 인계

- 실제 로컬 파일 서버 프로세스·launcher Job·Codex와의 제품 종단, 실제 OS 부작용/wire, Scanner와 C 독립 T/S 인수는 미실행이다. 합성 MCP 수신 계수를 이 범위로 일반화하지 않는다. 폴더 대화상자는 offscreen에서 선택 반환을 대역으로 검증했다.
- EXE-01 지문은 Python 실행 파일만 덮고 실행할 패키지는 덮지 않는다. `-I -m guard_app.servers.file_server`가 작동하려면 앱 Python에 해당 패키지가 설치되어 있어야 한다. 새 설치·패키징 검증은 이번 범위에서 하지 않았다.
- 파일 시스템을 등록 이후에도 고정하는 기능은 없다. 실행 시 C resolver·파일 서버의 재검증이 계속 필요하다. B/C 계약은 변경하지 않았다.
- B에는 source/Profile 선택 저장·정책 적용·resolver 보존 리뷰, C에는 파일 서버 args/루트 일치·경로 검증·실제 프로세스 독립 T33 인수를 요청한다. 도구 종류를 정의하기 위한 B/C 구현 요청은 필요 없다.
- 현행 B의 접근 거부 사유 문자열에는 이전 AGT 값이 남아 있다(A105에도 기록). ACCESS 코드 이행 완료를 주장하지 않으며 새 AGT 코드를 추가하지 않았다.

관련: I1·I2·I6·I7·I8·I9·I10, T33·T38·T10·T18·T29 및 S01의 A 연결 범위, 설계 0.4·0.5·2·4.2·10.1·12, EXE-01·CFG-01·ACCESS-01, #63·#64 수용, C2.3·C2.11, A49·A105·A128.

PR: 미정.
