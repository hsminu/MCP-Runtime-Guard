# A126 · 정책 화면 표가 폭을 다 쓰고 Codex 제외 도구가 잘리지 않음

2026-10-28. 사용자 요청: 정책 · 경계 화면의 표가 폭을 다 쓰고, Codex 제외 도구의 이름·이유가 잘리지 않게 한다. 범위는 `ui/policy.py`의 표 두 개(서버 접근 규칙, Codex에서 뺀 도구)와 그 테스트다. 정책 판정·저장 흐름은 바꾸지 않았다.

## 한 일

- 두 표를 같은 함수 `_page_table`로 만든다.
  - 앞 열은 내용 폭(최소 120px), 마지막 열은 남은 폭을 다 쓴다.
  - 줄바꿈을 켜고 행 높이를 내용에 맞춘다. 긴 이름·이유는 말줄임 없이 다음 줄로 넘어간다.
  - 표 자체 세로 스크롤을 끄고 행 수만큼 늘어난다. 페이지 스크롤 하나로 모든 행을 본다.

## 검증

- 새 테스트(`tests/unit/test_ui_policy.py`):
  - `test_policy_tables_fill_the_page_width_and_show_every_row`: 두 표가 페이지 폭을 채우고 모든 행이 표 안에 보임
  - `test_excluded_tool_name_and_reason_are_never_cut_off`: 긴 도구 이름·이유의 열 폭·행 높이가 내용 크기 이상임
  - 글꼴 측정은 환경마다 달라 `sizeHintForColumn`·`sizeHintForRow`로 비교한다.
- 시연 데이터로 화면을 오프스크린 캡처해 확인했다.
- 전체 테스트·정적 검사 결과는 A128과 같은 실행이다.

## 남은 것

- 실제 Windows 고DPI·화면 낭독기 확인은 하지 않았다(A36·A37 전체 GUI 인수, C).

관련: A91, A95, A121. PR은 아직 없다(A 브랜치 작업).
