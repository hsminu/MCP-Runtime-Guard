# A219 · 현황·감사 기록 화면 제거

2026-10-30 · `wip/remove-pages`(#113 head `6af7a4d` 위). PR: 미정. 출처: 사용자 전달("pr 115 일하는데 거기서 현황이랑 감사기록은 없애기로 회의했어"), 지시("여기서 하는데 그거에 더해서 pr 115 리뷰 및 요청한것 해줘").
관련: A212(화면 이동 새로고침), #115(C 검사 화면).

## 왜

- 회의에서 사이드바 관리 메뉴의 **현황**과 **감사 기록**을 없애기로 했다.
- **현황**(`OverviewPage`)
  - 제품 bridge는 `OverviewSnapshot`을 보내지 않는다. `set_overview_snapshot`을 부르는 곳은 `examples/ui_demo.py`뿐이었다.
  - 그래서 실제 앱에서는 늘 빈 화면이었다.
- **감사 기록:** `_placeholder_page`가 만든 "연결한 뒤 표시할 화면임" 안내뿐이었다.
- 감사 행을 저장하는 B의 Store는 그대로다. 화면만 없앴다.

## 한 일

- **메뉴·스택**
  - `ui/presentation.py` `PAGES`에서 `overview`·`audit`를 뺐다.
  - `ui/window.py`에서 사이드바 관리 목록, 메뉴, 페이지 스택에서도 뺐다.
  - `_placeholder_page`와 그 순회도 지웠다. 자리표시 화면은 감사 기록 하나만 남아 있었다.
- **첫 화면:** 그대로 대화(`runs`)다.
  - `_connect_shell`과 `application.py`가 이미 `navigate("runs")`로 시작했다.
  - 초기 `_page` 값과 breadcrumb 초기값만 `overview`에서 `runs`로 맞췄다.
  - `navigate`는 같은 화면이어도 일찍 돌아가지 않으므로 시작 동작은 같다.
- **이동 경로:** 다른 화면에서 두 화면으로 가는 코드는 없었다. 현황 카드가 승인·로그·서버 화면으로 보내던 이동은 화면과 함께 없어졌다.
  - `navigate("overview")`·`navigate("audit")`는 기존 조기 반환으로 아무 일도 하지 않는다.
- **지운 파일과 이름:** `src`·`examples`·`tests`를 검색해 남은 사용처가 0인 것을 확인했다. `ServerSummary`는 서버 화면이 쓰므로 남겼다.
  - 파일: `ui/overview.py`, `ui/overview_rows.py`, `ui/overview_presentation.py`
  - 모델: `OverviewSnapshot`·`OverviewTotals`·`AttentionItem`·`FlowBucket`·`RecentCall`과 `ui/__init__` export
  - 그 밖: 아이콘 `overview`·`audit`, `GuardWindow.set_overview_snapshot`
- **`examples/ui_demo.py`:** 현황 스냅샷 만들기와 호출을 지웠다. 나머지 화면은 그대로 채운다.

## 시험

- **`test_ui_overview.py`(14개)를 지웠다.**
  - 현황 전용 8개는 버렸다.
  - 공통 5개는 옮겼다: 실행 모드 표시, 서버 목록 필터, 서버 상세 평문, KST 표시는 `test_ui_read_only_pages.py`로, 이동 신호는 `test_ui_keyboard.py`로 갔다.
  - "사이드바 Enter 이동"은 옮기지 않았다. 현황 카드 링크를 쓰던 `test_default_window_frame_fits_available_screen`이 이제 사이드바 버튼으로 같은 것을 확인한다.
- **다른 시험:** 현황 부분만 뺐다. 원래 지키던 것(Qt 소유, 표시 이스케이프, 좁은 폭, 이동 새로고침, 데모, T18 표시 필드)은 그대로다.
  - 대상: `test_a178_qt_ownership`, `test_ui_render_projection`, `test_ui_display_escaping`, `test_ui_responsive_detail`, `test_a212_page_refresh`, `test_ui_demo`, `test_ui_approval_t18`
- **새 시험 `test_a219_removed_pages.py`(10개)**
  - `PAGES`·사이드바·메뉴·스택에 두 화면이 없다(넓은 폭·320px).
  - 첫 화면이 대화다.
  - 없앤 화면으로 이동하면 현재 화면·이동 대상·선택 표시가 그대로이고 이동 신호가 나가지 않는다.

## Codex 결과에서 바꾼 것

- 새 시험 파일 설명이 "I1/T08"을 들고 있었는데 이 변경과 무관해서 한국어 설명으로 바꿨다.
- 위의 중복 시험 하나를 지웠다.

## 검증

Windows, offscreen, CI처럼 `QT_QPA_FONTDIR` 없음.

| 확인 | 결과 |
| --- | --- |
| 바꾼 시험과 `GuardWindow`를 쓰는 시험 25개 파일 | 428 통과, subtest 115 |
| 바꾼 Python 17개 `ruff check`·`ruff format --check` | 통과 |
| `mypy` | 144개 파일 통과 |
| import-linter | 7 kept, 0 broken |

되돌림 검사(`test_a219_removed_pages.py`)는 3건 모두 잡혔다.

| 되돌림 | 결과 |
| --- | --- |
| `PAGES`에 `audit`를 다시 넣음 | 3 실패 |
| 시작할 때 `navigate("runs")`를 빼서 첫 화면이 스택 0번(서버)이 됨 | 1 실패 |
| 없는 화면으로 이동하면 이동 대상을 지움 | 4 실패 |

- 전체 시험과 실제 앱 화면은 돌리지 않았다.
