# A73 · Codex 파이프의 A 내부 메시지 프레이밍

2026-10-18. C의 [#21 실행기 회신](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247401327)에 맞춰, 주입된 비동기 read/write/drain과 기존 JsonRpcClient 사이에 `adapters/codex_stream.py`를 추가했다. C 실행기의 최종 반환형을 정의하지 않는 A 내부 연결부다.

Codex 0.162.1의 공식 commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`, [stdio.rs](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-transport/src/transport/stdio.rs#L68)의 stdin lines와 출력 JSON 뒤 LF 추가·write_all을 확인했다. 실제 Codex 실행이나 schema generator 실행은 하지 않았다.

수신은 파싱 전에 버퍼를 `max_frame_bytes + 1`로 제한한다. 분할된 UTF-8 바이트와 연속 메시지를 처리하며 빈 줄·과대 메시지·미완성 EOF·read 계약 위반은 고정 오류로 닫는다. CRLF의 CR은 JSON 공백으로 남고 크기 상한에 포함한다. JSON/UTF-8 자체 검증은 기존 JsonCodec가 맡는다. 송신은 LF를 추가하고 write/drain을 직렬화하며, 실패·취소 뒤 자동 재전송하지 않는다.

`close()`는 로컬 대기를 깨우고 이후 송수신을 거부한다. 원본 스트림·프로세스 종료나 파일 삭제는 하지 않는다. 취소를 무시하는 주입 함수는 끝날 때까지 추적하고 늦은 원시 예외를 회수하지만, 실제 자원 정리 완료라고 주장하지 않는다.

검증: 새 `test_codex_stream.py` 35개와 기존 JSON-RPC 39개, 합계 **74 통과**(`-W error`). 정확한 크기 경계, 분할/연속 수신, malformed JSON, EOF, 송신 직렬화, read/write/drain과 close 경합, 취소 무시 뒤 late error, JSON-RPC 요청/응답/알림/서버 요청, send deadline 뒤 write 1회·재시도 0을 합성 바이트 계수로 확인했다. Ruff·format·mypy 통과.

관련 I6·I8·I9, T39–T46 연결부의 사전 검증이다. 실제 프로세스·Guard 도구 dispatch·wire 수신 부작용·L1/L2 인수는 미실행이다. 이 모듈은 Codex 프레이밍이며 C의 관리 대상 MCP 수신 상한 구현을 대체하지 않는다. B·C·공통 설정과 기존 JsonRpcClient API는 변경하지 않았다. PR은 생성하지 않았다.
