# A131 · 승인 대기 강조(대화 안내 띠, 활동 패널 승인 구역, 줄 단위 승인 사본)

2026-10-28. 사용자 피드백: 이 앱의 중심은 MCP 도구 호출의 승인인데 화면에서 잘 드러나지 않는다. 강하지 않게, 은은하지만 잘 보이게 강조해 달라는 요청이었다. 계약(`domain/*`·`ports.py`)과 B·C 소유 파일은 바꾸지 않았다.

## 바꾸기 전

- 대화 화면에서 승인 대기는 머리의 작은 "승인 대기" 알약과 입력창 위 12px 회색 안내 한 줄뿐이었다.
- 활동 패널의 승인 구역은 Task 상태·실행 컨트롤 아래에 있었고, 다른 구역과 모습이 같았다.
- 승인 사본은 메타데이터를 " · "로 이은 한 줄이라 376px 패널에서 잘렸다. 또 `visible_review_text`가 문자열 전체를 바꾸면서 "전체 인자" 앞의 줄바꿈이 `\n` 표시로 보였다(C 리뷰 A129 제안).

## 근거(가벼운 조사)

- Primer는 주의 상태에 옅은 배경과 옅은 테두리를 쓰고, 진한 강조는 따로 아껴 둔다([Primer 토큰 명세](https://unpkg.com/@primer/primitives@11.10.0/DESIGN_TOKENS_SPEC.md)).
- 보안 경고는 자주 보이면 습관화돼 무시된다. 실제로 필요할 때만 띄우고 내용이 바뀌는 경고가 주의를 오래 끈다([Vance 외, MISQ 2018](https://misq.org/tuning-out-security-warnings-a-longitudinal-examination-of-habituation-through-fmri-eye-tracking-and-field-experiments.html), [Anderson 외, JMIS 2016](https://jmis-web.org/articles/1304)).
- 색·채도·위치는 전주의 속성이라 작은 면적으로도 눈에 띈다([Storytelling with Data](https://www.storytellingwithdata.com/blog/2011/10/google-example-preattentive-attributes), [Georgia Tech 지각 강의 노트](https://www.cc.gatech.edu/~stasko/7450/16/Notes/perception.pdf)).
- 그래서 강조는 기존 warn 토큰(옅은 배경, 옅은 테두리, 3px 왼쪽 막대)만 쓰고, Guard가 `WAITING_APPROVAL`이라고 할 때만 보인다. 움직임은 넣지 않았다. ember accent는 그대로 브랜드와 실행 중에만 쓴다.

## 한 일

- **대화 화면 승인 안내 띠(`ui/sessions.py`, `ui/style.py`).**
  - 입력창 바로 위에 `approvalBand`를 둔다. 승인 아이콘, "승인이 필요한 도구 호출이 있음"(음슴체), "수신처와 전체 인자를 확인한 뒤 승인하거나 거절하세요."(하세요체), "승인 사본 확인" 버튼으로 이뤄진다.
  - `TaskStatus.WAITING_APPROVAL`일 때만 보인다. 그동안 회색 안내 줄은 숨긴다. 단 Codex 연결 실패 안내는 띠 위에 그대로 보인다.
  - 좁은 창(500px 미만)에서는 머리·입력 버튼처럼 세로로 쌓인다.
- **띠 버튼 흐름(`ui/window.py`).**
  - 새 신호 `SessionWorkspace.approval_review_requested`를 받아 활동 패널을 연다(좁은 창은 결과 화면).
  - 활동 패널의 "승인 사본 확인"이 켜져 있으면 포커스를 주고 누른다. 즉 같은 REVIEW 명령을 같은 승인 ID로 보낸다. 승인·거절은 여전히 활동 패널에서 사람이 누른다.
  - 레이아웃이 끝난 뒤 결과 스크롤을 승인 구역 위쪽으로 옮긴다.
- **활동 패널 승인 구역(`ui/runtime.py`).**
  - 승인 대기 중에만 `approvalCard[attention="true"]`로 옅은 warn 배경과 왼쪽 막대를 칠하고, 좌우 12px 안쪽 여백을 준다.
  - 사본 상자는 승인 대기가 시작될 때부터 12줄 높이로 연다. 사본이 도착해도 화면이 밀리지 않고, 스크롤 위치가 맞는다.
- **줄 단위 승인 사본(`services/bridge.py`, `ui/runtime.py`).**
  - `_review_fields(snapshot)`가 (고정 이름, 값) 쌍을 만든다. `_review_header`는 같은 쌍을 " · "로 이은 한 줄이라 기존 출력이 그대로다.
  - `ApprovalReview`에 `fields`(기본값 `()`)를 더했다. 위치 인자로 만드는 기존 코드는 그대로 동작한다.
  - 패널은 한 필드를 한 줄씩 따로 `visible_review_text`로 바꾸고, 빈 줄 뒤에 "전체 인자:"와 인자를 둔다. 값 안의 줄바꿈은 여전히 `\n` 표시를 남긴다. 그래서 값이 만든 줄은 진짜 필드나 진짜 "전체 인자:" 줄과 구분된다. `fields`가 비면 예전처럼 한 줄 머리를 쓴다.
- **사이드바 세션 행(`ui/sessions.py`).** 승인 대기 행은 tone `approval`로 그린다. 마름모 표시는 같고, 상태 글자("승인 필요 · 대기 중")만 warn 색이다. 연결 필요·정리 미확인 행은 그대로 회색 글자다.

## 검증

- `tests/unit/test_ui_sessions.py`
  - 띠는 `WAITING_APPROVAL`에서만 보이고 회색 안내는 그동안 숨는다. RUNNING·HALTED로 바뀌면 띠·구역 강조·행 tone이 모두 풀린다.
  - 띠 버튼은 닫혀 있던 활동 패널을 열고 `("review", "approval-one", 1)` 하나만 보낸다.
  - 줄바꿈과 "전체 인자:"를 넣은 위조 도구 이름은 표시 줄을 늘리지만, 위조 줄은 `\n` 표시를 달고 있어 진짜 "전체 인자:" 줄은 하나뿐이다.
- `tests/unit/test_ui_approval_t18.py`(T18): HTML·링크 페이로드가 필드마다 한 줄씩 일반 텍스트로 보이고, 필드 4곳과 인자 1곳에 그대로 남는다.
- `tests/unit/test_bridge.py`: `_review_fields`가 기존 한 줄 머리와 같은 필드·값을 낸다.
- `tests/unit/test_ui_icons_style.py`의 스타일 감사(색은 팔레트만, accent 예약, 10px 상자 카드 3종, 4px 격자)는 그대로 통과한다. 띠는 8px 모서리라 상자 카드 목록에 들지 않는다.
- 오프스크린 캡처(라이트·다크, 1280×800, 데모 "ask" 시나리오)로 띠, 패널 강조, 줄 단위 사본을 눈으로 확인했다.
- 전체(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`):
  - 단위 4,852개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 41개 통과
  - ruff check·ruff format --check(231개 파일)·mypy(100개 소스 파일) 통과

## 남은 것

- 띠 문구는 고정이다. 도구 이름 같은 요청별 정보는 사본(활동 패널)에서만 보인다. `TaskProjection`에 신뢰할 수 있는 도구 이름이 없어서다.
- 사이드바 "승인 대기" 메뉴에 개수를 붙이지 않았다. 세션 수와 승인 목록 조회 수가 다를 수 있어서다.

관련: A129(C 리뷰 제안), A130, I7(띠는 Guard가 확정한 상태로만 켜짐), R09, T18.
