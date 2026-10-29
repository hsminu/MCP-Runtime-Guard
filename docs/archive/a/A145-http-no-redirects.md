# A145 · MCP HTTP 같은 URL 리다이렉트 재전송 차단

2026-10-29. PR: 미정.

## 변경

- `src/guard_app/adapters/mcp_transport/streams.py`의 유일한 HTTP 클라이언트 생성 위치 `_http_client`에 `max_redirects=0`을 추가했다. `follow_redirects=False`는 유지한다. `http_stream_factory`가 이 클라이언트를 SDK에 전달하므로 요청별 `follow_redirects=True`에도 적용된다.
- `tests/unit/test_mcp_http.py`의 기존 `ProtocolProbe`·`serve_loopback`을 사용했다. 첫 tools/call만 같은 URL로 307/308을 반환하고 다음 요청은 성공하는 대조 서버다.
- 팩토리가 SDK에 전달하는 실제 클라이언트에서 POST의 `follow_redirects=True`를 강제했다. 각각 `TooManyRedirects`, 실제 POST 1회, tools/call 1회, 부작용 0회다.
- 기존 worker 종료 시험에 같은 URL 307/308을 추가했다. 실제 MCP transport의 tools/call은 `WorkerInterrupted`, worker는 FAILED, 후속 호출은 `WorkerClosed`다. dispatch·wire 각각 1회이며 정상 종료를 확인했다. 기존 다른 URL 리다이렉트·401·500도 유지했다.
- 제품·시험 변경은 요청한 두 파일뿐이다. 별도 보고 파일 대신 이 기록에 현황 문안을 포함했다. 작업 전부터 있거나 동시 작업 중 생긴 다른 파일 변경은 수정하지 않았다.

## 설치 코드 근거

설치 버전은 `httpx 0.28.1`, `mcp 1.29.0`이다(`.venv/Scripts/python.exe -m pip show httpx mcp`).

- `.venv/Lib/site-packages/httpx/_client.py:1619–1623`: 요청별 `follow_redirects`가 명시되면 클라이언트 기본값 대신 사용한다.
- 같은 파일 `1685–1689`: `_send_handling_redirects`의 반복문 시작에서 `len(history) > self.max_redirects`이면 `TooManyRedirects`를 던진다.
- 같은 파일 `1691–1694`: 요청 hook과 실제 `_send_single_request`는 위 검사 뒤에 실행된다.
- 같은 파일 `1704–1708`: 첫 리다이렉트 응답을 history에 추가하고 본문을 읽은 뒤 반복한다. `max_redirects=0`이면 다음 반복에서 `1 > 0`이므로 두 번째 요청 hook·전송 전에 멈춘다. 첫 요청 자체를 막는 설정은 아니다.

관련 기준: I6(실패 처리), I8(결과 불명 쓰기 미재전송), T15·T16, 개발문서 9.1–9.4, Transport 계약·URL-01. 결과 불명 쓰기 미재전송의 직접 불변식 ID는 I8이다. S02의 URL 정규화 검증은 기존 구현을 유지하며 이번 작업에서 범위를 넓히지 않았다.

## 실행 검증

Git Bash, 로컬 `.venv`, 시험은 `QT_QPA_PLATFORM=offscreen`으로 실행했다.

| 명령 | 결과 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/unit/test_mcp_http.py` | 22 passed |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/unit/test_mcp_http.py tests/unit/test_mcp_transport.py` | 복원 후 60 passed |
| `.venv/Scripts/python.exe -m ruff check src/guard_app/adapters/mcp_transport/streams.py tests/unit/test_mcp_http.py` | 통과 |
| `.venv/Scripts/python.exe -m ruff format --check src/guard_app/adapters/mcp_transport/streams.py tests/unit/test_mcp_http.py` | 2 files already formatted |
| `.venv/Scripts/python.exe -m mypy` | 114 source files 통과 |
| `.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini` | 7 kept, 0 broken |
| `git diff --check` | 통과 |

시험 실행마다 FastMCP의 `lifespan` 미해결 forward reference에 대한 `IncompleteFieldDefinitionWarning` 1개가 있었다. 전체 단위 시험·C 독립 인수는 실행하지 않았다.

## 회귀 대조

제품 파일에서 `max_redirects=0` 줄만 제거하고 `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/unit/test_mcp_http.py -k factory_blocks_same_url`을 실행했다. 실행 뒤 `finally`에서 원래 바이트를 복원하고 바이트 일치를 확인했다.

| 신규 시험 | 정상 | 제한 제거 |
| --- | --- | --- |
| 강제 follow_redirects=True, 같은 URL 307 | POST 1회, TooManyRedirects | POST 2회, `assert 2 == 1`로 실패 |
| 강제 follow_redirects=True, 같은 URL 308 | POST 1회, TooManyRedirects | POST 2회, `assert 2 == 1`로 실패 |

회귀 실행 결과는 2 failed, 20 deselected, exit 1이다. 설정 복원 뒤 위의 관련 시험 60개가 통과했다.

## 남은 한계와 인계

- mcp 1.30.0 자체로는 돌리지 않음(C의 고정 변경 PR에서 A144로 재확인).
- `gh pr view 73 --repo hsminu/MCP-Runtime-Guard --comments`는 api.github.com 연결 권한 제한으로 실패했다. 댓글 원문은 확인하지 못했고, 사용자 제공 요지와 로컬 A144·A145 설명을 근거로 작업했다.
- B는 transport 변경 리뷰, C는 A145 main 반영 뒤 mcp 고정 변경 PR을 진행한다. 계약/API 변경은 없다. 커밋·push·main 반영은 수행하지 않았다.
- 현황 완료 줄 제안: `- [x] A145 HTTP 클라이언트 max_redirects=0과 같은 URL 307/308 재전송 차단 · [기록](archive/a/A145-http-no-redirects.md)`
- 현황 남은 줄 제안: `- [ ] A144 mcp 1.30.0 고정 PR에서 SDK 내부 의존 세 곳과 같은 URL 307/308 차단 재확인 — 막힘: A145 main 반영 후 C 고정 변경 PR`
