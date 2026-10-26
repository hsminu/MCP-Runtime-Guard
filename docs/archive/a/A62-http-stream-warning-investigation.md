# A62 · A60 HTTP 스트림 경고 원인 조사

## 재현과 원인

기준 A `74cb30a`, CPython 3.12.15, MCP SDK 1.29.0. lock 기준 환경 동기화 뒤 수정 전 전체 단위 테스트는 2,426개·subtest 20개가 통과했다. 기존에 보고된 B 테스트의 간헐 실패는 이 실행에서 재현하지 못했다.

`PYTHONWARNINGS=error::ResourceWarning`과 `-W error::pytest.PytestUnraisableExceptionWarning`을 적용하면 `tests/unit/test_mcp_http.py`는 47개 통과·4개 실패였다. 실제 FastMCP HTTP 시나리오 4개만 다시 실행해도 3개 실패·1개 통과로 스트림 경고가 재현됐다.

테스트 전용 진단 플러그인으로 AnyIO 스트림 생성 스택과 소멸 시 `_closed`를 기록했다. 발견한 미닫힘 수신 스트림 6개의 생성 위치는 모두 설치된 MCP SDK의 서버 측 코드다.

| 위치 | 생성 지점 | 개수 |
| --- | --- | --- |
| `mcp/server/streamable_http.py:710` | `_handle_get_request`의 SSE 응답 스트림 | 2 |
| `mcp/server/streamable_http.py:614` | `_handle_post_request`의 SSE 응답 스트림 | 4 |

두 경로는 `EventSourceResponse(content=sse_stream_reader, ...)`에 수신 스트림을 넘기며, 명시적인 `sse_stream_reader.aclose()`는 `except Exception` 안에만 있다. 관측된 경고는 이 수신 스트림의 미닫힘 소멸에서 발생했다. 이번 진단에서 앱 HTTP client가 만든 스트림의 미닫힘은 기록되지 않았다.

[구조화된 근거](../evidence/a60-http-stream-origins.json)에 명령·환경·계수·할당 위치를 남겼다. 진단 플러그인은 테스트 프로세스에만 적용했고 제품 코드와 설치된 SDK 파일은 바꾸지 않았다.

## 판단과 남은 일

- 지연된 GC 때문에 할당한 테스트와 경고로 실패하는 테스트 이름이 다를 수 있다. 경고가 보고된 테스트 이름만으로 원인을 판단하면 안 된다.
- 이 결과를 과거 B assertion 실패의 원인으로 확대하지 않는다. 그 실패는 구체적인 재현 근거가 아직 없다.
- 경고 억제·전역 monkeypatch·임의 SDK 버전 변경은 하지 않았다. SDK 수정 버전 검증 또는 C의 정식 HTTP fixture 인수에서 별도 처리해야 한다.
- A60 전체를 해결 완료로 표시하지 않는다. HTTP 경고의 원인 조사만 완료했다.
- 관련: I8·I9, T14–T16·T19 transport 종료 검증의 보조 근거. C 독립 인수 완료가 아니다.
- PR 미생성. A 로컬 조사 기록이며 SDK 유지보수자나 B·C에게 메시지를 보내지 않았다.
