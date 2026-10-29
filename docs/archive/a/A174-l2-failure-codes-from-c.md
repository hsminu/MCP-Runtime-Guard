# A174 · A151 L2 실패 코드 사본을 C 상수로 교체

2026-10-30 · `wip/a155-a151`, 기준 `bba10f3` 위. 관련 PR: [#91](https://github.com/hsminu/MCP-Runtime-Guard/pull/91), #83 C 참고 4. 이 변경의 PR은 아직 만들지 않았다.
관련: A151, I6·I9·I11, 개발문서 2.2·10.4절·T22(A 시작 거부·화면 연결).

## 문제

`session_preparation.py`가 C의 L2 실패 코드 28개를 복사해 관리했다. #91에서 C가 공개한 `codex_l2.L2_FAILURE_CODES`로 통합한다는 A 리뷰 2번 약속을 반영했다.

## 한 일

- 사본을 지우고 `from guard_app.security.codex_l2 import L2_FAILURE_CODES as L2_FAILURE_CODES`로 명시적으로 재수출한다.
- `services/bridge.py` 재수출, `session_startup.py` 소비, `ui/presentation.py`의 bridge 경유 import는 그대로 둔다. UI가 security를 직접 가져오지 않는다.
- A 시험에서 preparation·bridge·startup·presentation이 C 상수와 동일 객체를 쓰는지 확인한다. 값이 같은 사본으로 되돌아가도 검출할 수 있다.
- C 상수의 모든 코드에 대해 시작 거부 코드가 보존되고 화면에 “Codex 시작 검사를 통과하지 못함.”이 나오며 내부 코드가 안내 본문에 노출되지 않는지 확인한다.

변경 파일:

- `src/guard_app/services/session_preparation.py`
- `tests/unit/test_a151_l2_startup.py`
- 이 완료 기록
- `docs/a-status.md`의 기존 A151 사본 교체 완료 줄(A174 기록 링크)

A173과 제품·시험·완료 기록 파일은 겹치지 않는다. 두 항목 모두 명시적으로 요구된 `docs/a-status.md`만 공유하며, A173 새 줄과 기존 A151 줄을 각각 반영한다.

## 검증

Windows, Python 3.12.15, 지정 `.venv`, `PYTHONPATH=src`, Qt offscreen, 바이트코드 생성 끔. pytest에는 `-q -p no:cacheprovider --basetemp=.cache/codex-a155/pt-174-2`를 사용했다.

- 관련 9개 파일: 225 passed, 5 deselected, 23 subtests passed. 신규 동일 객체 시험 1건·전체 코드 시험 28건을 포함한다.
- 실행 파일(모두 `tests/unit/`): `test_a151_l2_startup.py`, `test_session_preparation.py`, `test_session_runtime.py`, `test_session_collaborators.py`, `test_application_ids.py`, `test_codex_preparation.py`, `test_codex_product_flow.py`, `test_a_architecture.py`, `test_codex_l2.py`.
- 네트워크·실제 Codex 실행을 하지 않기 위해 `test_codex_l2.py`에서 다음 5개를 제외했다: `test_mock_model_scripts_forced_calls_then_a_final_answer`, `test_mock_model_refuses_wrong_token_other_paths_and_chunked_bodies`, `test_extra_unreadable_request_fails_an_otherwise_good_run`, `test_real_codex_passes_l2`, `test_deadline_ends_a_codex_that_never_answers`.
- 최종 변경 Python 6개(A173 포함)에 `ruff check`·`ruff format --check` 통과. `mypy src`: 138개 통과. import-linter: 7 kept, 0 broken. `git diff --check` 통과. 기존 줄 끝은 `file`로 CRLF 유지를 확인했다.
- 중간 실행은 새 시험에서 receipt를 bridge 재수출로 잘못 가져와 28 failed, 197 passed, 5 deselected, 23 subtests passed였다. 실제 정의 모듈 `session_preparation`에서 가져오도록 시험을 고친 뒤 위 최종 결과를 얻었다. 첫 포맷 검사에서 파일 끝 빈 줄 1건이 지적되어 Ruff로 정리했다.
- 전체 시험, 실제 L2·네트워크·인증정보·사용자 앱 데이터는 검증에 사용하지 않았다.

## 남은 것

- C 소유 `test_codex_l2.py::test_a_l2_failure_codes_match_until_a_uses_the_constant`는 소유 범위 밖 파일 변경 금지에 따라 수정하지 않았다. 실행은 통과했지만 이제 사본 동등성 검사는 자명하다. C에 해당 시험 제거 또는 새 의미로 정리를 넘긴다. A의 회귀 검증은 값 비교 대신 위 동일 객체 시험과 전체 코드 화면 시험으로 수행한다.
- 실제 Codex의 자체 도구 차단 효과와 독립 T22 인수는 C 범위다. 이번에는 상수 사용·시작 거부·화면 연결만 확인했다.
- B/C 소유 파일과 공통 문서는 변경하지 않았고 커밋하지 않았다.
