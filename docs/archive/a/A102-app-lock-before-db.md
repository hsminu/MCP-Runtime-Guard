# A102 · 단일 실행 잠금을 DB 열기 전으로 옮기고, 잠금이 없으면 앱을 시작하지 않음

2026-10-26. PR #35에서 C가 A에 물은 "단일 실행 잠금을 시작 순서 어디에 둘지"(REC-01의 C2.13 몫)에 대한 A 구현이다. A101 시작 순서에서 찾은 빈틈을 사용자 지시로 고쳤다.

## 고친 문제(A101 순서)

- 제품 시작은 `bootstrap_product`에서 SQLite를 먼저 열었다. 잠금(`prepare_app_folders`)은 그 뒤 세션 시작 준비에서 잡았다.
- 잠금 실패(`APP_ALREADY_RUNNING`)도 세션 차단 사유(`CODEX_PREPARATION_FAILED`)로만 남았다. 그래서 두 번째 인스턴스가 같은 `--db-path`로 승인·정책 화면을 쓸 수 있었다.
- Codex 준비(CODEX_HOME·실행 파일)가 실패하면 잠금을 풀었다. 앱은 DB를 연 채 계속 실행됐다.
- A101 이전에는 잠금이 아예 없었으므로 새로 생긴 퇴행은 아니다.

## 한 일

- `services/codex_preparation.py`
  - 새 메서드 `CodexProcessPreparation.claim()`이 `prepare_app_folders()`를 부른다. 단일 실행 잠금, 폴더 권한, 남은 Task 폴더 삭제가 여기서 일어난다.
    - 실패하면 C의 `AppPathError`를 그대로 낸다.
    - 두 번 부르면 `CODEX_FOLDERS_CLAIMED`다.
  - 세션 시작 준비는 더 이상 폴더를 준비하지 않는다. claim 전에 부르면 `CODEX_FOLDERS_UNCLAIMED`다. 세션 차단 사유로는 `CODEX_PREPARATION_FAILED`로 보인다.
  - Codex 준비가 실패해도 잠금은 `close()`까지 유지한다.
- `__main__.py`
  - 제품 모드는 인자 검사 직후 `claim()`한다. QApplication·창·bridge·DB보다 먼저다.
  - 실패하면 stderr에 고정 문구를 쓰고 종료 코드 1로 끝난다. 창을 띄우지 않고 DB 파일도 만들지 않는다.
    - `APP_ALREADY_RUNNING`: "MCP Runtime Guard가 이미 실행 중이라 시작하지 않는다"
    - 그 밖의 C2.13 코드(`APP_` 형식): "앱 폴더를 준비하지 못해 시작하지 않는다(코드)"
    - 코드 형식이 아닌 예외 메시지는 싣지 않는다.
  - 창·bridge·Qt 루프는 `_run`으로 나눴다. 잠금은 `_run`이 어떻게 끝나든 `main`의 finally에서 푼다. 전에는 `app.exec()`만 감쌌다.
- 시연 모드(`--demo`)는 전처럼 잠금을 잡지 않는다.
- B6.17 제안이 전제한 순서(C 단일 소유를 확보한 뒤 DB 연결)와 맞는다.

## 검증

- `tests/unit/test_codex_preparation.py`: 실제 폴더·DACL·잠금을 쓴다.
  - claim 전 시작 준비는 거부되고 앱 폴더도 만들지 않는다.
  - 반복 claim은 거부된다. 다른 소유자의 claim은 `APP_ALREADY_RUNNING`이다. close 뒤에는 다시 잡힌다.
  - 실행 파일 거부 3종(미설치·경로 없음·크기 다름) 뒤에도 잠금이 유지되고, close 뒤에 다시 잡힌다. A101 테스트의 "실패 뒤 잠금 해제" 기대를 이 결정에 맞게 뒤집었다.
  - `main`은 다른 인스턴스가 잠금을 쥐고 있으면 1을 돌려준다. 안내 문구가 나오고, 경로가 없고, DB 파일이 없고, bridge를 만들지 않는다.
  - `main`이 bridge를 만드는 시점에 잠금이 이미 잡혀 있고 DB 파일은 아직 없다. `main`이 끝나면 잠금이 풀린다.
- `tests/unit/test_sqlite_app.py`: claim 거부 코드 3종(실행 중·폴더 코드·코드가 아닌 문자열)
  - 문구가 맞고 원문은 나오지 않는다.
  - DB 파일을 만들지 않고 bridge도 만들지 않는다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15)
  - 단위 4,297개 통과·1개 생략, contract 41개 통과
  - Ruff·포맷(저장소 추적 파일)·mypy(85개 파일) 통과
  - 경고 5개는 모두 기존 경고다. 4개는 AGT 패키지 사용 중단 경고(`test_agt_real.py`), 1개는 `pydantic_settings`의 `lifespan` 정의 경고(`test_mcp_http.py`)다. A101 기록에 "경고 5개 모두 AGT"라고 쓴 것은 이 구분이 틀렸다.

## 남은 한계

- 잠금은 사용자별 앱 폴더(`%LOCALAPPDATA%\MCPRuntimeGuard`) 기준이다. 다른 Windows 사용자가 같은 DB 파일을 여는 것은 막지 못한다. DB 파일 자체의 단일 소유는 REC-01의 A·B 몫으로 남는다.
- 안내는 stderr에만 나온다. 창을 띄우지 않으므로 터미널 없이 실행하면 아무것도 보이지 않는다. 대화상자는 만들지 않았다.
- 제품 모드는 Windows에서만 시작한다. C2.13 폴더 연산이 Windows 전용이라 다른 OS에서는 `APP_PATHS_UNSUPPORTED`로 끝난다. 전에는 앱이 뜨고 세션만 막혔다.
- 남은 Task 폴더 삭제가 창을 띄우기 전에 Qt 메인 스레드에서 돈다. 남은 폴더가 크면 그만큼 시작이 늦어진다(미측정).
- GUI를 실제로 띄워 보지는 않았다.

관련: REC-01(C2.13 몫), I9, T43(A 부분), C2.13, PR #35, A101. PR은 아직 없다(A 브랜치 작업).
