# A179 · #101 B-ACCEPT-01: READY 뒤 예외의 Store 종료와 앱 잠금 순서

2026-10-30 · `wip/a179`(head `48e0b36` 기준). PR: 다음 A PR(아직 없음)
관련: #101 B-ACCEPT-01, REC-01·REC-05, A110·A116. I6·I9, 개발문서 11.5·12.1절, T19·S04 로그 보호 회귀.

## 문제

READY 뒤 `app.exec()`가 예외를 내면 `run_application`의 종료 관찰을 건너뛰었다. `main()`의 `confirmed=None` 경로는 backend와 Store writer가 끝나기 전에 앱 잠금을 풀어, 두 번째 소유자가 들어올 수 있었다.

## 한 일

- `services/bridge.py`: 메시지 없는 `BackendCleanupUnconfirmedError`를 추가했다.
- `ui/application.py`: `window.show()`부터 `app.exec()`까지 `try/finally`로 감싸고, 정상 반환 flag가 없으면 `bridge.close()`와 남은 관찰 예산의 `wait_done()`을 실행한다. 종료 미확인 또는 `cleanup_unconfirmed`는 새 예외, 종료 확인은 원래 예외 전파다. 정상 반환 경로는 유지했다.
- `__main__.py`: 로그 보호 설치 뒤 함수 안에서 새 예외를 import하고, 그 예외만 `(EXIT_CLEANUP_UNCONFIRMED, False)`로 처리한다. handler는 정리하고 앱 잠금은 유지한 채 `_end_unconfirmed()`로 간다. 데모도 같은 종료 경로다.
- `test_a179_ready_exception_lock.py`: 실제 claim·bootstrap_product·SQLiteStore·BackendBridge·Qt 창으로 READY 뒤 예외 두 경우와 시작 전 창 생성 실패를 시험했다. 별도 `_ProcessStartGate`와 임시 앱 root를 사용했다. 모델·계정 연결은 주입 대역이며 외부 서버·네트워크·인증정보는 사용하지 않았다.
- 종료 확인 시험은 `codex.close()` 진입 순간 `(thread_done, backend_alive, writer_alive, execution_enabled) == (True, False, False, False)`와 두 번째 claim 성공을 확인한다. 미확인 시험은 backend 정지를 막고 `os._exit` 대역에서 종료 코드 3 및 두 번째 claim 거부를 확인한다. 대역 종료 뒤에도 잠금은 유지된다. teardown에서 막힌 정지를 풀고 backend·writer·잠금을 모두 정리한다.
- 가짜 bridge 시험은 show·start·exec·창 닫기 뒤 예외, 종료 미확인 flag, 남은 시간 예산 및 정상 반환값을 확인한다. 기존 로그 시험은 새 예외에서도 handler 정리·잠금 유지·종료 호출을 확인한다. 기존 일반 예외는 시작 전 또는 종료 확인 뒤 예외라는 의미로 유지했다.
- Qt 객체는 메인 스레드에서 `deleteLater` → DeferredDelete 전달 → `processEvents` → `gc.collect()` 순서로 정리한다. 기존 파일과 새 파일의 CRLF를 유지했다.
- A179 지정 설계에서 벗어난 제품 동작 변경은 없다. 넓은 except·finally return·계약 변경은 추가하지 않았다.

## 검증

Windows 로컬 Python 3.12. 환경은 다음과 같았다. 임시 파일·검사 캐시는 `.cache/codex-a179/`에만 두었다. 전체 시험은 실행하지 않았다.

```powershell
$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; $env:QT_QPA_FONTDIR='C:/Windows/Fonts'; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONIOENCODING='utf-8'
$py='..\..\..\.venv\Scripts\python.exe'
```

모든 pytest 명령은 `& $py -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a179/pt-<n>`으로 실행했다. `pt-1`은 `--tb=short`, 나머지 아래 명령은 `--tb=no`였다.

| 실행 | pytest 대상·조작 | 결과 | 종료 코드 |
| --- | --- | --- | --- |
| pt-1 | `tests/unit/test_a179_ready_exception_lock.py` 단독 | 19 passed | 0 |
| pt-4 | application의 finally를 제거한 뒤 `test_ready_exception_confirmed_releases_only_after_store_end`와 `test_ready_exception_unconfirmed_keeps_lock_through_process_exit` | 2 failed, 고의 회귀 검출 | 1 |
| pt-5 | main의 새 except를 제거한 뒤 위 미확인 시험과 `test_product_log_settings_redaction_and_cleanup[cleanup_exception]` | 2 failed, 고의 회귀 검출 | 1 |
| pt-6 | 아래 지정 관련 시험 묶음, 복구된 제품 코드로 한 번 실행 | 12 failed, 236 passed, 44 subtests passed | 1 |

`pt-4`의 두 대상은 `tests/unit/test_a179_ready_exception_lock.py::<시험 이름>`, `pt-5`의 로그 대상은 `tests/unit/test_product_key_and_log.py::test_product_log_settings_redaction_and_cleanup[cleanup_exception]`다.

```powershell
& $py -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a179/pt-6 --tb=no tests/unit/test_a179_ready_exception_lock.py tests/unit/test_a180_cancel_during_apply.py tests/unit/test_product_key_and_log.py tests/unit/test_codex_preparation.py tests/unit/test_a138_product_startup.py tests/unit/test_bridge.py tests/unit/test_a_architecture.py tests/unit/test_a155_tool_settings.py tests/unit/test_a156_destinations.py tests/unit/test_session_collaborators.py
```

관련 묶음의 실패는 A180 정책·목적지 2개 사례와 그 안의 실패 하위 단언 10개다. A179 및 기존 관련 시험의 실패는 없었다. A180 상세는 [별도 기록](A180-cancel-during-apply.md)에 있다. 실행한 pytest 프로세스에서 종료 코드 3이나 access violation은 관찰하지 않았다. 시험 안에서 관찰한 종료 코드 3은 의도한 `_end_unconfirmed` 대역 값이다.

각 고의 회귀는 `try/finally`로 회귀 직전 바이트를 복원했고 SHA-256이 일치했다. 두 해시는 회귀 전과 복구 후 각각 같다.

- `application.py`: `7E3C4304DC73648E430F59D7FE4E6111B1D9209E2A14FD49284B4AB2EB0EE528`
- `__main__.py`: `7EBA4DA0299AE4DCC0307DDFF999C2268259A77B50C3EAF92E0FA5B476F8B7A8`

바뀐 Python 6개(제품 3개, A179·A180 새 시험, 기존 로그 시험)를 다음 명령의 대상으로 검사했다.

- `ruff check <6개 파일>`: 통과, 종료 코드 0. 초안 `ruff check --fix`는 7개 형식·parametrize·with 지적과 종료 코드 1이었고 시험 코드만 보정했다.
- `ruff format <6개 파일>`: 3 reformatted, 3 unchanged, 종료 코드 0. 뒤의 `ruff format --check <6개 파일>`: 6 already formatted, 종료 코드 0.
- `& $py -m mypy src`: 140 source files 통과, 종료 코드 0.
- `..\..\..\.venv\Scripts\lint-imports.exe --config tools/ci/importlinter.ini --cache-dir .cache/codex-a179/import-linter`: 7 kept, 0 broken, 종료 코드 0.
- `git diff --check`: 통과, 종료 코드 0.
- `RUFF_CACHE_DIR=.cache/codex-a179/ruff`, `MYPY_CACHE_DIR=.cache/codex-a179/mypy`를 지정했다.

### A 검수에서 다시 잰 것

- A180 수정 뒤 pt-6과 같은 10개 파일 묶음을 다시 돌렸다. 238 passed, 54 subtests passed, 종료 코드 0. pt-6의 실패 12개는 A180 결함이었고 수정 뒤 남지 않았다.

## 남은 것

- B가 같은 재현으로 다시 확인해야 한다.
- 실제 프로세스를 강제로 끝내는 시험은 수행하지 않았다. 실제 앱 잠금은 사용했지만 `os._exit`는 시험용 예외로 바꿨다.
- 전체 시험·실제 모델·외부 서버·실제 사용자 앱 데이터·육안 GUI 검증은 수행하지 않았다.
