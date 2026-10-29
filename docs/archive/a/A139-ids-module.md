# A139 · A 모듈 ID·토큰 생성 통합

2026-10-29. PR: 미정.

## 한 일

- `services/bridge_ingress.py`: `new_command_id()`는 C의 `new_id("command")`, `new_draft_key()`는 `draft:`와 `new_suffix()`를 사용한다. `bridge_id(None, generate=True)`도 같은 명령 ID 공급자를 사용한다. 전달받은 ID 검증은 유지한다.
- `services/bridge.py`: 두 함수를 UI에 재노출한다. `ui/runtime.py`의 실행·제어, `ui/sessions.py`의 시작·메시지·초안 생성을 이 함수들로 연결했다.
- `services/product_assembly.py`: Guard에 넘기는 request·approval 공장을 `new_id`로 바꿨다.
- `services/session_runtime.py`: Task 키는 `new_id("session")`, 제품 Store 감사 공장은 `new_id("audit")`다.
- `services/codex_preparation.py`: 계정 폴더는 `new_id("account")`, 자원 소유 토큰은 `new_token()`이다. `services/session_cleanup.py`의 소유 토큰 중복 비교는 `token_matches()`다. 준비 모듈의 딕셔너리 조회와 프로세스 객체 동일성 검사는 유지한다. 이 조회 전체가 일정 시간이라는 주장은 하지 않는다.
- `tests/unit/test_application_ids.py`: 생성 지점별 형식·반복 생성 중복, 실제 SQLite Task 저장, Guard·Store 주입 공장, UI의 5개 생성 경로, 토큰 비교 연결을 검사한다.

`src/guard_app/__main__.py`, `services`, `adapters`, `ui`에서 `uuid`, `random`, `token_hex`, `token_urlsafe`, `urandom`, `compare_digest`를 다시 검색했다. 남은 일치는 B `catalog_schema.py`의 JSON Schema 형식 이름 `uuid`뿐이다. `tests/` 전체의 `uuid4` 참조도 검색했으며 삭제한 제품 모듈의 `uuid4` 속성을 패치하는 기존 시험은 없었다. 시험 자체의 UUID 생성은 바꾸지 않았다.

## 형식·계약 대조

| 값 | 변경 뒤 | 영향 |
| --- | --- | --- |
| request·approval·audit·session·account | 기존 접두어 + `-` + 소문자 hex 32자리 | 길이·문자 집합 유지. UUID 버전 비트 제약 대신 C의 128비트 난수 사용 |
| 초안 키 | `draft:` + 소문자 hex 32자리 | 기존 형식 유지 |
| 자동 명령 ID | `command-` + 소문자 hex 32자리(40자) | 기존 접두어 없는 32자에서 변경. 명령 상관관계를 나타내는 식별자라 `new_id` 사용. bridge 256자 제한 이내이며 명령 추적·UI 회귀 통과 |
| 자원 소유 토큰 | URL 안전 문자열 43자, 기본 32바이트 난수 | 기존 hex 32자에서 변경. `TaskResources`의 비공개 메모리 토큰이고 DB·폴더 이름에는 쓰지 않는다. 소유권 검사 256자 제한 이내 |

B의 `SQLiteStore._identifier`는 비어 있지 않은 문자열과 surrogate 부재를 검사한다. Guard의 ID 공장 계약과 C `ids.py`의 형식을 대조했고, C `paths.py`의 Task 폴더 이름 규칙(`[A-Za-z0-9][A-Za-z0-9_-]{0,127}`)에도 session·account 값이 들어맞는다. 관련 기존 시험에서 실제 폴더·프로세스 수명 및 Guard·SQLite 연결을 함께 실행했다.

`tools/ci/importlinter.ini`의 `ui-through-bridge`는 UI의 직접 `security` import를 금지하고 간접 import를 허용한다. UI는 bridge 함수만 import한다. import 계약·B/C 구현·공통 문서는 바꾸지 않았다.

관련: C2.7, 개발문서 0.4·2.2·10.4·12.1·13.1, I3·I7·I9. T08은 import 계약 부분, T21·T46은 Task 식별·소유권의 A 단위 연결 부분이다. 전체 T/S 인수를 주장하지 않는다. REC-04의 실행 ID 연결은 A138에 남는다.

## 실제 검증

Git Bash에서 실행했다. pytest 환경은 다음과 같다.

```bash
export TMPDIR="$PWD/.cache/a-work/A139-tmp" TEMP="$PWD/.cache/a-work/A139-tmp" TMP="$PWD/.cache/a-work/A139-tmp" QT_QPA_PLATFORM=offscreen
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A139-tmp-2 tests/unit/test_application_ids.py tests/unit/test_bridge*.py tests/unit/test_codex_preparation.py tests/unit/test_product_assembly.py tests/unit/test_session_runtime.py tests/unit/test_session_preparation.py tests/unit/test_session_collaborators.py tests/unit/test_runtime.py tests/unit/test_ui_sessions.py tests/unit/test_sqlite_app.py tests/unit/test_ids.py
```

- 282 passed, 113 subtests passed, 1 warning, 35.32초. 경고는 `test_runtime.py::test_human_review_approve_and_shutdown_use_real_mcp`의 Pydantic `lifespan` forward reference 미해결 경고다.
- 아래 변경 파일 9개에 `ruff check`와 `ruff format --check`: 통과. 최초 Ruff 검사에서 import 정렬을 고쳤고, 남은 긴 줄은 formatter로 고친 뒤 재검사했다.

```bash
.venv/Scripts/python.exe -m ruff check src/guard_app/services/bridge.py src/guard_app/services/bridge_ingress.py src/guard_app/services/codex_preparation.py src/guard_app/services/product_assembly.py src/guard_app/services/session_runtime.py src/guard_app/services/session_cleanup.py src/guard_app/ui/runtime.py src/guard_app/ui/sessions.py tests/unit/test_application_ids.py
.venv/Scripts/python.exe -m ruff format --check src/guard_app/services/bridge.py src/guard_app/services/bridge_ingress.py src/guard_app/services/codex_preparation.py src/guard_app/services/product_assembly.py src/guard_app/services/session_runtime.py src/guard_app/services/session_cleanup.py src/guard_app/ui/runtime.py src/guard_app/ui/sessions.py tests/unit/test_application_ids.py
.venv/Scripts/python.exe -m mypy
.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

- mypy: 114개 소스 파일 통과. import-linter: 7 kept, 0 broken. diff 공백 검사 통과.
- 전체 단위 시험은 실행하지 않았다. 실제 codex.exe·모델·외부 인증 시험도 실행하지 않았다.

## 회귀 대조

`.venv/Scripts/python.exe .cache/a-work/A139-mutations.py`로 제품 코드에 회귀를 하나씩 넣었다. 각 실행은 `test_application_ids.py -k <해당 시험>`만 수행하고 45초 상한을 뒀다. 매번 `finally`에서 원래 바이트로 복원하고 바이트 일치를 확인했다.

| 제품 회귀 | 잡은 새 시험 | 결과 |
| --- | --- | --- |
| bridge 명령 ID를 유효한 고정 문자열로 변경 | `test_bridge_generated_ids_and_explicit_id_preservation` | 실패 확인 |
| bridge 초안 키를 유효한 고정 문자열로 변경 | 같은 시험 | 실패 확인 |
| request 공장을 고정 문자열로 변경 | `test_guard_id_factories_keep_storage_format` | 실패 확인 |
| approval 공장을 고정 문자열로 변경 | 같은 시험 | 실패 확인 |
| session ID를 고정 문자열로 변경 | `test_session_ids_are_distinct_persisted_task_keys` | 실패 확인 |
| audit 공장을 고정 문자열로 변경 | `test_product_store_receives_distinct_audit_factory` | 실패 확인 |
| 소유 토큰을 유효한 고정 문자열로 변경 | `test_preparation_uses_distinct_tokens_and_account_folder_ids` | 실패 확인 |
| account ID를 고정 문자열로 변경 | 같은 시험 | 실패 확인 |
| `token_matches`를 `==`로 되돌림 | `test_resource_claim_uses_token_comparator` | 실패 확인 |
| UI 실행 ID를 고정 문자열로 변경 | `test_ui_allocates_distinct_bridge_identifiers[run]` | 실패 확인 |
| UI 제어 ID를 고정 문자열로 변경 | 같은 시험 `[control]` | 실패 확인 |
| UI 메시지 ID를 고정 문자열로 변경 | 같은 시험 `[send]` | 실패 확인 |
| UI 시작 ID를 고정 문자열로 변경 | 같은 시험 `[start]` | 실패 확인 |
| UI 초안 키를 고정 문자열로 변경 | 같은 시험 `[draft]` | 실패 확인 |

14건 모두 해당 시험 1개 실패(exit 1)를 확인했다. 복원 뒤 다음 명령은 11 passed(1.97초)였다.

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A139-tmp-3 tests/unit/test_application_ids.py
```

## 남은 일과 한계

- B6.8 복구의 `new_id("run")` 연결은 A138에서 제품 Store 명시 시작과 함께 한다.
- 반복 생성의 중복 없음은 표본 검사다. 모든 미래 생성의 무충돌이나 타이밍 특성을 증명하지 않는다.
- B·C에 구현 변경 요청은 없다. 담당 리뷰는 기존 소유권 규칙에 따른다.
- `docs/a-status.md`는 사용자 취합 대상이라 수정하지 않았다. 시작부터 존재한 사용자 변경과 `.claude/`는 건드리지 않았다.
