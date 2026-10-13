# A07·A08 · 동일 SDK 합성 왕복 실행 준비서

> 2026-10-13: A 단독 SDK 조사는 C fixture를 기다리지 않는다. `python -m examples.verify_stdio_spike`로 고정 합성 서버를 먼저 실행한다. 아래 C 격리·계수 조건은 정식 인수 조건이며 독립 스파이크의 착수 조건이 아니다. 실제 A 왕복 결과는 [리뷰 반영 기록](a-review-fixes.md)을 따른다.

2026-10-12, `feat/a-application` 당시 체크아웃 기준. [A07·A08 지시](a-prompts/01-contracts.md#a07), [A02 설치 조사](../integration-notes.md), [A04 C 인수 요구](a-platform-review.md), [설계](../technical-design.md) 9·14.2절/부록 A를 대조했다. **A07 stdio READ와 A08 Streamable HTTP SEND의 실제 왕복은 미실행**이다. 당시 체크아웃에 C의 격리 합성 fixture/harness, 검증 완료 launcher/endpoint, 요청별 계수가 없었다. `git ls-remote origin`에서 C HEAD `0f30f25`가 로컬 추적 ref와 일치함을 확인했고, 해당 ref의 트리에도 fixture/harness 파일이 없었다. 설치·import 및 API 시그니처 확인은 왕복 증거가 아니다. 최신 인수 상태는 [A 작업 현황](a-progress.md)을 따른다.

## 지금 재현한 사전 점검

레포 루트에서 아래 읽기 전용 명령을 실행했다. 출력은 레포 루트의 `.venv\Scripts\python.exe`, CPython `3.12.15`, `mcp 1.29.0`, `httpx 0.28.1`이었다. 이는 **현재 클라이언트 환경**의 버전이며 C 서버의 버전이나 확정 lock은 아니다.

```powershell
& .\.venv\Scripts\python.exe -c "import sys, importlib.metadata as m; print(sys.executable); print(sys.version.split()[0]); print('mcp', m.version('mcp')); print('httpx', m.version('httpx'))"
& .\.venv\Scripts\python.exe -c "import inspect; from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client; from mcp.client.streamable_http import streamable_http_client; print(inspect.signature(ClientSession)); print(inspect.signature(stdio_client)); print(inspect.signature(streamable_http_client)); print(inspect.signature(ClientSession.call_tool)); print(inspect.signature(StdioServerParameters))"
```

설치된 `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`는 `(read, write, session_id_getter)`를 제공한다. 구식 설계 예시의 `streamablehttp_client(..., httpx_client_factory=...)`를 그대로 복사하지 않는다. `stdio_client(server, errlog=...)`와 `ClientSession(read, write, ...)`가 있고, `ClientSession.call_tool(name, arguments=None, read_timeout_seconds=None, progress_callback=None, *, meta=None)` 시그니처를 확인했다. `httpx.AsyncClient`의 `follow_redirects=False`, `trust_env=False`와 `httpx.AsyncHTTPTransport(retries=0)` 인자도 확인했다. 정적 API 확인만으로 재전송, redirect, 격리, 프로세스 정리가 작동했다고 판정할 수 없다.

## C가 제공해야 실행할 수 있는 입력과 증거

아래는 실행 **전제 조건**이며 메서드 이름의 신규 계약을 확정하는 표가 아니다. C가 제공한 실제 파일·명령·버전을 A가 실행 시점에 대조하고, 불일치하면 중단한다.

| 입력/증거 | A07 stdio READ | A08 HTTP SEND |
| --- | --- | --- |
| 재현 환경 | C fixture 서버의 Python 실행 파일·`mcp` 정확한 버전, 설치/lock 근거, 승인된 합성 자료의 경로와 기대 `resource_id`, 테스트별 임시 디렉터리 | 같은 Python/MCP SDK 버전, 정확한 서버 시작·준비·종료 명령, 무인증 `127.0.0.1` endpoint와 C의 endpoint 검증 결과, 합성 목적지·본문 |
| 안전한 실행 | C `security.launcher`가 검증한 **절대 실행 파일·인자 리스트·최소 환경**과 `errlog`를 받는 격리 `TextIO`; 프로세스 ID/종료 확인법 | C fixture 전용 포트/URL, 외부 네트워크·프록시 없이 접근하는 방법, redirect 수신기와 응답 유실/재연결 모드의 별도 설정법 |
| 관측 | 요청별 `stdio_tools_call_received` 또는 동등한 수신 지표의 의미, `initialize`·`tools/list` 분리, 결과 구조/출처 관측, stdout 프로토콜과 stderr 격리 증거 | 요청별 `wire_tools_call`(=설계 `wire_post`), `wire_protocol`, `receiver_effect`의 독립 계수; 원 endpoint와 redirect 대상 각각의 수신 계수, 수신 효과의 영속 기록 |
| 대응·주입 | 클라이언트 호출 ↔ fixture 요청의 안전한 ID 연결, 시작 실패·잘못된 구조/출처·시간초과 주입법, 각 경우의 새 fixture 초기화/정리법 | 클라이언트 호출 ↔ JSON-RPC ID ↔ ASGI ingress ↔ 도구 효과의 안전한 대응법, 정상·307/308·수신 후 응답 유실·재연결 주입법 |

계수 sidecar에는 요청 ID·메서드·단계·계수·시각·안전한 오류 코드만 둔다. 문서 원문, 토큰, 전체 인자, HTTP body, 예외 문자열은 수집하지 않는다. 응답/계수 파일 누락, 파싱 오류, ID 대응 실패, 초기값 불명, 예상 주입 미발생은 `0회`가 아니라 **검증 불가/실행 실패**로 기록한다. C는 각 모드의 정확한 실행 명령과 종료 확인 방법을 함께 인계해야 한다. 이 문서의 설계 코드 블록을 실행 명령으로 대체하지 않는다.

특히 현재 MCP `stdio_client`는 `StdioServerParameters.env`를 주더라도 SDK 기본 환경과 병합한다는 A02 정적 조사 결과가 있다. `env`만 넘겼다는 이유로 최소 환경을 충족했다고 보지 않는다. C launcher가 이 경로에서 실제 자식 환경을 검증·제한한 증거가 있어야 A07을 실행한다. stdio 서버 내부의 별도 외부 통신처럼 계수로 보지 못하는 경로는 `검증 불가`로 남긴다.

## 공통 실행 절차

1. C가 인계한 정확한 fixture 소스/커밋·실행 명령·주입 모드·계수 형식을 적고, 양쪽 프로세스에서 Python/MCP SDK 버전을 **실행 직전** 확인한다. 버전이 다르면 같은 SDK 왕복으로 진행하지 않는다. 외부 서비스 주소나 비합성 데이터가 있으면 실행하지 않는다.
2. 사례마다 새 임시 디렉터리, 새 fixture 인스턴스, 고유한 안전한 요청 상관 ID를 쓰고 계수 초기값을 확인한다. 한 사례의 결과가 다음 사례에 섞이지 않게 한다. C 정의의 sidecar가 ID를 연결해야 하며 JSON-RPC ID만 Guard의 신뢰 `request_id`로 간주하지 않는다.
3. C가 제공한 정확한 시작·준비 확인 명령을 사용한다. 준비 확인 실패는 호출 0 성공이 아니라 `시작 실패`다. 호출 전과 후의 요청별 계수 스냅샷 및 차이를 보존한다.
4. 클라이언트의 **같은 소유 asyncio task**에서 transport 컨텍스트 안에 `ClientSession`을 중첩해 열고 `initialize` → `list_tools`/정의 확인 → 합성 `call_tool` 1회 → 세션·transport 닫기를 순서대로 수행한다. SDK가 출력 스키마 확인용 `tools/list`를 추가로 보낼 수 있으므로 프로토콜 계수와 `tools/call`을 분리한다. 세션 종료에 따른 HTTP DELETE 등도 SEND 계수로 합산하지 않는다.
5. 결과는 `isError`, 구조화 결과 유무/필드 이름, 기대 식별자 일치 여부와 안전한 상태 코드만 기록한다. 원시 content·본문·인자·SDK 예외 메시지를 화면이나 기본 로그에 남기지 않는다. stderr·logging·message/progress callback과 SDK logger도 격리한 상태에서만 실행한다.
6. 세션·서버 종료를 C 절차로 확인하고 프로세스 잔존, 열린 연결, 임시 효과 수를 기록한다. 정리 실패는 별도 실패로 남긴다. 결과 표에는 `사전 점검`, `실제 왕복`, `오류 주입`, `미실행/검증 불가`를 분리한다.

이 절차는 **격리 fixture 안의 SDK 호환성 검사**다. 제품에서 Runner·UI·AGT가 transport를 직접 호출하는 경로를 만들지 않는다. Guard의 판정·예약·승인·응답 공개 검증은 B/C 통합 경로(A38/A39)에서 한다.

## A07: stdio READ 1회

1. C launcher가 확정한 `StdioServerParameters(command=<검증된 절대 경로>, args=<검증된 리스트>, env=<검증된 최소 환경>)`만 사용한다. C가 지정한 실제 격리 `TextIO`를 `stdio_client(..., errlog=...)`에 전달하고 SDK logger/콜백의 원시 출력을 별도 격리한다. `stdout`은 MCP 프로토콜 전용임을 C 계수/파서로 확인한다.
2. 같은 task에서 `stdio_client`와 `ClientSession`을 차례로 열어 초기화·도구 정의 확인 후 C가 지정한 합성 READ 도구를 한 번 호출한다. 서버/도구 명칭과 인자 스키마는 fixture가 제공한 실제 정의에서 확인하며 문서 예시 `read_document`를 존재하는 API라고 가정하지 않는다.
3. SDK 반환 구조에서 기대 `resource_id`와 요청한 합성 경로가 일치하는지, 구조화 필드가 있는지, `isError` 여부를 확인한다. 형식이 다르면 실제 구조와 불일치 이유만 기록한다. C가 정의한 수신 지표로 **해당 요청의 READ 도구 수신 1회**를 확인한다. READ에는 저장 부작용이 없으므로 `receiver_effect=1`을 기대하지 않고, C가 별도 정의한 의미만 사용한다.
4. 컨텍스트를 역순으로 닫고 C의 프로세스 종료 관측값을 확인한다. 정상 사례 후 시작 실패, 구조 불명/잘못된 `resource_id`, 시간초과를 각각 **새 인스턴스**에서 주입한다. 호출 이전의 실패와 수신 뒤의 실패를 구분하고 SDK가 준 반환/예외 종류 및 수신 계수만 기록한다. 이 오류 표면은 Guard의 `Withheld`/`HALTED` 판정을 증명하지 않는다.

## A08: Streamable HTTP SEND 1회

1. C가 검증한 정확한 `127.0.0.1` endpoint의 무인증 합성 fixture만 사용한다. 현재 설치 API에서는 명시적 `httpx.AsyncClient(follow_redirects=False, trust_env=False, auth=None, transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False), ...)`를 만들어 `streamable_http_client(url, http_client=client, terminate_on_close=True)`에 넘긴다. 실제 timeout 값과 client 수명은 사례 기록에 남긴다. 이 정적 설정만으로 모든 replay 경로가 차단됐다고 선언하지 않는다.
2. 같은 task에서 HTTP client → MCP transport → `ClientSession` 순서로 열고 `initialize`·`list_tools` 후 C가 지정한 합성 SEND 도구를 **한 번** 호출한다. 모든 컨텍스트를 역순으로 닫는다. GET SSE 재연결, 종료 DELETE, `initialize`·`tools/list`는 SEND `tools/call` POST와 분리한다.
3. 호출 전후 C sidecar에서 해당 상관 ID의 `wire_tools_call=1`, `receiver_effect=1`을 각각 확인하고 서버가 저장한 무해한 효과 식별자와 SDK 결과를 대응한다. `wire_protocol`은 별도 기록한다. `wire_tools_call`과 `receiver_effect`의 값이 다르면 어느 지점에서 갈렸는지 기록하고 정상 왕복으로 판정하지 않는다.
4. 307/308, 처리 후 응답 유실, reconnect는 C가 별도 수신기·주입 모드·원/redirect endpoint 계수를 제공한 뒤 **별도 사례**로 실행한다. 원 endpoint에 이미 도착한 POST가 있다면 그 수는 0으로 쓰지 않는다. redirect 대상의 무단 `tools/call`/효과 0, 진행 SEND의 추가 POST 0, 결과 불명 상태는 각각 독립 관측한다. 인증 갱신 replay는 무인증 MVP 범위 밖이며 미실행으로 남긴다. 응답 유실 뒤 실제 Guard `UNKNOWN_OUTCOME`/Task 잠금은 C의 T14·T15 통합/공격 테스트가 있어야 판정한다.

호환 harness에서 `approval_consumed`와 제품 `ServerWorker._execute`의 `host_dispatch`는 **N/A (Guard/제품 worker 미사용)** 이다. 0으로 바꾸거나 제품 I8 통과 증거로 사용하지 않는다. 제품 통합 시에는 요청별 `(approval_consumed, host_dispatch, wire_tools_call, receiver_effect)`를 C의 계약대로 함께 측정한다.

## 실행 기록 양식과 현재 판정

| 사례 | 환경·C fixture 커밋/명령 | 상관 ID·수명/결과 | 요청별 수신/프로토콜/효과 계수 | 판정 |
| --- | --- | --- | --- | --- |
| A07 정상 READ | 미제공 | 미실행 | 미측정 | 선행 조건 대기 |
| A07 시작 실패·invalid·timeout | 주입 수단 미제공 | 미실행 | 미측정 | 선행 조건 대기 |
| A08 정상 SEND | 미제공 | 미실행 | 미측정 | 선행 조건 대기 |
| A08 307/308·응답 유실·reconnect | 주입 수단 미제공 | 미실행 | 미측정 | 선행 조건 대기 |

재개 조건은 C의 격리 fixture/harness·정확한 시작/정리 명령·launcher/endpoint 검증·요청별 계수/ID 대응 인계다. A07/A08 결과는 실행 후 [연동 기록](../integration-notes.md)에 실제 버전·명령·관측값·실패/미실행 범위를 남긴다. 이 준비서는 I1·I5·I8·I9의 **검증 계획**이며 T14–T16·T19·T36 또는 보안 불변식 통과 기록이 아니다.
