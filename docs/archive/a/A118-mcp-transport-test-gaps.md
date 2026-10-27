# A118 · mcp_transport 테스트 빈틈 메우기

2026-10-27. A113에서 찾은 테스트 빈틈 9개를 다뤘다. 이 빈틈은 일부러 넣은 회귀를 기존 테스트가 못 잡던 것이다. 테스트만 추가했고 제품 코드는 바꾸지 않았다.

## 한 일

새 테스트 7개로 7개를 메웠다.

| 빈틈 | 새 테스트 | 확인하는 것 |
| --- | --- | --- |
| 결과 블록 수 한도 | `test_result_content_block_count_is_a_bound_of_its_own` | 바이트 예산이 넉넉해도 블록이 한도보다 하나 많으면 거부한다. |
| 모델 extra 필드 | `test_result_extra_fields_count_against_the_copy_budget` | SDK가 모델 밖에 둔 필드도 복사 예산에 든다. |
| 같은 컨테이너 재등장 | `test_result_container_seen_twice_is_refused` | 공유되거나 순환하는 컨테이너는 거부한다. 같은 내용의 별도 사본은 통과한다. |
| 파이프 쓰기 실패 | `test_pipe_write_failure_fails_the_connection_gate_without_its_text` | 쓰기 오류가 연결 gate를 고정 사유로 실패시킨다. 오류 원문은 사유에 들어가지 않는다. |
| 잘못된 파이프 프레임 | `test_malformed_pipe_frame_fails_the_connection_gate_and_reaches_no_reader` | gate가 `TOOLS_LIST_MALFORMED`로 실패하고, SDK에는 아무것도 전달되지 않는다. |
| owner 종료 때 대기 명령 | `test_owner_cancelled_mid_call_settles_the_commands_still_queued` | 호출 중 SDK가 owner를 취소하면 실행 중 호출은 `WorkerInterrupted`, 대기 호출은 `WorkerClosed`로 끝난다. 대기 호출은 서버에 가지 않는다. |
| 목록 수집 실패 세션의 READY | `test_real_sdk_session_without_a_gate_is_never_ready_for_calls` | gate 없는 실제 SDK 세션은 목록을 `TOOLS_LIST_INGRESS_REQUIRED`로 남기고 시작에 실패한다. tools/list는 보내지 않는다. |

- 앞의 셋은 `test_mcp_transport.py`의 결과 예산 테스트 옆에 넣었다. owner 종료 테스트도 같은 파일이다. 나머지 셋은 `test_mcp_ingress.py`에 넣었다.
- 목록 수집 실패 경우는 gate가 있는 세션에서는 ingress 감시가 먼저 세션을 멈춰서 회귀가 드러나지 않는다. 그래서 gate가 아예 없는 실제 SDK 세션으로 시험했다(legacy SDK-spawned stdio와 같은 경우).
- owner 종료 경우: SDK의 스트림 태스크가 실패하면 SDK가 호출 중인 owner를 취소한다. 이때는 다른 정지 경로가 큐를 비우지 않으므로, owner 종료 처리가 대기 명령을 끝내야 한다. 이 처리가 없으면 대기 중인 호출자는 끝없이 기다린다.

## 테스트로 잡을 수 없는 2개

나머지 2개는 회귀를 넣어도 관찰할 수 있는 동작이 바뀌지 않는다. 그래서 테스트를 쓰지 않았다. 겹쳐 둔 방어라 코드도 그대로 뒀다.

- **방문한 노드 수 한도(`self._nodes > _MAX_RESULT_VALUE_NODES`):** 컨테이너를 펼칠 때 자식 수 검사가 "방문 + 대기 + 새 자식 ≤ 한도"를 먼저 보장한다. 그래서 방문 수가 한도를 넘는 일이 없다. 한도가 0 이하면 다음 검사가 같은 `ResultTooLarge`로 막는다. 무작위 결과 3,000개로 원래 코드와 회귀 코드를 비교했고 결과 차이는 없었다.
- **세션 종료 누락(`teardown_missing`):** 이 값을 판정에 쓸 때(`_settle_owner_exit` 이후)는 `async with tracker` 블록이 이미 끝난 뒤다. 들어간 세션은 언어 규칙상 반드시 `__aexit__`를 거치므로 `exit_attempted`가 참이 된다. finally가 중간에 끊기면 세 값이 모두 거짓이다. 어느 쪽이든 "들어갔는데 나가기를 시도하지 않음"은 나오지 않는다.

## 검증

- A113 때 쓴 회귀 18개를 다시 넣었다. 16개를 잡았다(A113 때는 9개). 새로 잡힌 7개는 각각 해당 새 테스트가 실패했다. 못 잡은 2개는 위의 2개다.
- 새 테스트 7개는 3번 반복해 모두 통과했다.
- 전체 테스트 4,653개가 통과했다(skip 1). Ruff, format, mypy(strict)도 통과했다.

관련: I9, A113. PR은 아직 없다(A 브랜치 작업).
