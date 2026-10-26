# A89 · Codex API 키 로그인 입력란

2026-10-19. PR #31(contracts 2026-10-18 A89 결정)의 A 화면 부분이다. A65의 "모델 비밀값을 화면에서 받지 않는다"는 이 범위에서 대체됐다.

## 한 일

- **로그인 화면(`ui/model_settings.py`)에 API 키 입력란과 "API 키로 로그인" 버튼을 추가했다.** 규칙은 Scanner 키 화면과 같다.
  - 가린 입력칸이고, 오른쪽 클릭 메뉴와 입력기(IME)는 끈다.
  - 보내기 직전에 입력칸부터 비운다. 화면을 숨겨도 비운다.
  - 키는 Qt 신호로 내보내지 않는다. 창(`ui/window.py`)이 주입한 전송 함수 하나만 받고, 그 함수가 bridge의 기존 `account_login_api_key` 명령으로 Codex `account/login/start`에 넘긴다. Guard와 앱은 키를 저장하지 않고, 저장은 Codex가 keyring에 한다.
  - 로그인 결과가 API 키 로그인이면 상태에 끝 4자리만 붙인다(4자 이하 키는 붙이지 않는다). 다른 상태가 되면 끝 4자리도 지운다.
  - 입력란은 로그아웃 상태에서 전송 함수가 연결됐을 때만 켜진다. 준비 중·기기 코드 대기·로그인 상태에서는 꺼진다.
- bridge 명령 기록(`CommandEvent`)에는 키가 들어가지 않는다. `_Command.user_text`는 `repr=False`다(기존).

## 검증

- `test_ui_model_settings.py`
  - 입력칸 속성(가림, 메뉴·IME 꺼짐)
  - 전송 함수로 한 번만 전달되고 입력칸이 바로 비는지
  - 끝 4자리만 표시되고 라벨·`repr`에 키가 없는지
  - 상태별 비활성화, 숨김 때 비움
  - 창에서 `account_login_api_key` 명령으로 이어지는지
- 기존 "키 입력칸 없음" 단언 2곳(`test_ui_model_settings.py`, `test_codex_account.py`)을 결정에 맞게 "가린 입력칸 하나"로 바꿨다.
- 전체 결과는 A95 기록과 같은 실행이다.

## 남은 것

- 실제 app-server로 API 키 로그인을 확인하는 건 C launcher·T43을 기다린다.
- 로컬 A 브랜치에만 커밋했다. PR #28 병합 뒤 main을 합쳐 PR #29에 올린다.

관련: I9, T18.
