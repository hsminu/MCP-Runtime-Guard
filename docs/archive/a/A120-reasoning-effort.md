# A120 · 대화 입력창에서 고른 추론 수준을 Codex에 전달

2026-10-28. 대화 입력창의 모델 칩에서 추론 수준을 고르면 그 턴부터 Codex `turn/start`의 `effort`로 보낸다. 모델은 C의 lock이 고정한 `gpt-6.1-sol` 하나 그대로다. B·C 계약과 domain·ports는 바꾸지 않았다.

## 한 일

- **프로토콜(`adapters/codex_protocol.py`).**
  - `REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")`를 두었다. `config/codex/model_catalog.json`이 이 모델에 내놓는 수준에서 `ultra`만 뺐다. `ultra`는 하위 에이전트에 넘기는데, L1이 하위 에이전트를 끈다.
  - `turn_start_params(thread_id, text, effort)`는 목록 밖의 값을 `CODEX_INPUT_INVALID`로 거부한다. `None`이면 `effort`를 싣지 않아 세션의 현재 수준을 유지한다.
- **세션·Runner(`services/codex_session.py`, `services/runner.py`).** `send(text, effort)`와 `add_user_message(content, effort)`가 다음 한 번의 `advance()`에만 값을 넘긴다.
- **bridge(`services/bridge.py`).**
  - `request_message(..., effort=)`가 같은 목록으로 다시 검사한다. 모르는 값은 큐에 넣지 않고 `ValueError`로 거부한다.
  - `CODEX_MODELS`(선택 목록), `CODEX_DEFAULT_EFFORT = "low"`(모델 카탈로그의 기본값)를 두었다.
- **화면(`ui/sessions.py`).** 입력창 모델 칩은 "GPT-6.1-Sol · 추론 낮음"처럼 한국어로 보이고, 툴팁에 `Codex gpt-6.1-sol · effort low`를 둔다. 시작된 Task의 모델은 바꿀 수 없다. 합성 모델은 모드만 보이고 추론 수준을 보내지 않는다.
- `examples/legacy_runner.py`의 합성 Runner는 같은 시그니처를 받고 값은 무시한다.

## 검증

- 새 테스트와 바뀐 테스트:
  - `test_codex_protocol.py`: 고르지 않으면 `effort`가 없고, `ultra`·`minimal`·빈 값·대문자는 거부한다. 허용 목록이 고정 모델 카탈로그의 수준 안에 있고 기본값을 포함하는지 카탈로그 파일로 대조한다.
  - `test_codex_session.py`: 가짜 app-server가 받은 두 `turn/start` 중 고른 턴에만 `effort`가 있다.
  - `test_codex_task_runner.py`: 고른 값은 그 메시지에만 실리고 다음 메시지에는 `None`이다.
  - `test_bridge_streaming.py`의 `test_picked_reasoning_level_reaches_the_runner_and_unknown_levels_never_queue`: 고른 값이 Runner에 닿고, `ultra`는 큐에 들어가지 않는다.
  - `test_ui_sessions.py`의 `test_model_chip_picks_the_reasoning_level_and_keeps_a_started_task_model`, `test_synthetic_model_shows_its_mode_and_sends_no_reasoning_level`.
- 전체 테스트(`QT_QPA_PLATFORM=offscreen`, `tests` 전체): 4,874 통과, 4 skip, subtest 20. 실패 1개는 C 소유 `test_paths.py::test_app_root_is_under_local_app_data`이고 이 변경 전부터 실패했다. ruff check·format, mypy(strict)는 통과했다.

## 남은 것

- 가짜 app-server로만 확인했다. 실제 codex.exe가 `effort`를 그 턴부터 반영하는지는 A19·A20과 같은 이유(L2·CFG-01)로 아직 확인하지 못했다.
- 모델을 더 넣으려면 C의 lock(`allowed_models`)과 L2가 먼저 바뀌어야 한다.

관련: A88, A107. PR은 아직 없다(A 브랜치 작업).
