# A08 · HTTP 연결 직전 C canonical endpoint 재검사(C2.5)

2026-10-28. A08·A12의 HTTP adapter(`http_stream_factory`)는 endpoint 모양을 자체 규칙으로만 확인했다. HTTPS, 또는 `127.0.0.1`·`::1` 리터럴 HTTP이고 자격증명·query·fragment가 없는지까지였다. C가 endpoint 정규형 규칙(`security/endpoints.py`의 `canonical_endpoint`, C2.5)을 [PR #50](https://github.com/hsminu/MCP-Runtime-Guard/pull/50)으로 main에 넣었다. 그래서 네트워크로 나가기 직전의 두 번째 검사를 C 규칙으로 바꿨다.

## 한 일

- `adapters/mcp_transport/streams.py`의 `_endpoint_url`이 C `canonical_endpoint`를 부른다.
  - 입력이 이미 C 정규형이어야 통과한다. 대문자, 기본 포트 명시, `..`, 퍼센트 인코딩 경로처럼 C가 정규화할 수 있는 모양도 여기서는 고치지 않고 거부한다. 설정과 등록에는 정규형만 저장하기로 했기 때문이다(C2.5).
  - 정규형 문자열을 httpx가 다른 모양으로 요청하게 되면 그것도 거부한다. 요청마다 이 URL과 같은지 비교하기 때문이다(`_request_constraint`).
  - 실패는 고정 문구 "Invalid HTTP MCP endpoint" 하나다. 입력 원문도 C의 `EndpointError` 코드도 넘기지 않는다.
- 허용 범위가 C 규칙을 따라 바뀌었다.
  - HTTP는 loopback IP 리터럴만 받는다. 이제 `127.0.0.0/8` 전체(예: `127.0.0.2`)와 `[::1]`이다. 이전에는 `127.0.0.1`과 `::1`만 받았다.
  - HTTPS라도 C가 금지한 주소는 거부한다. link-local(`169.254.169.254`), IPv4-mapped(`::ffff:127.0.0.1`), 16진수·정수 IPv4 표기가 그 예다.
- `http_stream_factory` 설명문을 바꿨다.

## 검증

- `tests/unit/test_mcp_http.py`
  - 거부 목록에 정규형이 아닌 URL 7개를 더했다. 대문자 scheme, 대문자 host, 빈 경로, 기본 포트 443·80 명시, `..`, `%2e%2e`다.
  - C가 금지한 주소 4개도 더했다.
  - 거부는 모두 같은 고정 문구이고 원문이 없다.
  - 정규형 5개는 그대로 통과한다. IDNA host, `127.0.0.2`, `[::1]`, 루트 경로를 포함한다. httpx가 요청할 URL 문자열이 입력과 같다.
  - 기존 loopback 왕복, JSON/SSE, 재전송 차단 테스트가 그대로 통과한다.
- 전체 결과는 A132·A133·A134와 같은 실행이다(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`).
  - 단위 5,202개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(238개 파일)·mypy(102개 소스 파일) 통과

## 남은 것

- 설정 파일을 읽을 때(A129)는 아직 endpoint를 C 정규형으로 검사하지 않는다. HTTP 서버 설정의 서버별 URL 검사는 B의 다음 PR이다([#58](https://github.com/hsminu/MCP-Runtime-Guard/pull/58) 답변). 그게 들어오면 첫 검사는 그쪽이고 여기는 두 번째 검사로 남는다.
- 등록 화면(`_url_error`)과 A128 붙여넣기 채우기는 아직 C 입구를 쓰지 않는다. C 리뷰([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52#issuecomment-6463307897))가 안내한 대로 `normalize_endpoint`로 정규형을 보여 주고 거부 코드(`ENDPOINT_REJECT_CODES`)별 문구를 쓰는 일이 남았다.
- 이름 해석 뒤 주소 검사와 인증 확장은 하지 않았다. 독립 fixture 인수는 C 몫이다.
- PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
