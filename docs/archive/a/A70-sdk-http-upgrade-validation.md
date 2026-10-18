# A70 · HTTP SDK 수정 버전 격리 검증

2026-10-18. A60의 SDK HTTP 경고에 대해 제품 환경을 바꾸지 않고 MCP 1.30.0을 별도 디렉터리에 설치해 비교했다. 기존 `.venv`의 MCP 1.29.0, 공통 설정·lock, C fixture는 수정하지 않았다.

## 확인한 변경

공식 [1.29.1→1.30.0 변경](https://github.com/modelcontextprotocol/python-sdk/compare/v1.29.1...v1.30.0)의 `src/mcp/server/streamable_http.py`에서 POST와 GET SSE 수신 스트림의 종료가 예외 처리 안에서 `finally`로 이동했다. 1.29.1 소스에는 해당 수정이 없다. 이는 기존 SSE 수신 스트림 미닫힘 관측과 일치한다.

[1.30.0 릴리스](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0)는 HTTP redirect 처리, 서버 세션 만료·개수 제한, OAuth 동작도 바꾼다. 특히 SDK는 주입한 HTTPX client의 `follow_redirects` 설정을 더 이상 그대로 사용하지 않는다. 현재 A adapter의 별도 endpoint 검사와 기존 redirect 회귀는 유지되지만, 경고 수정만을 이유로 버전을 바로 올릴 수는 없다.

## 실제 검증

CPython 3.12.15, Windows. `python -m pip install --no-deps --target .test-artifacts/a70-sdk-130 mcp==1.30.0`으로 SDK만 격리했고 나머지 의존성은 기존 환경을 사용했다. 이는 새 lock 전체 조합의 인수가 아니다.

`PYTHONWARNINGS=error::ResourceWarning`과 `-W error::pytest.PytestUnraisableExceptionWarning`을 함께 적용했다.

| 범위 | MCP 1.29.0 | 격리 MCP 1.30.0 |
| --- | --- | --- |
| `test_mcp_http.py -k real_fastmcp`의 JSON/SSE·stateful/stateless 4개 | 1 통과·3 실패, 수신 스트림 ResourceWarning | 4 통과, 경고 없음 |
| HTTP 단위 51개 전체 | 이번 비교에서 전체 재실행하지 않음 | 최초 50 통과·1 실패, 소켓/Proactor transport 미닫힘 |
| HTTP 단위 전체, `-X tracemalloc=10` | 실행하지 않음 | 테스트 본문 51 통과지만 종료 시 같은 종류의 경고로 **프로세스 exit 1** |

tracemalloc의 소켓 할당 스택은 AnyIO `connect_tcp` → asyncio `create_connection` → `_connect_sock`을 가리켰다. 해당 경고를 SSE 메모리 스트림 경고와 같은 원인이라고 결론내리지 않는다. GC 시점에 따라 실패 테스트나 종료 단계가 달라졌으며, 소켓 소유 경로의 추가 조사가 남는다. 기존 B assertion 간헐 실패의 원인도 확정하지 않았다.

명령 출력은 로컬 ignored `.test-artifacts/a70-sdk-129-fastmcp.log`, `a70-sdk-130-fastmcp.log`, `a70-sdk-130-tests.log`, `a70-sdk-130-tracemalloc.log`에 있다. 경고 억제, 설치된 SDK 수정, 제품 monkeypatch는 하지 않았다.

## 완료 범위와 의존성

A가 할 수 있는 수정 버전 조사·격리 비교를 완료했다. A60 전체 경고 해소는 완료가 아니다. 공통 의존성 변경은 main 대상 검토와 C 조합 인수, 남은 소켓 종료 경고 검증 후 결정해야 한다. 관련 I8·I9, T14–T16·T19의 보조 근거이며 C 독립 인수를 대체하지 않는다. PR은 생성하지 않았다.
