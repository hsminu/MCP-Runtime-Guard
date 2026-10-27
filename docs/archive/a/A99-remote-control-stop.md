# A99 · Codex 원격 제어가 `disabled`가 아니면 Task 정지(C-CODEX-07)

2026-10-26. PR #35에서 C가 기록한 C-CODEX-07의 A 조치다.

Codex 0.162.1 app-server는 원격 제어를 켤 수 있다. 켜지면 원격 클라이언트가 Guard 도구 등록과 L3를 건너뛰고 app-server에 붙는다. RPC까지 막는 설정(`allow_remote_control=false`)은 시스템·MDM requirements에서만 읽어서 앱 L1으로 넣을 수 없다.

## 한 일

- `services/codex_session.py`
  - `remoteControl/status/changed`의 `status`가 `"disabled"`가 아니면 `CODEX_L3_VIOLATION`으로 Task를 정지한다.
    - params가 객체가 아니거나 `status`가 없거나 다른 형식이어도 정지한다.
  - 세션 관리자는 `remoteControl/*` 요청을 보내지 않는다. 전과 같고, 테스트로 고정했다.
  - `start()` 중에 정지가 정해지면 시작을 실패시킨다. 전에는 소유자 처리기가 아직 없어서 그 정지가 첫 `send`까지 드러나지 않았다.
  - 시작 뒤 처리기를 등록하기 전(Store 기록 동안)에 정해진 정지는 `set_stop_handler`에서 한 번 보고한다. 전에는 처리기가 없으면 보고한 것으로 표시하고 버렸다.
- `services/codex_account.py`(로그인용 app-server 연결)
  - 같은 알림에서 `disabled`가 아니면 연결을 닫는다. 이후 호출은 `AccountError`가 되고, 기다리던 기기 코드 로그인은 FAILED가 된다.
  - 시작 중 `initialized` 전송이 실패해도 `AccountError`로 낸다.
- 테스트용 가짜 app-server는 실제 0.162.1처럼 `initialize` 직후 `disabled` 상태를 알린다.

## 검증

- 형식은 `openai/codex` `rust-v0.162.1` 소스에서 확인했다.
  - `app-server-protocol/src/protocol/v2/remote_control.rs`: `{status, serverName, installationId, environmentId}`, `status`는 `disabled`·`connecting`·`connected`·`errored`
  - `common.rs`: 서버 알림은 `remoteControl/status/changed` 하나다. 나머지 `remoteControl/*`는 클라이언트 요청이다.
- 테스트
  - `test_codex_session.py`: `disabled` 허용과 요청 없음, 시작 중 비-disabled면 시작 실패(5가지 값), turn 중 비-disabled·형식 오류면 정지(5가지), turn 사이 정지는 처리기로 한 번 보고, 처리기 등록 전 정지는 등록 때 보고
  - `test_codex_account.py`: 비-disabled면 로그인 대기가 FAILED가 되고 이후 호출 거부, 시작 중이면 `AccountError`
  - 수정 전 코드에서 새 테스트 17개가 실패했다. `disabled` 허용 테스트 1개만 통과했다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15): 단위 4,202개 통과·1개 생략, contract 41개 통과, Ruff·포맷·mypy(82개 파일) 통과.

## 남은 한계

- 실제 codex.exe로 비-disabled 상태를 만들어 보지 않았다. L3 정지 검증은 C6.15, 시작 상태 확인은 C6.14다.
- 로그인 연결은 닫기만 한다. 프로세스 종료는 launcher 소유자(C 어댑터)가 한다. 로그인 연결의 실제 배선은 아직 없다.

관련: I11, T22, C-CODEX-07. PR은 아직 없다(A 브랜치 작업).
