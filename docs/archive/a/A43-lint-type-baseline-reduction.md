# A43 · A 파일의 린트·포맷·타입 기준선 축소

2026-10-18. 2026-10-14 A+B 통합 때 둔 A 파일 예외를 줄였다. 공통 설정(`pyproject.toml`)을 바꾸므로 A→main PR 본문에 공통 설정 변경으로 따로 적는다. 의존성은 바꾸지 않았다(ENV-01).

## 바꾼 것

- **포맷 제외 목록 12개 삭제.** 11개는 이미 포맷돼 있었고 `src/guard_app/adapters/model_client.py`는 파일이 없다.
- **mypy.**
  - A 모듈을 strict로 옮겼다: `__main__`, `adapters/*`의 A 파일, `bridge`, `runner`, `ui/*`. 이를 위해 빠진 타입 주석 14곳을 채웠다.
    - `mcp_transport`의 asynccontextmanager 반환형과 로그 factory
    - UI 슬롯 인자, `resizeEvent`, `_sync_selected_task`, `StateMessage.set_state(as_of)`
    - `__main__`의 `closeEvent`
  - `__main__`의 `attr-defined` 예외도 지웠다. `QApplication.instance()`를 `isinstance`로 좁혀서 필요가 없어졌다.
  - 이미 strict였던 `ui.runtime` 별도 항목도 지웠다.
  - 완화 목록에는 AGT 제거 대상(`adapters/agt*`, B2.11)과 Scanner 어댑터(`adapters/cisco*`, C)만 남겼다.
- **ruff 파일별 예외**
  - `bridge.py`의 `ASYNC109`·`S101`·`SIM105`를 없앴다.
    - `_cleanup_bindings(timeout=)`를 `limit_s`로 이름을 바꿨다.
    - try/pass는 `contextlib.suppress`로 바꿨다.
    - assert 5곳은 고정 코드 `RuntimeError`나 `MESSAGE_UNAVAILABLE`로 바꿨다.
  - `mcp_transport.py`의 `S101` 2곳을 명시 분기로 바꿨다.
  - `N818`은 `ResultTooLarge`가 `DispatchError.exception_type`(감사 메타데이터) 값이라 유지하고, 사유를 주석으로 남겼다.
  - `__main__`의 `E501`은 위반이 0건이라 지웠다.
  - `ui/**`의 `N815`는 Qt가 정한 이름이 아닌 시그널 3개(`localDraftPrepared`·`removeRequested`·`folderDraftPrepared`)를 snake_case로 바꾸고 지웠다. Qt 재정의용 `N802`는 유지했다.

테스트 파일의 `I001` 예외(`test_mcp_http`·`test_runtime`·`test_sqlite_app`)는 남겼다. Qt 플랫폼 환경변수를 import보다 먼저 설정해야 해서 실제 위반이 있다. 조사 단계의 "0건" 판단은 틀렸다.

## 검증

- ruff 전체·format 171파일 통과(커밋하지 않는 `docs/tasks` 제외)
- mypy 71소스 통과
- 전체 단위 테스트 **3,318개 통과, 1 skip, subtest 20**, 기존 경고 5개
