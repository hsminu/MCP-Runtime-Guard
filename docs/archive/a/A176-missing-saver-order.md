# A176 · #99 B 리뷰 제안: 저장기 없음은 Registry 쓰기 전에 거부됨을 시험으로 고정

2026-10-30 · `feat/a-application`(#99 head `b30ebbb` 위). PR: [#99](https://github.com/hsminu/MCP-Runtime-Guard/pull/99) B 리뷰의 막지 않는 제안.
관련: A173, I6. 동작 변경 없음.

## 제안

B는 `save_tool_settings`에서 `self._saver is None` 검사를 `apply_settings` 앞으로 옮기자고 했다. 저장기가 없으면 Registry를 먼저 쓴 뒤 `REG_SETTINGS_UNAVAILABLE`로 끝나서, 불필요하게 `review_unknown()`과 재시작 요구가 생긴다는 근거였다.

## 확인한 것

- 그 순서는 이미 지켜지고 있다. `save_tool_settings`는 정책 잠금을 잡은 뒤 가장 먼저 `check_registration()`을 부른다. 이 함수가 저장기가 없으면 `REG_SETTINGS_UNAVAILABLE`로 거부한다(`save_registration`도 같은 함수를 쓴다).
- `apply_settings` 뒤의 `self._saver is None` 검사는 실제로는 닿지 않는다. `_saver`는 `connect_registration_saver`에서 None에서 저장기로 바뀔 뿐 다시 None이 되지 않는다. 이 검사는 아래 호출의 타입 좁히기를 겸한다.

## 한 일

- `src/guard_app/services/session_policy.py`: 그 검사 앞에 `check_registration()`이 이미 거부했다는 주석 한 줄을 달았다. 코드 동작은 바꾸지 않았다.
- `tests/unit/test_a155_tool_settings.py`: `test_missing_saver_refuses_before_apply_settings`를 더했다(PENDING·ACTIVE 2건).
  - 저장기가 없으면 `apply_settings`를 부르지 않고 `REG_SETTINGS_UNAVAILABLE`로 답한다.
  - 설정 파일·Registry 행은 그대로다. 재시작 요구·등록 불명 표시는 없다. peer 호출은 0이다.
- `docs/a-status.md`: A176 완료 줄

## 검증

Windows, 로컬 `.venv` Python 3.12, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `PYTHONDONTWRITEBYTECODE=1`. pytest에는 `-q -p no:cacheprovider --basetemp=.cache/a176/pt-<n>`을 붙였다.

- 새 시험: 2 passed
- 고의 회귀
  - `check_registration()`의 저장기 검사를 잠시 지웠다. 그러면 `apply_settings` 뒤의 검사만 남는다.
  - 새 시험 2건이 모두 실패했다(`apply_settings` 호출).
  - 원래 바이트로 되돌린 뒤 SHA-256 일치를 확인했다.
- 관련 6개 파일: 169 passed, 1 deselected(네트워크를 쓰는 `test_bridge_registration.py::test_registration_collector_reports_a_closed_port_as_a_start_failure`)
  - 파일: `test_a155_tool_settings`·`test_a156_destinations`·`test_a105_activation`·`test_bridge_registration`·`test_session_runtime`·`test_a_architecture`
- 정적 검사 통과
  - `ruff check`·`ruff format --check`: 바뀐 Python 2개
  - `mypy src`: 140개
  - import-linter: 7 kept
  - `git diff --check`

## 남은 것

- 없음. 전체 시험은 돌리지 않았다.
