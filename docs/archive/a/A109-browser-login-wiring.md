# A109 · 기기 코드 제한 안내와 브라우저 로그인 A 연결부

2026-10-29. 시작 기준은 `feat/a-application`의 `d1f1be3`, 깨끗한 작업 트리다. A115·A136은 `4c0aa43`으로 커밋돼 있었고 A100 후속은 A140으로 구현돼 있어 다시 수정하지 않았다.

사용자는 기기 코드 제한 안내와 브라우저 로그인 지원을 결정했다. 작업 중 C의 URL 검증·브라우저 열기 입구가 없는 것을 확인했고, 사용자는 **A 연결만 구현하고 C 요청은 초안으로 남기도록** 범위를 정했다. 이 기록은 A 구현 완료이며 제품 브라우저 로그인 전체 완료가 아니다.

## 한 일

- `CodexAccountClient.start_browser_login()`은 `account/login/start`에 `{"type":"chatgpt"}`를 보내고 `type`·`loginId`·`authUrl`을 확인한다. 반환 URL은 백엔드의 주입 함수에만 넘기며 `AccountView`·Qt 이벤트·명령 장부에 넣지 않는다.
- A 소유 `BrowserLoginOpener`는 `Callable[[str], Awaitable[bool]]`다. C의 검증과 플랫폼 열기 결과를 A가 받을 연결부이며 C 공개 API를 확정한 것이 아니다. C의 전용 오류는 연결 시 고정 실패로 변환해야 한다.
- `CodexProcessPreparation.account_access(open_browser=...)` → `AccountAccess` → `ProductAccount` → 계정 클라이언트로 함수를 전달한다. 기본값은 `None`이다. 제품 시작부는 아직 함수를 넘기지 않으므로 브라우저 버튼이 비활성화되고 준비 중 안내가 나온다. 없는 입구를 대역 성공으로 연결하지 않았다.
- 열기 요청의 성공은 `PENDING_BROWSER`일 뿐이다. 같은 `loginId`의 완료 알림을 기다리고 `account/read`로 실제 상태를 읽은 뒤 표시한다. 성공 알림만 있고 계정이 없으면 로그인 상태로 표시하지 않는다.
- 열기 대기는 계정 RPC 상한(기본 30초), 사람 로그인 대기는 기존 15분이다. 열기 실패·시간초과는 Codex 로그인 취소를 요청한다. 응답 형식 불명·RPC 실패·호출 취소로 계정 작업이 끝나지 못하면 제품 계정 프로세스도 종료한다. 종료 미확인 시 기존 규칙대로 대체 프로세스를 만들지 않는다.
- bridge는 계정 RPC만 직렬화한다. 사람을 기다리는 동안 Task lock·계정 lock·제어 worker를 점유하지 않는다. 대기 중 중복 시작은 기존 대기 상태를 반환하고 새 RPC를 보내지 않는다. 취소한 대기의 세대 번호를 폐기해 늦은 완료가 다음 로그인 상태를 덮지 못하게 했다.
- 기기 코드 요청 실패 뒤에도 이미 확인한 브라우저 연결 가능 상태를 유지한다. 기존 API 키·로그아웃 경로와 C-CODEX-07 감시는 유지했다.
- 화면에 브라우저 버튼, Guard에서 사용할 Codex 계정이라는 목적, 기기 코드 제한 시 대안, 완료 뒤 Guard로 돌아오는 안내를 추가했다. 새 문구는 `ui/presentation.py`에 모았다. 명령 큐 거부·처리 미확인도 로그인 성공으로 표시하지 않는다.
- `CancelledError` 포착은 브라우저 시작 중 Codex의 로그인 대기를 취소하기 위한 정리 경계다. 정리의 `AccountError`는 고정 코드이며 원문을 공개하지 않고, 원래 `CancelledError`는 다시 올린다. 넓은 예외 포착을 추가하지 않았다.

## 실제 검증

Windows, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`. 마지막 제품·테스트 소스 기준 결과다.

- 새 `test_codex_browser_login.py` 18개: 정상 완료와 계정 확인, C 입구 미연결 시 RPC 0회, 제품 구성부 주입, 잘못된 응답 후 프로세스 종료, 열기 실패·시간초과 취소, 취소 RPC 실패에도 취소 전파, 중복 시작 1회, 늦은 완료 격리, 기기 코드 실패 뒤 브라우저 대안, UI/bridge 연결·큐 거부 처리.
- 로그인·제품 계정·Codex 준비·화면·A 구조 검사: **286 통과**.
- 전체 단위: **5,486 통과, 5 skip, 20 subtest 통과**, 실패 0, 253.31초.
- contract: **45 통과**, 7.33초.
- Ruff check, Ruff format --check(266개 파일), mypy(112개 소스), `git diff --check` 통과.
- 명령: `.venv/Scripts/python.exe -m pytest -q -rs -p no:cacheprovider`, `-m pytest -q -p no:cacheprovider tests/contract`, `-m ruff check src tests examples`, `-m ruff format --check src tests examples`, `-m mypy`.
- skip은 심볼릭 링크 생성 권한 4개와 `GUARD_TEST_CODEX_EXE` 미설정으로 실제 L2 미실행 1개다. 경고 1개는 기존 `pydantic_settings`의 `lifespan` 미해결 forward reference다.
- 기존 기기 로그인 취소 테스트는 `account/read`의 `SIGNED_OUT`과 `LOGIN_CANCELLED`를 확인하도록 바꿨다. 취소한 waiter가 뒤늦게 `LOGIN_NOT_COMPLETED`로 덮는 이전 동작을 더 이상 기대하지 않는다.
- **늦은 완료 방어 보완**(위 전체 실행 뒤 평가에서 찾은 빈틈)
  - `_await_login`의 세대 검사를 빼도 위 테스트가 모두 통과했다. 지금 구현에서는 lock을 쥔 취소가 대기 Task를 먼저 끊으므로 세대 검사에 닿는 경로가 없다. 대기 포트가 취소를 흡수할 때(안쪽 대기를 shield로 감싼 구현)만 이 검사가 늦은 결과를 막는다. 검사는 남기고 이유를 주석으로 적었다. 동작은 바꾸지 않았다.
  - 새 테스트 2개(`AccountCommands` 직접 실행, Qt 없음)
    - 취소를 한 번 흡수하는 대기 포트: 취소 뒤 늦게 돌아온 이전 대기가 다음 로그인(기기 코드)의 대기 상태를 덮지 않는다. 다음 로그인은 그대로 취소된다.
    - 취소 RPC 도중 같은 로그인의 완료 알림: 이전 대기는 lock을 기다리다 취소되고, 화면에는 취소 결과(`SIGNED_OUT`, `LOGIN_CANCELLED`)만 나간다.
  - 회귀를 일부러 넣어 확인했다. 매번 원래 바이트로 되돌렸다.

| 회귀 | 결과 |
| --- | --- |
| 세대 검사 제거 | 잡음(첫 새 테스트만 실패) |
| 로그인 취소가 대기를 끊지 않음 | 잡음(새 테스트 2개 포함 3개 실패) |
| 늦은 대기가 lock 없이 화면에 냄 | 잡음(둘째 새 테스트만 실패) |

  - 보완 뒤 실행: 로그인·bridge·A 구조 관련 339개 통과. Ruff check·format --check(266개 파일), mypy(112개 소스), `git diff --check` 통과. 전체 단위는 다시 돌리지 않았다.

## 근거와 한계

- 프로토콜은 [공식 app-server 브라우저 로그인 문서](https://learn.chatgpt.com/docs/app-server)와 고정 0.162.1의 실제 브라우저 로그인 관찰이 있는 [A108](A108-codex-real-login-turn.md)을 근거로 삼았다. 선택 기능이나 임의 OAuth 구현을 추가하지 않았다. 이번 작업에서 고정 실행 파일 프로토콜을 다시 실측하지는 않았다.
- 관련 불변식은 I6(실패·미확인), I9(로그인 URL·비밀값 비노출), I11(로그인 연결은 도구·모델 turn을 만들지 않음)이다. T19·T43·S04의 비노출 요구를 A 단위 대역으로 보조하며 실제 로그·앱 폴더·자격증명 정리의 C 독립 인수를 대체하지 않는다. S02에 해당하는 URL 함정 검증도 C 입구가 없어 미실행이다.
- 실제 브라우저 열기·실제 계정 로그인·모델 호출·keyring 변경·보안 스캔은 실행하지 않았다. 열기 성공은 주입형 대역으로만 검증했다. 기본 브라우저의 새 탭 동작과 취소 뒤 플랫폼 부작용은 C에서 확인해야 한다.
- `domain/*`·`ports.py`·B/C 소유 구현·공통 문서는 수정하지 않았다. 공통 계약·설계의 로그인 방식 확대 반영은 별도 main 대상 제안 사항이다. 사용자 지정에 따라 C 요청 초안은 최종 보고에 남기고 게시하지 않는다.
- PR: 이번 변경의 PR 생성·댓글 게시 없음. 커밋·푸시하지 않았다.
