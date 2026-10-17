# A47 · 신뢰하지 않는 텍스트 공통 표시 위젯

## 한 일

- `src/guard_app/ui/untrusted_text.py`를 새로 만들었다. 모델 답변, 서버·도구 설명, Scanner `threat_summary`처럼 Guard가 믿지 않는 텍스트를 표시하는 공통 위젯이다.
  - `untrusted_display_text(value, max_chars=None)`는 승인 상세와 같은 `visible_review_text`(`approval_detail.py`)로 모든 문자를 시각화한다. 제어 문자, bidi, zero-width, 혼동 문자는 코드포인트로 표시된다.
  - 실제 줄바꿈은 줄바꿈으로 남긴다. 여러 줄 답변을 읽을 수 있게 하려는 것이다. CR·탭 등 나머지 제어 문자는 시각화한다.
  - 길이 상한은 시각화 전 원문 글자 수로 센다. 코드포인트 표기 때문에 실제 내용이 잘리지 않고, 표시하지 않은 글자 수를 따로 알린다.
  - `UntrustedTextView`는 읽기 전용 `QPlainTextEdit`과 `PlainText` 라벨만 쓴다. HTML·마크다운·링크 해석이 없고, 링크 상호작용 플래그도 켜지 않는다. "검증되지 않은 내용" 표시는 선택이다. `clear()`는 문서 사본을 비운다.
- 화면 연결은 하지 않았다. 실행 탭 모델 답변(bridge 이벤트), 등록 화면 설명(#20 3번), Scanner 경고(C4.9·SCAN-01)는 각 계약이 정해진 뒤 이 위젯을 쓴다.

## 실제 검증

- `tests/unit/test_ui_untrusted_text.py` 12개 통과.
  - T18 계열: HTML 태그, `<img src>`, `<a href>`, 마크다운 링크와 이미지, `javascript:`가 원문 그대로 표시된다. loopback HTTP 요청 0건, `QDesktopServices` URL 처리기 호출 0건, Python `openUrl` 호출 0건이다.
  - 비가시 문자 시각화와 줄바꿈 유지, 300자 상한과 생략 글자 수, 표시 선택, 비우기, 잘못된 입력·상한 거부.
- `uv sync --locked` 환경에서 `ruff check`·`ruff format --check`·`mypy` 통과, `pytest -q` 2343 통과(경고 5, subtest 20).

## 남은 한계

- 기존 실행 탭 검토 패널(`ui/runtime.py`)은 줄바꿈까지 `\n` 표기로 시각화한다. 이번에 바꾸지 않았다(AGT 제거 PR과 겹치는 파일 회피).
- Qt·Python은 메모리 안전 삭제를 보장하지 않는다. `clear()`는 표시 사본을 버릴 뿐이다.
- 300자 상한의 저장 쪽 의미(C4.17)와 표시 쪽 생략 문구가 같은지는 C 구현 후 맞춘다.

## 관련

- I9(원문 비노출: 로그로 보내지 않음), 12.4절 승인 화면 렌더링 요구, T18, contracts 2026-10-15 Scanner 경고 표시 결정.
- 커밋: 이 기록과 같은 커밋. PR 없음(미푸시).
