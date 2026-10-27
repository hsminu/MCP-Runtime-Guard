# A101 · C launcher·앱 폴더·CODEX_HOME을 세션 준비에 연결, 로컬 codex.exe 지정

2026-10-26. C2.13 기록이 A에 넘긴 "SessionPreparation 어댑터" 단계다. 사용자 요청은 두 가지였다. 실제 Codex 실행을 앱에 붙이고, 각자 로컬 codex를 연동할 수 있게 하는 것이다.

## 한 일

- **C 브랜치 로컬 병합(`1365d9b`)**: PR #35(C2.12 launcher·C2.13 앱 폴더)가 아직 main에 없어서 `origin/feat/c-security-platform`을 A 브랜치에 병합했다. 충돌은 없었다. main의 #29·#31·#32 병합도 함께 들어왔다.
- **`services/codex_preparation.py`(새 파일)**: `CodexProcessPreparation`이 C2.13 기록의 순서대로 C 모듈을 묶어 `SessionPreparation`을 만든다.
  - 앱 시작
    - `prepare_app_folders()`: 단일 실행 잠금을 잡고 남은 Task 폴더를 지운다.
    - `write_codex_home`
    - 실행 파일이 있고 고정 크기와 같은지 확인한다. 없으면 `CODEX_EXECUTABLE_MISSING`, 다르면 `CODEX_EXECUTABLE_MISMATCH`다.
    - `CodexLauncher`를 만든다.
    - 구성 영수증: config = CODEX_HOME 묶음 해시, executable = 고정 SHA-256, model = 모델 정보 파일 SHA-256.
    - L2(C6.14)가 없으므로 L2 결과 없이 돌려준다.
  - Task 준비: Task 폴더를 만들고 `CodexLauncher.launch`를 부른다. 실행이 거부되면 만든 폴더를 지운다.
  - 종료 확인: `LaunchedProcess.close()`.
  - 정리: 종료가 확인된 Task만 폴더를 지운다. 확인 전이면 `CODEX_EXIT_UNCONFIRMED`다.
  - 시작 중 실패하면 잠금을 푼다. `close()`도 잠금을 푼다.
  - 오류는 고정 코드만 가진다. 경로는 싣지 않는다.
- **codex.exe 위치**
  - 기본값은 `tools/install_codex.py`가 설치하는 `%LOCALAPPDATA%\MCPRuntimeGuard\codex\0.162.1\codex.exe`다.
  - `python -m guard_app --db-path <DB> --codex-exe <절대 경로>`로 다른 사본을 지정할 수 있다. 상대 경로이거나 `--demo`와 함께 쓰면 거부한다(exit 2).
  - 어느 쪽이든 launcher가 실행마다 크기·SHA-256을 고정값과 대조한다.
- **제품 시작(`__main__`)**: 제품 모드는 이 준비를 `bootstrap_product`에 넘긴다. 앱이 끝나면(`app.exec()` 뒤) 잠금을 푼다.
- **시작 준비 실패는 앱이 아니라 세션만 막는다(`session_runtime.py`)**
  - 전에는 준비가 주입된 제품 시작에서 L2가 없거나 준비가 실패하면 `bootstrap_product`가 실패했고, bridge는 `STARTUP_FAILED`로 앱 전체를 멈췄다.
  - 이제 `prepare_startup()`이 한 번만 시도하고, 거부 코드를 세션 시작 차단 사유로 남긴다. 승인·정책 화면은 그대로 쓸 수 있다.
  - 개발문서 10.4절의 "결과가 없거나 실패면 시작을 거부한다"는 Task 시작 문맥으로 읽었다. 개발용 건너뛰기는 두지 않았다.
  - 첫 거부 코드(`CODEX_L2_UNAVAILABLE` 등)를 차단 사유와 이후 시도의 거부 코드로 그대로 쓴다. 전에는 두 번째부터 `CODEX_PREPARATION_FAILED`로 뭉뚱그렸다. 콜백 자체가 실패한 경우는 전처럼 `CODEX_PREPARATION_FAILED`다.

## 로컬 codex 연동

1. 고정 빌드(0.162.1 win32-x64, SHA-256 `dd13bdb1…`)의 codex.exe를 준비한다.
   - 가: `python tools/install_codex.py`로 기본 위치에 설치한다.
   - 나: npm 전역 `@openai/codex@0.162.1`의 `node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`를 `--codex-exe`로 지정한다. 이 PC의 사본은 크기·SHA-256이 고정값과 같았다.
2. `python -m guard_app --db-path <DB 경로> [--codex-exe <codex.exe 절대 경로>]`로 실행한다.
3. 화면에 보이는 것
   - 연결됨: "Codex 시작 검사가 준비되지 않았어."(L2 대기)
   - 실행 파일이 없거나 다름, 폴더 준비 실패, 다른 인스턴스 실행 중: "Codex 세션 준비를 완료하지 못했어."

## 검증

- `tests/unit/test_codex_preparation.py` 10개
  - 실제 폴더·DACL·잠금과 이 Python의 python.exe를 고정한 고정값 사본으로 돌렸다(C 테스트와 같은 방식).
  - 시작 준비와 L2 없음, 반복 시작 거부, 닫은 뒤 잠금 재획득
  - Task 폴더 실행 → 종료 확인 전 정리 거부 → 종료 확인 → 폴더 삭제 → 다시 정리하면 거부
  - 해시가 다른 실행 파일은 launcher가 거부하고 폴더가 지워짐
  - 기본 위치 미설치·지정 경로 없음·크기 다름 거부, 오류에 경로 없음, 실패 뒤 잠금 해제
  - 시작 뒤 바뀐 CODEX_HOME 거부
  - 제품 시작: 연결되면 차단 사유가 `CODEX_L2_UNAVAILABLE`, 실행 파일이 다르면 `CODEX_PREPARATION_FAILED`. 어느 쪽이든 앱은 시작한다.
- `test_session_runtime.py`
  - 부팅 시 L2 없음 → 앱 시작, 차단 사유 유지, 시작 준비 1회
  - 차단 사유가 첫 거부 코드 그대로
  - 기존 기대값 2곳을 이 결정에 맞게 바꿨다.
- `test_sqlite_app.py`: `--codex-exe` 상대 경로·`--demo` 조합 거부 2개
- **실제 codex.exe 0.162.1(npm 전역 사본) 1회 확인**
  - 임시 루트(`%LOCALAPPDATA%\a101-smoke`)에서 다음 순서가 모두 됐다.
    - 시작 준비
    - Task 폴더에서 실행
    - `initialize` 요청에 `result` 응답
    - `close()` True, 종료 코드 0
    - Task 폴더 삭제
  - 확인 뒤 임시 루트는 지웠다. 스크립트는 저장소에 넣지 않았다.
- 전체 검사(A 브랜치 병합본, Windows 11, Python 3.12.15)
  - 단위 4,291개 통과·1개 생략, contract 41개 통과
  - Ruff·포맷·mypy(85개 파일) 통과
  - 경고 5개는 기존 AGT 패키지 사용 중단 경고(`test_agt_real.py`)다.

## 남은 한계

- **실제 세션은 아직 열리지 않는다.**
  - L2(C6.14)가 없어서 모든 세션 시작이 `CODEX_L2_UNAVAILABLE`로 거부된다.
  - L2가 생겨도 Profile 설정 파일(CFG-01: B 변환 함수·C 파일 연산)이 없으면 Guard가 조립되지 않는다. 그러면 세션 factory도 없다(`CODEX_SESSION_UNAVAILABLE`).
- 준비 실패 사유는 화면에서 모두 "세션 준비를 완료하지 못했어"로 보인다. 콜백 예외를 고정 코드 하나로 바꾸는 기존 경계 때문이다. 실행 파일 없음·다른 인스턴스 실행 중을 따로 보이려면 그 경계를 손봐야 한다.
- 단일 실행 잠금은 앱 폴더 기준이다. DB 파일은 여전히 `--db-path`로 받으므로, 같은 DB를 두 인스턴스가 쓰는 것은 막지 못한다(REC-01, A·B).
- 실행 파일 크기 확인은 미리 알려 주는 용도다. 실제 판정은 launcher의 SHA-256 대조다.
- GUI를 실제로 띄워 차단 문구를 눈으로 본 확인은 하지 않았다. 제품 시작 경로와 차단 사유는 테스트로 확인했다.
- PR #35가 main에 병합되기 전에 A 브랜치를 올리면 C 커밋이 함께 보인다.

관련: I9, I11, T22·T43(A 부분), C2.12, C2.13, C6.13, PR #35. PR은 아직 없다(A 브랜치 작업).
