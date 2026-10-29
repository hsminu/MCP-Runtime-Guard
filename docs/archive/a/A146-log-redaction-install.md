# A146 · 제품 진입점 로그 가림 설치

2026-10-29. 관련: I9, S04, 개발문서 0.4·2·13.2절, C2.8(#74). PR: 미정.

## 한 일

- `__main__.main()`의 첫 동작으로 C `install_log_redaction()`을 import하고 한 번 호출한다. 인자 파서 생성·해석보다 앞선다. Qt·bridge·화면 import는 `_run()`으로 옮겨 제품 import 부작용도 설치 뒤에 일어나게 했다. 모듈 로딩에는 표준 라이브러리와 상수 정의만 남는다.
- `bootstrap_product`, `assemble_guard`, 합성 시연 조립에는 설치를 넣지 않았다. `main --demo`는 같은 진입점 방어를 쓰지만 조립 함수를 직접 부르는 시험에는 영향을 주지 않는다.
- C 구현은 레코드 생성 함수를 중복으로 감싸지 않는다. 예외 훅 3개와 warnings도 바꾼다. C의 `_uninstall_for_tests`는 감싼 생성 함수를 남기고 비활성화하므로, A 시험은 진입 전 생성 함수·활성 상태·예외 훅·warnings의 현재 함수와 저장된 이전 함수를 모두 보존하는 fixture로 정확히 복원한다. `main()`을 부르는 기존 시험 세 파일(`test_codex_preparation`, `test_product_settings`, `test_sqlite_app`)에 적용했다. `BackendBridge` import가 `_run()`으로 옮겨져 세 파일의 대역은 `guard_app.services.bridge.BackendBridge`를 바꾼다.
- 새 시험은 새 모듈 네임스페이스의 import와 `main --help` 호출 순서를 기록한다. 실제 설치 뒤 파서에서 라이브러리 레코드가 `LOG_WITHHELD`인지 확인한다. 제품 Guard 조립과 시연 조립을 실제 실행하고 설치 호출 0회와, 레코드 생성 함수·`sys.excepthook`·가림 활성 상태가 그대로인지 확인한다. 뒤 단언은 조립 모듈이 패치 전에 import해 둔 설치 함수를 부르는 경우도 잡는다.
- handler(A147), C 구현, 공통 문서, `docs/a-status.md`는 수정하지 않았다.

## 실제 검증

Windows, 로컬 `.venv` Python 3.12, Git Bash. 기본 사용자 임시 폴더는 접근 거부되어 아래 작업 폴더 경로로 실행했다.

```bash
export TMPDIR=$PWD/.cache/a-work/a146-tmp TEMP=$PWD/.cache/a-work/a146-tmp TMP=$PWD/.cache/a-work/a146-tmp QT_QPA_PLATFORM=offscreen
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/a146-final2 tests/unit/test_product_entry_logging.py tests/unit/test_codex_preparation.py tests/unit/test_product_settings.py tests/unit/test_codex_browser_login.py tests/unit/test_account_runtime.py tests/unit/test_ui_model_settings.py tests/unit/test_product_assembly.py tests/unit/test_codex_product_flow.py tests/unit/test_stdio_logging.py tests/unit/test_call_logs.py
```

- 최종 결과: 113 통과, 서브테스트 40 통과, 9.52초. 전체 단위·contract·보안 스캔은 미실행이다.
- 변경 Python 7개 파일에 `python -m ruff check`와 `python -m ruff format --check`: 통과. 대상은 `__main__.py`, `ui/presentation.py`, `tests/unit/{logging_state,test_product_entry_logging,test_codex_preparation,test_product_settings,test_codex_browser_login}.py`다.
- `.venv/Scripts/python.exe -m mypy`: 114개 소스 통과.
- `.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini`: 7개 계약 유지, 0개 위반.
- `git diff --check`: 통과.
- 처음 실행은 시험 fixture 등록·파서 대역 방식 오류와 임시 폴더 접근 거부로 실패했다. 시험 구현과 실행 경로를 고친 뒤 위 결과를 확인했다.
- 리뷰 보강 뒤(`test_sqlite_app` 대역·fixture, 조립 시험 전역 상태 단언): `test_product_entry_logging`, `test_sqlite_app`, `test_codex_preparation`, `test_product_settings`, `test_codex_browser_login` 5개 파일 75 통과, 서브테스트 68 통과. 고친 시험 3개 파일 Ruff check·format check 통과.

회귀마다 해당 시험 노드 하나를 `pytest -q -p no:cacheprovider --basetemp=.cache/a-work/a146-mut-<이름>`으로 실행했다. 제품 파일은 매번 `finally`에서 원래 바이트로 복원하고 일치까지 확인했다.

| 고의 회귀 | 시험 결과 |
| --- | --- |
| `main()` 설치 제거 | 시작 순서 시험 1개 실패 |
| 파서 생성 뒤로 설치 이동 | 시작 순서 시험 1개 실패 |
| `bootstrap_product`에 설치 추가 | 조립 미설치 시험 1개 실패 |
| 합성 시연 `bootstrap`에 설치 추가 | 조립 미설치 시험 1개 실패 |
| `assemble_guard`에 설치 추가 | 조립 미설치 시험 1개 실패 |
| `session_runtime` 모듈 수준에서 설치 함수를 import하고 `bootstrap_product`에서 호출, `test_codex_preparation`이 먼저 import하는 순서 | 호출 기록은 0회로 남지만 전역 상태 단언으로 조립 미설치 시험 1개 실패 |

## 남은 한계

- C의 모양 기반 가림·직접 stderr/print 한계는 C2.8 그대로다. 원문을 로그에 넘기지 않는 1차 방어를 대체하지 않는다. A147 handler는 별도 작업이다.
- #74 A 리뷰 댓글 원문은 GitHub 연결 도구 404, 웹 조회 실패, 로컬 gh 네트워크 거부로 읽지 못했다. 사용자 작업 지시·현황 줄·C2.8 기록·현재 C 구현을 근거로 연결했다. 원문 확인은 남는다.
- 새 B/C API나 계약 변경 요청은 없다. C의 변경 리뷰·독립 인수는 별도다.
