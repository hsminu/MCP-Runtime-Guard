# A206 · 서버 상세 창: 검사 결과 무한 로딩 수정과 배치 정리

2026-10-30 · `wip/next`. 출처: 사용자 보고("Scanner · Registry 상태 무한로딩", "정보 나열 가독성").
관련: I7(검사 결과는 활성화·판정을 바꾸지 않음), C4.13(경고 표시), A32·A201.

## 문제

- **무한 로딩:** 서버 상세 창은 A32(2026-10-12)의 `ScanStatusCard`를 썼다. 열 때 카드를 `LOADING`으로 두고 `scan_snapshot_requested(server_id, token)`를 창 밖으로 보냈다.
  - 이 신호를 받는 곳은 `examples/ui_demo.py`와 시험뿐이고, 제품 코드에는 없었다.
  - 그래서 제품에서는 영원히 "불러오는 중…"이었고, 칸 12개가 기본값 "미제공"으로 남았다.
- **데이터는 이미 있었다:** A201부터 bridge의 등록 요청 `scan_status`가 답하고 있었다. 이 요청은 Registry에 저장된 마지막 검사(`last_scan`)가 현재 도구 지문과 맞으면 그 보고서를 `ScanState`로 돌려준다. 등록 검사 창은 이미 이 경로와 `ScanReportPanel`을 쓰고 있었다.
- **가독성:** 라벨 위·값 아래인 칸 9개가 세로로 쌓이고, 그 밑에 카드 칸 12개가 또 쌓였다. Registry·세션 상태는 두 번 나왔다.

## 한 일

- **검사 결과 경로(`ui/servers.py`, `ui/scan_report.py`):**
  - 상세 창의 검사 영역을 A201 `ScanReportPanel`로 바꿨다.
  - 창을 열 때 등록 bridge(`attach_activation`)가 있으면 새 command id로 `RegistrationRequest("scan_status", server_id)`를 보내고 "검사 결과 확인 중"을 보인다.
  - bridge가 없으면 바로 "검사 기능이 연결되지 않음"을 보인다.
  - 응답은 지금 열린 창의 command id와 서버가 같을 때만 반영한다.

    | 응답 | 화면 |
    | --- | --- |
    | `SCAN_STATUS` | 보고서 |
    | `SCAN_UNAVAILABLE`·`REG_UNAVAILABLE` | 미연결 |
    | 그 밖의 코드, command `REJECTED`·`UNCONFIRMED` | "검사 결과를 확인하지 못함." |

    `REG_UNAVAILABLE`은 bridge가 server_id 없이 돌려주므로 command id로만 묶는다.
  - `scan_changed`는 열린 창과 같은 서버일 때만 갱신한다.
  - 닫힌 창, 이전 열기, 다른 서버의 응답은 무시한다.
  - `ScanReportPanel`에 `set_loading()`·`set_failed()`를 더했다. 기존 `set_state`는 그대로다.
- **죽은 경로 정리:** 다음을 지웠다. 제품 생산자가 없던 A32 표시 틀이고, 같은 정보는 A201 패널이 C `ScanService` 결과로 보여 준다.
  - `ui/scan_status.py`(`ScanStatusCard`·`ScanStatusProjection`)
  - `ServerListPage.scan_snapshot_requested`·`set_scan_snapshot`·token
  - `GuardWindow.scan_snapshot_requested`·`set_scan_snapshot`
- **데모(`examples/ui_demo.py`):**
  - 가짜 `BackendBindings`에 `registration` 응답기를 더했다. 이 응답기는 `scan_status`에만 가짜 `ScanState`로 답하고, 다른 action에는 지금처럼 `REG_UNAVAILABLE`로 답한다. 그래서 데모도 제품과 같은 명령 경로를 탄다.
  - 장면은 다섯 가지다.
    - docs: 완료·LOW
    - mail: 완료·MEDIUM
    - drive: 검사 중
    - crm: 저장 결과가 지금 도구와 다름
    - wiki: 기록 없음
- **배치:**
  - 머리는 표시 이름(`pageTitle`)과 server_id(`secondary`), 최대 등급 배지다.
  - 그 아래 묶음 셋을 둔다.

    | 묶음 | 항목 |
    | --- | --- |
    | 상태 | Registry 등록 상태, 세션 상태, 설정 확인 |
    | 연결 설정 | 연결 방식, 신뢰 경계, 실행 파일 지문 |
    | 최근 검사 | 검사 결과 |

  - 각 행은 `ResponsiveRow`(400px 기준)다. 넓으면 라벨 148px 왼쪽·값 오른쪽이고, 좁으면 위아래로 접는다.
  - 창 안 제목 "MCP 서버 등록 상세"는 창 제목과 겹쳐 지웠다.
  - 라벨·값 문구는 그대로다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 새 시험 `test_a206_server_detail.py` | 통과 |
| 대상 11개 파일(새 시험, `test_ui_read_only_pages`, `test_server_add`, `test_a178_qt_ownership`, `test_ui_responsive_detail`, `test_ui_demo`, `test_a201_scan_report_ui`, `test_a202_reviewed_descriptions`, `test_ui_icons_style`, `test_bridge_registration`, `test_a205_selection_wheel`) | 208 passed, 106 subtests passed |
| `ruff check`·`ruff format --check`(src·tests·examples 388개), `mypy src`(147개), import-linter(계약 7개 유지) | 통과 |
| 남은 참조: `ScanStatusCard`·`ScanStatusProjection`·`scan_snapshot`·`ui.scan_status` | 없음 |
| 되돌림 6가지(아래 표) | 모두 시험 실패. 복원 뒤 바이트 같음 |
| 실제 스타일로 오프스크린 렌더링(640·300px) | 넓을 때 두 열, 좁을 때 접힘, 가로 스크롤 없음 |

새 시험은 다음을 확인한다.

- 미연결 시 로딩이 없다.
- 요청은 1건이고 command id에 묶인다. 재열기 때 새 요청을 보낸다.
- 다른 command·다른 서버·닫은 뒤의 응답은 무시한다.
- `REG_UNAVAILABLE`, command 실패 2종, `outdated`를 구분해 보인다.
- `scan_changed`는 같은 서버만 반영한다.
- 묶음 순서, 600px 두 열, 260px 접힘·가로 스크롤 0, Registry 상태가 한 번만 나온다.

되돌림 6가지는 다음과 같다.

| 되돌린 것 | 결과 |
| --- | --- |
| 요청 송신 | 13개 실패 |
| command id 대조 | 4개 실패 |
| 서버 대조 | 2개 실패 |
| `scan_changed` 서버 대조 | 1개 실패 |
| command 실패 처리 | 2개 실패 |
| 미연결 즉시 표시 | 1개 실패 |

지우거나 바꾼 기존 시험은 다음과 같다.

| 시험 | 바꾼 것 |
| --- | --- |
| `test_server_add.py` | 지운 `ScanStatusCard` 단위 시험을 뺐다 |
| `test_a178_qt_ownership.py` | 카드 대신 `ScanReportPanel` |
| `test_ui_read_only_pages.py` 두 시험 | token 대신 command id 결속, 100회 재열기 창 누수 없음 |
| `test_ui_responsive_detail.py` | `scan_report` |
| `test_ui_demo.py` | crm 새 문구, 다섯 장면 |

## 한계

- 실제 codex.exe·실제 Registry DB로 띄워 보지는 않았다. 주입 bridge와 데모 backend로 확인했다.
- 검사 결과는 현재 도구 지문과 맞는 마지막 저장 검사 하나만 보인다. 이전 검사 이력은 없다.
- 상세 창에서는 다시 검사할 수 없다. 재검사는 기존 등록 검사 창(PENDING 서버)에서 한다.
- 검사 영역의 모델 설명 상자(`UntrustedTextView`)가 내용보다 높게 잡힌다. 동작에는 문제가 없다.
