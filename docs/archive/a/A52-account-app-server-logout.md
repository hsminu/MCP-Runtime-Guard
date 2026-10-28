# A52 · 로그인 화면 app-server 연결과 로그아웃 자격증명 정리(#21)

2026-10-28. A89에서 로그인 화면과 계정 클라이언트(`CodexAccountClient`)는 만들었지만 제품에는 붙이지 않았다. C launcher(C2.12)와 앱 폴더(C2.13)가 없었기 때문이다. 이제 둘 다 main에 있고, C가 로그아웃 정리 함수(`clear_codex_credentials`, C2.15)를 만들어 A 호출 규칙을 정했다([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21), 2026-10-27 결정). 사용자 결정: 실행 중인 세션이 있으면 로그아웃을 막는다.

## 한 일

- **로그인 화면 전용 app-server(`services/account_runtime.py`의 `ProductAccount`).**
  - 화면이 처음 계정 요청을 보낼 때 C launcher로 codex.exe를 하나 띄운다. 폴더는 Task 폴더 규칙 그대로 `tasks\account-<uuid>`다.
  - 이 프로세스는 turn을 돌리지 않고 도구도 등록하지 않는다. `account/*`만 부른다. 그래서 L2 결과를 기다리지 않는다. CODEX_HOME 해시 대조와 실행 파일 SHA-256 대조는 launcher가 실행마다 한다.
  - 원격 제어 알림이 오면 계정 클라이언트가 연결을 닫는다(C-CODEX-07). 닫힌 연결은 다시 쓰지 않고, 프로세스를 끝낸 뒤 다음 요청에서 새로 띄운다.
  - 앱을 닫을 때 Task 세션 정리와 함께 끝낸다. 종료를 확인하지 못하면 `CODEX_CLEANUP_UNCONFIRMED`로 보고한다.
  - 시작 준비가 거부된 실행(고정 codex.exe 없음 등)에서는 화면이 UNAVAILABLE을 받는다.
- **로그아웃 순서(#21).**
  1. Task app-server가 있을 수 있으면 거부한다. 기준은 정리가 증명되지 않은 Task가 하나라도 있거나 세션을 만드는 중인 요청이 있는 경우다. 화면에는 지금 계정 상태와 `LOGOUT_SESSIONS_RUNNING`이 간다.
  2. 확인과 같은 순간에 "로그아웃 중" 표시를 켠다. 그동안 새 세션은 `CODEX_SIGNING_OUT`으로 거부된다. 확인과 표시 사이에 await가 없어서, 세션 생성 요청은 확인에 잡히거나 표시를 본다.
  3. `account/logout`. Codex가 거부하면 고정 코드 `CODEX_ACCOUNT_FAILED`이고 아무것도 지우지 않는다.
  4. 계정 클라이언트를 닫고 프로세스를 끝낸다(`LaunchedProcess.close()`). True가 아니면 `LOGOUT_EXIT_UNCONFIRMED`이다. 지우지 않고, 이 실행에서는 계정 요청을 더 받지 않는다.
  5. C `clear_codex_credentials(app_paths.codex_home)`를 작업 스레드에서 부른다. `CodexConfigError`면 `LOGOUT_CLEANUP_FAILED`이다.
  6. 끝나면 "로그아웃 중"을 끄고 bridge가 새 세션 가능 여부를 다시 게시한다.
- **`CodexProcessPreparation.account_access()`**: launcher 실행과 정리 함수를 A 쪽 형식(`AccountAccess`)으로 묶는다. C 오류(`LaunchError`·`AppPathError`·`CodexConfigError`)는 경로·원문 없이 계정 오류나 False로 바뀐다.
- **`__main__`·`bootstrap_product`**: 제품 실행이 같은 잠금 소유자(`codex`)에서 `account_access()`를 넘긴다. 시연 실행은 계정 기능이 없는 그대로다.
- **화면 문구.**
  - 설정 화면 상태 문구 뒤에 붙인다.
    - "실행 중인 세션이 있어 로그아웃하지 않음. 세션을 모두 끝낸 뒤 다시 시도하세요."
    - "로그아웃했지만 Codex 종료를 확인하지 못해 저장된 로그인 정보를 지우지 않음. 앱을 다시 시작하세요."
    - "로그아웃했지만 저장된 로그인 정보를 지우지 못함. 로그아웃을 다시 누르세요." 이때는 로그아웃 버튼을 켜 둔다.
  - 모르는 코드는 보이지 않는다.
  - 세션 차단 안내: "Codex 로그아웃이 진행 중임. 로그아웃이 끝난 뒤 새 세션을 시작하세요."
- `CodexAccountClient`에 `sign_out()`(`account/logout`만)과 `closed`를 더했다. `AccountView`에 고정 코드 `notice`를 더했다.

## 검증

- `tests/unit/test_account_runtime.py`(새 파일, 가짜 app-server·프로세스)
  - 순서가 launch → `account/logout` → 종료 확인 → 자격증명 정리다. 다음 요청은 새 app-server로 로그아웃 상태를 읽는다.
  - 세션이 있을 수 있으면 `account/logout`을 보내지 않는다.
  - Codex 거부, 종료 미확인, 정리 실패 각각에서 지우지 않거나 실패를 알린다. 정리 실패는 다시 요청할 수 있다. 종료 미확인 뒤에는 새 프로세스를 띄우지 않는다.
  - 원격 제어가 켜진 app-server는 끝내고 다시 쓰지 않는다.
  - 시작 준비가 거부된 실행에서는 읽기·로그인·로그아웃이 모두 UNAVAILABLE이고 "로그아웃 중"이 켜지지 않는다.
  - `SessionRuntime`
    - 정리 중에는 새 세션이 `CODEX_SIGNING_OUT`이다.
    - 열린 세션은 로그아웃을 막고, 정지·정리 뒤에는 통과한다.
    - 세션을 만드는 중에도 로그아웃을 막는다.
    - 앱 종료 때 계정 app-server를 끝내고, 종료 미확인이면 `CODEX_CLEANUP_UNCONFIRMED`이다.
  - 화면 문구, 버튼 상태, 모르는 코드 비노출, bridge의 재게시를 확인한다.
- `tests/unit/test_codex_preparation.py`(Windows, python.exe를 고정 codex.exe 대신 실행)
  - 계정 app-server가 자기 폴더에서 돌고, 종료가 확인되면 폴더가 지워진다.
  - 시작 준비 전이면 UNAVAILABLE이다. launcher가 거부하면 폴더가 남지 않는다.
  - 정리 함수가 앱의 `codex_home`으로 작업 스레드에서 불린다. C 오류는 False다.
  - `main()`이 `account_access()`를 제품 조립에 넘긴다.
- `tests/unit/test_a_architecture.py`: 새 모듈 `services/account_runtime`을 A 서비스로 분류했다. 금지 import·넓은 예외 규칙을 그대로 통과한다.
- 전체(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`)
  - 단위 5,160개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(236개 파일)·mypy(102개 소스 파일) 통과

## 남은 것

- **실제 확인(T43)은 C와 함께 한다.** 실제 codex.exe로 로그인한 뒤 로그아웃하고, `secrets/*.age`와 keyring 항목이 없어지는지 본다. 이 기록의 테스트는 가짜 app-server와 가짜 정리 함수다.
- 정리 실패나 종료 미확인 뒤 앱을 다시 시작하면 계정 상태가 "로그인되어 있지 않음"이라 로그아웃 버튼이 꺼진다. 남은 암호문을 화면에서 지울 방법이 없다. 같은 실행에서 정리 실패를 본 직후에만 다시 누를 수 있다. 상태 확인을 누르면 그 안내도 사라진다.
- 계정 폴더는 종료가 확인되면 지운다. 지우지 못하면 다음 앱 시작이 정리한다(A102). 계정 app-server의 Codex 로그 DB에 무엇이 남는지는 확인하지 않았다.
- 계정 app-server는 앱이 닫힐 때까지 살아 있다. Task app-server와 같은 CODEX_HOME을 쓴다. `account/read`는 토큰 갱신을 요청하지 않는다(`refreshToken: false`).
- 로그인(기기 코드·API 키)은 Task 세션과 겹쳐도 막지 않는다. 로그인 도중의 토큰 갱신 경합은 10.4절의 Task 단위 정지 규칙을 따른다.

관련: A89, A102, A108, C2.12, C2.13, C2.15, C-CODEX-07, I9, T43. PR: A 브랜치([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)에 더할 예정).
