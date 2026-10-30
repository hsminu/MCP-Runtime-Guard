# A216 · offscreen 시험에 Windows 글꼴 폴더 지정

2026-10-30 · `wip/next` · PR #113. 출처: #114 B 보고(A 화면 시험 3건 실패), #114 A 리뷰에서 A가 고치기로 함, 사용자 지시("이어서 해").
관련: A215(로컬 CI 실행), A190(정책 화면), A189(설정 화면).

## 문제

- CI(`ci.yml`)는 `QT_QPA_PLATFORM=offscreen`만 주고 `QT_QPA_FONTDIR`는 주지 않는다. 이때 offscreen Qt는 글꼴을 하나도 찾지 못한다(`QFontDatabase.families()` 0개).
- 그래서 대체 글자 폭으로 배치를 재고, 칸 너비·세로 좌표를 보는 A 화면 시험이 실패했다.
  - B가 main `4d8c7a6`에서 본 3건: `test_ui_policy`의 칸 너비 2건(`100 >= 183`), `test_ui_settings_page`의 입력칸·버튼 높이 1건(`328 == 329`)
  - 이 브랜치에서 하나 더: `test_ui_responsive_detail::test_server_list_keeps_identity_and_status_at_small_width`
- A215의 "로컬 CI 실행"은 `QT_QPA_FONTDIR=C:/Windows/Fonts`를 준 채 돌렸다. 그래서 CI와 같은 환경이 아니었고, 이 실패들을 보지 못했다.
- 실제 앱은 Windows 플랫폼 플러그인으로 시스템 글꼴을 쓰므로 화면 코드의 문제가 아니다.

## 한 일

- `tests/unit/test_ui_icons_style.py`
  - Windows에서 `QT_QPA_FONTDIR`가 없으면 `%WINDIR%\Fonts`로 정한다. 같은 파일이 이미 `QT_QPA_PLATFORM`을 이렇게 정하고 있다.
  - 사용자가 준 값은 그대로 둔다. Windows가 아니면 바꾸지 않는다.
- 공통 `conftest.py`·`pyproject.toml`·`ci.yml`은 건드리지 않았다. 앞의 둘은 소유가 정해지지 않았고(ENV-01), `ci.yml`은 C 담당이다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`. 모두 `QT_QPA_FONTDIR` 없이, CI처럼 `QT_QPA_PLATFORM=offscreen`만 주고 돌렸다.

| 확인 | 결과 |
| --- | --- |
| 수정 전: PySide6를 쓰는 시험 파일 54개 | 4 실패, 1126 통과 |
| 수정 후: 같은 54개 | 1130 통과, 하위 141 통과 |
| 수정 후: `test_ui_policy.py`·`test_ui_settings_page.py`만 | 50 통과 |
| 바꾼 파일 `ruff check`·`ruff format --check` | 통과 |

- Qt를 쓰지 않는 시험은 글꼴과 무관하다. A215 전체 실행 결과를 그대로 쓴다.
- 되돌림: 추가한 줄이 없는 상태가 위 "수정 전" 실행이다(4 실패).

## 한계

- `test_ui_icons_style`이 QApplication 생성 전에 import되어야 효과가 있다.
  - 전체 실행은 수집 단계에서 모든 시험 모듈을 먼저 import하므로 해당한다.
  - 한 파일만 돌릴 때는 그 파일이 이 모듈을 import해야 한다. 실패했던 파일들은 모두 import한다.
- `test_ui_responsive_detail` 실패가 이 브랜치에서만 나온 원인은 확인하지 않았다. 실제 글꼴에서는 통과한다.
- GitHub Actions는 결제 문제로 여전히 돌지 않는다.
