# A178 · #92 중단 원인: 부모 없는 하위 레이아웃의 Qt 위젯 이중 소유 제거

2026-10-30 · `wip/next`(head `48e0b36`, A177 + 최신 main 병합 기준). PR: 다음 A PR(아직 없음)
관련: #92 후속, A171, A172. 직접 대응하는 T/S ID는 없음. I9는 시험 진단의 원문 비공개에 적용하며, 판정·전송 계약은 변경하지 않음.

## 문제

PySide6 6.12.0에서 부모 위젯이 없는 레이아웃에 먼저 위젯을 넣으면 Python 소유가 남는다. 나중에 하위 레이아웃을 붙여도 기존 위젯의 소유가 넘어가지 않아 Qt 부모와 Python 래퍼가 같은 위젯을 소유한다. 페이지 순환을 worker GC가 수거하면 Shiboken이 자식 삭제를 메인 스레드에 미루고, Qt가 페이지와 함께 먼저 지운 자식을 다시 삭제하면서 중단될 수 있다.

A172의 최종 C++ 대상 미확정은 이번 확인으로 승인 페이지의 이중 소유 자식까지 좁혀졌다. A172 기록 자체는 변경하지 않았다.

## 한 일

부모 없는 직접 생성 레이아웃 29곳·13개 파일을 점검했다. 이미 위젯을 넣기 전에 붙이는 5곳은 유지하고, 24곳의 부착을 생성 바로 다음 줄로 옮겼다. 추가로 `ServerAddForm._new_form()`이 부모 없이 반환하는 2곳을 같은 방식으로 고쳤다. 제품 변경은 12개 파일·26곳이다.

| 파일(`src/guard_app/ui/`) | 위치와 부착 순서 |
| --- | --- |
| `approvals.py` | `ApprovalListPage.filters`·`pager`를 `layout`에 먼저 붙임. `filters` 앞에만 A178 영어 주석 추가 |
| `call_logs.py` | `CallLogPage._actions_layout`을 `layout`에 먼저 붙임 |
| `destinations.py` | `DestinationDialog.form`을 `layout`에 먼저 붙임 |
| `model_settings.py` | `CodexLoginInfo.key_row`·`buttons`를 `layout`에 먼저 붙임 |
| `overview.py` | `OverviewPage.top`을 `outer`에, `footer`를 `servers`에 먼저 붙임 |
| `overview_rows.py` | `ListRow.top`·`meta`를 `layout`에 먼저 붙임 |
| `policy.py` | `ToolAllowDialog.buttons`를 `layout`에 먼저 붙임 |
| `runtime.py` | `RuntimePanel.notice`를 `session_layout`에 먼저 붙임 |
| `server_add.py` | `ServerAddForm.paste` → `paste_actions`를 바깥부터 부착. `common`·`boundary`·`actions`를 `outer`에 먼저 부착. `_FolderRuleRow.row`·`LocalFolderRulesForm.actions`도 먼저 부착 |
| `servers.py` | `ServerListPage.filters` → `_choices`를 바깥부터 부착 |
| `sessions.py` | `SessionWorkspace.titles` → `facts`를 바깥부터 부착. `words`·`controls`도 먼저 부착 |
| `window.py` | `GuardWindow.brand`를 `side`에 먼저 붙임 |

같은 부모에 다른 항목이 삽입되는 구간을 가로지른 이동은 0곳이다. 별도 코드 블록 재배치나 `insertLayout`은 필요하지 않았다. 부모별 항목 삽입 순서, 호출·상수·import의 AST 다중집합이 기존과 같음을 확인했다. 화면 문구·stretch·alignment·여백·간격·방향 전환은 유지했다.

`PolicyPage.server_area`, `LocalFolderRulesForm._rows_layout`, `FileServerFolders._rows`, `RuntimePanel._session_actions`·`_approval_actions`는 기존 순서를 유지했다. `server_add_folders.py`는 변경하지 않았다. 별도 `setLayout` 부착 경로는 없었다. 제품에 Shiboken 호출·소유권 보정·순회 helper·monkeypatch를 추가하지 않았다.

- `tests/unit/test_a178_qt_ownership.py`: 8개 시험. `gc.get_objects()`·유효성·Python 소유·부모 연결을 검사하고 이번 시험의 루트에 도달하는 객체만 보고한다. 실패 진단은 타입·부모 타입·`objectName()`뿐이다. 자식 열거 API는 사용하지 않는다.
- 대조 시험: 잘못된 부착 순서 1개, 올바른 순서 0개. 다른 루트의 결함을 섞지 않는 것도 확인한다.
- 제품 시험: `GuardWindow`; `MetricRow` 3개·`ServerRow`·`AttentionRow`·`RecentCallRow` 각 1개가 생성된 `OverviewPage`; `ServerAddForm`·`LocalFolderRulesForm`·`FileServerFolders`에 규칙 행 각각 1개 이상; `ApprovalDetailDialog`·`ServerDetailDialog`·`ToolSettingsDialog`·`DestinationDialog`·`ToolAllowDialog`·`PlainDetailDialog`; `RuntimePanel`과 표시 상태의 `RecoveryPanel`; `ScanStatusCard`·`TaskStatusCard`·`ScannerKeyForm`·`UntrustedTextView`; 서버 행 1개가 있는 `PolicyPage`; 세션 2개인 `SessionWorkspace`. 요청된 화면 중 구성하지 못한 것은 없다.
- 각 시험의 루트와 별도 사이드바 위젯을 메인 스레드에서 `deleteLater` → `sendPostedEvents(DeferredDelete)` → `processEvents` → `gc.collect()` 순서로 정리한다. 시험용 bridge는 시작하지 않고 닫는다.
- `tests/unit/test_store_query_link.py`: A171 fixture의 주석 2줄을 실제 원인을 설명하는 3줄로 교체했다. 실행 코드는 그대로다.
- `docs/a-status.md`: A177 바로 아래 A178 완료 1줄을 추가했다. A 검수 측정(아래) 뒤 "A 화면 시험 묶음이 모두 통과한 뒤 종료 중 abort" 줄을 완료로 바꿨다. `run_application` 미완료 줄은 유지했다.
- A 검수: `test_populated_overview`에서 소유 검사를 행 존재 확인(`itemAt`)보다 먼저 하도록 순서를 바꿨다. Python의 `itemAt` 호출이 PySide의 레이아웃 소유 hook을 다시 돌려 결함을 가릴 수 있어서다.
- 수정 파일과 새 파일은 CRLF를 유지했다.

## 검증

Windows, 로컬 `.venv` Python 3.12, PySide6·Shiboken 6.12.0. `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `QT_QPA_FONTDIR=C:/Windows/Fonts`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`. pytest에는 `-q -p no:cacheprovider --basetemp=.cache/codex-a178/pt-<n>`을 붙였다. 임시 파일·mypy 캐시는 `.cache/codex-a178/`만 사용했고 Ruff·import-linter 캐시는 끄고 실행했다.

- `audit.py`: 변경 전 49개 → 변경 후 0개, 완결 실행의 종료 코드 각각 0. 최초 첫 줄 파이프 조회는 종료 코드 -1이어서 전체 출력을 끝까지 받는 방식으로 변경 전 측정을 다시 했다. 고의 회귀 복원과 최종 정리 뒤에도 0개·종료 코드 0이다.
- `page_probe.py`·`-X faulthandler`: `approvals worker` 5회 종료 코드 `[0, 0, 0, 0, 0]`, 이중 소유 각 0개·약한 참조 생존 각 False. `window worker` 3회 종료 코드 `[0, 0, 0]`, 이중 소유 각 0개·약한 참조 생존 각 True. 전체 창 probe는 창의 GC 수거를 입증하지 않는다.

| 실행 | 결과 | pytest 종료 코드 |
| --- | --- | --- |
| `pt-1` 새 시험 최초 | 7 passed, 1 failed. 시험 자료의 `OverviewSnapshot.calls`를 `recent_calls`로 수정 | 1 |
| `pt-2` 새 시험 단독 | 8 passed | 0 |
| `pt-3` `ApprovalListPage.filters` 고의 회귀 | 7 passed, 1 failed. `QLineEdit` 2개·`SelectionBox` 1개와 부모 `ApprovalListPage` 확인 | 1 |
| `pt-4` `ListRow.top` 고의 회귀 | 7 passed, 1 failed. 긴 진단 목록 축약 때문에 보조 스크립트의 `ServerRow` 문자열 확인은 실패. 파일 복원은 완료 | 1 |
| `pt-5` `ListRow.top` 고의 회귀 재확인 | 7 passed, 1 failed. 한 객체씩 출력하도록 진단을 정리한 뒤 `KeyboardActionButton` 3개·`QLabel` 4개, 부모 `AttentionRow`·`RecentCallRow`·`ServerRow` 확인 | 1 |
| `pt-6` 지정 관련 17개 파일, 한 묶음 1회 | 257 passed, 106 subtests passed, 1 warning | 0 |
| `pt-7` 최종 새 시험 단독 | 8 passed | 0 |

`pt-6` 경고는 `IncompleteFieldDefinitionWarning` 1건이다. 시험 종료 뒤 abort·access violation은 관측하지 않았다. 묶음 실행 뒤 이미 안전했던 런타임 그리드 2곳의 불필요한 부착/간격 줄 이동을 제거하고, 최종 `pt-7`에서 `RuntimePanel`을 포함한 소유 시험을 다시 통과했다.

고의 회귀는 매번 저장한 원래 바이트로 복원했다. 회귀 전·복원 후 SHA-256 일치:

- `approvals.py`: `A92358ED997496375E58778D97CD1A8319C2880252AA1234134771298C23478A`
- `overview_rows.py`: `A4120153587DF2D139D75DDD3691EE0F3B9AC59E859366A4A03E303C9650ABD0`

- `ruff check`·`ruff format --check`: 변경 Python 14개 통과, 각각 종료 코드 0. 최초 새 시험의 import 줄 길이·포맷 지적은 수정했다. 최종 `runtime.py` 재검사도 각각 0.
- `mypy src`: 140개 소스 파일 통과, 종료 코드 0.
- `lint-imports --config tools/ci/importlinter.ini --no-cache`: 7 kept, 0 broken, 종료 코드 0.
- 제품 12개 파일의 AST 호출·상수·import 다중집합 및 부모별 삽입 순서 동일. 변경 Python 14개의 CRLF 확인.
- `git diff --check`: 통과, 종료 코드 0.

### A 검수에서 추가로 잰 것

매회 새 프로세스, `-X faulthandler`, `QT_QPA_PLATFORM=offscreen`. 수정 전은 main `e0f08b3`(A 화면 코드는 이 작업의 기준과 같음), 수정 후는 이 변경을 얹은 작업 사본이다. 종료 코드 3은 faulthandler의 Aborted다.

| 대조 | 수정 전 | 수정 후 |
| --- | --- | --- |
| `page_probe.py approvals worker`(승인 페이지 자기 순환 → worker GC) | 3/3 중단 | 0/5 중단 |
| A 화면 시험 9개 파일 묶음(A172에서 종료 중 abort가 난 순서: `test_store_query_link`·`test_ui_approvals`·`test_call_logs`·`test_a_architecture`·`test_approval_detail`·`test_ui_approval_t18`·`test_ui_display_escaping`·`test_ui_keyboard`·`test_ui_render_projection`) | 6회 중 2회 중단(3·5회차, 모두 98 passed 뒤) | 10회 중 0회, 매회 98 passed |
| A171 fixture를 끈 `test_store_query_link`+`test_store_recovery`(#92 재현) | 5/5 중단 | 0/5, 매회 56 passed |

- fixture는 `autouse=True`를 잠시 `False`로 바꿔 껐고, 매 묶음 뒤 보관한 원래 바이트로 되돌려 SHA-256 일치를 확인했다.
- 고의 회귀를 하나 더 했다. `ListRow.meta`를 원래 순서(위젯 먼저, 부착 나중)로 되돌리면 새 시험이 1 failed·7 passed로 실패하고, 진단에 `LabelPill`·`QLabel`과 부모 `ServerRow`·`AttentionRow`·`RecentCallRow`가 나온다. 복원 뒤 SHA-256은 위 `overview_rows.py` 값과 같다.
- 순서 수정 뒤 새 시험 단독 8 passed, 이중 소유 감사 0개.

## 남은 것

- 실제 Store·모델을 넣은 제품 종단에서 worker GC가 페이지를 수거하는지는 미확인이다. 전체 창 probe에서도 창 참조는 남았다.
- 위 9개 파일 묶음과 #92 재현에서는 종료 중 abort가 사라졌다. 다른 시험 순서·전체 시험·제품 `run_application` 종료의 abort는 따로 재지 않았다.
- A171 fixture는 방어로 남긴다. 공용 conftest로 Qt 정리를 옮기는 안(#99 A 회신에서 C에 물음)은 원인이 제품 코드에서 고쳐져 필요 없어졌다.
- 제품 `run_application` 수명 정리는 별도 항목이다.
- 전체 시험·실제 창 육안 확인은 실행하지 않았다. A UI 변경의 C 리뷰는 별도다.
