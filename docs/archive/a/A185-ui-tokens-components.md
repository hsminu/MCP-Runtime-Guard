# A185 · 공통 색·글자·컴포넌트 정리(파란 주 동작, 상태 알약과 등급 태그 구분)

2026-10-30 · `claude/a-ui-ux-revamp-e419b9`(origin/main `0682a4d` 위). PR: 아직 없음(로컬 커밋, 사용자 허락 뒤 올림).
관련: 기술 설계 12.4(PlainText, 색만으로 뜻을 전하지 않음), T18 표시 기준. 사용자 UI·UX 전면 개선 요청 2·8절의 첫 단계.

## 한 일

- `ui/style.py` 라이트·다크 팔레트를 다시 썼다. 주 동작·선택·포커스는 파랑(`primary` #2563EB), 본문 #111827, 보조 #475569, 성공·주의·거부는 초록·주황·빨강, 입력·카드 테두리는 `field_line`·`hairline_strong`으로 보이게 했다. 다크는 요소마다 같은 역할 이름으로 따로 정했다. 옛 `accent`(주황 브랜드색)는 지웠다.
- 글자: 기본 14px, 페이지 제목 26px, 절 제목 17px, 카드 제목 15px, 보조 13px, 최소 12px. 버튼·입력 높이와 둥근 모서리(6px), 카드 모서리 8px를 맞췄다. 포커스는 파란 테두리다.
- 상태와 보안 등급을 다르게 그린다. 상태 알약(`StatusPill`)은 둥근 모양에 tone마다 다른 기호(체크·마름모·X·열린 호·점)를 붙인다. 등급(`LabelPill`·`SecurityLabel`)은 각진 외곽선 태그에 방패 아이콘을 붙이고 청록·보라·자홍을 쓴다.
- `ui/widgets.py`에 재사용 컴포넌트를 더했다: `Disclosure`(접는 자세히), `ResponsiveRow`(좁으면 세로로 다시 배치), `FitStack`(현재 페이지 높이만 차지하는 스택), `SecretLineEdit`(가림·표시 전환, 복사·잘라내기·끌기 차단, 숨길 때 다시 가림), `SectionCard`(제목·상태·동작·본문 한 칸).
- `ui/icons.py`에 more·copy·eye_off·alert·edit·trash·arrow_down·filter 선 아이콘을 더했고, 비활성 아이콘은 `disabled_text`로 그린다.
- 세션 목록 행(`session_list.py`)은 52px로 늘리고 선택 행에 왼쪽 파란 막대와 굵은 제목, 진행 중 행은 파란 열린 호를 쓴다. 내비 선택(`window.py`)은 파란 글자·아이콘이다.
- `tests/unit/test_ui_icons_style.py`: 색 리터럴은 팔레트에만 둔다는 검사를 유지하고, `accent` 사용 수 검사를 `primary`·의미 색 선택자 허용 목록 검사로 바꿨다. 두 테마의 본문·보조·주 동작 대비(WCAG), 등급 색과 상태 색 분리, 12px 미만 글자 금지, 새 컴포넌트 동작 시험을 더했다.

## 검증

2026-10-30, Windows / Python 3.12 / 메인 checkout `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| `test_ui_icons_style.py`·`test_ui_sessions.py`·`test_server_add.py`·`test_runtime.py`·`test_ui_approval_t18.py`·`test_ui_keyboard.py`·`test_ui_responsive_detail.py`·`test_a178_qt_ownership.py`·`test_ui_overview.py`·`test_ui_demo.py` | 155 passed, 106 subtests passed |
| `ruff check`·`ruff format --check`(바꾼 13개 파일) | 통과 |
| `mypy src` | 통과(140개 파일) |
| `lint-imports --config tools/ci/importlinter.ini` | 7 kept, 0 broken |
| 화면 캡처(가짜 데이터, offscreen 1260×850 라이트·다크) | 세션·설정·정책·로그·서버 추가 화면에서 파란 선택·알약 기호·방패 태그 확인 |

## 한계

- 전체 단위 시험은 사용자 지시로 돌리지 않았다. 관련 파일만 돌렸다.
- 실제 Windows 창(125%·150% 배율 포함)은 이 단계에서 보지 않았다. 배율 확인은 마지막 캡처 단계에서 `QT_SCALE_FACTOR`로 한다.
- 네트워크·인증정보·사용자 앱 데이터 폴더는 쓰지 않았다. B·C 소유 파일과 공통 문서는 고치지 않았다.
