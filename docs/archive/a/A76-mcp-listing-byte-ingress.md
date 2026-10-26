# A76 · MCP 목록의 파싱 전 바이트 제한과 파이프 연결

2026-10-18. [#21 C 회신](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247463861)에 따라 `mcp_ingress.py`와 `mcp_transport.py`에 A 소유 수신 제한을 구현했다. C가 파이프를 제공하고 A가 파싱 전에 제한한다. 관련 문서 PR은 [#24](https://github.com/hsminu/MCP-Runtime-Guard/pull/24)이며, 이 작업에서 PR을 생성하거나 B·C·공통 계약을 수정하지 않았다.

`ToolListLimits`에 기본 페이지 1MiB·조회 전체 8MiB 설정을 추가했다. LF 프레이밍을 제거한 JSON-RPC 원시 바이트에 공백·envelope를 포함해 적용하며, 파싱 후 재직렬화로 대신하지 않는다. 페이지마다 누적 예산을 초기화하지 않고 새 조회에서만 초기화한다. 실제 목록 응답의 ID를 확인해 누적량을 차감한다. 응답을 파싱하기 전에는 ID를 신뢰할 수 없으므로 조회 중의 다른 알림도 페이지·남은 예산 중 작은 값 이하인 경우만 받아들이는 보수적 제한이 있다.

`validated_stdio_stream_factory(read_bytes, write_bytes, drain)`는 검증된 외부 파이프를 SDK 세션에 연결하는 A 내부 입구다. 실행·경로·stderr 격리·프로세스 종료는 구현하지 않았다. 기존 SDK 실행 경로는 시연용 일반 호출에 남겨 두되, raw ingress가 없는 실제 SDK 세션은 목록 조회를 보내기 전에 `TOOLS_LIST_INGRESS_REQUIRED`로 거부한다. 직접 주입한 테스트 세션은 의미 검증용이며 원시 바이트 제한의 증거가 아니다.

오류·취소·시간 초과는 고정 사유의 미완료 목록과 연결 실패로 유지한다. 부분 목록을 완료·안전으로 바꾸거나 자동 재전송하지 않는다. 늦은 실패도 worker의 신규 호출을 막는다. 겹친 조회가 기존 scope를 종료하지 않으며, 늦은 옛 ID와 SDK가 강제 변환할 수 있는 별칭 ID·혼합 envelope를 구분한다.

6.1 high가 구현했고 루트가 지시·검증했다. 별도 astra medium 두 명의 엄격한 QA에서 동시 ping이 목록 요청 ID를 선점해 다른 응답 크기를 차감하는 경합을 재현했다. 실제 SDK의 ID 배정 직전에 같은 task에서 등록하도록 수정하고 회귀 테스트와 독립 재검증을 통과했다. 이는 설치된 MCP 1.29.0의 `send_request`가 첫 await 전에 ID를 배정하는 동작에 의존하므로 SDK 변경 시 다시 확인해야 한다.

검증: 신규 ingress 단위 85개(이 파일의 연결부 46개·A77 HTTP 39개), 관련 6파일 회귀 201개 통과. 최종 전체 **2,703개·subtest 20개 통과**, 기존 경고 5개. Ruff 전체·format 147파일·mypy 68소스 통과. 두 QA의 최종 미해결 지적은 없었고 루트도 독립 재현 스크립트들을 재실행했다. 로컬 ignored 근거는 `.test-artifacts/a76-final-suite.log`, `astra_protocol_repro.py`, `lifecycle_close_repro.py`다.

관련 I6·I8·I9·I10, T10·T14–T16·S04 의도의 A 단위 회귀다. 실제 C launcher/프로세스·경로 인수나 C5.8 독립 집행 검증을 완료한 것은 아니다. C 구현과 Registry 연결, B의 SCAN-01 계약은 계속 대기한다.
