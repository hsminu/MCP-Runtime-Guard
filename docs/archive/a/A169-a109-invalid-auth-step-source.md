# A169 · A109 첫 브라우저 로그인의 `invalid_auth_step` 출처 확인(원인 미확정)

2026-10-29 · `wip/next`(A168 커밋 `1ea9848` 위). PR: 아직 없음(로컬 커밋).
관련: A109, A142, I9·I11. 코드 변경 없음. 실제 로그인은 하지 않았다.

## 배경

[A109 실제 로그인](A109-real-browser-login.md)의 첫 시도에서 사용자가 `invalid_auth_step`을 보고했다. 취소한 뒤 새 요청으로는 완료됐다. 원인은 정하지 않았다.

## 확인한 것

- 이 저장소의 `src/`·`tests/`·`docs/`에 이 문자열은 A109 기록과 그 근거 JSON 말고는 없다. Guard 화면 문구도, 고정 코드도 아니다.
- 고정 Codex 0.162.1 실행 파일(`codex.exe`, 지문은 `codex.lock.json`)에도 이 문자열이 없다. 바이트 검색 결과 0건이다.
  - 같은 파일에 있는 Codex 자체의 로그인 콜백 오류는 다음과 같다: "State mismatch", "Missing authorization code. Sign-in could not …", "OAuth callback error: …".
  - 마지막 문구는 로그인 서비스가 콜백에 실어 보낸 오류 값을 이어 붙여 보여 주는 형식으로 보인다. 실행 파일 문자열만 보고 판단한 것이고 동작은 확인하지 않았다.
- A의 브라우저 로그인(`services/codex_account.py` `start_browser_login`)
  - `account/login/start`(`type: chatgpt`) 한 번에 Codex가 준 `authUrl`을 C의 열기 함수로 한 번만 연다. URL은 백엔드에만 두고 다시 쓰지 않는다.
  - 열기에 실패하거나 시간이 넘으면 그 요청을 `account/login/cancel`로 닫는다.
  - 그러니 같은 요청의 URL을 두 번 열거나, 지난 요청의 URL을 다시 여는 경로는 A 코드에 없다.

## 판단

`invalid_auth_step`은 Guard나 고정 Codex가 만든 값이 아니다. 로그인 서비스 쪽에서 나온 값으로 본다. 두 가지 중 어느 쪽인지는 모른다.

- 로그인 서비스의 웹 페이지가 직접 보여 준 것
- 로그인 서비스가 콜백으로 보낸 오류를 Codex의 "OAuth callback error:" 페이지가 보여 준 것

원인(어떤 단계가 순서에 맞지 않았는지)은 미확정이다. 짐작할 수 있는 경우는 있지만 확인하지 않았다. 예: 브라우저에 남은 이전 로그인 화면, 같은 화면을 두 번 진행함.

## 다음에 재현되면 볼 것

인증정보·URL 전체·요청 식별자는 남기지 않는다.

- 문구가 보인 페이지의 호스트만 남긴다. 로그인 서비스 페이지인지, `localhost` 콜백 페이지인지를 가르기 위해서다.
- 문구 앞에 "OAuth callback error:"가 붙었는지 본다.
- 그 직전에 같은 브라우저에서 이전 로그인 화면이나 다른 요청을 진행했는지 본다.
