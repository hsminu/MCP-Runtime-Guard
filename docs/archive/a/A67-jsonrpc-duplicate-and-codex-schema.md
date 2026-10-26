# A67 · JSON-RPC 중복 요청 방어와 Codex 0.162.1 형식 확인

2026-10-18. 진행 중인 동일 서버 요청 ID가 두 번 들어오면 처리기를 중복 실행하던 경로를 수정했다. 처리기 task 예약 전에 ID를 등록하고, 응답 전 중복은 `PROTOCOL_ERROR`로 채널을 닫는다. 정수·문자열 ID 및 송신·수신 ID 공간은 분리하며 완료된 요청 ID는 다시 사용할 수 있다. 완료 callback 지연·처리기 시작 전 취소·처리기 내부 close에서도 추적을 정리한다. 오류에 요청 원문을 싣지 않는다.

## 버전 근거

로컬 npm 패키지 메타데이터에서 0.162.1을 확인하고 공식 `rust-v0.162.1` 태그의 commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`를 대조했다.

- [rpc.rs](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/rpc.rs): 모든 envelope에 `jsonrpc` 필드가 없고 RequestId는 문자열 또는 i64다. 기존 `version_field=False` 지원과 일치한다.
- [common.rs](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/protocol/common.rs): `item/tool/call`은 DynamicToolCallParams/Response를 사용한다.
- [요청 schema](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/schema/json/DynamicToolCallParams.json): arguments·callId·threadId·tool·turnId 필수, namespace 선택. callId와 JSON-RPC request ID는 별개다.
- [응답 schema](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/schema/json/DynamicToolCallResponse.json): contentItems·success 필수. contentItems는 inputText/inputImage/inputAudio 형식이다.
- [thread.rs](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/protocol/v2/thread.rs): environments·dynamicTools는 experimental 필드라 기본 생성 JSON만으로 확인하면 빠질 수 있다.

## 검증과 한계

`python -m pytest -q tests/unit/test_jsonrpc_client.py -W error`: **39 통과**. 숫자·문자 ID, 두 envelope 모드, 처리기 시작 전 중복, 완료 후 재사용, 송수신 ID 충돌, 종료 정리를 검증했다. 해당 파일 Ruff·format·mypy 통과. 합성 handler 진입은 중복 요청에서 최대 1회이고 실제 도구 dispatch는 하지 않는다. 관련 I6·I9, T39–T46의 연결부 사전 검증이다.

실제 Codex 프로세스·schema generator·stdio 프레이밍·L1/L2·모델·Guard 중계는 실행하지 않았다. 이 adapter는 메서드별 검증을 구현하지 않으며 해당 연결은 B·C 계약 후 세션 관리자에서 한다. 다른 담당 파일·공통 설정은 변경하지 않았고 PR은 생성하지 않았다.
