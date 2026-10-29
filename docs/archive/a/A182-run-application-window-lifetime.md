# A182 · 제품 `run_application` 창·페이지 수명 정리

2026-10-30 · `wip/a179`(head `05938b3` 기준). PR: 다음 A PR(아직 없음)
관련: [A172](A172-store-query-link-abort-cause.md)·A178·[A179](A179-ready-exception-lock-order.md), 개발문서 12.1절, I9. T19·S04의 예외 출력 방지 회귀 보조 검증이며 공격·로그 마스킹 전체 인수는 아니다.

## 문제

backend 종료를 확인해도 `run_application`이 창·패널·페이지 삭제를 GC에 맡겼다. A172에서 늦은 lifecycle 콜백보다 먼저 창을 삭제하면 오류가 났으므로, 종료 확인 뒤 남은 이벤트를 처리하고 메인 스레드에서 명시적으로 삭제해야 한다. A179의 예외 경로에서 Store 종료 확인과 잠금 유지 의미는 보존해야 한다.

작업 브랜치 `wip/a179`에는 A178이 없어 아래 검증 표는 A178 없이 잰 것이다. A178과 합친 결과는 "A 검수에서 다시 잰 것"에 있다.

## 한 일

- `src/guard_app/ui/application.py`: 이번 timer 정지 → `QCoreApplication.sendPostedEvents()` → `QCoreApplication.processEvents()` → `window.deleteLater()` → `sendPostedEvents(None, QEvent.Type.DeferredDelete)` 순서의 정리를 추가했다. 영어 주석에 `(A182, A172)` 근거를 남겼다.
- 정상 반환은 `done and not bridge.cleanup_unconfirmed`인 경우에만 정리한다. A179 예외 경로는 같은 종료 확인 뒤 정리하고 원래 예외를 전파한다. 확인되지 않으면 기존 `BackendCleanupUnconfirmedError` 또는 `(code, False)` 의미를 유지하며 창을 삭제하지 않는다. 살아 있는 backend가 보낼 큐 신호의 대상이 필요하고, 이후 제품은 `_end_unconfirmed()`로 종료하기 때문이다.
- `aboutToQuit`의 이번 `bridge.close` 연결은 finally에서 해제한다. 미확인 경로에서도 해제할 수 있다. 실제 Qt 종료 신호 또는 창 닫기에서 이미 close를 요청했으며, 예외 경로는 finally에서 명시적으로 요청한다. 연결 해제는 backend를 멈췄다고 판정하거나 창을 삭제하는 동작이 아니다. 미확인 반환 후 프로세스를 끝내는 기존 경로도 바꾸지 않았다.
- 제품 `gc.collect()`·넓은 except·새 로그·계약 변경·UI의 금지 계층 import는 추가하지 않았다. 원래 예외 객체의 identity, 남은 종료 관찰 예산과 A179 잠금 시험을 유지했다.
- `tests/unit/test_a182_window_lifetime.py`: 실제 BackendBridge 스레드와 실제 창에 I/O 없는 bindings를 주입했다. patched exec 실행 중 `topLevelWidgets()`로 창을 얻고 승인·호출 로그 페이지는 공개 속성으로, RuntimePanel은 `attach_runtime` 인자로 받았다. 자식 열거 API는 쓰지 않았다.
- 정상 반환·READY 뒤 예외·실제 Qt exec 반환을 메인/worker GC 각각 시험했다(6개). 두 미확인 경로도 시험했다(2개). 삭제 직전에 메인 스레드·RuntimePanel의 CLOSED 표시·창 lifecycle 콜백의 공개 draft 초기화를 관찰한다. 정상 경로는 CLOSED가 아직 큐에 있고 draft가 남은 상태에서 exec를 끝내므로 삭제 전 이벤트 처리 순서를 확인한다.
- 창·패널·승인·호출 로그 페이지 네 래퍼의 `shiboken6.isValid == False`, topLevelWidgets의 GuardWindow 부재와 timer 정지를 확인했다. 예외는 원래 객체가 그대로 전파됐다. 미확인 경로는 네 래퍼가 모두 유효하고 backend가 살아 있었다. 모든 경로에서 이후 `aboutToQuit.emit()`이 이번 bridge의 close 계수를 올리지 않았다.
- `sys.excepthook`·`sys.unraisablehook`·`threading.excepthook`·Qt 메시지 handler와 stderr를 관찰했다. offscreen의 알려진 size-hint 경고만 제외하고 예외·다른 Qt 메시지는 고정 코드로 실패시킨다. 미확인 시험은 차단을 풀고 backend 종료·join을 확인한 뒤 timer와 창을 직접 정리한다. 이 timer 정리는 QApplication을 계속 쓰는 시험에만 있다.
- **기존 시험 수정 전 확인한 원인:** A179의 가짜 Signal에는 disconnect가 없어 15개가 실패했다. `tests/unit/test_a179_ready_exception_lock.py`에 대역 disconnect와 시험 timer 정리를 추가하고 중복 aboutToQuit 연결 해제를 제거했다. 키·로그 시험 5개는 제품이 이미 삭제한 창에 teardown이 hide를 호출해서 실패했다. `tests/unit/test_product_key_and_log.py`는 제품 삭제를 단언하고 유효한 창만 직접 정리한다. 두 파일은 지정 목록 외 추가 변경이다.
- `docs/a-status.md`의 `run_application` 수명 정리 줄을 완료로 바꿨다.

## 검증

Windows, Python 3.12.15, PySide6·Shiboken6·Qt 6.12.0, pytest 9.1.1을 설치된 환경에서 확인했다. 전체 시험은 실행하지 않았다. 임시 파일·로그·검사 캐시는 `.cache/codex-a182/` 안에 두었다.

```powershell
$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; $env:QT_QPA_FONTDIR='C:/Windows/Fonts'; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'
$py='..\..\..\.venv\Scripts\python.exe'
```

pytest는 모두 `& $py -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a182/pt-<n> --tb=short <대상>`으로 실행했다. TEMP·TMP·Ruff·mypy·import-linter 캐시도 작업 임시 디렉터리 아래로 지정했다.

| 회차 | 대상·조작 | 결과 | 종료 코드 |
| --- | --- | --- | --- |
| pt-6 | 새 시험 단독, 초기 시험 보정 뒤 | 8 passed | 0 |
| pt-7 | 기존 A179, 대역 보정 전 | 15 failed, 4 passed | 1 |
| pt-8 | 명세의 7파일 묶음 그대로 | A178 파일 누락, no tests ran | 4 |
| pt-9 | A178을 뺀 6파일, 키·로그 teardown 보정 전 | 5 failed, 108 passed, 21 subtests passed | 1 |
| pt-10 | 정리 호출 두 곳 제거, 정상·예외 각각 main GC 노드 | 2 failed, 창 무효 단언에서 고의 회귀 검출 | 1 |
| pt-11 | 확인 조건 없이 항상 정리, 미확인 예외 노드 | 1 failed, 1 error. 창 유지 단언 실패와 늦은 콜백 예외 hook 검출 | 1 |
| pt-12 | 새 시험 새 프로세스 반복 1 | 8 passed | 0 |
| pt-13 | 새 시험 새 프로세스 반복 2 | 8 passed | 0 |
| pt-14 | 새 시험 새 프로세스 반복 3 | 8 passed | 0 |
| pt-15 | 새 시험 새 프로세스 반복 4 | 8 passed | 0 |
| pt-16 | 새 시험 새 프로세스 반복 5 | 8 passed | 0 |
| pt-17 | 기존 시험 보정 뒤, 존재하는 관련 6파일 | 113 passed, 21 subtests passed | 0 |

pt-10·11은 각각 실행기 finally에서 제품 파일을 저장한 원래 바이트로 복원했다. 두 회차 모두 복원 SHA-256은 `8d0c5a79026f0702afdc28bcd383b7d57f5c2ed95f505cecee94c599229d3e21`로 원본과 일치했다. pt-12–17은 모두 traceback·RuntimeError·abort 없이 프로세스가 끝났다.

pt-17 대상은 `test_a182_window_lifetime.py`, `test_a179_ready_exception_lock.py`, `test_product_key_and_log.py`, `test_a138_product_startup.py`, `test_codex_preparation.py`, `test_product_settings.py`(모두 `tests/unit/`)다. pt-8은 A178 시험 파일이 이 브랜치에 없어 수집되지 않았다.

| 정적 검사 | 결과 | 종료 코드 |
| --- | --- | --- |
| `ruff check` 변경 Python 4파일 | 통과 | 0 |
| `ruff format --check` 같은 4파일 | 4 files already formatted | 0 |
| `& $py -m mypy src` | 140 source files, 오류 없음 | 0 |
| `lint-imports --config tools/ci/importlinter.ini --cache-dir .cache/codex-a182/import-linter` | 199 files·1200 dependencies, 7 kept·0 broken | 0 |
| `git diff --check` | 통과 | 0 |

### A 검수에서 다시 잰 것

A178이 들어 있는 `wip/next`에 이 변경을 얹어 다시 쟀다. a-status 충돌은 A178의 종료 중 abort 완료 줄을 살리고 이 항목 줄만 바꿔 풀었다.

- 작업 브랜치에서 관련 7개 파일(pt-17의 6개 + `test_a180_cancel_during_apply.py`): 120 passed, 56 subtests passed, 종료 코드 0.
- `wip/next`에서 관련 10개 파일: 179 passed, 56 subtests passed, 종료 코드 0.
  - pt-17의 6개
  - `test_a178_qt_ownership.py`
  - `test_a180_cancel_during_apply.py`
  - `test_bridge.py`
  - `test_a_architecture.py`
- `wip/next`에서 새 시험을 새 프로세스로 3회: 매회 8 passed, 종료 코드 0.

## 남은 것

- 실제 데스크톱 창의 종료를 화면으로 확인하지 않았다. offscreen의 실제 Qt 이벤트 루프를 종료한 뒤 deleteLater·DeferredDelete로 네 래퍼가 무효화되는 것은 직접 확인했다.
- A178과 합친 검증은 위 관련 묶음과 반복 3회까지다. A 화면 시험 9개 파일 묶음의 반복 대조는 A178에서 했고, 이 변경 뒤에는 다시 돌리지 않았다(그 묶음은 `run_application`을 거치지 않는다).
- 새 시험은 실제 Store·모델·외부 서버를 쓰지 않는다. 관련 A179 시험은 기존 임시 Store·앱 잠금 검증을 실행했지만, 실제 제품 종단·사용자 데이터·GUI 수동 검증이나 dispatch·wire·부작용 계수는 측정하지 않았다.
- 다른 Qt 버전·플랫폼·시험 순서·더 늦은 외부 signal 생산자와 C의 독립 인수는 미확인이다. 일반적인 Qt 객체 수명 안전성으로 확대하지 않는다.
- 네트워크·인증정보·사용자 앱 데이터 폴더는 쓰지 않았다. 공통 문서와 B·C 소유 파일은 수정하지 않았다.
