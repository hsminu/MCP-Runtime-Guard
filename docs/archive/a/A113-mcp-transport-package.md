# A113 · 리팩터링 2단계: mcp_transport를 패키지로 나눔

2026-10-27. 동작은 바꾸지 않았다. 커밋은 두 개다. 하나는 그대로 옮기기만 하고, 다른 하나는 긴 함수를 나눈다. B가 둘을 따로 리뷰할 수 있다.

## 형태를 패키지로 정한 이유

개발문서 2.2는 "`mcp` SDK는 `adapters.mcp_transport`만 import"라고 정해 두었고, 지금 코드는 실제로 그 규칙을 지킨다. 형제 모듈(`adapters/mcp_streams.py` 등)로 쪼개면 이 규칙이 깨진다. 그래서 같은 이름의 패키지로 나눴다(사용자 선택).

## 한 일

### 1. 그대로 옮기기 (`94cc5ec`)

`adapters/mcp_transport.py`(1,365줄)를 `adapters/mcp_transport/` 패키지로 옮겼다.

| 모듈 | 맡는 일 |
| --- | --- |
| `__init__` | 공개 API: `McpTransport`, `ServerWorker`, `StreamFactory`, `ToolListLimits`, `ToolListing`, `ResultTooLarge`, `collect_tool_definitions`, 스트림 팩토리 3개, `StdioServerParameters` |
| `streams` | stdio, C가 검증한 파이프, 미리 승인한 HTTP 주소용 스트림 팩토리 |
| `sdk_session` | SDK `ClientSession`. 서버 스키마로 검증하지 않는 tools/call과 응답 전달 재확인을 맡는다. |
| `tool_listing` | tools/list 수집과 그 한도 |
| `result_budget` | tools/call 결과를 복사하기 전에 적용하는 예산 |
| `sdk_logging` | SDK 로그 원문 차단. `logging`을 쓰는 유일한 모듈이다. |
| `worker` | 서버 하나의 세션 소유자와 명령 큐 |
| `transport` | `McpTransport` |

- 정의 37개는 모두 AST가 원래와 같다. 바뀐 것은 import와, 모듈 경계를 넘게 된 이름 10개의 앞 밑줄뿐이다.
- `from guard_app.adapters.mcp_transport import ...` 경로가 그대로다. 그래서 Guard 조립(`product_assembly`)과 다른 호출자는 고치지 않았다. mcp SDK는 여전히 이 패키지 안에서만 import한다(I1).
- `ResultTooLarge`는 감사 메타데이터(`DispatchError.exception_type`)에 쓰이는 이름이라 바꾸지 않았다. pyproject의 파일 단위 N818 예외는 지우고, 클래스 줄의 예외로 옮겼다.
- 테스트와 예제는 바뀐 위치로 monkeypatch·import 대상을 옮겼다. 예: `sdk_session._UnvalidatedClientSession`, `streams.httpx`, `result_budget._MAX_*`, `worker._State`, `sdk_logging.SDK_LOG_SCOPE`.
- A 규칙 테스트(A111):
  - 패키지 하위 모듈을 검사 대상에 넣었다.
  - 새 규칙: mcp SDK를 import하는 소스 모듈은 transport 패키지 안에만 있어야 한다.
  - transport 규칙을 "패키지 밖에서 transport를 쓰는 곳은 Guard 조립뿐"으로 고쳤다.
  - `logging` 허용 위치를 `sdk_logging`으로, 넓은 except 허용 위치를 `worker.ServerWorker._execute`로 옮겼다.
  - 위반 5개를 일부러 넣어 모두 잡는 것을 확인했다: 패키지 밖 SDK import, bridge의 하위 모듈 import, worker의 `logging`, streams의 넓은 except, `__init__`의 `subprocess`.

### 2. 긴 함수 나누기

| 모듈 | 나눈 것 |
| --- | --- |
| `result_budget` | 결과 검사를 `_ResultWalk` 클래스로 옮겼다. 바이트 차감, 스칼라 처리, 컨테이너 펼치기가 각각 메서드다. |
| `streams` | 파이프 송수신은 `_receive_frames`·`_send_frames`로, HTTP는 `_endpoint_url`(주소 검사)·`_request_constraint`(요청 제한)·`_http_client`(클라이언트 설정)로 나눴다. |
| `tool_listing` | 페이지 하나 처리를 `_add_page`로 뺐다. |
| `transport` | 시작 창을 `_start_in_windows`로 뺐다. 한도 검사는 worker와 같은 `require_result_copy_budget`·`require_pending_limit`를 쓴다. |
| `worker` | `_run`을 `_initialize`·`_serve`·`_settle_owner_exit`로, `wait_closed`의 실패 판정을 `_close_failure`로 나눴다. 세 곳에 반복되던 "고장 난 세션 정지"는 `_stop_unhealthy` 하나로 합쳤다. |

- 효과 순서, 오류 이름, 메시지는 그대로다. 이 커밋에서는 테스트를 바꾸지 않았다.
- 차이 하나: `httpx.Timeout`을 연결마다 만들지 않고, 팩토리를 만들 때 한 번 만들어 함께 쓴다. 값이 같고 불변 객체라 동작은 같다.

## 검증

- 전체 테스트 4,646개가 통과했다(skip 1). Ruff, format, mypy(strict)도 통과했다.
- 실제 MCP SDK로 HTTP·stdio 스파이크(`examples.verify_http_spike`, `examples.verify_stdio_spike`)를 끝까지 돌렸다.
- 2번 변경에 회귀 18개를 일부러 넣었다. 9개는 잡았다:
  - 쿠키 유지
  - 두 번째 GET 허용
  - 루프백이 아닌 평문 HTTP 허용
  - 리다이렉트 따라감
  - 중복 도구 이름 허용
  - 시작 창 무제한
  - 고장 세션의 큐 유지
  - ingress 닫기 실패 무시
  - 대기 명령 한도 미검사
- 못 잡은 9개는 나누기 전 코드(`94cc5ec`)에 같은 회귀를 넣어도 못 잡았다. 이번 변경 때문이 아니라 원래 있던 테스트 빈틈이다.

| 못 잡은 회귀 | 이유 |
| --- | --- |
| 결과 블록 수 한도 제거 | 테스트의 복사 예산(256바이트)이 먼저 막는다. |
| 결과 노드 수 한도 제거 | 자식 수 검사가 같은 한도를 먼저 막는다. |
| 모델 extra 필드를 검사 안 함 | extra 필드가 있는 결과를 쓰는 테스트가 없다. |
| 같은 컨테이너 재등장 허용 | 해당 입력을 쓰는 테스트가 없다. |
| 파이프 쓰기 실패 때 gate를 실패시키지 않음 | 쓰기 실패 경로 테스트가 없다. |
| 잘못된 프레임을 gate에 기록하지 않음 | 기록된 사유를 확인하는 테스트가 없다. |
| 세션 종료 누락을 실패로 보지 않음 | 그 상황을 재현하는 테스트가 없다. |
| owner가 끝날 때 대기 명령을 거절하지 않음 | 다른 정지 경로가 먼저 큐를 비운다. |
| 목록 수집이 실패한 세션을 READY로 둠 | 워커 상태를 확인하는 테스트가 없다. |

- 측정 방법은 A111과 같다. [근거](../evidence/a113-mcp-transport-package-2026-10-27.json)

| 항목 | A112 뒤 | A113 |
| --- | --- | --- |
| mcp_transport의 기준 초과 함수 | 10개 | 1개: `_endpoint_url`(25줄·분기 18) |
| `ServerWorker` | 383줄·메서드 16개 | 379줄·메서드 20개 |
| mcp_transport 크기 | 파일 1개·1,365줄 | 파일 8개·1,582줄 |
| A 전체 기준 초과 함수 | 59개 | 50개 |
| A 모듈 | 46개·14,247줄 | 53개·14,466줄 |

## 남은 것

- **공통 문서 경로:** 개발문서와 team-workflow에 파일 경로 `adapters/mcp_transport.py`가 적혀 있다. 사용자 문서라 이 브랜치에서는 고치지 않았다. main PR로 경로 갱신을 제안해야 한다. 소유(A)와 리뷰(B), SDK import 경계는 바뀌지 않는다.
  - 개발문서: 소유 표(111줄), 디렉터리(290줄), 골격 주석(1119줄), Semgrep 넓은 except 예외 경로(1813줄). 1813줄은 `adapters/mcp_transport/worker.py`가 돼야 한다.
  - team-workflow: 74줄
- **B 리뷰:** team-workflow에 따라 mcp_transport는 B가 리뷰한다.
- **`ServerWorker` 크기:** 아직 300줄을 넘는다. 상태 플래그와 큐를 함께 쓰는 상태 기계라 더 쪼개지 않았다.
- **`_endpoint_url`:** 거부할 주소 모양을 한 목록으로 모은 함수라 분기 수만 높다. 나누지 않았다.
- **테스트의 비공개 접근:** `transport._workers`, `worker._state` 등은 상태 기계를 직접 보는 시험이라 그대로 뒀다.
- **테스트 빈틈:** 위 표의 9개.

관련: I1·I9, T08, 개발문서 2.2·13.1. PR은 아직 없다(A 브랜치 작업).
