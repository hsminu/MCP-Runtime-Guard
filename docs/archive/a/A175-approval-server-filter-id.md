# A175 · 승인 목록 서버 거르기를 B 서버 ID 규칙으로 검사

2026-10-30 · `wip/a-server-filter`(main `d24eee5`, #92 병합 뒤 기준 위). PR: 아직 없음(로컬 커밋).
관련: [#92](https://github.com/hsminu/MCP-Runtime-Guard/pull/92) B 리뷰 1의 참고·후속 댓글, B6-05, T18(승인 화면 표시).

## 문제

승인 목록의 서버 거르기가 검색어 패턴(`[A-Za-z0-9_.:-]{0,64}`)을 함께 써서, B가 허용하는 한글·긴 서버 ID를 보낼 수 없었다. 반대로 밑줄 두 개나 끝 밑줄은 검색어 패턴에 맞아 서버 ID 검사에서 걸러지지 않았다.

## 한 일

- `ui/approval_presentation.py`에 `valid_server_filter`를 두고, 빈 문자열 또는 `services.bridge.valid_server_id`가 허용한 값만 통과시켰다. B 규칙을 복제하지 않았다.
- `ui/approvals.py`의 화면 요청과 `ui/store_views.py`의 B 쿼리 변환이 같은 함수를 쓴다. 잘못된 입력은 보내지 않고 오류 상태로 두며, 화면은 이전 행과 조회 토큰을 비운다.
- 서버 ID의 `strip()`을 없앴다. 정확 일치 ID를 그대로 전달하고, 끝의 제어 문자가 검사 전에 사라져 통과하는 일을 막는다. B가 허용하는 앞뒤·중간 공백도 그대로 전달한다.
- 검색어 패턴과 검색어의 기존 앞뒤 공백 처리는 유지했다. 안내는 검색어 전용 문구와 서버 ID 전용 고정 문구로 나눴다. 입력 원문은 오류 문구에 넣지 않는다.
- `test_store_query_link.py`·`test_ui_approvals.py`에 19개 매개변수 사례를 추가하고 기존 연결 시험을 보강했다. 한글, 65자 ID, 공백 포함 ID, 빈 값 전달과 밑줄 두 개·끝 밑줄·NUL·줄바꿈·zero-width 거부를 확인한다. 한글 검색어 거부, 오류 뒤 빈 필터 복구, 오래된 응답 거부도 확인한다.
- UI → StoreQueryLink → 제품 BackendBridge → 조회 대역에서 정상 필터별 B 쿼리 1건과 잘못된 필터별 추가 요청 0건을 확인했다. 이는 조회 요청 계수이며 도구 dispatch·wire·부작용 계수가 아니다.

## 빈 값 처리 근거

기존 `approval_list_query`의 `server_id=query.server_id or None`을 유지했다. B `ApprovalListQuery.server_id`는 `NonEmptyString | None`이며 기본값은 `None`이다. B `SQLiteStore.list_approvals`의 개수·행 조회 모두 `(:server_id IS NULL OR r.server_id=:server_id)`를 사용한다. 따라서 UI의 빈 문자열은 B에 `None`으로 전달되어 서버 제한 없이 조회되고, 비어 있지 않은 ID는 정확 일치로 비교된다. B 코드·계약은 바꾸지 않았다.

## 검증

Windows, 로컬 `.venv` Python 3.12, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `QT_QPA_FONTDIR=C:/Windows/Fonts`, `PYTHONDONTWRITEBYTECODE=1`.
모든 pytest 실행에 `-q -p no:cacheprovider --basetemp=.cache/codex-a175/pt-<n>`을 붙였다.

- `pt-1`: 수정한 시험 2개 파일, 47 passed. 이후 기존 bridge 연결 시험의 정상·거부 요청 계수를 보강했다.
- `pt-2`: `rg -l 'approval_presentation|ApprovalListPage|approval_list_query' tests/unit` 결과 8개 파일과 `test_a_architecture.py`·`test_ui_icons_style.py`, 총 10개 파일에서 133 passed. 검색 결과의 B `test_query_contracts.py`도 실행만 했으며 수정하지 않았다.
- `ruff check`·`ruff format --check`: 변경한 Python 파일 5개 통과, 포맷 변경 필요 0개.
- `mypy src`: 소스 138개, 오류 0개.
- 가상환경 `lint-imports.exe --config tools/ci/importlinter.ini --no-cache`: 파일 197개·의존성 1179개 분석, 계약 7 kept·0 broken.
- `git diff --check` 통과. `file`로 기존 수정 파일 6개가 모두 CRLF임을 확인하고 유지했다. 새 완료 기록도 CRLF다.

고의 회귀:

- `approvals.py`·`store_views.py`를 잠시 `HEAD d24eee5`의 원래 코드로 바꿨다(Git 작업 트리 전환 명령 없이 파일 바이트만 교체).
- 새 시험 함수 `test_approval_server_filter_reaches_b_query_unchanged`·`test_server_filter_emits_the_exact_id`를 실행했다. `pt-3`: 6 failed·2 passed. 두 경로에서 한글·65자·공백 포함 ID 사례가 각각 실패하고 빈 값 사례는 통과했다.
- `finally`에서 수정본의 원래 바이트로 복원하고 SHA-256 일치를 확인했다. `approvals.py`: `6ce2f06a83bdb3e34d00e02dba1261055b427172cfb969e6642d7dc455b054d5`, `store_views.py`: `c9e3a76317e7ac7307009c3293c2e6e7b78fb2bcf62c3dc33c37df8d8628715a`.
- 복원 후 같은 시험을 다시 실행한 `pt-4`: 8 passed.

## 남은 것

- 전체 시험·실제 Windows 창 수동 확인·실제 SQLite 행을 대상으로 한 서버 거르기는 실행하지 않았다. B SQL의 빈 값·정확 일치 조건은 코드로 확인했고, 이번 실행 검증은 A 화면·쿼리 변환·제품 bridge와 조회 대역 범위다.
- 도구 dispatch·wire·부작용 및 C 독립 T/S 인수는 이번 검증 범위 밖이다. 관련 불변식의 전체 인수를 주장하지 않는다.
- B·C 소유 파일과 공통 문서는 바꾸지 않았다. 커밋·push는 하지 않았다. B·C 리뷰는 검수 후 별도다.
