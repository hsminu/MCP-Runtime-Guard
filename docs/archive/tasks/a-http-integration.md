# A08/A12 · HTTP MCP 연결

2026-10-13, `feat/a-application`. 사용자의 “http mcp 연결해서 푸쉬까지” 요청으로
Streamable HTTP adapter와 실제 loopback 합성 앱을 연결했다.

## 범위와 소유권

- A `adapters/mcp_transport.py`의 stream factory, 합성 앱·CLI·서버,
  adapter/runtime 단위 테스트, 의존성 명세·문서만 변경했다.
- B `domain/*`, `ports.py`, Guard·SQLite와 C endpoint/Registry/fixture는 변경하지 않았다.
  MCP 실행은 기존 Guard → 서버별 worker → ClientSession 경로를 유지한다.
- 설계 9.1–9.4·12장, I1·I2·I3·I5·I6·I8·I9,
  T08·T14–T16·T19·T20·T36 관련 A 검증이다. C 독립 인수는 남아 있다.

## 구현과 지원 범위

`http_stream_factory(endpoint, io_timeout_s=5)`가 worker 내부에서 HTTPX client와
MCP stream의 진입·종료를 소유한다. endpoint는 신뢰 구성 루트가 C 등록·검증을 거쳐
공급해야 한다. 현재 CLI는 고정 합성 서버만 실행하며 임의 외부 URL을 받지 않는다.

- MCP 1.29.0의 `streamable_http_client(url, http_client=..., terminate_on_close=True)` 사용.
  기존 stdio와 같은 `ClientSession`·Guard·원시 응답 게이트를 사용한다.
- JSON 또는 POST SSE 응답, stateful/stateless 서버를 로컬 TCP로 확인했다.
- `follow_redirects=False`, HTTP transport `retries=0`, `auth=None`, client/transport
  `trust_env=False`, TLS `verify=True`. 환경 proxy·자동 인증·인증 갱신 replay를 사용하지 않는다.
  서버 cookie를 다음 요청에 실어 보내지 않는다.
- 연결 상한 4개, keepalive 2개. I/O timeout은 유한한 양수·최대 300초다.
  worker의 전체 call/start/close 상한은 별도로 적용한다.
- (2026-10-13 QA 2차) 응답 read timeout(`read_timeout_s`, 기본 60초)을 연결·쓰기 상한과 분리했다.
  `McpTransport`는 read timeout이 call timeout보다 짧으면 생성을 거절한다. 전에는 JSON 응답 모드에서
  I/O timeout보다 느린 정상 도구 한 번이 SDK task group을 취소해 그 서버 worker를 재시작 전까지
  못 쓰게 만들었다. read timeout보다 오래 응답이 없는 서버는 여전히 세션이 끝나고 이후 호출은
  차단 쪽으로 실패한다.
- (2026-10-13 QA 2차) SDK `ClientSession.call_tool`은 서버 `outputSchema`로
  `tools/list`와 `jsonschema.validate`를 이벤트 루프에서 실행해, 서버의 `$ref`가 Guard 프로세스의
  임의 URL 요청이나 루프 정지를 일으킬 수 있었다. A transport는 이 검증 단계를 뺀 호출을 쓰며,
  결과는 신뢰하지 않은 채 B ResponseGate로 간다. 서버 스키마로 structuredContent를 검증하지 않는다.
- 정확히 같은 endpoint의 POST/GET/DELETE만 보낸다. 알림 GET은 한 번만 열고,
  이후 GET 재연결·`Last-Event-ID` 응답 재개는 HTTP 전송 전에 거절한다.
  SDK가 재개를 시도하더라도 새 wire 요청은 나가지 않는다. 잃어버린 SEND를
  자동 복구·재제출하거나 새 세션에서 반복하지 않는다.
- HTTP 상태 오류·timeout·잘못된 응답은 기존 DispatchError 경계로 귀결된다.
  Guard가 SEND는 `UNKNOWN_OUTCOME`, READ는 미공개·정지로 처리한다.
- SDK/HTTPX worker와 앱 내부 합성 서버의 로그 context를 격리한다.
  원문·세션 ID·쿠키·예외 본문 대신 `MCP_LOG_WITHHELD`만 공개 로그에 남긴다.

URL 형태는 최대 2,048자, HTTPS 또는 literal `127.0.0.1`/`::1`의 HTTP만 지원한다.
userinfo·query·fragment·공백/제어문자·역슬래시·잘못된 host 표기는 거절한다.
이 검사는 **C의 DNS/IP·경계 허용 목록 검증을 대신하지 않는다**. 현재 인증 서버·TLS
실서버·프록시·legacy HTTP+SSE 연결은 인수하지 않았다.

SDK의 HTTP task-group이 오류로 세션을 종료하면 worker는 예외를 동반한 context 종료를
`SessionCloseFailed` 등으로 보고할 수 있다. 이를 정리 성공으로 바꾸지 않았다.
검증에서는 해당 owner task가 끝났는지도 별도로 확인했다. 정상 종료는 HTTP client close와
stateful 세션 DELETE를 확인했으며, DELETE의 성공이 원격 SEND 취소를 뜻하지 않는다.
SDK task group이 오류로 취소된 실패 경로에서는 SDK의 `finally` DELETE도 함께 취소되어 원격 서버에
세션이 남을 수 있다(MCP 1.29.0 동작). 이 경로의 정리는 미구현 한계로 둔다.

## 앱과 재현

```powershell
uv sync --locked
uv run python -m guard_app --demo --agt-mode real --mcp-transport http
uv run python -m guard_app --demo --agt-mode real --mcp-transport http --scenario deny
uv run python -m examples.verify_http_spike --agt-mode real
uv run python -m examples.verify_http_spike --agt-mode mock
uv run pytest -q
```

`examples/http_spike_server.py`는 임시 포트를 bind한 채 Uvicorn과 FastMCP를 실행한다.
외부 주소 대신 `127.0.0.1`에서 합성 READ/SEND만 제공하고 종료 시 listener·server를 닫는다.
AGT 준비를 먼저 확인하고 서버·transport를 연다. 종료는 transport → 서버 → AGT 순서다.
기본 `--mcp-transport stdio` 경로도 유지한다. 모델·Store·Registry는 기존 합성 대역이다.

## 검증 관측과 한계

새 `.cache/http-lock-check` 환경에 `uv sync --locked`로 설치한 뒤 실행했다.
Windows x64 CPython 3.12.15, MCP 1.29.0, HTTPX 0.28.1, Uvicorn 0.54.0,
AGT core 5.0.0, PySide6 6.12.0 조합이다.

| 실행 | 결과 |
| --- | --- |
| 새 환경 `uv pip check` | 66개 설치 배포판의 선언된 의존성 충돌 없음 |
| 전체 `pytest -q --tb=short`, Qt offscreen | **1,282 passed**, 22.55초 |
| 위 전체 실행의 HTTP adapter / Qt runtime | 각각 37개 / 8개 통과 |
| HTTP 앱 smoke | AGT real/mock 각각 8경로 통과 |
| 변경 Python 8개 Ruff check·format check / `git diff --check` | 통과 |

관찰된 경고는 기존 AGT import deprecation 4건과 FastMCP/Pydantic Settings의
`lifespan` forward reference 경고 1건이다. 외부 SDK 파일을 수정하거나 경고를 숨기지 않았다.

HTTP adapter 단위 probe는 실제 TCP 수신 측에서 JSON-RPC `tools/call` POST와 부작용을
각각 센다. 초기화·tools/list·알림·GET·DELETE는 tools/call 계수에서 제외한다.
A가 작성한 독립 프로토콜 대역이며 C의 정식 수신 fixture로 보고하지 않는다.

| 요청별 검증 | host dispatch | 수신 tools/call POST | 수신 부작용 |
| --- | ---: | ---: | ---: |
| 정상 SEND | 1 | 1 | 1 |
| 307·308·401·404·500 (각각) | 1 | 1 | 0 |
| 부작용 뒤 잘못된 JSON·timeout·SSE 유실 (각각) | 1 | 1 | 1 |
| 부작용 이후 호출자 취소 | 1 | 1 | 1 |
| 초기화 실패 | 0 | 0 | 0 |

오류 SEND는 모두 결과 불명이며 새 요청으로 다시 제출해도 Guard가 거절했다.
redirect 대상 수신은 0건, `Last-Event-ID` 요청·cookie/Authorization 전달도 0건이었다.
실제 FastMCP JSON/SSE × stateful/stateless의 4조합과 Qt HTTP real/mock 승인 경로를 확인했다.
합성 앱의 allow·deny·승인·거절·취소·긴급 차단·AGT deny-send/deny-all 8경로도 통과했다.
앱 smoke는 host_dispatch/receiver_effect를 표시하며 위 별도 probe의 wire 계수와 혼동하지 않는다.

C의 endpoint·Registry·지문·wire 수신 상한·Scanner·보안/공격 인수, B6 영속 감사와
실제 모델 연결은 후속 작업이다. HTTPS 검증 설정을 확인한 것으로 실제 TLS 서버 시험을 대체하지 않는다.

## API 근거

- 설치 MCP 1.29.0 `mcp/client/streamable_http.py`: HTTP client 주입, POST 전송,
  GET/Last-Event-ID 재연결과 종료 DELETE 경로를 직접 확인했다.
- 설치 HTTPX 0.28.1: AsyncClient의 redirect/auth/trust_env와 AsyncHTTPTransport의 retries/verify/limits 확인.
- [MCP 2025-06-18 전송 명세](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports):
  JSON/SSE 응답, GET 재개, 세션 DELETE 의미를 대조했다.
