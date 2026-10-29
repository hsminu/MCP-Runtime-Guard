# A142 · A109 후속: 거절된 계정 요청의 app-server 유지와 계정 명령 거부·미확인 문구

2026-10-29. 시작 기준은 main `9a8c117`(#70·#72·#75 병합 뒤, A 브랜치 fast-forward)이다. A109(#70) 평가에서 남긴 두 지적을 고쳤다. A109 기록은 고치지 않았다.

## 한 일

- **계정 RPC 실패를 둘로 나눔**
  - `CodexAccountClient._call`: app-server가 JSON-RPC 오류로 답하면(`JsonRpcRemoteError`) `AccountRefusedError`를 낸다. 시간 초과·연결 끊김·형식 오류(`JsonRpcError`)는 지금처럼 `AccountError`다. `AccountRefusedError`는 `AccountError`의 하위 형식이고 표시 코드는 둘 다 `CODEX_ACCOUNT_FAILED`다.
  - `ProductAccount._request`: `AccountRefusedError`면 계정 app-server를 그대로 둔다. 응답 형식 불명·연결 실패·취소는 A109대로 프로세스를 끝낸다(I6).
  - `cancel_login`: 취소가 거절되면 Codex의 로그인 콜백이 남을 수 있다. 그래서 `AccountError`로 바꿔 프로세스를 끝낸다. 화면의 로그인 취소와 브라우저 열기 실패 뒤의 내부 취소가 모두 이 경로다.
  - 결과: API 키 거절, 기기 코드 거절(계정·워크스페이스가 막은 경우), 브라우저 로그인 시작 거절 뒤에도 다음 요청이 같은 app-server를 쓴다. #70 본문에 알린 "API 키 로그인이 오류로 끝나도 계정 app-server를 새로 띄움"은 이제 해당하지 않는다.
- **계정 명령의 거부·미확인 화면(`ui/model_settings.py`)**
  - 거부(REJECTED, 백엔드 작업 전): 마지막 계정 상태를 유지하고 버튼을 되살린다. 문구는 기존 `COMMAND_REFUSALS`를 쓰고, 목록에 없는 코드(`SHUTTING_DOWN` 등)는 `ACCOUNT_REQUEST_REFUSED`다. 이전에는 모두 FAILED와 "로그인 요청 처리를 확인하지 못함"이었다. 그래서 대기 중인 로그인의 취소가 큐에서 거부되면 취소 버튼이 꺼졌고, 로그아웃·상태 확인 거부에도 로그인 문구가 나왔다.
  - 미확인(UNCONFIRMED): 지금처럼 FAILED로 두되, 문구를 명령별로 고른다. 상태 확인·로그아웃·로그인 취소는 새 문구, 로그인 시작 세 가지는 기존 `LOGIN_REQUEST_UNCONFIRMED`다.
  - 새 문구는 `ui/presentation.py`에 두었다: `LOGIN_CANCEL_UNCONFIRMED`, `LOGOUT_REQUEST_UNCONFIRMED`, `ACCOUNT_READ_UNCONFIRMED`, `ACCOUNT_REQUEST_REFUSED`.
- 넓은 예외 처리를 추가하지 않았다. `CancelledError` 처리는 바꾸지 않았다.

## 실제 검증

Windows, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

- `tests/unit/test_codex_browser_login.py`에 시험을 더했다.
  - 로그인 시작 거절 3가지(API 키·기기 코드·브라우저) 뒤 프로세스가 하나 그대로이고 닫힘 0회, 다음 `account/read`가 같은 프로세스에서 답한다.
  - 취소 거절 2경로(열기 성공 뒤 화면 취소, 열기 실패 뒤 내부 취소)에서 프로세스가 닫히고 오류 형식이 `AccountError`다.
  - 미확인 6개 명령의 문구. "로그인 요청" 문구는 로그인 시작에만 나온다.
  - 거부 뒤 상태 유지: 대기 중 로그인의 취소 버튼이 다시 켜지고, 로그인 상태의 로그아웃 버튼이 그대로다.
  - 기존 큐 거부 시험의 기대를 "확인하지 못함"에서 "접수하지 않음"과 SIGNED_OUT 유지로 바꿨다.
- 회귀를 일부러 넣어 확인했다. 매번 원래 바이트로 되돌렸다. 대상 시험은 `test_codex_browser_login.py`·`test_account_runtime.py`·`test_ui_model_settings.py`다.

| 회귀 | 결과 |
| --- | --- |
| 거절이면 프로세스를 두는 분기 제거 | 잡음(1개 실패) |
| 오류 응답도 일반 `AccountError`로 냄 | 잡음(1개 실패) |
| 거절된 취소를 바꾸지 않음 | 잡음(2개 실패) |
| 화면 거부를 예전처럼 FAILED로 | 잡음(2개 실패) |
| 미확인 문구를 모두 로그인 문구로 | 잡음(3개 실패) |
| 거부 뒤 보기 복원 생략 | 잡음(2개 실패) |

- 계정·로그인·화면 문구 관련 15개 파일: 686 통과. 문구 감사·bridge·UI 등 관련 시험: 1,510 통과. 그 뒤 시험 하나의 Ruff PT012만 정리했고, 로그인 관련 4개 파일을 다시 돌려 60 통과했다.
- `ruff check .`, `ruff format --check .`(286개 파일), `mypy`(113개 소스), `lint-imports --config tools/ci/importlinter.ini`(7개 유지), `git diff --check` 통과.
- 전체 단위 시험은 돌리지 않았다.

## 한계

- 실제 codex.exe가 API 키·기기 코드 거절을 JSON-RPC 오류로 보내는지는 실측하지 않았다. 가짜 app-server의 오류 응답으로만 확인했다. 결과 객체로 실패를 알리면 지금처럼 FAILED 보기로 끝나고 프로세스는 유지된다.
- 기기 코드 시작 결과의 형식이 맞지 않을 때 FAILED를 돌려주며 프로세스를 두는 기존 동작은 이번 범위 밖이라 바꾸지 않았다.
- A109는 C 리뷰 없이 #70으로 병합됐다. A109와 이 변경의 C 독립 리뷰가 남는다.
- 관련 불변식은 I6(결과 불명은 정리), I9(오류 원문 비노출)이다. 실제 계정 로그인·T43은 실행하지 않았다.
- PR: 미정.
