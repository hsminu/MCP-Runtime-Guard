# A89 · Codex 로그인 화면과 계정 클라이언트

2026-10-18. A52 중 B·C 없이 할 수 있는 부분이다.

## 한 일

- **`services/codex_account.py`: `CodexAccountClient`.**
  - app-server의 `account/*` 메서드로 로그인 상태를 다룬다: 읽기, ChatGPT 기기 코드 로그인 시작·완료 대기, 취소, 로그아웃, API 키 로그인.
  - 자격증명은 Codex가 keyring에 직접 저장하고, Guard는 저장하지 않는다.
  - 화면에는 계정 종류와 요금제만 보이고 이메일은 보이지 않는다.
  - 서버 오류 원문은 `CODEX_ACCOUNT_FAILED` 하나로 감춘다.
- **bridge.**
  - `request_account(action)`를 control 레인으로 처리한다. 허용 동작 목록 밖은 거부한다.
  - 결과는 `account_changed`로 보낸다.
  - 계정 포트가 없으면 READY 때 "UNAVAILABLE"을 알린다.
  - API 키를 실어 보낼 때는 `repr`에 나오지 않는 필드를 쓴다.
- **설정 화면(`ui/model_settings.py`).**
  - 상태 확인, ChatGPT로 로그인, 로그인 취소, 로그아웃 버튼을 둔다.
  - 기기 코드 로그인 중에는 주소와 코드를 선택 가능한 일반 텍스트로 보인다. 링크로 만들지는 않는다.
  - C launcher가 없으면 버튼을 비활성화하고 "준비 중"을 안내한다.

## 사용자 확인이 필요한 것

**API 키 로그인 입력란은 만들지 않았다.** A65 결정과 기존 테스트가 "모델 비밀값을 화면에서 받지 않는다"이기 때문이다. Codex의 API 키 로그인은 클라이언트가 키를 받아 `account/login/start`로 넘겨야 한다. 이를 화면에 열지는 사용자 결정이 필요하다. 백엔드(`login_api_key`)와 테스트는 준비돼 있다.

## 검증

`test_codex_account.py` 5개:

- 기기 코드 로그인과 이메일 비노출
- 실패·취소된 로그인
- API 키 전달과 공백 제거
- 화면 상태 변화(비밀 입력란 없음)
- bridge 경로와 미연결 표시

실제 app-server로 로그인하는 것은 막혀 있다. C launcher·T43을 기다린다.

관련: I9, 10.4절 로그인.
