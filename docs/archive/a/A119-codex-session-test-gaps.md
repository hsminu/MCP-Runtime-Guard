# A119 · Codex 세션 테스트 빈틈과 멈춤 메우기

2026-10-27. A114 회귀 주입에서 드러난 테스트 빈틈 4개와 멈춤 1개를 메웠다. 테스트만 바꿨고 제품 코드는 그대로다. 모두 `tests/unit/test_codex_session.py`에서 바꿨다.

## 한 일

| 빈틈 | 새 테스트 | 확인하는 것 |
| --- | --- | --- |
| 끝난 답의 크기 한도 | `test_finished_messages_over_the_cap_together_are_refused` | 각각은 한도 안인 메시지 둘이 합쳐서 한도를 넘으면 `CODEX_ANSWER_TOO_LARGE`. 스트리밍 조각 없이 끝난 메시지만 보낸다. |
| 다른 turn의 조각 | `test_streamed_pieces_of_another_turn_are_never_shown` | `send()`가 기다리는 turn만 화면에 흘린다. 다른 turn ID의 조각은 보이지 않는다. |
| 중단된 turn | `test_interrupted_turn_has_its_own_code` | `interrupted`로 끝난 turn은 `CODEX_TURN_FAILED`가 아니라 `CODEX_TURN_INTERRUPTED`다. |
| 빈 ID | `test_empty_thread_id_fails_the_start`, `test_empty_turn_id_fails_the_turn_instead_of_waiting_for_it` | 빈 thread ID는 `CODEX_START_FAILED`, 빈 turn ID는 기다리지 않고 바로 `CODEX_TURN_FAILED`다. |

- 빈 ID 응답은 가짜 app-server를 상속한 `EmptyIdAppServer`로 만든다.
- **멈춤:** turn 종료가 `send()`를 깨우지 않는 회귀를 넣으면, 시간 제한 없이 `send()`를 기다리던 테스트가 끝나지 않았다. 이 파일의 다른 테스트처럼 그런 호출 7곳을 `asyncio.wait_for(..., 2)`로 감쌌다. 이제 같은 회귀가 들어오면 2초 안에 실패로 드러난다.

## 검증

- A114 때 쓴 회귀 18개를 다시 넣었다. 18개 모두 테스트가 실패해서 잡혔다. A114 때는 13개만 실패했고 1개는 멈췄다.
  - 전에 못 잡던 4개는 각각 위의 새 테스트가 실패해서 잡혔다.
  - 멈추던 회귀는 이제 시간 제한에 걸려 실패한다.
- 새 테스트 5개는 3번 반복해 모두 통과했다.
- 전체 테스트 4,661개가 통과했다(skip 1). Ruff, format, mypy(strict)도 통과했다.

관련: I9, A90, A114. PR은 아직 없다(A 브랜치 작업).
