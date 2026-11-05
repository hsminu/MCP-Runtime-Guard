# A220 · 시작 거부 문구를 print 대신 로거로(Sparrow 04.01)

2026-11-05 · `feat/a-application` · PR #118. 출처: 트랙B 소스코드 취약점 결과 보고서(Sparrow SAST, 분석 2026-10-30), 사용자 지시("우리 A담당 버그니까 고치고 PR올려").
관련: A102(잠금 거부), A106(DB 거부), A129(설정 파일 거부), A146(로그 가림 설치), A147(로그 파일 handler).

## 문제

- Sparrow가 행안부 보안약점(Python) 2022 "04.01 오류 메시지 정보노출"로 `src/guard_app/__main__.py` 두 곳을 보고했다. 위험도는 둘 다 "높음"이고, 보고서 전체 이슈가 이 2건이다.
  - 55행: 단일 실행 잠금 거부(`codex.claim()`의 `AppPathError`)
  - 71행: 제품 DB 파일 거부(`codex.db_file`의 `AppPathError`)
  - 둘 다 `print(_claim_refusal(error), file=sys.stderr)`였다. 잡은 예외의 `args[0]`로 만든 문자열을 `print`로 냈다.
  - 보고서 의견: 에러 메시지를 직접 출력하지 말고 logger로 기록할 것.
- 실제로 나가던 문자열은 고정 문구다. `_claim_refusal`은 `APP_[A-Z_]{1,60}` 모양의 코드만 붙이고 경로·예외 원문은 내지 않는다. A102 시험이 경로가 안 나오는 것을 이미 단언한다. 노출되는 내용은 없었지만 검사 규칙(잡은 예외 → `print`)에는 걸린다.
- 63행 설정 파일 거부(`_settings_refusal(error.code)`)도 같은 모양인데 보고되지 않았다. Sparrow가 사용자 정의 속성 `.code`는 예외 정보로 보지 않은 것으로 보인다(미확정).

## 한 일

- `__main__.py`에 `_notice(text)`를 두었다. `guard_app.startup` 로거에 ERROR로 남기고, 그 기록 하나만 현재 stderr로 보낸다.
  - 로그 파일에는 쓰지 않는다. 이 거부들은 앱 폴더를 잡기 전이나 확인하는 중에 나와서 로그 파일(A147)이 아직 없다. 잠금을 못 잡은 경우에는 다른 실행이 쓰는 로그 파일이라 써서도 안 된다. 그래서 보고서 의견 중 "로그 파일에 기록"은 따르지 않고 stderr 안내를 유지했다.
  - 로거 이름은 `guard_app.`으로 시작해야 한다. `python -m guard_app`으로 실행하면 `__name__`이 `__main__`이 되고, C 가림(`install_log_redaction`)이 그 이름의 메시지를 `LOG_WITHHELD`로 바꾼다.
  - handler는 기록 하나 동안만 붙인다. 모듈에 handler를 고정하면 만들 때의 stderr를 붙잡는다. `logging.lastResort`에 기대면 루트에 다른 handler가 생기는 순간 안내가 사라진다.
- 55·63·71행의 `print`를 `_notice`로 바꿨다. 63행은 보고되지 않았지만 같은 모양이라 함께 바꿨다.
- `print` 두 곳은 그대로 두었다. 90행(로그 파일 열기 실패)과 173행(정리 미확인 종료)은 고정 문자열이고 예외 값을 쓰지 않는다.
- 문구·종료 코드·잠금 해제 순서는 바뀌지 않았다.

## 시험

- `test_a_architecture.py::test_a_caught_error_never_reaches_print`(새 시험): `__main__`에서 `except ... as 이름`으로 잡은 이름이 `print` 인자에 들어가면 실패한다. A 모듈 중 `print`를 쓸 수 있는 것은 기존 규칙상 `__main__`뿐이다.
- `test_sqlite_app.py::test_cli_product_does_not_start_without_the_app_lock`(단언 추가): `print`를 부르면 실패하게 했다. 그다음 `guard_app.startup` ERROR 기록이 하나이고 stderr 출력이 그 메시지와 같은지 본다. 경로가 안 나오는 기존 단언은 그대로다.

## 검증

2026-11-05, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| 수정 전 새 규칙 시험 | 실패: 55·63·71행 |
| 수정 후 `__main__`을 부르는 시험 7개 파일(`test_a_architecture`, `test_codex_preparation`, `test_product_settings`, `test_product_key_and_log`, `test_a138_product_startup`, `test_a179_ready_exception_lock`, `test_sqlite_app`의 잠금 거부 시험) | 118 통과, 하위 21 통과 |
| pytest 밖 실제 진입점(잠금 거부를 주입한 `main()`, 코드 3종) | 종료 코드 1, stdout 0바이트, stderr 한 줄. 수정 전과 stderr 바이트가 같음 |
| 단위 시험 전체 `pytest -q`(`QT_QPA_PLATFORM=offscreen`) | 5788 통과, 26 건너뜀, 하위 1303 통과(6분 27초) |
| 계약 시험 `pytest -q tests/contract` | 45 통과 |
| `ruff check .`·`ruff format --check .`(401개) | 통과 |
| `mypy`(145개) | 통과 |
| import-linter | 계약 7개 유지 |
| `bandit -r src -ll -q`(lock 버전 1.9.4, `uvx`로 실행) | 0건, 종료 코드 0 |
| 되돌림: `__main__.py`만 수정 전(HEAD `f3a1fd7`)으로, 별도 worktree에서 | 4 실패: 규칙 시험(55·63·71행), 잠금 거부 시험 3건(`print` 호출) |

## 한계

- Sparrow 재검사는 하지 않았다. 이 환경에 도구가 없다. 검사기가 `_notice` 안의 `logging.StreamHandler(sys.stderr)`를 출력으로 볼지는 미확정이라 재검사로 확인해야 한다.
- Semgrep은 돌리지 않았다. 바뀐 코드에 `except`·`eval`·`subprocess` 같은 규칙 대상 구문은 없다.
- 로컬 `.venv`에는 lock에 있는 `bandit`이 설치되어 있지 않았다. venv를 다시 맞추지 않고 `uvx`로 같은 버전만 따로 돌렸다.
- `docs/weakness_register.md` 반영은 공통 문서라 사용자 몫이다.
