# A210 · codex.exe가 없거나 버전이 다를 때 원인과 설치 명령 보여 주기

2026-10-30 · `wip/next` · PR #110. 출처: 사용자 요청("codex.exe가 없으면 'Codex 실행 기능이 아직 준비 중'이라고만 나와요 ... 안내와 코드를 바로 보여 주면 좋아요").
관련: A101(Codex 준비), A151(L2 시작 검사), C6.13(`tools/install_codex.py`).

## 문제

- codex.exe가 없으면 시작 준비가 이미 고정 코드 `CODEX_EXECUTABLE_MISSING`(크기가 다르면 `CODEX_EXECUTABLE_MISMATCH`)를 냈다. 그런데 화면까지 오는 동안 두 곳에서 지워졌다.
  - `session_startup.call_preparation`이 모든 실패를 `CODEX_PREPARATION_FAILED`로 바꿨다. `StartupGate`도 예외로 끝난 시작 준비의 코드를 담지 않았다.
  - `CodexProcessPreparation._launch_account`가 코드 없는 `AccountUnavailableError`를 냈다.
- 그래서 로그인 화면은 "Codex 실행 기능이 아직 준비 중이라 …"만 보였다. 채팅은 "Codex 세션 준비를 완료하지 못함 … 앱 설정을 확인하고 …"만 보였다.

## 한 일

- `session_preparation.py`
  - `CODEX_INSTALL_CODES`(두 코드)를 더했다.
  - 고정 코드 하나만 싣는 `PreparationRefusedError`를 더했다. `CodexPreparationError`가 이를 상속한다.
- `call_preparation`
  - 두 설치 코드만 그대로 `SessionRuntimeError(code)`로 넘긴다. 나머지는 지금처럼 `CODEX_PREPARATION_FAILED` 또는 `BackendCleanupError`다.
  - 바꾼 예외의 `__context__`도 지워 원래 예외(경로·원문)가 남지 않게 했다. `from None`은 문맥을 숨기기만 하고 객체는 남긴다.
- `StartupGate.check`: 시작 준비가 `SessionRuntimeError`로 끝나면 그 코드를 `blocker()`와 이후 시도에 쓴다. 시작 준비는 다시 하지 않는다.
- Codex 준비·계정
  - 시작 때 실행 파일 검사가 설치 코드로 실패하면 그 코드를 기억한다. 계정 실행은 `AccountUnavailableError(code)`를 낸다.
  - `ProductAccount`는 허용된 두 코드일 때만 `AccountView("UNAVAILABLE", notice=code)`로 보낸다.
- 문구(`presentation.py`): 설치 명령은 고정 상수 `.venv\Scripts\python.exe tools\install_codex.py`다. 경로나 원문은 싣지 않는다.
  - 로그인 화면: "codex.exe가 설치되지 않음. 프로젝트 폴더에서 아래 명령을 실행한 뒤 앱을 다시 시작하세요." 아래에 선택 가능한 명령과 "설치 명령 복사" 버튼을 보인다.
  - 채팅 새 세션 안내: 같은 안내에 줄바꿈하고 명령을 붙인다.
  - 버전이 다르면 첫 문장이 "설치된 codex.exe가 고정 버전과 다름."이다.
- 기존 `test_codex_preparation.py`의 기대값 하나를 바꿨다. 크기가 다른 실행 파일의 시작 거절 코드가 `CODEX_PREPARATION_FAILED`에서 `CODEX_EXECUTABLE_MISMATCH`가 된다. 이번 변경의 의도된 결과다.
- 구현은 Codex(`gpt-6-astra`, high)가 했다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| Codex 실행: 새 시험 `test_a210_codex_install_notice.py`(31개)와 codex_preparation, session_preparation, account_runtime, a151_l2_startup, ui_model_settings, a_architecture, a178_qt_ownership | 전부 통과(Codex 보고) |
| Codex 실행: `ruff check`·`ruff format --check`, `mypy src`, import-linter, `git diff --check` | 통과(Codex 보고) |

리뷰에서는 시험을 다시 돌리지 않았다. 되돌림 검사만 새 시험 파일 하나로 돌렸다(전부 복원 뒤 바이트 같음).

| 되돌림 | 잡은 시험 |
| --- | --- |
| 설치 코드 통과 끔 | 4 실패 |
| `StartupGate`가 거절 코드 저장 안 함 | 2 실패 |
| 계정용 거절 코드 저장 안 함 | 2 실패 |
| `AccountUnavailableError` 코드 버림 | 8 실패 |
| 설치 줄 항상 보임 | 2 실패 |
| 바꾼 예외의 원문 문맥 남김 | 6 실패 |

## 한계

- 실제 앱에서 codex.exe를 지우고 띄워 보지는 않았다.
- 명령은 프로젝트 폴더의 `.venv`가 있는 개발 실행을 전제한다. 설치 파일로 배포하는 구성은 아직 없다.
- `--codex-exe`로 준 경로가 없을 때도 같은 설치 안내가 나온다. 그 경로는 화면에 싣지 않는다.
