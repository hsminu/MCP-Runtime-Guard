# A09·A10 · tools/list 수집 경로와 개수·시간·깊이 상한

## 한 일

- `src/guard_app/adapters/mcp_transport.py`에 tools/list 수집 경로를 새로 만들었다. 전에는 없었다.
  - `ToolListLimits`: contracts 2026-10-15 Scanner 운영 상한의 기본값을 설정 가능하게 담았다. 서버별 도구 256개, 목록 32페이지, Tool JSON 중첩 64단계, 페이지 10초, 수집 30초다.
  - `collect_tool_definitions(session, limits)`: cursor로 페이지를 따라가며 Tool 객체를 서버가 보낸 필드 그대로 고정한다. 확장 필드를 포함하고 `exclude_unset`을 쓴다.
    - 상한을 넘거나 실패하면 미완료 `ToolListing(complete=False, tools=(), reason=고정 코드)`를 돌려준다. 정의를 자르거나 일부만 남기지 않는다. 도구 0개·완료·안전으로 취급하지 않는다.
    - 고정 코드: `TOOLS_LIST_TOO_MANY_TOOLS`, `TOOLS_LIST_TOO_MANY_PAGES`, `TOOL_DEFINITION_TOO_DEEP`, `TOOLS_LIST_TIMEOUT`, `TOOLS_LIST_FAILED`, `TOOLS_LIST_MALFORMED`. A가 제안하는 이름이며, C Scanner·Registry의 코드와 맞출 대상이다.
    - 서버 오류 원문은 담지 않는다. `repr`은 개수와 코드만 보여준다.
  - `ServerWorker`·`McpTransport`의 `tool_list_limits` 옵션: 켜면 `initialize()` 뒤, READY 전에 수집한다.
    - 이때 initialize는 `startup_timeout_s`(기본 10초, 결정된 초기화 10초)로 따로 묶는다. 시작 대기 전체는 초기화 + 수집 상한 + 1초다.
    - 수집이 미완료여도 worker는 READY가 된다. 미완료는 Registry·Scanner가 경고로 다룰 결과이고, 판정은 transport가 하지 않는다.
    - 결과는 `McpTransport.tool_listing(server_id)`로 조립 코드에만 준다. 기본값은 꺼짐이다. 기존 조립(examples)은 바꾸지 않았다.
- 하지 않은 것:
  - 1MiB 페이지·8MiB 누적 바이트 상한: contracts 2026-10-13의 측정 기준 2는 파싱 전 JSON-RPC 바이트다. 그런데 MCP SDK가 stream 리더 안에서 먼저 파싱한다. C stream ingress가 필요하다(GitHub #21 5번). `ToolListLimits` docstring에 확장 지점으로 적었다.
  - 연결 직후 등록 지문과 대조하고 PENDING으로 내리는 일(A15·A16): C Registry(C4.10·C4.12)와 FP-01이 필요하다.

## 실제 검증

- `tests/unit/test_mcp_tool_listing.py` 19개 통과.
  - 여러 페이지 완료, 확장 필드·float 보존, repr 비노출
  - 도구 수 초과 시 부분 정의 없음, 기본 256개 경계(256 완료·257 초과), 페이지 상한(같은 cursor 반복 포함)
  - 깊이 64 허용·65 거부
  - 페이지·전체 시간초과, 서버 오류의 원문 비노출, 형식 오류·surrogate
  - 잘못된 상한 거부
  - worker 순서(enter → initialize → list → call), 미완료여도 호출 가능, 기본 꺼짐(list 호출 0), 수집을 켜도 initialize 개별 상한 유지
  - 합성 HTTP 서버(`examples/http_spike_server`) 실제 왕복 수집 완료(receiver_effect 0)
- 기존 transport·HTTP 테스트를 합쳐 108개 통과.
- `uv sync --locked` 환경에서 `ruff check`·`ruff format --check`·`mypy` 통과, `pytest -q` 2388 통과(경고 5, subtest 20).

## 남은 한계

- 수집 정의는 SDK가 파싱하고 다시 직렬화한 것이라, 바이트 단위 원문은 아니다. 지문 정규화는 FP-01(B·C)에서 정한다.
- 페이지 시간초과 뒤에 SDK 요청을 취소하므로, 세션이 정상인지는 다음 호출에서 드러난다. 미완료 결과를 Registry가 어떻게 처리할지(경고 또는 PENDING)는 C 결정이다.
- 실제 사용자 등록 서버로는 검증하지 않았다. 합성 fixture와 합성 HTTP 서버만 썼다.

## 관련

- I6(판단 불가를 완료로 취급하지 않음), I10의 기반(지문 대조는 후속), T17(검사기 미완료 경고, C), contracts 2026-10-13·10-15 수신·운영 상한 결정, GitHub #21 5번.
- 커밋: 이 기록과 같은 커밋. PR 없음(미푸시).
