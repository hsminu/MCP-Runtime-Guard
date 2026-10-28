# A133 · 연결이 끊긴 서버를 이번 실행 동안 PENDING으로 봄(#61 A 후속 3)

2026-10-28. [#61](https://github.com/hsminu/MCP-Runtime-Guard/pull/61) A 리뷰에서 찾은 것이다. MCP 서버 세션이 실행 중에 끊기면 worker는 그 서버의 다음 호출을 `WorkerClosed`로 거부한다. 하지만 Guard는 그 거부를 예약 뒤에 받는다. 그래서 다른 Task가 그 서버를 부를 때마다 결과 불명(`POST_DISPATCH_ERROR`)으로 정지했다. 실행 중 재연결은 없으므로, 끊긴 서버의 다음 호출은 예약 전에 거부해야 한다.

## 한 일

- **worker가 서버 고장을 한 번 알린다(`ServerWorker(on_failed=...)`).**
  - READY였던 세션이 요청한 닫기가 아닌 이유로 멈추면 부른다. 세션이 깨진 호출, 감시 상한(watchdog)을 넘긴 호출, 수신 gate 실패, 세션 context가 스스로 끝난 경우(HTTP 요청 오류 등)다.
  - 넘기는 것은 없다. 오류 원문도 예외 타입도 넘기지 않는다.
  - 요청한 닫기, 시작 실패, 시간 안에 정리된 호출 시간 초과에서는 부르지 않는다.
- **세션이 깨진 경우를 하나 더 알아본다.** 서버 출력이 끝나면 mcp 1.29.0 `BaseSession._receive_loop`는 기다리던 요청 모두에 고정 오류(코드 `CONNECTION_CLOSED`, 문구 "Connection closed")로 답하고 쓰기 스트림을 닫는다. 이 오류를 닫힌 스트림과 같이 다룬다. 서버가 같은 코드·문구를 직접 보내면 자기 세션만 이번 실행 동안 멈춘다.
  - 이게 없으면 호출 중에 서버가 죽었을 때 worker가 READY로 남는다. 다음 Task 하나가 더 결과 불명으로 잠긴다.
- **transport가 서버 ID만 전달한다(`McpTransport(on_server_failed=...)`).**
- **`LiveCheckedRegistry.server_failed(server_id)`가 그 서버를 PENDING으로 본다.**
  - LIST-01(A100)과 같은 방식이다. Guard는 그 서버의 새 호출을 예약 전에 `REG_NOT_ACTIVE`로 거부한다. 기존 거부 사유라서 계약 변경이 아니다.
  - 승인 대기 중인 호출도 재확인에서 같은 사유로 무효가 된다. LIST-01과 같은 상태 확인 경로다. 이 경우만 따로 테스트하지는 않았다.
  - 새 Codex 세션의 도구 목록에서 빠진다(`unavailable`, `offers_tool`). 같은 `offers_tool`을 쓰는 세션 화면의 "도구 사용 가능"도 꺼진다.
  - 지문은 실제 값 그대로다. C Registry에는 아무것도 쓰지 않는다. 다음 실행에서 다시 연결하고 다시 대조한다.
  - LIST-01과 달리 대조(seal) 전에 온 고장도 막는다. 대조가 끊긴 연결을 되살리지 않기 때문이다.
- `assemble_guard`가 transport와 Registry 보기를 연결한다.

## 결과

- 서버가 죽은 뒤 그것을 처음 만나는 호출 하나는 여전히 예약 뒤 실패다. 그 Task는 결과 불명으로 정지한다(READ면 `Withheld(POST_DISPATCH_ERROR)`).
  - stdio 서버의 출력 종료(EOF)는 미리 알아채지 못하고 다음 호출에서 안다. HTTP는 진행 중인 요청이 실패할 때 안다.
- 그 뒤 호출은 모두 예약 전 `Denied(REG_NOT_ACTIVE)`이고 그 Task는 RUNNING으로 남는다. 서버 하나가 죽어서 결과 불명으로 잠기는 Task는 최대 하나다.

## 검증

- `tests/unit/test_mcp_transport.py`
  - 세션이 깨지면(`ConnectionError`, SDK의 `CONNECTION_CLOSED`) 서버 ID 하나만 한 번 알린다. 그 서버의 다음 호출은 `WorkerClosed`이고 다른 서버는 계속 쓴다. 닫은 뒤에도 다시 알리지 않는다.
  - 서버가 보낸 오류 응답은 고장이 아니다. 코드만 같거나 문구만 같으면 worker가 그대로 쓰인다.
  - 요청한 닫기, 시작 실패, 정리된 시간 초과에서는 알리지 않는다.
  - watchdog을 넘긴 호출과 호출 중 owner가 끝난 경우는 알린다.
  - 콜백이 호출 가능하지 않으면 생성 때 거부한다.
- `tests/unit/test_mcp_http.py`: 307·308·401·500으로 HTTP 세션이 끝나면 `["docs"]` 한 번만 알린다.
- `tests/unit/test_product_assembly.py`(실제 SDK, SQLite, B Guard)
  - 서버가 유휴 중에 죽은 경우와 호출 중에 죽은 경우 각각
    - task-2는 `Withheld(POST_DISPATCH_ERROR)`, HALTED
    - task-3은 `Denied(REG_NOT_ACTIVE)`, RUNNING, wire 호출 없음, 감사는 `before_dispatch`
    - Registry 보기는 PENDING, 새 세션에 줄 도구 없음, C Registry 기록은 ACTIVE 그대로
  - `CONNECTION_CLOSED` 분기를 끄고 돌리면 "호출 중" 경우가 실패한다. task-3도 결과 불명으로 잠긴다. "유휴 중" 경우는 닫힌 스트림 경로로 통과한다. 두 경로를 각각 덮는다는 확인이다.
  - 대조 전에 온 고장도 대조 뒤까지 PENDING이다.
- 재현 스크립트(stdio EOF, task 3개): 수정 전에는 task-2·task-3이 모두 `Withheld`이고 Registry 보기는 ACTIVE였다. 수정 뒤에는 task-3이 `Denied(REG_NOT_ACTIVE)`이고 PENDING이다.
- 전체 결과는 A08·A132·A134와 같은 실행이다(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`).
  - 단위 5,202개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(238개 파일)·mypy(102개 소스 파일) 통과

## 남은 것

- `REG_NOT_ACTIVE`를 쓰는 데 대한 B 의견은 아직 없다. 기존 사유라서 먼저 진행했다.
- stdio 출력 종료를 미리 알아채면 첫 호출도 예약 전에 막을 수 있다. 수신 gate(목록 수집용)에 종료 신호를 더해야 해서 이번에는 하지 않았다.
- 실제 launcher 프로세스 종료로 확인하는 것은 C launcher 파이프 연결(A09·A10) 뒤다.
- PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
