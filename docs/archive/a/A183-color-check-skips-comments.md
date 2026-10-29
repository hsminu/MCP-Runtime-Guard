# A183 · #107 C 리뷰 1번: UI 색 리터럴 검사가 주석의 이슈 번호를 읽지 않게 함

2026-10-30 · `feat/a-application`(#107 head `1add6fd` 위). PR: [#107](https://github.com/hsminu/MCP-Runtime-Guard/pull/107) C 리뷰 1번(병합을 막는 항목).
관련: T18 표시 기준. 제품 코드 변경 없음.

## 지적

C의 전체 시험에서 `test_ui_sources_use_palette_colors_brand_accent_and_grid_spacing`이 `[('application.py', ['#101'])]`로 실패했다. A179가 `ui/application.py`에 단 주석 `# #101, REC-01/REC-05: ...`의 `#101`이 색 리터럴 정규식 `#[0-9a-fA-F]{3}\b`에 걸렸다. 이슈 번호가 세 자리가 된 뒤로는 `ui/*.py` 주석의 `#1xx`가 모두 걸린다. C는 주석과 정규식 중 무엇을 고칠지 A가 정하라고 했다.

## 결정과 한 일

- 시험을 고쳤다. 주석은 화면에 색을 칠하지 않으므로 색 리터럴 검사만 `tokenize`로 주석 토큰을 뺀 코드·문자열에서 한다. 스타일 문자열과 `QColor("...")` 같은 코드 안의 색은 그대로 잡는다.
- 주석은 그대로 둔다. 다른 A 주석처럼 이슈를 `#101` 형식으로 적는다.
- 같은 시험의 나머지 검사(eyebrow, accent 사용 수, 여백 숫자)는 원문 그대로 본다.
- 바꾼 파일은 `tests/unit/test_ui_icons_style.py` 하나다.

## 검증

2026-10-30, Windows / Python 3.12 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`. #107 head `1add6fd`에 이 변경만 얹은 상태다.

| 확인 | 결과 |
| --- | --- |
| 고친 시험 | 통과 |
| 주석 제외를 되돌림(C 실패 재현) | `[('application.py', ['#101'])]`로 실패 |
| `application.py` 코드에 문자열 `"#101"`을 임시로 더함 | 실패(코드 속 색은 계속 잡음) |
| `application.py`에 주석 `# probe #abcdef`를 임시로 더함 | 통과 |
| `test_ui_icons_style.py` 전체 | 6 passed |
| `ruff check`·`ruff format --check`(바꾼 파일) | 통과 |

임시 변경 뒤 `application.py`의 `git hash-object`가 HEAD blob과 같았다.

## 한계

- 이 변경 뒤 전체 단위 시험은 다시 돌리지 않았다. C의 전체 실행에서 실패한 것은 이 시험 1개였다.
- docstring은 문자열 토큰이라 계속 검사 대상이다. `ui/*.py` docstring에 `#1xx`를 쓰면 여전히 걸린다.
- 네트워크·인증정보·사용자 앱 데이터 폴더는 쓰지 않았다. 공통 문서와 B·C 소유 파일은 수정하지 않았다.
