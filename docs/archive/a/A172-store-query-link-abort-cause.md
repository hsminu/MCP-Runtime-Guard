# A172 · #92 조회 연결 시험 중단 경로 조사

2026-10-30 · `wip/next`(기준 커밋 `0be9852`). PR: [#92](https://github.com/hsminu/MCP-Runtime-Guard/pull/92) 후속.
관련: 개발문서 12.1절(스레드 모델), [A171](A171-store-query-link-qt-cleanup.md), #92 B·C 리뷰. Qt 객체 수명·중단 재현에 직접 대응하는 T/S ID는 없다. I9에 따라 기록에는 객체 타입·함수·고정 실패 분류만 옮겼다. 계약 변경 없음.

## 문제

A171 fixture가 없는 조회 연결 시험 뒤에 저장소 복구 시험을 실행하면 프로세스가 중단된다. A171은 새 위젯의 명시적 삭제와 메인 스레드 GC를 함께 넣어 중단을 막았으나, 객체·GC 스레드·중단 이유는 확인하지 않았다. 이번 작업은 제품 수정 없이 그 경로를 조사했다.

## 확인한 것과 미확정인 것

**이번 계측에서 확인한 경로는 작업 스레드의 GC와 메인 스레드의 C++ 지연 삭제가 이어지는 중단이다.** `test_link_keeps_both_pages_unconnected_without_a_store_query_source`가 남긴 `ApprovalListPage`·`StoreQueryLink`·`BackendBridge`·`CallLogPage`와 닫힌 `ProactorEventLoop`의 래퍼가 `sqlite-store-writer`의 자동 1세대 GC에서 수거된다. 그 GC가 끝난 뒤 메인 스레드의 Shiboken `BindingManager::runDeletionInMainThread`에서 QtCore의 QObject 소멸 경로를 거쳐 C 런타임 `_purecall` → `abort`가 실행됐다. 주요 객체만 추적한 3회에서 같은 순서를 관찰했다.

따라서 메인 스레드의 faulthandler 스택과 B의 다른 스레드 GC 가설은 모순이 아니다. **GC가 실행된 스레드와 abort가 실행된 스레드가 다르다.** abort 시점에는 해당 GC가 이미 끝났으며, 메인의 Python 프레임이 `asyncio`의 `_poll`에 있어도 그 아래 native 지연 삭제가 실행될 수 있다. writer가 기다리는 스택도 이 순서와 맞는다. `QThread` 실행 중 삭제나 Qt `qFatal` 문구는 관찰하지 않았다.

**최종 C++ 객체의 주소·하위 타입과 `_purecall`에 도달한 정확한 virtual 호출은 미확정이다.** `ApprovalListPage`가 포함된 객체 그래프까지 좁혔고 이 페이지에 순환 참조를 만든 단독 재현도 같은 native 경로로 중단됐다. 그러나 `ApprovalListPage` 자체와 그 자식 중 어느 C++ 객체가 마지막 삭제 대상인지, 어느 수명 오류가 pure virtual 호출을 만들었는지는 확정하지 못했다. 이 기록은 특정 Qt 자식이나 Shiboken 내부 결함을 원인으로 단정하지 않는다. A171의 과거 모든 abort와 이번 access violation까지 같은 원인이라고 확정하지도 않는다.

## 한 일

- `src/`, B·C 파일, 공통 문서, 기존 완료 기록은 바꾸지 않았다. `test_store_query_link.py`만 허가된 변형으로 잠시 바꿨고 매 실행 묶음의 `finally`에서 원래 바이트로 복원했다.
- `.cache/codex-a172/`에 실행기, 회차별 stdout·stderr, GC·finalize 로그, native 관찰기, 독립 재현과 단계별 JSON을 남겼다. `diag_plugin.py`는 `-p diag_plugin`일 때만 동작한다.
- GC 시작·끝, 세대·수거 수, Python/native 스레드 ID, 시험 시작·끝, QObject·Shiboken 래퍼와 이벤트 루프의 weakref finalize를 `os.write`로 기록했다. QObject 이름·부모·메인 QThread 소속·Python 소유 여부, QThread의 실행 여부와 루프의 닫힘 여부는 **추적을 건 시점의 값**이다. finalize에서 이미 사라진 객체를 다시 조회하지 않는다.
- 전체 래퍼 추적이 중단을 없애는 관찰 효과를 확인했다. 같은 플러그인의 전체 추적, snapshot 없는 경량 추적, 주요 객체만 추적하는 모드를 따로 측정했다.
- Qt 메시지 handler는 고정 `qWarning` 대조로 동작을 확인했다. 자체 자식 프로세스의 CRT SIGABRT handler와 `RtlCaptureStackBackTrace`로 native 스택을 얻었다. DLL의 설치된 PE export만 읽었으며 심볼 서버·네트워크·시스템 설정은 사용하지 않았다. 관찰 후 기존 faulthandler handler로 넘겼다.
- `docs/a-status.md` A171 바로 뒤에 A172 조사 완료 줄을 더했다. 최종 C++ 대상 미확정을 표시했다.

## 검증

Windows, Python 3.12.15, PySide6·Shiboken6·Qt 6.12.0, pytest 9.1.1의 기존 `.venv`를 썼다. 매회 새 프로세스이며 전체 시험은 실행하지 않았다. 환경과 버전은 `environment.json`, 명령·결과·파일 해시는 `results.json`에 있다.

```powershell
$env:PYTHONPATH='src;.cache/codex-a172'
$env:QT_QPA_PLATFORM='offscreen'
$env:QT_QPA_FONTDIR='C:/Windows/Fonts'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:QT_FORCE_STDERR_LOGGING='1'
$env:TEMP=(Resolve-Path .cache/codex-a172).Path
$env:TMP=$env:TEMP
$py='..\..\..\.venv\Scripts\python.exe'

# 실행기가 원본을 보관하고 git show HEAD~1의 fixture 없는 바이트를 사용한다.
& $py .cache/codex-a172/run_investigation.py 0
& $py .cache/codex-a172/run_investigation.py 1
& $py .cache/codex-a172/run_investigation.py 2
& $py .cache/codex-a172/run_investigation.py 3
& $py .cache/codex-a172/run_investigation.py pairs
& $py .cache/codex-a172/run_investigation.py light
$env:A172_PLUGIN='1'; $env:A172_ROOTS='1'; $env:A172_NATIVE='1'
& $py .cache/codex-a172/run_investigation.py custom diag-roots none 3 tests/unit/test_store_query_link.py tests/unit/test_store_recovery.py
```

실제 자식 pytest 명령은 모두 아래 형식이다. 플러그인 사용 회차에만 `-p diag_plugin`이 추가된다. 로그 위치는 `A172_DIAG_LOG`로 주입한다. `light`의 앞 3회는 `A172_NO_SNAPSHOT=1`, native 기록은 `A172_NATIVE=1`, 주요 객체 한정은 `A172_ROOTS=1`이다. 후속 비계측 대조는 이 플래그를 주지 않은 새 셸에서 실행했다.

```powershell
& $py -X faulthandler -m pytest -q -s -p no:cacheprovider --basetemp=.cache/codex-a172/pt-<n> <노드들>
```

### 단계 0·1 — 메시지와 정리 단계

- stderr 강제·출력 캡처 해제·faulthandler 조건에서 3/3 abort, 모두 종료 코드 3. abort 전 Qt fatal 문구는 없었다. 이후 native 관찰에서 CRT `_purecall`을 확인했으므로 Qt 메시지 처리기를 반드시 통과하는 경로가 아니었다. `QT_LOGGING_RULES` 추가 변경은 하지 않았다.
- `gc.collect()`만 남긴 조건 10/10 정상 종료, 매회 47 passed.
- 새 최상위 위젯의 `deleteLater`·`DeferredDelete`·`processEvents`만 남긴 조건도 10/10 정상 종료, 매회 47 passed.
- 이 표본에서 각 단계는 단독으로 중단을 억제했다. 어느 하나가 반드시 필요하거나 항상 충분하다고 일반화하지 않는다.

### 단계 2 — 시험 분리

번호는 fixture 없는 A 파일의 정의 순서다. 각 노드 하나와 B 복구 파일 전체를 함께 3회씩 실행했다.

| 번호 | A 시험 노드 |
| --- | --- |
| 1 | `test_store_rows_become_screen_rows_without_a_tier_or_a_guessed_expiry` |
| 2 | `test_screen_queries_follow_b_limits_and_keep_the_search_case` |
| 3 | `test_lane_runs_one_read_per_kind_and_keeps_only_the_newest_waiting` |
| 4 | `test_lane_answers_failed_for_errors_timeouts_wrong_requests_and_no_source` |
| 5 | `test_lane_close_cancels_running_reads_and_refuses_new_ones` |
| 6 | `test_bridge_refuses_malformed_queries_and_answers_failed_before_ready` |
| 7 | `test_link_fills_both_pages_from_the_store_and_shows_failures_as_unknown` |
| 8 | `test_window_reads_both_pages_from_the_runtime_store` |
| 9 | `test_bridge_close_reports_a_store_read_that_does_not_end` |
| 10 | `test_link_keeps_both_pages_unconnected_without_a_store_query_source` |

- 단일 노드 10종은 모두 0/3 중단, 합계 30회 정상 종료(매회 38 passed).
- 화면을 만드는 7–10번의 두 개 조합 6종을 각 3회 실행했다. 8+10은 3/3 abort. 7+10은 abort 2회와 access violation 1회(`0xC0000005`). 나머지 네 조합은 중단 0/3, 매회 39 passed.
- A 전체+B의 `test_send_unknown_wins_over_later_read` 하나는 **0, 3, 3**, 즉 abort 2/3, 정상 회차 11 passed. 작업 중 설명의 3/3은 최종 회차 JSON 대조로 정정했다.
- 검사한 조합에서는 A 두 시험으로 줄였다. 모든 두 개 조합이나 모든 순서를 탐색하지 않았으며, 단일 A 시험에 위험 객체가 없다는 뜻도 아니다. 뒤의 단독 GC 실험에서 GC 시점을 바꾸면 중단됐다.

### 단계 3 — 스레드·객체·native 경로

전체 추적은 A 전체+B 전체 0/3 중단, 8+10+B 전체도 0/3 중단이었다. 전체 `gc.get_objects()` 순회와 추적 등록은 할당·GC 시점을 바꾸므로 정상 종료 로그만으로 원래 현상을 부정할 수 없다. snapshot 없는 GC/native 기록은 2/3 중단, 주요 객체 한정 추적은 3/3 중단이었다.

주요 객체 한정 회차 84–86에서는 다음을 모두 확인했다(`abort-evidence.json`).

1. 추적 시점의 identity를 대조하면 다음 객체들은 10번 시험 teardown에서 발견된 같은 객체들이다: `BackendBridge`, 부모 없는 `ApprovalListPage`, 그 페이지가 부모인 `StoreQueryLink`, 부모 없는 `CallLogPage`, 닫히고 실행 중이지 않은 `ProactorEventLoop`. QObject 소속은 모두 메인 QThread다.
2. B의 `test_invalid_or_unresolved_rolls_back_all_rows[ready-dispatch]`에서 `sqlite-store-writer`가 1세대 GC를 시작한다. 위 객체들의 finalize가 그 writer에서 실행된다. GC는 매회 971개를 수거하고 끝난다. 경량 native 회차 78–79의 같은 세대·스레드에서는 966개였다.
3. 그 뒤 메인 스레드에서 SIGABRT가 발생한다. 스택의 핵심은 `BindingManager::runDeletionInMainThread` → `QtWidgets.pyd`의 삭제 코드 → QtCore의 QObject 소멸 경로 → `VCRUNTIME140._purecall` → `ucrtbase.abort`다. abort 시점의 GC 진행 표는 비어 있다.
4. 계측이 중단 시험 위치를 바꿨다. 비계측의 `_poll` 스택과 native 경로를 구분해 보관했다. 기존 A171 20회의 GC 세대·객체까지 소급해서 관찰한 것은 아니다.

native 로그의 `nearest_export`는 주소 바로 앞의 export 이름이지 모든 프레임의 정확한 함수 심볼은 아니다. `_purecall`, `abort`, `runDeletionInMainThread`와 모듈·주소를 근거로 썼고, 특히 export 간격이 큰 Python 프레임 이름을 그대로 함수 이름으로 해석하지 않았다. 완전한 디버그 심볼이나 C++ `this` 포인터는 확보하지 못했다.

`finalize_order.py`의 1회 대조에서는 순환 객체 두 개의 finalize가 모두 실행된 **뒤**에 두 객체의 `destroyed`가 실행됐다. `deleteLater` 대조에서는 C++ 삭제가 finalize보다 먼저였다. 따라서 “마지막 finalize가 바로 다음 삭제 대상”이라는 전제는 성립하지 않는다. 84–86의 마지막 finalize가 닫힌 이벤트 루프라는 이유로 루프를 범인으로 판단하지 않았다.

### 단계 4 — 소유 관계와 단독 재현

`StoreQueryLink.__init__`는 `_approvals`·`_calls`·`_bridge`를 강하게 참조한다. 10번 시험은 승인 페이지를 link의 Qt 부모로도 지정한다. `cycle-edges.json`에서 `gc.get_referents(approvals)`가 그 `StoreQueryLink`를 포함하고, link의 사전이 approvals를 다시 가리키는 것을 확인했다. `close()` 뒤에도 이 순환 그래프는 남는다. `BackendBridge.close()`는 host 종료 요청이며 QObject·화면 그래프의 명시적 삭제 함수가 아니다. `BackendHost._thread_main`은 루프를 닫고 `_loop=None`으로 정리한다. 이번 관찰의 닫힌 루프 잔류를 SQLiteStore 미종료의 증거로 쓸 수 없다.

설치 파일에서 확인한 근거는 다음과 같다.

- PySide6 `typesystem_widgets_common.xml`의 `QWidget`에는 `delete-in-main-thread="true"`가 있다.
- Shiboken `basewrapper_p.h`에는 `delete_in_main_thread` 플래그, `bindingmanager.h`에는 `addToDeletionInMainThread`·`runDeletionInMainThread`, `sbkdestructorentry.h`에는 destructor 함수와 C++ instance 포인터를 담는 항목이 있다.
- 설치된 DLL을 관찰한 native 스택이 이 메인 스레드 삭제 경로와 일치한다. 설치 패키지의 헤더·typesystem·glue는 읽었지만, 소멸 경로의 전체 Qt/Shiboken C++ 구현 소스는 확보하지 못했다. 그 안의 정확한 dangling 객체나 double deletion 여부는 미확정이다.

단독 스크립트는 제품 모듈과 Qt만 import하고 시험 모듈은 import하지 않는다. SQLite·네트워크·인증정보·실제 모델을 쓰지 않는다. `linked_pages_probe.py`는 시작하지 않은 `BackendBridge`와 두 페이지·link를 만들고 닫은 뒤 참조를 놓는다. main은 메인 `gc.collect()`, worker는 별도 `probe-collector`의 `gc.collect()`(2세대), delete는 메인에서 위젯을 명시적으로 삭제한 뒤 worker GC다.

```powershell
& $py -X faulthandler .cache/codex-a172/finalize_order.py
& $py -X faulthandler .cache/codex-a172/linked_pages_probe.py worker linked
$env:A172_PROBE_TAG='linked-corrected'
& $py .cache/codex-a172/run_probes.py linked
```

| 단독 대상 | 메인 GC, 1·2·3회 코드 | worker GC, 1·2·3회 코드 | 먼저 삭제 후 worker GC, 1·2·3회 코드 |
| --- | --- | --- | --- |
| 제품 bridge·두 페이지·link, 관찰 콜백 보정 후 | 0, 0, 0 | 3, 3, 3 | 0, 0, 0 |
| `ApprovalListPage`+인위적 자기 순환 | 0, 0, 0 | 3, 3, 3 | 0, 0, 0 |
| `CallLogPage`+자기 순환 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| 기본 `QWidget`+자기 순환 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| Signal이 있는 작은 QWidget 파생형+자기 순환 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |

첫 linked 대조도 같은 종료 코드였으나 destroyed 콜백이 신호 인자를 이름으로 잘못 받아 정상 종료 중 `TypeError`를 출력했다. 그 로그는 보존하고 콜백을 고쳐 9회를 다시 실행했으며, 위 표의 linked 행은 재실행 결과다. exit 0만으로 오류 없는 검증이라고 처리하지 않았다. 최초 탐색 `linked-worker-1`도 abort였으나 위 반복 표에 합산하지 않았다.

추가 축소 실험도 `stage-4-probes.json`에 회차별로 남겼다. 모두 worker GC이며 아래 숫자는 차례대로 종료 코드다.

| 변형 | 회차별 결과 |
| --- | --- |
| 순환을 추가하지 않은 승인 페이지 | 0, 0, 0. 참조를 놓을 때 메인에서 이미 삭제됨 |
| 승인 페이지의 ApprovalTable을 기본 QTableWidget으로 바꿈 | 3, 3, 3 |
| 승인 페이지의 viewport event filter 제거 | 3, 3, 3 |
| 승인 페이지의 정렬 SelectionBox 먼저 삭제 | 3, 3, 3 |
| ApprovalTable 단독 / 부모 QWidget에 붙인 ApprovalTable | 각각 0, 0, 0 |
| 부모+VBox / Box / 중첩 Box / PlainTextEdit / 버튼이 든 중첩 Box | 각각 0, 0, 0 |
| SelectionBox / QComboBox / QTabBar 단독 | 각각 0, 0, 0 |
| 승인 페이지의 lane_tabs, filters, state_message, selection_detail, pager, refresh_button, previous_button, next_button을 각각 먼저 삭제 | 각 변형 1회, 모두 3 |
| 승인 페이지의 table을 먼저 삭제 | 1회 코드 1. 실험 자체 오류로 인과 판정에서 제외 |
| 승인 페이지의 filters·pager를 함께 먼저 삭제 | 3, 3, 3 |

이 탐색은 내부 C++ 대상을 하나로 특정하지 못했다. 별도 `window_probe.py`의 GuardWindow만 닫고 참조를 놓은 대조는 main·worker 각 1회 중단 없이 끝났지만 창은 GC 후에도 살아 있었다. 기본 QWidget 대조의 정상 종료를 모든 QWidget의 스레드 안전성으로 일반화하지 않는다.

### 단계 5 — 제품 위험과 제안

시험 전용으로만 분류할 수는 없다. 중단을 만드는 페이지·link는 제품 코드이며, 제품 import만 한 단독 그래프에서 재현했다. 다만 현재 `GuardWindow`는 페이지를 자식으로 계속 보유하고 `run_application`도 실행 중 창·패널·bridge를 보유한다. 실제 제품 세션에서 페이지가 worker GC로 같은 방식으로 수거되어 죽는지는 확인하지 못했다.

`application_probe.py`는 제품 `run_application`에 I/O 없는 bindings를 주입했다. 처음 3회는 `stop_session` 없는 불완전한 대역이어서 시작 실패와 닫기 경합을 포함했다. 중단은 0/3, 창은 모두 반환 뒤 worker GC에도 살아 있었고, 이벤트를 처리하지 않고 명시적 삭제를 추가한 경우 2/3에서 늦은 `GuardWindow` lifecycle 콜백의 `RuntimeError`가 발생했다. 동일 대역에서 먼저 이벤트를 처리한 3회는 그 오류가 없었지만 시작 실패 대조였다.

대역을 계약에 맞게 보정하고 READY를 확인한 별도 3회(`application-ready-drain`)에서는 앱 반환 코드 0, backend 정리 확인, thread_done을 모두 관찰했다. 반환 뒤 worker GC에도 창은 3/3 살아 있었다. 메인에서 남은 이벤트를 처리하고 `deleteLater`·DeferredDelete를 처리한 뒤에는 창이 3/3 무효가 됐고, abort·Python traceback은 없었다. 이것은 실제 Store·모델까지 포함한 제품 종단 검증은 아니다.

제안만 남기며 이번에 구현하지 않는다.

| 파일·함수 | 제안 |
| --- | --- |
| `ui/application.py::run_application` | backend 종료가 확인된 경로에서 남은 lifecycle 이벤트를 처리하거나 연결을 끊고, 창을 메인 스레드에서 명시적으로 삭제·확인한 뒤 반환하도록 수명 순서를 정한다. 미확인 backend가 살아 있는 경로에는 그대로 적용하지 않는다 |
| `ui/store_views.py::StoreQueryLink`와 그 소유 화면 | 페이지를 교체·폐기하는 기능에서는 신호 연결 해제와 link·페이지 삭제를 포함한 명시적 disposal을 소유자가 수행하게 한다. 단순 `close()`와 순환 GC에 맡기지 않는다 |
| `tests/unit/test_store_query_link.py::cleanup_qt_objects` | A171의 두 단계를 우선 유지한다. 주석의 가설 설명은 worker GC 후 메인 지연 삭제로 중단한 관찰에 맞게 바꾸는 것을 제안한다 |

GC만·삭제만 각각 10회 성공했다고 fixture를 즉시 축소하지 않는다. 명시적 위젯 삭제로 C++ 수명을 먼저 끝내고 GC로 잔류 Python 그래프를 정리하는 순서는 관찰과 맞는다. B writer 수정은 별도 보강이며 이 조사에서 그 필요성을 취소하거나 수정 효과를 판정하지 않았다.

### pytest 회차 전체

종료 코드 0은 정상 완료, 3은 faulthandler의 Aborted, AV는 `0xC0000005` access violation이다. 정상 결과 열은 해당 묶음의 정상 회차만 나타낸다. 모든 회차의 개별 명령·stdout 파일명·소스 SHA-256은 `results.json`에 있다.

| 전역 회차 | 조건 | 회차 순서대로 종료 코드 | 정상 회차 결과 |
| --- | --- | --- | --- |
| 1–3 | `stderr` | 3, 3, 3 | 없음 |
| 4–13 | `gc` | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 47 passed |
| 14–23 | `delete` | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 47 passed |
| 24–26 | `node-1` | 0, 0, 0 | 38 passed |
| 27–29 | `node-2` | 0, 0, 0 | 38 passed |
| 30–32 | `node-3` | 0, 0, 0 | 38 passed |
| 33–35 | `node-4` | 0, 0, 0 | 38 passed |
| 36–38 | `node-5` | 0, 0, 0 | 38 passed |
| 39–41 | `node-6` | 0, 0, 0 | 38 passed |
| 42–44 | `node-7` | 0, 0, 0 | 38 passed |
| 45–47 | `node-8` | 0, 0, 0 | 38 passed |
| 48–50 | `node-9` | 0, 0, 0 | 38 passed |
| 51–53 | `node-10` | 0, 0, 0 | 38 passed |
| 54–56 | `single-recovery` | 0, 3, 3 | 11 passed |
| 57–59 | `diag` | 0, 0, 0 | 47 passed |
| 60–62 | `pair-7-8` | 0, 0, 0 | 39 passed |
| 63–65 | `pair-7-9` | 0, 0, 0 | 39 passed |
| 66–68 | `pair-7-10` | 3, 3, AV | 없음 |
| 69–71 | `pair-8-9` | 0, 0, 0 | 39 passed |
| 72–74 | `pair-8-10` | 3, 3, 3 | 없음 |
| 75–77 | `pair-9-10` | 0, 0, 0 | 39 passed |
| 78–80 | `diag-light-native` | 3, 3, 0 | 47 passed |
| 81–83 | `diag-pair` | 0, 0, 0 | 39 passed |
| 84–86 | `diag-roots` | 3, 3, 3 | 없음 |
| 87–89 | `final-none` | 3, 3, 3 | 없음 |
| 90–92 | `final-both` | 0, 0, 0 | 47 passed |

최종 fixture 없는 대조 3/3 abort, 원래 fixture 대조 3/3 정상 종료(매회 47 passed)를 다시 확인했다. 파일 복원 SHA-256은 `3412e1da1c0be21d38e0c9b883292d41bd43be74d7a1cd03e63149a371e8688c`로 보관 원본과 같다. 시험 파일·이 기록은 LF, 현황 문서는 CRLF로 유지했다.

## 남은 것

- 최종 C++ instance·하위 타입·virtual 호출과 그 메모리 수명 오류는 미확정이다. native 디버그 심볼 또는 삭제 큐의 instance 추적이 필요하다. 별도의 access violation도 직접 계측하지 못했다.
- 전체 래퍼 계측의 관찰 효과, 다른 GC 시점·시험 순서·환경, 실제 제품 Store·모델을 포함한 종료 경로와 C의 독립 재검증은 남아 있다. dispatch·wire·부작용 계수는 측정하지 않았다.
- `.cache/codex-a172/`는 gitignore된 로컬 산출물이다. 핵심은 `stage-0.json`–`stage-3.json`, `stage-4-probes.json`, `stage-5.json`, `abort-evidence.json`, `results.json`, `diag_plugin.py`, `native_abort.py`, 독립 재현 스크립트와 회차별 로그다. 기존 `.cache/codex-pr96/`는 건드리지 않았다.
- 커밋·push·브랜치 변경·외부 프로세스 종료·네트워크·인증정보·시스템 설정 변경은 하지 않았다. 저장소 변경은 이 완료 기록과 A172 현황 한 줄뿐이다.
- **검수 중 따로 본 것(A171 fixture가 들어간 main `d24eee5`):** A175 검수에서 A 화면 시험 묶음을 돌렸을 때 6회 중 3회가 중단됐다. 순서는 `test_store_query_link`·`test_ui_approvals`·`test_call_logs`·`test_a_architecture`·`test_approval_detail`·`test_ui_approval_t18`·`test_ui_display_escaping`·`test_ui_keyboard`·`test_ui_render_projection`였다.
  - 중단은 모든 시험이 통과한 뒤 프로세스 종료 중에 났다(`78 passed` 출력 뒤 `Fatal Python error: Aborted`, Python 프레임 없음, 종료 코드 3). A175 브랜치에서도 2/6이었다.
  - 이 묶음의 다른 A 화면 시험은 위젯을 `close()`만 하고 삭제하지 않는다. 이번 경로(worker GC 뒤 메인 지연 삭제)와 같은 원인인지는 확인하지 않았다.
  - 전체 시험(C 리뷰 3, 5,650 passed)의 파일 순서에서는 나지 않았지만, 시험 순서에 따라 결과가 실패(종료 코드 3)로 바뀔 수 있다.
- 후속:
  - A 화면 시험 전체에 같은 정리를 넣을 방법을 정한다. `tests/unit` 공용 conftest는 다른 담당 시험에도 걸리므로 합의가 필요하다.
  - 제품 `run_application`의 창·페이지 수명 정리를 위 제안대로 구현할지 정한다.
