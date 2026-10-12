# 외부 연동·호환성 기록

담당 A, 리뷰 B·C. 아래 로컬 환경의 설치·import·API 시그니처 확인은 실행했다. **stdio·HTTP 실제 왕복, AGT·Scanner 호환성 및 보안 동작 검증은 미실행**이다. [개발문서](technical-design.md) 9·10·12장과 부록 A에 따라 실제 환경에서 확인한 내용만 기록한다. 정확한 버전, SDK 메서드, 지원 기능을 문서 예시만 보고 확정하지 않는다.

## 호환성 확인표

| 항목 | 확인할 내용 | 상태 | 실제 버전 / 근거 / 재현 결과 |
| --- | --- | --- | --- |
| Python·MCP SDK | Python 3.12, 클라이언트·fixture의 동일 SDK 조합 | 설치·import 확인, 왕복 미실행 | 2026-10-12 A02: CPython 3.12.15, `mcp 1.29.0`. fixture의 SDK는 아직 확인하지 않음 |
| stdio READ | 세션 진입·호출·종료, 구조화 결과·resource_id | 미실행 | 미확정 |
| HTTP SEND | Streamable HTTP 왕복, tools/call·부작용 계수 | 미실행 | 미확정 |
| redirect·retry | 307·308, 재연결, 쓰기 요청 자동 재전송 없음 | 설치 코드 조사, 동작 미실행 | SDK 기본 HTTP 팩토리는 redirect를 따름. A02 조사와 A12·A16·A39 실측 필요 |
| 원시 출력 격리 | stderr·로그·progress 콜백과 형식 변환 예외 | 원문 누출 재현, 격리 미완료 | A14에서 MCP 1.29.0의 잘못된 알림이 callback 밖 root logger에 남는 것을 합성 표지로 확인. C 공개 handler 정책·격리 sink와 A39 검증 필요 |
| AGT | pre-call 거부권, 실행 분리, AccessResult 번역, 의존성 충돌 | 현재 환경 미설치·미실행 | 실제 AGT 연결·충돌 여부 미확인 |
| Cisco Scanner | MCP SDK 의존성 충돌 여부, C의 별도 환경 결정과 연계 | 현재 환경 미설치·미실행 | C의 설치 환경·버전 미확인 |
| 모델 제공자 | Task별 대화·핸들·캐시 분리, 시연 환경 승인 상태 | 미실행 | 미확정 |
| PySide6 bridge | 메인·백엔드 스레드 연결, 제어 큐, 안전한 종료 | offscreen 커널 단위 검증 | PySide6/Qt 6.12.0. A24/A25 커널 시험 10건 통과; 실제 앱 구성·C Store 수명·확정 상태 UI는 미연결 |
| lock 재설치 | 새 가상환경에서 같은 stdio·HTTP 검증 재현 | 미실행 | 미확정 |

## 조사 기록 형식

각 조사에는 날짜, 담당자, 환경·정확한 버전, 공식 자료 또는 설치된 API 근거, 시도한 방법, 실제 결과, 판정, 영향을 받는 계약, B·C 확인을 남긴다. 토큰·본문·전체 인자와 로컬 비밀 환경값은 기록하지 않는다.

AGT를 모의 연동으로 선택하면 불가능했던 실제 연결 조건과 mock 표시 위치를 기록한다. 관측할 수 없는 전송 경로는 검증 불가로 남긴다.

## 2026-10-11 · A 로컬 개발환경 준비

- 환경: Windows x86-64, 레포 내부 `.devtools/uv`의 uv 0.13.0, `.devtools/python`의 CPython 3.12.15, `.venv`의 전용 가상환경. 전역 Python 3.11과 분리했다. `.devtools/`, `.venv/`, `.cache/`는 Git에서 제외한다.
- 후보 패키지: `mcp 1.29.0`, `pydantic 2.14.0`, `PySide6 6.12.0`, `PyYAML 6.0.3`, `jsonschema 4.25.1`, `httpx 0.28.1`, `uvicorn 0.54.0`; `pytest 9.1.1`, `pytest-asyncio 1.4.0`, `hypothesis 6.169.0`, `ruff 0.17.0`, `mypy 2.4.0`, `import-linter 2.15`, `bandit 1.9.4`, `semgrep 1.180.0`, `pip-audit 2.10.1`.
- 확인 결과: 가상환경의 Python은 3.12.15/`win-amd64`; `mcp`·PySide6 import 성공; `uv pip check`에서 설치 패키지 106개의 의존성 충돌 없음. 설치된 SDK의 `ClientSession`에는 `logging_callback`·`message_handler`, `stdio_client`에는 `errlog`, `streamablehttp_client`에는 `httpx_client_factory` 인자가 있다. 각 도구의 버전 명령은 실행했다. Semgrep 버전 명령은 Codex 제한 실행에서 `socketpair` 접근 오류가 났고, 제한을 벗어난 실행에서는 1.180.0으로 성공했다.
- 설치 중 관찰: 정적 검사 도구를 추가할 때 해결기가 `mcp 1.30.0 → 1.29.0`, `jsonschema 4.26.0 → 4.25.1`로 조정했다. 이 조합은 아직 stdio·HTTP 왕복으로 검증하지 않았으므로 `requirements.lock`으로 확정하지 않는다. AGT와 Cisco Scanner는 아직 설치·확인하지 않았다.
- 사용: 레포 루트에서 `.\.venv\Scripts\python.exe`로 Python·pytest를 실행한다. `ruff.exe`, `lint-imports.exe`는 `.venv\Scripts`에 있다. 현재 `mypy.exe`의 uv 실행 파일은 Windows에서 경로 오류가 있으므로 `python -m mypy`로 실행한다. 로컬 uv를 다시 사용할 때는 `UV_PYTHON_INSTALL_DIR=.devtools\python`, `UV_CACHE_DIR=.cache\uv`를 레포 절대 경로로 지정한다.

이 기록은 개발환경 준비 결과다. A1의 stdio READ·HTTP SEND 왕복, redirect·retry·원시 출력 격리, AGT 판정 전용 연동, C fixture 계수는 별도로 검증해야 한다.


## 2026-10-12 · A UI 시안 정보 구조·호출 상세·승인 대기열

- 범위: `ui-preview/app.js`, `styles.css`, `README.md`. 활성 서버·승인 대기·차단 호출을 상단에 두고 연결 서버, 확인 항목, 최근 호출, 참고용 호출 흐름 순서로 재배치했다. 호출 로그의 우측 패널을 상세 대화상자로 교체했다.
- 승인 요청 누적: 오래된 순, 검색·MCP 필터·정렬, 6건 단위 페이지, 대기/이력 분리, 처리 후 다음 요청, 30분 이상 대기 표식과 12건 추가 데모를 구현했다. 전체 날짜/초 단위 시간/KST 표시와 CSV ISO 일시를 추가했다. 최초 판정과 승인 처리 상태, 실행 결과를 구분한다.
- 관련 기준: 설계 2·12장, I3·I5·I8·I9, T18 표시 요구. 호출 상세에 원문·인증정보·전체 인자를 추가하지 않았다. 승인·거절은 브라우저 내부 예시 상태만 변경한다.
- 검증: JavaScript 문법 검사와 최소 DOM 대역을 쓰는 Node VM의 20개 동작 검사 성공. 122건 누적, 페이지 경계, 빈 결과, 처리 이력·로그 상태 동기화, 중복 처리, 날짜 시간대 변환, HTML 이스케이프 및 CSV를 확인했다. T18 전체 인수 통과를 의미하지 않는다.
- 미실행/한계: 브라우저 도구의 `file://` 접근 제한으로 실제 화면 렌더링·반응형·키보드 포커스 검증은 미실행. 백엔드가 없어 승인 소비·host dispatch·wire·receiver effect 검증은 불가. MCP·AGT·Qt 연동과 의존성 변경은 없다.
- 계약 변경 없음. B·C 인수 시 승인 만료·취소·무효화 이벤트 및 실제 일시·판정·단계별 감사 데이터의 공급 계약 확인이 필요하다. 시안의 대기 시간은 고정 기준시각을 사용하며 클라이언트에서 승인을 임의 만료시키지 않는다.


## 2026-10-12 · A MCP 서버 설정 화면 구체화

- 변경: `ui-preview/app.js`, `styles.css`, `README.md`. 서버 목록을 ID·transport·엔드포인트·tier·max_label·등록 상태·세션·도구 수 표로 변경하고 검색/필터를 추가했다. 상세 창에 연결 설정, 검사/지문 수집 상태, 로그에서 관측한 도구와 설정 JSON을 표시한다.
- 등록 폼: stdio(command 절대 경로, args 문자열 배열, env_allow 이름 목록)와 Streamable HTTP(url)를 분리했다. 방식 변경 시 비활성 입력은 보존하되 등록 데이터에는 포함하지 않는다. 표시 이름과 고유 ID를 구분하고, 외부 max_label의 CONFIDENTIAL을 제한한다. 새로운 서버는 PENDING 예시 상태만 가진다.
- 설계 연결: 10.1·10.2·12장, I7·I9·I10, T18의 일반 텍스트 렌더링 요구. transport로 신뢰 경계를 추정하지 않으며, ACTIVE 등록 예시와 실제 미연결·Scanner 미실행·지문 미수집을 분리한다. 등록 형식 검사는 프런트엔드 시안용이며 C의 launcher·endpoint 검증을 구현한 것이 아니다.
- 검증: JavaScript 문법 검사, 최소 DOM 대역의 Node VM 동작 검사 20개 성공. 경로/인자/환경변수/URL 입력, 중복 ID, 전송 방식 전환, 경계·등급, 등록/상세/필터/초기화 등을 확인했다. 실제 브라우저 화면·키보드 검증은 file URL 접근 제한으로 미실행. 실제 연결·프로세스 실행·Scanner·host dispatch·wire·receiver effect는 미실행이다.
- 계약 변경 없음. ID/표시 이름 입력 규칙, 등록 payload, 세션·프로토콜·Scanner·지문 이벤트의 실제 연결은 B·C와의 계약 확정 후 진행한다.


## 2026-10-12 · A 글자 크기·현황 서버 등급 표시

- 본문·메뉴·버튼·입력 글자를 14–15px 중심으로 확대하고 보조 글자는 최소 12px로 조정했다. 표 행·입력 높이, 서버 카드의 4/2/1열 반응형 배치도 조정했다.
- 현황의 서버 카드에서 등록된 `max_label`을 한국어/영문 병기와 파랑(PUBLIC)·황토색(INTERNAL)·보라(CONFIDENTIAL) 배경으로 강조했다. 신뢰 경계와 최대 수신 등급을 분리하며, 등급이 없으면 미지정으로 표시한다. 다른 화면의 데이터 등급 칩도 같은 색상을 사용한다.
- 설계 12장·I7 표시 범위의 변경이며 계약·판정 변경 없음. JavaScript 문법 및 생성 마크업·CSS 정적 확인을 수행했다. 기존 file URL 도구 접근 제한으로 실제 브라우저 레이아웃 검증은 미실행이다.

## 2026-10-12 · A02 현재 개발환경과 설치 SDK API 재조사

- 담당·범위: A, `feat/a-application` 작업 트리. `README.md`, `AGENTS.md`, `AGENT.md`, `docs/contracts.md`, 설계 9·12장/부록 A와 A02를 대조했다. 이 절은 설치 코드의 정적 조사이며, 제품 계약 합의나 I8·I9 검증 통과가 아니다. 설치·업그레이드·lock 생성·네트워크 요청은 하지 않았다.
- 실제 실행 환경: 레포 루트의 `.venv\Scripts\python.exe`, CPython 3.12.15, Windows AMD64/64-bit. `sys.prefix`는 `.venv`, `sys.base_prefix`는 `.devtools/python/cpython-3.12.15-windows-x86_64-none`; `pyvenv.cfg`는 system site-packages 비활성. 현재 import 및 metadata 확인: `mcp 1.29.0`, `httpx 0.28.1`, `pydantic 2.14.0`, `PySide6`/Qt·Addons·Essentials·`shiboken6 6.12.0`, `anyio 4.15.1`, `httpcore 1.0.9`, `uvicorn 0.54.0`, `pytest 9.1.1`, `pytest-asyncio 1.4.0`. 설치 배포판 106개에서 AGT/agent-governance-toolkit 및 MCP Scanner 이름의 후보는 없었다. 따라서 두 제품과 MCP SDK의 **조합 충돌은 아직 판정 불가**다.
- 의존성 검사: `.devtools/uv/bin/uv.exe pip check --python .venv/Scripts/python.exe` 결과 `Checked 106 packages`, `All installed packages are compatible`. 처음 샌드박스 실행은 uv의 사용자 임시 캐시 쓰기 권한 오류로 실패했고, 같은 읽기 목적의 검사를 승인된 실행에서 다시 돌려 성공했다. 이는 **현재 설치 배포판의 선언된 의존성**만 검사하며 stdio/HTTP 왕복·AGT/Scanner 설치 호환성은 보증하지 않는다.

### MCP 1.29.0의 설치 코드 근거

| 확인 항목 | 현재 API·코드 근거 | 구현 영향 / 미확인 |
| --- | --- | --- |
| `ClientSession` | `mcp/client/session.py:122–151`: `ClientSession(read_stream, write_stream, read_timeout_seconds=None, ..., logging_callback=None, message_handler=None, ...)`. `initialize()`와 `list_tools()` 제공; `call_tool(name, arguments=None, read_timeout_seconds=None, progress_callback=None, *, meta=None)`는 같은 파일 `:386–415` | `async with ClientSession(...)`의 진입/종료는 `mcp/shared/session.py:221–238`의 task group을 관리한다. 같은 소유 task에서 transport와 session을 열고 닫는 설계는 아직 실제 왕복으로 확인하지 않았다. 정상 결과 뒤 `call_tool`이 출력 스키마 확인을 위해 `list_tools()`를 추가 호출할 수 있다(`session.py:417–441`). `tools/list`는 `tools/call` 계수와 분리해야 한다. |
| 콜백 | `mcp/client/session.py:69–72,104–105,122–151,614–630`; `mcp/shared/session.py:262–272,413–414` | `logging_callback(params: LoggingMessageNotificationParams)`, `message_handler(ServerRequest/ServerNotification/Exception)`, 요청별 `progress_callback(progress: float, total: float\|None, message: str\|None)`를 주입할 수 있다. 모두 원시 서버 입력으로 격리할 경로다. 콜백 호출·예외 표면은 A14에서 재현해야 한다. |
| stdio | `mcp/client/stdio/__init__.py:72–90,105–129,182–216`: `StdioServerParameters(command, args=[], env=None, cwd=None, ...)`, `stdio_client(server, errlog: TextIO=sys.stderr)`가 read/write 두 스트림을 yield하고 종료 시 프로세스·스트림을 정리 | `errlog`에 격리된 실제 TextIO를 넘겨야 한다. **중요:** `stdio_client`는 `server.env`가 있어도 `get_default_environment()`와 병합한다(`:27–69,127`); Windows 기본 상속 변수에는 `PATH`, 사용자 프로필 경로 등도 있다. 따라서 `env` 인자만으로 C가 지정한 허용 목록만 전달된다고 주장할 수 없다. C launcher와 허용 환경 기준을 합의하고 실제 자식 환경을 검증해야 한다. |
| Streamable HTTP | 현재 함수는 `streamable_http_client(url, *, http_client: httpx.AsyncClient\|None=None, terminate_on_close=True)` (`mcp/client/streamable_http.py:600–681`). read/write/session ID getter 세 값을 yield하며, 직접 준 HTTP client의 수명은 호출자가 관리한다. 기본 생성 client는 SDK가 관리하고 세션 ID가 있으면 종료 시 DELETE를 시도한다. | 설계 9.1절 예시의 `streamablehttp_client(..., httpx_client_factory=...)`도 설치돼 있지만 `:684–722`에서 deprecated로 표시되고 새 함수에 위임한다. 신규 구현의 client/timeout/redirect 설정은 `httpx.AsyncClient` 주입 경로를 기준으로 검토한다. 제품 세션 종료·요청 계수는 미측정이다. |
| Qt↔asyncio 검사 표면 | `PySide6.QtCore`의 `QObject`, `Signal`, `Slot`, `QThread`, `QCoreApplication`, `Qt.ConnectionType.QueuedConnection`; `PySide6.QtTest.QSignalSpy` import 성공. Python 3.12 `asyncio.new_event_loop`, `run_coroutine_threadsafe`, `loop.call_soon_threadsafe` 존재. `python -m pytest --version`은 9.1.1. | `pytest-qt`와 `qasync`는 현재 미설치이며 기본 설계는 별도 backend 스레드다. Qt 이벤트 전달·thread affinity·제어 큐·종료를 실행한 것은 아니므로 A24–A27과 A37에서 검사해야 한다. |

### redirect·재전송·원시 로그 판정

- **Redirect 제어 위치 확인:** MCP 기본 `create_mcp_http_client()`는 `httpx.AsyncClient(follow_redirects=True)`를 생성한다(`mcp/shared/_httpx_utils.py:23–87`). `httpx.AsyncClient`의 설치 시그니처는 `follow_redirects=False`를 받는다. 새 `streamable_http_client`에 명시적 `httpx.AsyncClient(follow_redirects=False, ...)`를 넘길 수 있으나, 307/308에서 `tools/call`이 재전송되지 않는다는 **동작 증거는 아직 없다**. A08의 합성 왕복과 A12·A39의 redirect 공격 조건에서 wire/부작용 계수로 확인해야 한다.
- **Retry·인증 replay:** 설치된 `httpx.AsyncHTTPTransport`의 `retries` 기본값은 0 (`httpx/_transports/default.py:279–310`); MCP `StreamableHTTPTransport._handle_post_request`에는 한 번의 `client.stream("POST", ...)` 호출이 있다(`mcp/client/streamable_http.py:334–375`). 이 정적 경로만으로 proxy·custom transport·모든 실패 조건의 중복 전송 0을 증명하지 못한다. `httpx.Auth.async_auth_flow`는 응답 뒤 새 Request를 산출할 수 있고 `AsyncClient._send_handling_auth`가 이를 다시 전송한다(`httpx/_auth.py:22–59,87–110`; `_client.py:1645–1677`). MVP 무인증 fixture에서는 auth를 주지 않는 범위로 A08을 수행하고, 인증 갱신은 별도 보안 결정과 실측 전까지 미지원으로 둔다.
- **Reconnect 구분:** SDK는 GET SSE stream의 자동 재연결과 POST 응답 SSE 중단 뒤 `Last-Event-ID`를 사용하는 GET 재연결 코드를 포함한다(`mcp/client/streamable_http.py:247–299,397–498`). 일반 `ClientSession.call_tool` 시그니처에는 재개 토큰 인자가 없다. 이 설치 코드에서 관찰한 GET 재연결은 POST 재전송과 동일하다고 볼 수 없지만, 재연결·응답 유실에서 SEND 결과가 안전하게 확정된다는 뜻도 아니다. A16·A39에서 진행 중 SEND의 `UNKNOWN_OUTCOME`, `wire_tools_call`, `receiver_effect`를 실제로 관측해야 한다.
- **원시 로그 위험:** SDK HTTP transport는 디버그 로그에 outbound JSON-RPC 메시지 및 SSE 메시지를 포맷하고(`mcp/client/streamable_http.py:218,547`), 초기화 파싱 실패 때 raw result를 warning으로 기록한다(`:194–198`). session의 출력 스키마 검증 오류는 원시 내용이 포함될 수 있는 예외 문자열을 만든다(`mcp/client/session.py:417–441`). stdio 파싱 오류도 SDK logger로 간다(`mcp/client/stdio/__init__.py:153–159`). 앱의 기본 로그 핸들러·예외 표시가 원문을 수집하지 않도록 SDK logger 격리와 오류명만 전달하는 경계를 A13·A14에서 설계해야 한다. 콜백 주입만으로 SDK 자체 logger가 자동 격리된 것은 아니다.
- **판정과 다음 조건:** A02의 설치/import·정적 API 조사는 완료. 패키징 버전/lock 확정은 보류. B는 Transport의 전송 전후 오류와 결과 불명 계약, C는 launcher 환경·합성 stdio/HTTP fixture·wire 계수 계약을 제공해야 A07·A08·A11–A16을 실제로 검증할 수 있다. `approval_consumed/host_dispatch/wire_tools_call/receiver_effect`는 이번 조사에서 발생·측정하지 않았고 I8·I9 및 T14–T16·T19·T36을 통과로 표시하지 않는다.

## 2026-10-12 · B 초안 사용 결정과 A18 명시적 AGT mock

- 사용자가 B 브랜치를 A 구현에 활용해도 된다고 명시했다. `0c02b61`의 `src/guard_app` B 소유 소스를 현재 A 트리에 가져왔고, 이후 B HEAD `f3c2d2b`에서 변경된 `guard.py`·`approvals.py`를 반영했다. 포트·도메인·Catalog 공개 계약은 두 커밋 사이에 바뀌지 않았다. 이는 `docs/contracts.md`의 공동 계약 동결이나 C 구현 완료를 뜻하지 않는다.
- 현재 환경에 특정 AGT 배포판이 설치되거나 지정되지 않아 A17의 실제 AGT 판정 전용 호출·의존성 호환 시험은 미실행이다. `adapters/agt.py`에는 **명시적으로 생성할 때만** 동작하는 `RuleBasedMockAccessChecker`를 구현했다. 안전하게 로드된 규칙 문서를 넘기는 입구만 제공하고 파일 경로 읽기/실행은 하지 않는다. 규칙은 등록 서버 ID와 도구 이름만으로 allow/deny를 정하며, 규칙 없음은 deny, Task 신뢰 맥락 불일치는 error로 반환한다. mock allow도 B Guard의 정책·사람 승인을 대체하지 않는다.
- `.venv`의 `pytest -q tests/unit/test_agt.py`는 7건 통과했다. 이는 B `AccessChecker` 포트에 대한 A mock 단위 검증이며 실제 AGT 연결, C의 `host_dispatch=0` 계수, 규칙 파일 안전 로딩/`agt_mode` 감사·UI 연결 검증이 아니다. 실제/mock 모드 선택과 공개 계약은 E4/E9 결정이 필요하다.

## 2026-10-12 · A 제품 모듈 단위 구현·Qt 표시 확인

- B 공개 `Transport.call`에 맞춘 서버별 장수 worker와 명령 future, 원문 없는 전송 오류를 `adapters/mcp_transport.py`에 구현했다. C 검증을 거친 stdio/HTTP stream factory와 SDK logger 격리 sink가 없으므로 실제 서버 factory를 제품 시작 경로에 연결하지 않았다. SDK timeout에서 무자료를 증명할 수 없어 `RawResult.failed=True`를 추정하지 않고 `DispatchError`로 B Guard에 보수 전달한다.
- B Catalog의 ACTIVE ToolView를 받는 provider 중립 변환과 `TaskRunner`를 구현했다. Runner는 관리 대상 제안을 `Guard.submit`으로만 보내고 `Released` 이후에만 결과 본문을 모델 대화에 넣는다. `Pending`은 일시정지, `Denied`는 고정 문구, `Withheld`/`OutcomeUnknown`은 모델 진행 중단으로 처리한다. 실제 모델 제공자·인증/허용 환경과 provider 핸들·캐시 격리는 미정이다.
- Qt 읽기 화면은 현황의 핵심 카드/연결 서버/확인 항목/최근 호출/호출 흐름과 서버 목록·상세를 표시한다. `GuardWindow` 기본값은 `미제공/조회 연결 대기`이며 실제 집계·Registry 조회를 추측하지 않는다. 합성 데이터로 Qt offscreen 1260×820·780×580을 렌더링했다. 화면 검사 때 Windows offscreen 플러그인의 글꼴 인식 문제를 피하려고 `C:/Windows/Fonts/malgun.ttf`를 명시적으로 로드했다. 이 결과는 네이티브 DPI·키보드 종합 검증이나 C backend 연동 인수가 아니다.
- 재검증: 현재 `.venv` Python 3.12.15에서 `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest -q tests\unit` 결과 **56 passed**(최종 반복 1.62s). A adapter/Runner/UI와 A 단위 테스트의 `ruff check`, `ruff format --check` 통과. `mypy --follow-imports=silent --ignore-missing-imports`는 A 소스 13개 파일에서 오류 0건이다. 실제 `approval_consumed / host_dispatch / wire_tools_call / receiver_effect`는 미측정이며 T08/T18/T21/T36 전체 통과로 주장하지 않는다.

## 2026-10-12 · A14 SDK 원시 로그 실증과 A10 종료 경합 수정

- 설치된 MCP 1.29.0의 `ClientSession`에 현재 A transport와 같은 폐기용 `logging_callback`/`message_handler`를 넣고 합성 메모리 스트림으로 잘못된 알림을 전달했다. `mcp/shared/session.py`의 형식 오류 경로가 알림 원문을 callback 밖의 **root logger**에 직접 기록했고, 합성 표지가 공개 logger capture에 나타났다. 실제 stdio/HTTP 서버를 연결하거나 T19/S04를 통과시킨 실험은 아니다.
- SDK의 `stdio_client` 기본 `errlog`는 정의 시점의 `sys.stderr`이고 `None`은 자식 stderr 상속이다. Windows 자식 프로세스에는 파일 descriptor가 있는 실제 격리 파일이 필요하다. `mcp/client/streamable_http.py`의 debug/warning 경로도 session ID·SSE·송신 메시지·raw 초기화 결과를 기록할 수 있다. 세션별 logger 교체나 단일 root logger filter만으로 여러 worker와 root 직접 기록을 안전하게 격리할 수 없다.
- C의 stderr 파일 수명/접근 정책과 앱 시작 전 **모든 공개 logging handler의 안전 이벤트 allowlist**가 마련되기 전에는 실제 stream factory를 제품 시작 경로에 연결하지 않는다. A14의 현재 정상 알림 callback 폐기는 부분 경계이며 `tools/list_changed` 지문 무효화(A15)를 대신하지 않는다.
- `McpTransport.close()`가 한 서버의 진행 호출을 기다리는 동안 다른 서버에 새 요청이 들어갈 수 있던 경합을 고쳤다. transport 전체의 닫힘 상태를 `call()`에서 먼저 확인하도록 했고 두 서버 회귀 테스트를 추가했다. `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q tests\unit\test_mcp_transport.py` 결과 **13 passed**다. 이 테스트는 실제 wire·수신 부작용 계수가 아니다.

## 2026-10-12 · A21/A24/A25·Qt 키보드 단위 보강

- B `HumanControl.reject/expire`는 승인과 Task가 아직 대기 중이어도 `Denied(APPROVAL_RACE)`를 반환할 수 있다. A `TaskRunner.resume_approval`은 이 비종결 응답에서 pending placeholder와 남은 모델 호출을 유지하고, 이후 실제 승인/거절 결과를 처리하도록 고쳤다. 다른 거절 뒤에는 Store Task 상태를 재조회해 HALTED/UNKNOWN/STALE 작업을 새 사용자 turn으로 열지 않는다. 연속 제안 사이에도 Task epoch를 다시 확인한다. 단, `GuardService.submit`에 `expected_epoch`가 없어 조회와 잠금 내부 제출 사이 경쟁은 남으며 B/C 계약 요청을 [포트 검토](tasks/a-port-review.md)에 기록했다.
- A `BackendBridge`는 Qt 메인 스레드와 별도 asyncio backend thread를 사용한다. 실행/사람 제어 큐, B `HumanControl` capability, 명령 접수/포화/종료 거절, 중복 ID 재실행 억제, startup 실패, 종료 미확정 상태를 A 소유 불변 메타데이터 신호로 전달한다. `BACKEND_RETURNED`는 적용 완료가 아니며 권위 있는 Store 조회가 필요하다. 실제 `__main__` 구성, C Store/Registry 시작·종료와 UI 상태 이벤트는 아직 없다.
- QTest 키 이벤트로 현황/사이드바 Return, 서버 표 화살표·Enter/Space/Escape·Tab 이동과 상세 창 포커스 복귀를 확인했다. 빈 선택에서 이전 행 상세가 열리던 결함과 표에서 연 상세가 버튼으로 포커스를 돌리던 결함을 고쳤다. 이 검증은 offscreen과 Windows Qt 100%에서 실행했으며 다른 배율, 미구현 A31–A36 화면과 T18 전체를 포함하지 않는다.
- 브리지 재검토에서 느린 승인 명령이 모든 일반 제어 worker를 차지할 때 긴급 차단이 뒤에 멈추는 경합, OS thread 시작 실패 뒤 미종료, 누적 명령 기록 한도 도달 뒤 제어 영구 거절을 재현했다. 승인/거절과 취소/긴급 차단을 bounded 큐와 예약 worker로 분리하고, thread 시작 실패를 고정 코드로 종결하며, 완료 명령 ID는 bounded 최근 기록으로 옮겼다. 이 캐시는 프로세스 내 최근 중복 억제이며 B/C의 내구적 멱등성 계약은 별도다.
- Windows 네이티브 Qt `platformName=windows`, 디스플레이 1920×1080, devicePixelRatio 1.0에서 합성 데이터로 1260×820·780×580 창을 표시·캡처했다. 최소 창에서 서버 표의 `최대 수신` 열이 밀리는 문제를 확인해, 좁은 폭에서는 ID·최대 수신·등록 상태 3열(표 viewport 490px, 가로 스크롤 0)로 바꿨다. 넓은 폭에서는 6열이 모두 보이며 가로 스크롤 0이다. 합성 캡처는 `.cache/a37-server-after-min.png`, `.cache/a37-server-after-wide.png`; 원문/비밀 자료는 없다. Windows Qt에서 UI 키보드/표시 테스트 19건 통과. 다른 실제 배율은 미시험이다.
- 전체 A 단위 검사: `.venv` Python 3.12.15, MCP 1.29.0, PySide6/Qt 6.12.0에서 `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest -q tests\unit` 결과 **83 passed**. A adapter/Runner/bridge/UI·A 테스트의 `ruff check`, `ruff format --check`와 A 소스 14개 파일의 `mypy --follow-imports=silent --ignore-missing-imports`도 통과했다. `approval_consumed / host_dispatch / wire_tools_call / receiver_effect`는 여전히 C fixture로 측정하지 못했다.

## 2026-10-12 · A31/A34/A36 독립 Qt 화면과 A37 네이티브 확인

- `ui/server_add.py`에 stdio/HTTP별 입력을 보존하는 로컬 등록 초안 폼을 추가하고 `GuardWindow`에서 `MCP 추가 초안`으로 열게 했다. JSON 문자열 인자 배열, 절대 실행 파일 경로, env 이름과 URL 형식의 **로컬 구문**만 확인한다. 미리보기와 `localDraftPrepared` 신호에는 실행 경로·인자·URL 원문을 넣지 않는다. 초안 확인은 Registry 등록이나 PENDING 확정이 아니다.
- `ui/approval_detail.py`는 미래 B의 인증된 승인 메모리 사본에서 전달받은 전체 수신처와 정규 UTF-8 인자 바이트를 `QPlainTextEdit`에 일반 텍스트로 표시한다. 비가시·bidi·혼동 문자는 코드포인트로 식별하고 원본 객체/바이트는 수정하지 않는다. 닫을 때 Qt 텍스트 사본을 비운다. 합성 HTML/링크/원격 이미지 문자열에서 Qt URL handler 호출이 없고 70,000자 수신처·인자가 잘리지 않음을 offscreen으로 확인했다. B 사본 조회/인가·승인 버튼과 C의 실제 외부 요청 계수가 없어 T18 전체 통과는 아니다.
- `ui/task_status.py`의 읽기 전용 카드와 `GuardWindow`의 에이전트 실행 화면에서 B `TaskStatus` 여섯 상태를 구분한다. `UNKNOWN_OUTCOME`은 수신측 확인 필요/기존 Task 실행 불가로 표시하고, 상태 미확인에는 제어 버튼을 제공하지 않는다. 백엔드 확정 Task 조회·명령 적용 이벤트는 아직 없다.
- Windows 네이티브 Qt `platformName=windows`, 디스플레이 1920×1080, devicePixelRatio 1.0, 창 780×580에서 등록 초안 폼의 세로 스크롤과 A34 독립 상세 창을 합성 데이터로 확인했다. A36 상태 카드 설명이 처음에는 잘려 최소 높이를 수정하고 같은 크기에서 다시 확인했다. 캡처는 `.cache/a31-form-native.png`, `.cache/a34-detail-native.png`, `.cache/a36-status-native.png`이며 비밀/실서버 자료는 없다. 다른 DPI와 실제 승인/Task 데이터는 미검증이다.
- 현재 A 단위 검사: `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest -q tests\unit` 결과 **114 passed**. Windows Qt에서 UI 관련 5개 파일 `test_ui_overview.py`, `test_ui_keyboard.py`, `test_server_add.py`, `test_approval_detail.py`, `test_task_status.py`는 **50 passed**. A adapter/Runner/bridge/UI와 단위 테스트에 `ruff check`, `ruff format --check`가 통과하고 A 소스 17개 파일에서 `mypy --follow-imports=silent --ignore-missing-imports` 오류가 없었다. 새 B/C 계약이나 실제 `approval_consumed / host_dispatch / wire_tools_call / receiver_effect` 측정은 없다.

## 2026-10-12 · A32/A33/A35 읽기 화면과 A37 좁은 창 확인

- A32 `ui/scan_status.py`는 C가 향후 제공할 공개 projection을 받아 검사 진행, required/completed/skipped/failed 분석기, 지문 상태, Registry 등록과 세션 연결을 분리해 표시한다. 서버 상세를 열 때 서버 ID와 UI 조회 토큰을 내보내고 현재 상세 창의 응답만 적용한다. 검사 완료를 통과나 관리자 활성화로 표시하지 않는다. C Scanner/Registry 조회·제어 계약이나 실제 검사 결과는 아직 없다.
- A33 `ui/approvals.py`는 B가 향후 제공할 공개 승인 요약의 6건 페이지 조회 의도를 내보낸다. 대기/이력, 검색·서버 필터·정렬·총건수, 오래된 응답·불완전 페이지·선택 소멸을 구분한다. 승인·거절 버튼이나 전체 인자 캐시는 없다. A35 `ui/call_logs.py`는 공개 DTO만으로 호출 목록·큰 일반 텍스트 상세·현재 표시 행 CSV 복사를 구성한다. 상세는 창별 토큰을 확인하며 CSV는 수식형 입력을 비활성화하고 offset 포함 ISO 시각을 쓴다. 실제 감사 조회·승인 사본·저장 계약은 없다.
- Windows 네이티브 Qt `platformName=windows`, 1920×1080, devicePixelRatio 1.0에서 합성 자료를 사용해 780×580 창의 새 세 화면을 표시·캡처했다. 승인/호출 표의 핵심 상태가 가로 스크롤 뒤로 밀리는 것을 확인해 좁은 폭에서 각각 승인 ID·서버·상태와 발생 시각·호출 ID·현재 상태가 바로 보이도록 수정했다. 서버 상세에서는 Scanner 카드를 상단으로 옮겼다. 캡처는 무시된 `.cache/a32-scan-native.png`, `.cache/a33-approvals-native.png`, `.cache/a35-logs-native.png`이며 비밀·실서버 자료가 없다. A33/A35 컴포넌트 폭 시험은 Windows Qt와 offscreen에서 각각 통과했다. 다른 DPI는 미시험이다.
- 전체 단위 검사: `$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest -q -p no:_hypothesis_pytestplugin tests\unit` 결과 **142 passed**. 새/변경 UI 9개 파일의 Ruff check·format check, A UI 소스 5개 파일의 mypy가 통과했다. 실제 `approval_consumed / host_dispatch / wire_tools_call / receiver_effect` 계수, 외부 요청 0, C Scanner·감사 연결은 측정하지 못했다.

## 2026-10-12 · A37 추가 키보드 확인

- Windows Qt `platformName=windows`, 화면 1920×1080, 논리 DPI 96, devicePixelRatio 1.0에서 780×580 합성 화면을 띄우고 실제 QTest 키 이벤트를 보냈다. A31/A33/A35/A34 화면의 Tab·Shift+Tab·방향키·Enter·Space·Escape를 검사했다. 앞 절의 같은 환경 합성 캡처와 함께 화면·입력 근거로 사용한다.

| 재현 단계 | 기대 / 관측 | 수정·재검증 |
| --- | --- | --- |
| A31 서버 ID부터 Tab으로 stdio 인자와 env 입력을 지난다 | 각 편집기에서 Tab은 다음 필드로 가야 하나 인자 편집기에 탭 문자가 들어가고 포커스가 남았다 | `args_json`과 `env_allow`에 `setTabChangesFocus(True)`를 적용했다. Tab·Shift+Tab이 인자↔env↔신뢰 경계로 이동하고 인자 문자열은 바뀌지 않았다. 방식 전환 후 HTTP URL·stdio 실행 파일도 키보드로 도달했다. |
| A33 대기/이력 탭, 검색·서버 필터·정렬·6건 페이지를 키보드로 조작한다 | 방향키가 탭과 정렬을 바꾸고, Tab 이탈이 조회 의도를 새로 내며, 다음 페이지 요청 때 기존 선택은 사라져야 한다 | 관측대로 동작했다. 로딩 동안 다음 버튼이 비활성이고 승인·거절 버튼은 없다. |
| A35 호출 표에서 아래 방향키·Enter로 두 번째 행 상세를 연 뒤 Escape, 버튼 Space로 다시 연 뒤 Escape를 누른다 | 요청은 선택한 호출 ID를 가리키고 닫으면 각각 표·버튼에 포커스가 돌아와야 한다 | 관측대로 동작했다. 네이티브 창 활성화 이벤트 처리 후 상세 본문에 포커스가 왔다. 검색으로 선택 행을 숨기면 상세 버튼이 비활성화됐다. |
| A34 상세 수신처→인자→닫기를 Tab/Shift+Tab으로 오가고 반복 Return 후 일반 Return을 누른다 | 반복 키로 동작하면 안 되고 일반 Return은 창을 닫고 원래 버튼으로 포커스를 돌려야 한다 | 관측대로 동작했다. 이 독립 상세 창에는 닫기 버튼만 있으며 원문 Qt 텍스트 사본은 닫을 때 비워졌다. |

- 수정 후 Windows 네이티브 Qt에서 `test_ui_keyboard.py` **16 passed**, 관련 5개 UI 테스트 파일 **58 passed**. `ruff check`, `ruff format --check`, A31 파일 mypy도 통과했다. 다른 DPI/모니터와 실제 B 승인 사본·C 감사 자료는 시험하지 않았다. 승인 실행 버튼과 실제 외부 요청 계수가 아직 없으므로 T18 전체 및 외부 요청 0은 인수하지 않았다.

## 2026-10-12 · A37 Qt 125%·150% 합성 배율과 초기 창 크기

- 환경: Windows 네이티브 Qt `platformName=windows`, CPython 3.12.15, PySide6/Qt 6.12.0, 물리 화면 1920×1080. OS의 실제 디스플레이 배율은 바꾸지 않고 프로세스별 `QT_SCALE_FACTOR=1.25`와 `1.5`를 사용했다. 아래 배율은 **Qt 시뮬레이션**이며 실제 125%·150% 모니터 DPI 검증을 뜻하지 않는다.
- 재현: 각 배율에서 `GuardWindow()`를 기본 크기로 생성하고 `show()`/Qt 이벤트 처리 후 `availableGeometry`와 `frameGeometry`를 비교했다. 780×580 **논리 px** 창에서는 합성 데이터로 A32 검사, A33 승인, A35 호출 화면을 두 배율에서 캡처했다. 150%에서 A31 등록 폼의 상단·최하단과 A34 긴 수신처/일반 텍스트 상세도 캡처했다. 파일은 Git에서 제외된 `.cache/a*-s125.png`, `.cache/a*-s150.png`이며 실제 서버·승인 원문은 없다.

| 배율·Qt 논리 화면 | 기대 / 수정 전 관측 | 수정 / 재검증 |
| --- | --- | --- |
| 125% · 1536×864, 가용 1536×826 | 기본 창 프레임 하단 845px로 가용 영역보다 19px 내려갔다. | 초기 크기에서 Qt 스타일 제목줄과 프레임 여유를 가용 영역에서 빼도록 수정. 프레임 1260×814px, 가용 영역 안에 들어옴. 관련 UI 테스트 85건 통과. |
| 150% · 1280×720, 가용 1280×688 | 기본 창 프레임 하단 728px로 가용 영역보다 40px 내려갔다. | 프레임 1260×678px, 가용 영역 안에 들어옴. 관련 UI 테스트 85건 통과. 900×671px A34 상세 창도 가용 영역 안에 들어옴. |
| 125%·150% · 780×580 창 | 승인 ID·서버·상태, 호출 시각·ID·현재 상태, Scanner와 관리자 활성화의 분리 문구가 보여야 한다. | 세 화면의 합성 캡처에서 해당 필드를 확인했다. 150% A31 폼은 스크롤 최하단에서 로컬 초안 확인 버튼에 도달했고, A34 긴 수신처는 말줄임 없이 가로 스크롤로 열람할 수 있었다. |

- 명령: `PYTHONPATH=src`, `QT_QPA_PLATFORM=windows`, `QT_SCALE_FACTOR=1.25` 또는 `1.5`로 `python -m pytest -q -p no:_hypothesis_pytestplugin`과 A31–A35/A37 관련 UI 단위 테스트 9개 파일을 실행했다. 수정 후 각각 **85 passed**. `QT_QPA_PLATFORM=offscreen`의 전체 `tests/unit`은 **165 passed**. 변경 UI/테스트 파일의 Ruff check·format check와 `ui/window.py` mypy는 통과했다.
- 범위: 실제 모니터 DPI 설정·다중 모니터 이동·더 작은 화면은 확인하지 않았다. 창의 780×580 최소 크기보다 가용 영역이 작은 경우에는 최소 크기를 유지한다. B 승인 사본·C Scanner/감사 연결과 T18 외부 요청 계수는 이 Qt 표시 시험에 포함되지 않는다. 계약 변경은 없다.

## 2026-10-12 · A37 창 크기 적응 재검증

- `GuardWindow` 최소 크기를 320×360 논리 px로 낮췄다. 폭이 900px 미만이거나 높이가 700px 미만이면 사이드바 대신 키보드로 여는 메뉴를 보여주고, AGT·모델 모드 표시를 다음 줄에 둔다. 현황은 미해결 확인 항목을 서버 목록 앞에 두고, 긴 공개 ID가 페이지 폭을 강제로 키우지 않게 했다.
- 승인 목록은 작은 폭에서 승인 ID·서버·상태를 유지하고 필터·페이지 이동을 세로로 배치한다. 호출 목록은 발생 날짜·시·분·초·KST, 호출 ID·현재 상태를 우선 표시하며 서버·도구와 나머지 열은 폭이 늘 때 추가한다. 좁은 표에서도 Tab은 다음 조작으로 이동하고 행은 방향키로 선택한다. 승인 선택 요약의 줄바꿈·제어·bidi 문자는 한 줄 코드포인트 표기로 바꿔 상태 줄 사칭을 막는다. 전체 승인 인자나 수신처를 이 공개 요약에 넣지 않는다.
- 서버 목록의 매우 좁은 폭에는 서버 ID·등록 상태를 남기고 최대 수신 등급 등은 상세에서 확인한다. 등록 초안 폼은 좁은 폭에서 라벨과 입력을 세로로 바꾸고 페이지 가로 스크롤 없이 최하단 확인 버튼에 도달한다. 서버·승인·호출 상세 대화상자는 부모 창과 화면의 가용 크기에 맞춰 열고 긴 내용은 내부 스크롤로 열람한다.
- Windows 네이티브 Qt 100%에서 합성 현황·승인·호출·서버·등록 초안 화면을 320×360/400, 540×580, 900×700, 1100×750으로 표시해 요청 크기 유지, 페이지·표 가로 스크롤 0, 하단 버튼 접근을 확인했다. 상세 창 3종은 320×400 부모에서 296×376으로 열렸다. Windows Qt의 `QT_SCALE_FACTOR=1.25`와 `1.5`를 별도 프로세스에 적용한 320×400·540×580 확인도 통과했다. 이는 **Qt 배율 시뮬레이션**이며 실제 OS DPI 변경이나 다중 모니터 이동 검증은 아니다. 합성 캡처는 OS 임시 폴더에만 두었고 Git에 넣지 않았다.
- 명령: `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `python -m pytest -q -p no:_hypothesis_pytestplugin -p no:cacheprovider tests/unit` 결과 **201 passed**. 변경 UI의 Ruff check·format check, 8개 UI 소스의 mypy도 통과했다. UI 정적 검토에서 긴 ID 가로 팽창·Tab 순환·낮은 창 버튼 겹침·좁은 로그 시간의 초 생략을 찾아 수정했다. 별도 보안 검토에서는 공개 승인 요약의 상태 사칭을 찾아 이스케이프 후 합성 입력으로 재확인했다. C의 실제 계수, B/C 제품 연결, 실제 DPI는 이번 UI 시험에 포함되지 않는다.
