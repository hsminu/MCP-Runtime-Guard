# A215 · 로컬 CI 실행과 A212 직접 적용 경로 수정

2026-10-30 · `wip/next` · PR #113. 출처: 사용자 지시("그냥 여기서 다 고쳐"). GitHub Actions가 결제 문제로 작업을 시작하지 못해(`semgrep`·`checks`), `.github/workflows/ci.yml` 단계를 로컬에서 돌렸다.
관련: A212(로그·승인 화면 이동 때 다시 읽기), C6.3·C6.4(CI 파이프라인).

## 로컬 CI 결과 (수정 전, `975584f`+A214 `56e93c0`)

| CI 단계 | 결과 |
| --- | --- |
| `requirements.lock`와 `uv.lock` 일치(`uv export --locked --all-groups --no-emit-project`) | 차이 없음(uv 0.13.0, `UV_OFFLINE=1`) |
| `ruff check .`·`ruff format --check .` | 통과(397개 파일) |
| `mypy` | 통과(147개) |
| import-linter | 계약 7개 유지 |
| `bandit -r src -ll -q` | 결과 0건, 종료 코드 0 |
| 단위 시험 `pytest -q` | 1 실패, 5601 통과, 26 건너뜀, 하위 1295 통과(8분 48초) |
| 계약 시험 `pytest -q tests/contract` | 45 통과 |
| Semgrep | 실행 못 함: Windows용이 없고 WSL Ubuntu에도 설치돼 있지 않다. 받는 것은 하지 않았다. 대신 아래 근사 검사를 했다 |

- Semgrep 근사: `tools/ci/semgrep.yml` 규칙 10개를 Python AST·정규식으로 옮긴 임시 검사기(저장소 밖)를 `src` 전체에 돌렸다.
  - 표본 파일로 규칙 10개가 모두 걸리는 것을 먼저 확인했다.
  - `# nosemgrep` 없이 걸리는 결과: 0건.
  - 예외 위치: 저장소 `tools/ci/check_semgrep_exceptions.check`에 넣어 SG-01–04와 설계 경계 10건과 정확히 같았다.
  - Semgrep 자체의 판정과 같다고 보장하지 않는다. 규칙 시험(`semgrep --test`)은 돌리지 못했다.

## 실패와 수정

- 실패: `test_ui_read_only_pages.py::test_window_routes_public_approval_and_log_queries`
  - A212부터 승인 화면으로 이동하면 목록을 다시 읽는다. 읽는 동안 기존 줄을 비우므로 이동 직후에는 대상(`approval-1`)을 고를 수 없다.
  - A212는 대상을 bridge `query_answered` 응답 뒤에만 다시 골랐다. 이 시험은 bridge 없이 창의 `set_approval_page`로 응답을 넣는 경로라 대상이 끝내 선택되지 않았다.
- 수정(`ui/window.py`)
  - `set_approval_page`가 현재 조회 응답을 받아들이면, 승인 화면에 대기 중인 이동 대상을 bridge 응답 때와 같이 한 번 고른다.
  - `set_approval_error`가 현재 조회의 실패를 받아들이면 대기 중인 대상을 버린다.
- 시험(`test_ui_read_only_pages.py`): 이동 직후에는 선택이 없고, 다시 읽은 응답을 넣은 뒤 선택된다고 고쳤다. 이전 조회 응답은 거절되고, 실패 뒤의 응답은 대상을 고르지 않는다는 단언을 더했다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| `test_ui_read_only_pages.py`, `test_a212_page_refresh.py` | 16개 통과 |
| `window.py`·바꾼 시험 `ruff check`·`ruff format --check`, `window.py` `mypy` | 통과 |

- 되돌림(`test_ui_read_only_pages.py`만, 복원 뒤 바이트 같음): 직접 경로 재선택 제거 → 1 실패, 실패 때 대상 유지 → 1 실패.
- 수정 뒤 전체 시험은 다시 돌리지 않았다. 바뀐 곳은 `window.py`의 두 메서드와 그 시험 하나다.

## 한계

- Semgrep은 근사 검사뿐이다. GitHub Actions가 다시 돌면 확인해야 한다.
- `set_approval_page`·`set_approval_error`는 지금 제품 코드에서 부르지 않는다(제품은 `StoreQueryLink`를 거친다). 두 경로가 같은 규칙을 따르게 맞춘 것이다.
