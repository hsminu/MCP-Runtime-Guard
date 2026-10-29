# A140 · 서버별 목록 응답 뒤 변경 알림을 대조 전에도 차단

2026-10-29. [#30 결정 기록](https://github.com/hsminu/MCP-Runtime-Guard/issues/30#issuecomment-6473122405)의 A 구현이다. 사용자가 이 인계 대화에서 서버별 목록 수신 뒤 알림부터 차단하도록 명시했다. 기존 A100 완료 기록은 당시 동작과 한계를 그대로 보존한다.

## 한 일

- 기존에는 모든 서버의 연결·목록 대조·Registry 기록이 끝난 `seal` 뒤 알림만 차단했다. 먼저 목록을 받은 서버가 다른 서버를 기다리는 동안 변경을 알려도 무시할 수 있었다.
- `sdk_session`은 transport가 배정한 `tools/list` 요청 ID의 응답을 SDK 수신 순서에서 식별하고, 서버별 수신 사실만 조립부에 알린다. 응답과 알림이 같은 바이트 묶음에 있어도 목록 수집 coroutine이 다시 실행될 때까지 기다리지 않는다.
- 설치된 mcp 1.29.0의 `BaseSession._receive_loop`가 응답마다 `_handle_response(SessionMessage)`를 기다리고 다음 알림을 처리하는 코드와 시그니처를 확인했다. 이 메서드의 override는 부모 처리를 그대로 호출하며 새 예외 포착 경계를 만들지 않는다.
- `McpTransport.on_listing_received` → `LiveCheckedRegistry.listing_received`로 서버별 수신 사실을 기억한다. 첫 페이지 이후 알림도 이미 받은 정의를 오래되게 만들 수 있으므로 차단한다.
- 첫 응답 전 알림은 기존처럼 건수만 센다. 그 뒤 알림은 전체 대조 전이어도 해당 서버만 이번 실행 동안 PENDING으로 취급하고, `seal`이 이 차단을 지우지 않는다.
- 새 호출은 `REG_NOT_ACTIVE`로 예약 전 거부, 대기 승인은 기존 재평가에서 무효, 새 세션 목록에서는 해당 도구 제외다. Registry 저장·새 Reason·자동 재조회/재전송은 추가하지 않았다. domain/ports와 B·C 소유 파일은 바꾸지 않았다.

## 실제 검증

Windows, Python 3.12.15, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`. 코드 커밋 `94c0668`이며 A115·A136 `4c0aa43`과 인계된 main 병합 이력을 포함한다.

- 새 단위 테스트: 목록 응답 직후 같은 묶음의 알림, 페이지 사이 알림, 다른 서버 목록 대기 중 알림, callback 입력 검사. 실제 고정 MCP SDK와 메모리 stdio 바이트 서버를 사용했다.
- 영향받은 서버의 실제 `tools/call` 수신 0회, 다른 서버 1회, 원 Registry ACTIVE 유지, 불필요한 목록 재조회 없음, 새 모델 도구 목록에서 영향받은 서버만 제외를 확인했다(I2·I7·I10, T10).
- 기존 초기 알림 허용·대기 승인 무효·Codex 도구 제외·수신 상한 시험을 포함한 관련 95개 통과.
- 수신 표시만 비활성화한 대조 실행에서 새 동작 시험 3개가 모두 실패했다. 소스 파일을 바꿔 회귀를 남기지 않았다.
- 전체 단위: **5,464 통과, 4 실패, 5 skip, 20 subtest 통과**, 244.91초. 실패는 C 소유 `test_write_loss_counts.py`의 HTTP hang·500·redirect 307·308 종료 기대값이다. 실제 `[]`, 기대 `['SessionCloseFailed']`이며 A132의 종료 완료 동작과 어긋난다. 해당 파일은 수정하지 않았다.
- 실패 단언 앞의 결과 불명 상태·재전송 차단·dispatch/wire/effect 계수는 통과했다. skip은 심볼릭 링크 권한 4개와 `GUARD_TEST_CODEX_EXE` 없는 실제 L2 1개다. 경고 1개는 서드파티 `lifespan`의 미해결 타입 경고다.
- contract 45개 통과. Ruff check·format(267개 파일), mypy(112개 소스) 통과.
- 명령: `.venv/Scripts/python.exe -m pytest -q -rs tests/unit`, `-m pytest -q tests/contract`, `-m ruff check .`, `-m ruff format --check .`, `-m mypy`.

## 남은 한계와 인계

- A 단위 검증이며 C의 실제 서버 알림 시점 관찰(C5.9)·독립 인수는 남았다. 서버가 알리지 않는 변경은 이 알림 처리로 검출할 수 없다.
- SDK 내부 수신 메서드를 사용하므로 MCP 버전을 바꿀 때 같은 묶음·페이지 사이 수신 순서 시험을 다시 확인해야 한다.
- A115·A136 기존 기록은 수정하지 않았다. A134 실제 writer 경합 재현은 B 조건 대기, A137 bridge 시험 빈틈과 A138 B6.8 제품 연결은 별도 후속이다.
- [구현 PR #70](https://github.com/hsminu/MCP-Runtime-Guard/pull/70). #52는 A115·A136 전에 이미 병합되어 이 PR로 후속 리뷰한다. 공통 계약·개발문서·검사 실행 기록은 역할 브랜치가 아닌 별도 main 대상 문서 PR로 제안한다.
