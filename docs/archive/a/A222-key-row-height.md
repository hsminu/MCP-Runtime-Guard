# A222 · 설정 화면 key 줄의 입력칸·버튼 높이를 줄 높이로 맞춤(CI 354 대 355)

2026-11-05 · `feat/a-application` · PR #118. 출처: PR #118의 GitHub Actions `checks` 실패, 사용자 지시("여기서 고쳐").
관련: A189(설정 화면 세 카드), A216(offscreen 글꼴, 같은 시험의 `328 == 329`).

## 문제

- GitHub Actions가 다시 돌기 시작하면서 PR #118의 두 커밋(`7586e1e`, `0696ac3`)에서 같은 시험 1건이 실패했다.
  - 시험: `test_ui_settings_page.py::test_three_cards_width_reflow_and_no_initial_validation_errors`
  - 실패: `assert 354 == 355`. 넓은 화면에서 Codex API 키 입력칸과 "API 키로 로그인" 버튼의 위쪽 y좌표가 1px 달랐다.
  - 나머지 5791·5803개는 CI에서 통과했다.
- 원인
  - 두 컨트롤은 `ResponsiveRow`의 가로 줄에 들어간다. 세로 크기 정책이 Fixed라 각자 높이로 가운데 정렬된다.
  - 높이는 글꼴 metric에서 나온다. 로컬은 입력칸 34px·버튼 33px로 차이가 1px이라 위쪽 좌표가 같았다. CI 러너는 글꼴이 달라 차이가 2px 이상이 되고, 버튼이 1px 내려갔다.
  - A216은 글꼴이 아예 없는 경우를 고쳤다. 이번은 글꼴은 있지만 metric이 다른 경우다.
- 화면에서도 두 컨트롤의 위아래 끝이 어긋난다. 그래서 시험을 느슨하게 하지 않고 배치를 고쳤다.

## 한 일

- `widgets.fill_row_height(*widgets)`: 세로 크기 정책을 Preferred로 바꿔, 가로 줄에서 가운데 정렬하지 않고 줄 높이를 채우게 한다.
- Codex API 키 줄(`model_settings`)과 Scanner 키 줄(`scanner_key`)의 입력칸·버튼에 썼다. 다른 `ResponsiveRow` 사용처(필터, 버튼만 있는 줄)는 그대로다.
- 결과(로컬, offscreen)
  - 넓은 화면: 입력칸·버튼 모두 37px, 위쪽 좌표 같음. 전에는 입력칸 34px(자기 sizeHint 37px보다 작음)·버튼 33px였다.
  - 좁은 화면(세로로 쌓임): 입력칸 37px·버튼 33px로 수정 전과 같다.

## 시험

- `test_key_rows_keep_field_and_button_one_height_whatever_the_font`(새 시험, 두 경우)
  - 버튼 또는 입력칸의 글꼴·padding을 줄여 다른 글꼴의 metric을 강제로 만든다(sizeHint 높이 차 2px 이상).
  - 두 key 줄 모두 위쪽 좌표와 높이가 같아야 한다.
- 수정 전 코드에서 이 시험은 두 경우 모두 실패했다(버튼이 작을 때 362 대 368, 입력칸이 작을 때 367 대 362). 같은 시험의 첫 판(버튼만 줄임)은 CI와 같은 1px 차(362 대 363)를 재현했다.

## 검증

2026-11-05, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| `test_ui_settings_page.py` | 34 통과 |
| 단위 시험 전체 `pytest -q`(`QT_QPA_PLATFORM=offscreen`) | 5802 통과, 26 건너뜀, 하위 1303 통과(실행 중 `model_settings.py`의 공백 한 칸만 바뀜) |
| 계약 시험 `pytest -q tests/contract` | 45 통과 |
| `ruff check .`·`ruff format --check .`, `mypy`, import-linter, bandit | 통과·0건 |
| GitHub Actions | 수정 전 두 커밋은 이 시험 1건만 실패. 이 수정 커밋의 실행 결과는 기록을 쓸 때 아직 없음(PR에서 확인) |

## 한계

- CI 러너의 글꼴로 직접 재현하지는 않았다. 로컬에서 글꼴 크기를 강제로 바꿔 같은 조건을 만들었다.
- 다른 화면의 입력칸+버튼 줄(서버 추가의 명령 줄 등)도 같은 모양일 수 있다. CI에서 실패하지 않아 이번에는 건드리지 않았다.
