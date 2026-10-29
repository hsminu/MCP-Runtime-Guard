# A198 · #109 A 리뷰 후속: 불일치 서버 시험·문구, Codex 스트림 기한 시험

2026-10-30 · `wip/next`(A184 위). 출처: [#109 A 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6505998748) 1번 A 후속과 5번.
관련: B14 `startable_servers`(REG_SETTINGS_MISMATCH 서버는 시작하지 않음), I6.

## 지적

- **시험 이름:** #109부터 `REG_SETTINGS_MISMATCH` 서버는 시작하지 않는다. 그런데 `test_a_blocked_server_whose_settings_are_ahead_of_its_registration_is_left_out`의 이름과 docstring은 아직 "반복 이름이 서버를 막는다"는 이전 경로를 말했다.
- **단언:** 이 시험과 `test_settings_that_differ_from_the_registration_hide_only_that_server`는 숨김만 확인했고, 서버를 띄우지 않는다는 단언은 없었다.
- **문구:** 서버 상세 문구 "이 서버의 도구는 숨겨짐"에는 연결하지 않는다는 뜻이 없었다.
- **간헐 실패:** `test_jsonrpc_deadline_cancels_stuck_drain_without_retrying_write`가 가끔 실패했다. 요청 기한이 30ms라, 이벤트 루프가 잠깐 밀리면 drain에 닿기 전에 기한이 지나기 때문이다.

## 한 일

- `test_product_assembly.py`:
  - 이름을 `test_a_server_whose_settings_are_ahead_of_its_registration_is_not_started`로 바꿨다.
  - docstring을 지금 동작에 맞췄다.
  - 반복 이름 정의를 뺐다.
  - `"docs" not in assembled.transport._workers`를 더했다.
- `test_product_settings.py`: 같은 서버가 transport worker에 없다는 단언을 더했다.
- `ui/servers.py`: `REG_SETTINGS_MISMATCH` 문구를 "설정 파일과 등록 내용이 다름. 이 서버는 연결하지 않고 도구를 숨김."으로 바꿨다. URL 거부 문구("이 서버는 연결하지 않음")와 같은 표현이다. `test_bridge_registration.py`가 새 문구 앞부분을 고정한다.
- `test_codex_stream.py`: 요청 기한을 0.03초에서 0.3초로 늘렸다. 시험의 다른 대기(1초)보다 짧아서 확인하는 내용은 같다.

## 검증

2026-10-30, Windows / Python 3.12 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 기한 시험을 한 프로세스에서 100회, GIL을 50ms씩 잡는 스레드와 함께 100회 | 모두 통과(바꾸기 전 같은 조건은 1/100·4/100 실패) |
| `test_codex_stream`·`test_product_assembly`·`test_product_settings`·`test_bridge_registration`·`test_a155_tool_settings`·`test_b_startable_settings` | 125 passed, 16 subtests passed |
| `ruff check`·`ruff format --check`(바꾼 5개 파일) | 통과 |
| 되돌림: 조립이 `startable_servers` 대신 모든 서버를 시작 | 두 시험 실패 |
| 되돌림: 옛 문구 | 문구 시험 실패 |

되돌릴 때마다 원래 파일 바이트와 `git diff`가 대조 전과 같은지 확인했다.

## 한계

- 전체 시험은 돌리지 않았다. 제품 코드 변경은 문구 한 줄이다.
- "시작하지 않음"은 transport worker가 없다는 것으로 확인했다. Peer에 들어온 initialize·`tools/list` 수는 세지 않았다.
