# A90 · 답변 스트리밍을 대화 화면에 연결

2026-10-19. 개발문서 UI 이벤트 표의 `ModelMessage(모델 답변 스트리밍)` 중 A 부분이다. 세션 관리자(A88)가 받던 `item/agentMessage/delta`를 대화 화면까지 이었다. B·C 계약과 domain·ports는 바꾸지 않았다.

## 한 일

- **세션(`services/codex_session.py`).**
  - `set_partial_handler`를 추가했다. 기존 `on_partial` 생성자 인자와 같은 자리를 쓴다.
  - 화면용 조각만 따로 모은다. `itemId`가 바뀌면 앞에 빈 줄을 넣어 agentMessage 사이를 나눈다.
  - 최종 답(`_turn_answer`)과 상한 계산은 원래 delta 그대로다.
- **런타임(`services/session_runtime.py`).**
  - 세션을 채택할 때 Task ID와 epoch를 런타임이 붙여 조각을 넘긴다. 모델·세션이 Task를 지정하지 않는다(I9).
  - `answer_partials` 바인딩으로 bridge 수신자를 등록한다. 종료 뒤 조각은 버린다.
- **bridge(`services/bridge.py`).**
  - `conversation_partial` 신호를 추가했다. 지금까지의 답 전체를 `ConversationEvent`로 보내고, 화면은 이전 것을 교체한다.
  - 실행 명령이 진행 중인 Task·epoch의 조각만 받는다. 다른 Task·이전 epoch·실행이 끝난 뒤의 조각은 버린다.
  - 0.1초(`PARTIAL_FLUSH_S`)마다 한 번만 보낸다. 실행이 끝날 때 남은 조각을 먼저 보내고 최종 답은 그 뒤에 보낸다. 70,000자(`MAX_PARTIAL_CHARS`)를 넘으면 갱신을 멈춘다.
- **대화 화면(`ui/sessions.py`, `ui/window.py`).**
  - 진행 중 답은 "Guard · 모델 답변 작성 중 (검증되지 않은 내용)" 블록 하나를 같은 자리에서 교체한다.
  - 최종 답이 오면 그 블록을 기존 답변 블록으로 바꾼다. 이미 답이 표시된 명령의 늦은 조각은 무시한다.
  - 최종 답 없이 명령이 끝나면 받은 내용을 "Guard · 모델 답변 중단됨 (완료되지 않은 내용)"으로 남긴다. 다음 명령의 조각이나 사용자 입력이 와도 같은 처리를 한다.
  - 표시 방식은 기존 답변과 같다. `untrusted_display_text`로 바꾸고 본문 줄마다 `│ `를 붙여 화자 표시 위조를 막는다.

## 검증

- 새 테스트 6개.
  - `test_codex_session.py`: agentMessage 사이 빈 줄, 나중에 등록한 수신자.
  - `test_session_runtime.py`: 조각에 붙는 Task·epoch, 종료 뒤 무시.
  - `test_bridge_streaming.py` 2개: 묶음 전송·명령 결속·최종 답 순서, 다른 Task·epoch·빈 조각·실행 뒤 조각 차단, `repr`에 본문 없음, 최종 답 없는 실행의 남은 조각.
  - `test_ui_sessions.py` 2개: 블록 교체·최종 답 교체·화자 위조와 bidi 표시·다른 epoch 무시·늦은 조각 무시, 중단 표시와 다음 블록 분리.
- 전체 테스트(`QT_QPA_PLATFORM=offscreen`, `tests` 전체): **3,498 통과, 1 skip, subtest 20**. ruff check·format(179파일)·mypy(81소스) 통과.

## 남은 것

- 가짜 app-server와 offscreen 위젯으로만 확인했다. 실제 codex.exe의 delta 빈도와 화면 체감은 C launcher가 들어온 뒤 확인한다.
- 승인 대기로 실행이 멈춘 동안의 답(`resume_approval`)은 NOTIFY-01 연결(A68) 때 같은 경로를 쓴다.

관련: I7·I9, T18.
