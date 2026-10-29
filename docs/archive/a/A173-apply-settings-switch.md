# A173 · #91 `apply_settings`로 도구 설정 저장 전환

2026-10-30 · `wip/a155-a151`, 기준 `bba10f3` 위. 관련 PR: [#91](https://github.com/hsminu/MCP-Runtime-Guard/pull/91). 이 변경의 PR은 아직 만들지 않았다.
관련: A155·A163, I1·I6·I7·I8·I10, 개발문서 10.1절·T10(A 연결 검증).

## 문제

A155는 ACTIVE 서버에 `request_review`를 호출한 뒤 파일을 저장했다. 등록 도구의 종류·인자는 재시작의 `observe`에 기대어 갱신했다. B가 `startable_servers`에서 `REG_SETTINGS_MISMATCH`를 제외하면 그 연결에 의존해서는 불일치를 풀 수 없다. #91 A 리뷰 1·4·5번 약속을 반영했다.

## 한 일

- `TaskPolicy.save_tool_settings`: `apply_settings` → 설정 파일 저장 → 정책 적용 → Guard 닫기·재시작 요구 순서로 바꿨다. ACTIVE·PENDING 모두 호출하며 BLOCKED 거부는 유지한다. `request_review`·`activate`는 호출하지 않는다.
- `prepare_policy_change`의 `loaded_profile.servers`에서 서버를 고른다. B가 이 값을 `change.document`와 같은 새 문서에서 검증·생성하므로 이전 설정이나 실행 가능한 서버 목록을 쓰지 않는다.
- Guard가 있으면 그 `invalidate_tool`, 없으면 None을 넘긴다. 정책 잠금 범위는 유지한다.
- `apply_settings`의 `RegistryError`는 쓰기 전 거부로 `REG_TOOLS_INVALID`에 매핑한다. 파일 저장·Guard 종료·재시작 요구·등록 불명 표시는 하지 않는다. 기존 화면 문구를 사용한다.
- 나머지 중단은 `finally`에서 `review_unknown()`·`REG_TOOLS_UNKNOWN` 재시작 요구·Guard 종료로 처리한다. 파일 저장/정책 적용의 고정 실패 코드는 유지하고, 취소와 예상 밖 예외는 전파한다. 넓은 except는 추가하지 않았다.
- `TOOL_FINGERPRINT_CHANGED` 화면 문구를 “도구 정의·설정 변경”으로 바꿨다.

`bridge_registration.py`는 바꾸지 않았다. `RegistrationCommands._request`의 recheck는 기존 stdio 설정을 읽어 `command_size`·`command_sha256`만 새 실행 파일 값으로 교체한다. confirm에서는 지문을 다시 확인하고 `refresh` → ACTIVE `request_review` → `save_registration`을 거친다. 이 분기에서는 도구 목록을 수집하지 않고 `tools`도 그대로다. C `ServerRegistry.apply_settings`·`_with_settings`는 등록 정의·지문을 유지하고 종류·목적지/자원 인자만 반영한다. B `record_settings_change`는 변경 도구가 없으면 revision만 확인해 ACTIVE를 유지한다. 따라서 여기서 교체하면 실행 파일만 바뀐 서버의 재검토를 생략하게 된다. 현재 `request_review`를 유지해야 한다.

변경 파일:

- `src/guard_app/services/session_policy.py`
- `src/guard_app/ui/presentation.py`
- `tests/unit/test_a155_tool_settings.py`
- `tests/unit/test_call_logs.py`
- 이 완료 기록
- `docs/a-status.md`의 A173 완료 줄

## 검증

Windows, Python 3.12.15, 지정 `.venv`, `PYTHONPATH=src`, Qt offscreen, 바이트코드 생성 끔. 모든 pytest는 `-q -p no:cacheprovider --basetemp=.cache/codex-a155/pt-173-<번호>`를 사용했다. 임시 자료는 이 worktree 안에 두었다.

- 집중 시험: `test_a155_tool_settings.py`·`test_call_logs.py`, 최종 57 passed.
- 관련 시험 19개 파일: 528 passed, 2 deselected, 16 subtests passed. 아래는 모두 `tests/unit/` 아래다.
  - `test_a_architecture.py`, `test_registry_store.py`, `test_server_registry.py`, `test_session_runtime.py`
  - `test_a105_activation.py`, `test_a138_product_startup.py`, `test_a151_l2_startup.py`, `test_a155_tool_settings.py`, `test_a156_destinations.py`
  - `test_account_runtime.py`, `test_bridge_registration.py`, `test_call_logs.py`, `test_codex_browser_login.py`, `test_product_settings.py`, `test_runtime.py`, `test_session_collaborators.py`
  - `test_ui_display_escaping.py`, `test_ui_overview.py`, `test_ui_sessions.py`
- 네트워크 금지로 제외: `test_runtime.py::test_human_review_approve_and_shutdown_use_real_mcp`, `test_bridge_registration.py::test_registration_collector_reports_a_closed_port_as_a_start_failure`.
- 실제 SQLite Store·C Registry로 Store 변경 뒤 파일 저장 실패/결과 불명을 각각 만든다. 기존에 설정된 `second` 도구를 READ→SEND로 바꾼 등록과 기존 파일이 `REG_SETTINGS_MISMATCH`를 만드는지 확인했다. 새 Registry·TaskPolicy·파일 saver를 만들고 도구 설정 preview/save로 같은 편집을 다시 저장하면 `server_issue`는 None, 서버는 PENDING이다. 재시작은 이 객체 재생성으로 모사했으며 Store 연결은 유지했다. 재수집·observe·activate 없이 복구한다. 실제 MCP peer 호출은 0이며 외부 wire·OS 부작용 계수로 일반화하지 않는다.
- ACTIVE/PENDING × Guard 유무 4건에서 새 서버 설정·무효화 콜백·파일/정책/종료 순서를 확인한다. 사전 거부 4코드, Store 실패·실제 revision 충돌·커밋 뒤 실패·취소·무효화 실패·파일 저장 실패/불명·정책 적용 실패에서 차단과 취소 전파를 확인한다.
- 고의 회귀: `session_policy.py`를 HEAD의 원래 `request_review` 코드로 임시 교체했다. `test_apply_settings_precedes_file_policy_and_close` 4건과 `test_resave_clears_persisted_settings_mismatch_without_connecting` 2건, 총 6건 모두 실패했다(수집 오류 없음). finally로 수정본 원래 바이트를 복원하고 SHA-256 `5cf800f8fabd2e0086b4bc5c0247d2768c2f3220d0f92488cce1fccd1bc9805d` 일치를 확인했다. 위 528건은 복원 뒤 실행 결과다.
- `ruff check`·`ruff format --check`: 변경 Python 4개 통과. `mypy src`: 138개 통과. import-linter: 7 kept, 0 broken. `git diff --check`: 통과. `file`로 기존·변경 파일의 CRLF 유지를 확인했다.
- 중간 실패: 첫 집중 실행은 시험의 잘못된 Reason import로 수집 오류 1건. 수정 뒤 55 passed·2 failed였는데, 미설정 OTHER 도구는 B의 비교 대상에서 빠지므로 불일치 시나리오가 성립하지 않았다. 기존 설정에 있던 도구의 종류 변경으로 고쳐 위 최종 결과를 얻었다. 제품 B/C 코드는 바꾸지 않았다.

## 남은 것

- B에 전달: 서버를 시작하거나 `observe`하지 않아도 도구 설정 재저장으로 불일치를 해소한다는 근거를 확인했다. `startable_servers`의 `REG_SETTINGS_MISMATCH` 제외는 B가 진행한다.
- C에 전달: `apply_settings`의 `RegistryError`가 쓰기 전에만 발생한다는 #91 A 리뷰의 계약 유지 요청. 현재 구현을 근거로 구분했으며 새 API 계약을 임의로 추가하지 않았다. C의 독립 T10 경쟁·실제 wire 계수 인수는 별도다.
- 실제 GUI 수동 조작·외부 서버·네트워크·실제 Codex·전체 시험은 실행하지 않았다. B/C 소유 코드·시험과 공통 문서는 변경하지 않았다.
