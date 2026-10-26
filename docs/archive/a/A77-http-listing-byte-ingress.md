# A77 · HTTP JSON·SSE 목록의 파싱 전 수신 제한

2026-10-18. A76의 조회 예산을 `mcp_http_ingress.py`의 HTTP 수신 계층에 연결했다. 응답 본문을 받는 단계에서 JSON 또는 SSE 이벤트 payload의 압축 해제 후 바이트를 제한한 뒤 SDK에 전달한다. SSE 연결 전체에 페이지 상한을 적용하지 않으며, 여러 data 줄 사이의 개행과 JSON 공백·envelope는 계산에 포함한다. HTTP 요청 재전송은 추가하지 않았다.

gzip·deflate는 제한된 크기로 압축을 풀어 HTTPX의 일괄 압축 해제가 제한 계층 앞에서 실행되지 않게 했다. 조회 중에는 identity/gzip/deflate만 지원하고, SSE는 UTF-8만 허용한다. SSE framing 메타데이터에도 별도 보수적 상한을 적용한다. raw-deflate의 zlib 헤더 오인식 fallback은 첫 출력 전 압축 입력 65,536바이트까지만 보관하며, 이미 출력했거나 이 범위를 넘긴 뒤에는 재해석하지 않는다. 일반 도구 결과에는 목록 크기 상한을 적용하지 않는다.

6.1 high 구현 후 astra medium 프로토콜 검토자가 실제 설치 SDK와 비교해 GET의 Content-Type 부분 문자열 허용에 따른 상한 우회, 빈 data 시작 이벤트 오판, 유효 raw-deflate의 헤더 충돌을 재현했다. 잘못된 media/charset 거부, 빈 시작 이벤트의 파싱·차감 생략, 제한된 raw-deflate fallback으로 수정하고 독립 재검증을 통과했다.

다른 astra medium 검토자는 응답 공개 후 close 오류/시간 초과가 발생해 목록 완료·READY를 먼저 내보내는 경합을 재현했다. 이제 목록 응답을 담은 POST의 마지막 SSE 이벤트와 JSON 본문은 자기 응답의 close 성공 뒤 SDK에 공개한다. close는 1초 상한이며 예상 I/O 오류와 시간 초과를 고정 `TOOLS_LIST_INGRESS_CLOSE_FAILED`로 보존한다. 오래 유지되는 GET은 페이지마다 닫지 않는다. 늦은 GET 실패는 worker 감시와 동기 admission 검사로 신규 호출을 막고 정리 미확인 상태를 보존한다. 그 전에 완료된 목록의 기록은 유지하되 실패한 worker를 사용 가능하게 두지 않는다.

검증: HTTP 단위 39개를 포함한 신규 ingress 85개, 관련 회귀 201개 통과. 실제 SDK 세션을 사용한 합성 JSON/SSE·압축·상한 초과 검증에서 초과 목록의 파서 진입 0을 확인했다. 독립 worker 재현에서 delayed OSError와 timeout 모두 목록 미완료·FAILED·`SessionCloseFailed`, 종료 후 잔여 task 0이었다. 루트도 동일 재현을 실행했다. 최종 전체 2,703개·subtest 20개 통과(기존 경고 5개), Ruff·format 147파일·mypy 68소스 통과. 두 QA 최종 미해결 지적 없음.

검증 환경은 MCP 1.29.0, HTTPX 0.28.1, httpx-sse 0.4.3, AnyIO 4.15.1, Pydantic 2.14.0이다. 관련 I6·I8·I9, T14–T16·S04 의도의 A 단위 검증이며 실제 배포 endpoint의 검증이나 C5.8 독립 인수를 대신하지 않는다. B·C·공통 설정은 수정하지 않았고 PR은 생성하지 않았다. 분담 근거는 [#21 회신](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247463861)과 문서 [PR #24](https://github.com/hsminu/MCP-Runtime-Guard/pull/24)다.
