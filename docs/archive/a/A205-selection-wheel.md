# A205 · 드롭다운이 마우스 휠로 바뀌지 않게

2026-10-30 · `wip/next`. 출처: 사용자 요청("드롭다운 휠로 안바뀌게").
관련: 개발문서 12.1·12.2절 화면 조작. 이 동작을 다루는 T/S ID는 없다.

## 문제

페이지를 휠로 내리다 커서가 드롭다운 위를 지나면 선택값이 바뀌었다. 도구 설정처럼 행마다 드롭다운이 있는 화면에서 특히 그랬다. Qt `QComboBox`의 기본 동작 두 가지 때문이다.

- focusPolicy가 `WheelFocus`라 휠만으로 포커스를 가져간다.
- 펼치지 않은 상태에서도 휠로 `currentIndex`를 바꾼다.

## 한 일

- **`ui/widgets.py`의 `SelectionBox`:**
  - `StrongFocus`로 바꿨다. 클릭·Tab으로만 포커스를 받는다.
  - `wheelEvent`는 값을 바꾸지 않고 `ignore()`만 한다. 그래서 휠이 부모 스크롤 영역으로 넘어가 페이지가 스크롤된다.
  - 포커스가 있어도 휠로는 바꾸지 않는다. 키보드 화살표, 펼친 목록 안의 휠, 클릭 선택은 그대로다.
  - 생성자는 `parent`를 받는다.
- **`ui/call_logs.py`:** 서버·상태·기간 필터 셋은 맨 `QComboBox`였다. 이를 `SelectionBox`로 바꿨다. 다른 화면 드롭다운은 이미 모두 `SelectionBox`다.
- **A204 후속(`ui/style.py`):** A204의 `QLabel#thinkingLabel` 색을 `$primary_text`에서 `$mute`로 바꿨다.
  - 의미 색은 정해진 선택자에만 쓰게 하는 시험(`test_ui_icons_style.py::test_semantic_colors_are_limited_to_explicit_selectors`)이 있는데, A204 대상 시험에 이 파일이 빠져 있었다.
  - 허용 목록을 넓히지 않고 기본 보조 색을 쓴다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 새 시험 `test_a205_selection_wheel.py`(9개). 포커스 있음·없음 × 위·아래 휠(값·신호·`isAccepted` 그대로), 키보드 Down 대조, 스크롤 영역 전파, 호출 로그 필터 셋의 타입과 휠(조회 요청 수 그대로) | 통과 |
| 대상 7개 파일(새 시험, `test_call_logs`, `test_a155_tool_settings`, `test_ui_icons_style`, `test_a204_conversation_text`, `test_ui_read_only_pages`, `test_server_add`) | 205 passed, 106 subtests passed |
| `ruff check`·`ruff format --check`(379개), `mypy src`(148개) | 통과 |
| 되돌림: `wheelEvent` 재정의 제거 | 8개 실패 |
| 되돌림: `StrongFocus` 제거 | 4개 실패 |
| 되돌림: 기간 필터를 `QComboBox`로 | 3개 실패 |
| 복원 뒤 | 바이트 같음 |

스크롤 전파 시험은 `QTest.wheelEvent(windowHandle, 위치, 방향)`로 창에 실제 휠을 넣는다. `QApplication.sendEvent(box, event)`는 이 Qt 빌드에서 부모로 전파되지 않았다.

## 한계

- 실제 마우스·터치패드의 고해상도 휠(픽셀 단위 delta)로는 보지 않았다. 같은 `wheelEvent`로 들어오므로 같은 처리를 받는다.
- 드롭다운 말고 휠로 값이 바뀌는 다른 위젯(`QSpinBox`·`QSlider`)은 지금 화면에 없다.
