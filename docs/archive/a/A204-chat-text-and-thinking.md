# A204 · 채팅 한글 표시 수정과 "생각 중" 표시

2026-10-30 · `wip/next`. 출처: 사용자 보고("채팅에 한글 깨지는 버그", "ai답변창에 생각중이나 그런 사고표시").
관련: I7, T18, 개발문서 12.4절(비가시·혼동 문자 표기).

## 문제

- 채팅의 사용자 메시지·모델 답변과 세션 제목이 `untrusted_display_text`를 거쳤다. 이 함수는 승인 화면용 `visible_review_text`를 그대로 쓴다.
- 이 규칙은 목적지·인자 비교를 위한 것이다. 혼동 문자, NFKC가 바꾸는 문자, 비 ASCII 공백, 조합형 자모에 코드포인트를 붙인다. 역슬래시는 두 배로 만든다.
- 그래서 한국어 채팅에 흔한 문자가 깨진 것처럼 보였다.

| 입력 | 바꾸기 전 화면 |
| --- | --- |
| `ㅋㅋ` | `ㅋ\u314Bㅋ\u314B` |
| `…` | `…\u2026` |
| `①` | `①\u2460` |
| `⚠️` | `⚠\uFE0F` |
| `3–5` | `3–\u20135` |
| `C:\Users` | `C:\\Users` |

- 채팅 본문은 승인 대상이 아니다. 등급·경계·승인은 모델 출력에서 도출하지 않는다(I7).
- 메시지를 보낸 뒤 첫 답 조각이 올 때까지 화면에 아무 표시가 없었다.

## 한 일

- **대화용 표시 함수(`ui/untrusted_text.py`):** `conversation_display_text`를 추가했다.
  - 원래 글자 없이 `⟦U+XXXX⟧`(12.4절 표기)로 바꾸는 것:
    - 숨은 문자(`is_hidden_char`: Cc·Cf·Cs·Co·Cn·Zl·Zp, Hangul filler 등)
    - 오른쪽→왼쪽 문자(bidi R·AL·AN)
    - 리터럴 `⟦`
  - `\n`·`\t`은 그대로 둔다. `\r\n`은 줄바꿈으로 본다.
  - 나머지는 그대로 보인다. 한글 호환·조합형 자모, 기호, 전각 문자, 결합 문자, 비 ASCII 공백, 역슬래시가 여기에 든다.
  - 표기가 `\u`가 아니라 `⟦`로 시작하므로 역슬래시를 두 배로 만들 필요가 없다. 리터럴 `⟦`도 표기하므로 화면의 모든 `⟦`는 표기의 시작이다.
- **적용 범위(`ui/sessions.py`):**
  - 대화 블록 `_write_block`: 사용자 메시지, 최종·작성 중·중단 답 모두에 적용된다.
  - 세션 제목의 heading·툴팁·목록 툴팁.
  - 승인 화면·서버 설명·`threat_summary`·정책 화면은 기존 `untrusted_display_text`·`visible_review_text` 규칙 그대로다.
- **생각 중 표시(`ui/session_transcript.py`, `ui/sessions.py`, `ui/style.py`):**
  - 대화 맨 아래에 임시 줄 `생각 중 · n초`를 둔다. 1초마다 갱신한다.
  - 이 줄은 turn이 아니어서 `toPlainText`·복사·빈 대화 판정에 나오지 않는다.
  - 켜는 조건은 다섯 가지를 모두 만족할 때다.
    - Task가 RUNNING이다.
    - 이 세션에서 보낸 메시지 실행 명령(`run_command`)이 대기 중이다.
    - 답 조각이 없다.
    - 그 명령의 최종 답을 아직 받지 않았다.
  - 다음 경우에는 끈다.
    - 첫 조각, 최종 답, 명령 정리(`settle_command`)
    - 승인 대기, 종료 상태
    - 중단 답, 대화 비우기
  - 새 Task 생성 대기에는 켜지 않는다.
  - Codex 추론 이벤트는 쓰지 않았다. 화면 상태만으로 판단하므로 services·bridge는 바꾸지 않았다.
- **검수에서 고친 것(Codex 첫 결과 대비):**
  - `conversation_partial`에 Codex가 넣은 "대기 중인 명령과 다른 조각 버림" 필터를 뺐다. 요청하지 않은 A90 동작 변경이다. 또 실행 화면의 `request_run`처럼 다른 명령 ID로 여는 스트림도 있다. 시험 대조군은 다른 epoch 조각(A90 규칙으로 무시)으로 바꿨다.
  - 생각 중 표시를 끄는 위치를 `_remove_from(0)`에서 `clear()`·`setPlainText()`로 옮겼다. 답 블록을 0번부터 교체할 때 꺼지는 숨은 부작용을 없앴다.

## 검증

2026-10-30, Windows / Python 3.12.15(unicodedata 15.0.0) / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 새 시험 `test_a204_conversation_text.py`. 문자 단위, 대화 위젯·복사·`toPlainText`, 제목, 생각 중 표시 시계·켜기/끄기 경로 6가지, 새 Task 생성, 대화 비우기, 아래 붙음 유지 | 통과 |
| 대상 5개 파일(새 시험, `test_ui_chat`, `test_ui_sessions`, `test_ui_session_manage`, `test_ui_untrusted_text`) | 152 passed |
| `ruff check`·`ruff format --check`(378개), `mypy src`(148개), import-linter(`tools/ci/importlinter.ini`, 계약 7개 유지) | 통과 |
| 승인 화면 규칙 고정: `visible_review_text("ㅋㅋ")` | 여전히 `ㅋ\u314Bㅋ\u314B` |
| 되돌림 11가지(아래 표) | 모두 새 시험 실패. 복원 뒤 바이트 같음 |

되돌림 11가지는 다음과 같다.

| 되돌린 것 | 결과 |
| --- | --- |
| 대화 함수를 승인 규칙으로 | 20개 실패 |
| 숨은 문자 조건 | 14개 실패 |
| `⟦` 표기 | 1개 실패 |
| `\r\n` 처리 | 1개 실패 |
| 조각 있음 조건 | 1개 실패 |
| 최종 답 조건 | 1개 실패 |
| RUNNING 조건 | 1개 실패 |
| `run_command` 조건 | 2개 실패 |
| 재호출 시 시간 유지 | 1개 실패 |
| `_write_block` 적용 | 1개 실패 |
| 제목 적용 | 2개 실패 |

기존 시험 기대값 변경은 두 파일이다. 둘 다 채팅·제목 경로라 새 함수가 맞다.

- `test_ui_chat.py` 2곳: 스트림 본문·코드 복사 기대값을 `conversation_display_text`로 바꿨다.
- `test_ui_session_manage.py` 1곳: 자동 제목 목록 툴팁 기대값을 `conversation_display_text`로 바꿨다.

## 한계

- 실제 IME 입력·실제 codex.exe 응답·실제 화면 배율에서는 보지 않았다.
- 사용자가 본 깨짐이 이 원인이 아닐 수도 있다. 글꼴 상자, 마지막 음절 누락 같은 경우다. 그러면 화면 캡처로 다시 확인한다.
- 생각 중 표시는 승인 뒤 다시 RUNNING이 되면 0초부터 다시 센다.
- 도구 호출 중이거나, 앞 조각 뒤 다음 조각을 기다리는 동안에는 표시하지 않는다.
- 비 ASCII 공백(NBSP, U+3000 등)은 대화에서 그대로 보인다. 승인 화면에서만 표기한다.
- Python `unicodedata` 15.0.0에 없는 새 문자는 Cn이라 표기된다.
