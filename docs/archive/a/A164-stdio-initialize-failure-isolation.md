# A164 · A158 initialize에서 실패한 stdio 서버를 그 서버만 격리

2026-10-29 · `wip/next`(PR #92 head `2950f79` 위). PR: 아직 없음(로컬 커밋).
관련: A158(A157에서 발견), A132, I6·I9, EXE-01, C2.6, T19.

## 원인

실제 launcher로 띄운 stdio 서버가 initialize 응답 전에 끝나면, worker의 시작은 `SessionStartFailed`, 닫기는 `SessionCloseFailed`였다. 제품 transport는 시작 실패를 서버별로 격리하지만 그 전에 닫기를 확인하므로, 이 서버 하나 때문에 시작 전체가 실패했다. 등록 목록 수집도 같은 이유로 "확인하지 못함"이었다.

세 서버로 재현했다(`-c pass`, 한 줄 읽고 끝남, 한 줄 읽고 종료 코드 7).

- initialize가 SDK의 고정 오류 `McpError('Connection closed')`로 끝난다. 읽기 스트림이 닫히면 mcp 1.29.0이 기다리는 요청에 주는 값이다.
- 세션 context를 나가면서 SDK 세션과 파이프 어댑터의 TaskGroup이 이 오류를 두 겹의 `ExceptionGroup`으로 다시 감싸 올린다. 잎은 본문이 낸 그 오류 객체 하나뿐이었다.
- C launcher의 종료 확인은 성공했다(`LAUNCH_EXIT_UNCONFIRMED` 없음). 그런데 `_SessionExitTracker`가 HTTP 요청 오류 묶음(A132) 말고는 모든 그룹을 종료 미확인으로 봤다.

initialize에 JSON-RPC 오류로 답하고 기다리는 서버도 같았다. 잎은 그 `McpError` 하나였고, 닫기는 `SessionCloseFailed`였다.

## 한 일

`adapters/mcp_transport/worker.py`

- `_only_the_body_error(group, body)`를 더했다. 그룹의 모든 잎이 본문이 낸 오류 객체 자신이면 종료 완료로 본다.
  - 근거: SDK 세션·파이프 어댑터·C launcher의 정리는 모두 `finally`·`__aexit__`에서 돈다. 정리 중 실패는 그룹에 더해지거나 그룹을 바꾼다. C launcher가 종료를 확인하지 못하면 `LAUNCH_EXIT_UNCONFIRMED`가 그룹 자리에 올라와 지금처럼 `SessionCloseFailed`다.
  - 본문 오류는 `Exception`만 인정한다. 취소는 해당하지 않는다.
  - 같은 내용의 다른 오류 객체나 다른 잎이 하나라도 있으면 지금처럼 미확인이다.
- `_SessionExitTracker`는 owner가 취소되는 중이면 어느 판정도 쓰지 않는다. 이 검사를 HTTP 판정과 새 판정 앞으로 뺐다(A132와 같은 조건).
- 결과
  - 제품 시작: 그 서버만 `SessionStartFailed`로 닫히고 빠진다. 다른 서버는 시작하고 Guard 호출이 통과한다. 그 서버는 목록이 없으므로, 등록되어 있으면 지금처럼 이번 실행 차단이다.
  - 등록: 목록 수집이 `SessionStartFailed`라서 `RegistrationCommands`가 쓰기 전 거부 `REG_SERVER_UNREACHABLE`("서버에 연결하지 못해 등록하지 않음.")로 답한다(A157).

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- 실제 launcher(고정 지문 `python.exe`)
  - `test_real_launcher_guard_listing_call_and_exit[exits]`: 정상 서버와 `-c pass` 서버를 함께 둔 제품 조립이 성공한다. 정상 서버 호출은 `Released`이고, 끝난 서버는 목록이 없고 ACTIVE가 아니다. 띄운 프로세스 2개 모두 끝났다.
  - `test_registration_listing_refuses_a_real_server_that_fails_initialize[exits·refuses]`: 바로 끝나는 서버와 initialize에 오류로 답하는 서버의 목록 수집이 모두 시작 실패(`_SERVER_START_FAILURES`)다. 프로세스 종료도 확인했다.
- launcher 대역: 파이프가 initialize 중 EOF일 때, C 종료 확인이 성공하면 transport 시작이 성공하고 그 서버만 빠진다. 확인이 실패하면 시작과 닫기가 모두 `SessionCloseFailed`다.
- worker 대역: 본문 오류만 감싼 경우(연결 끊김, 오류 응답)는 정상 종료다. 다른 잎이 있거나 같은 내용의 다른 객체면 `SessionCloseFailed`다. 오류 원문(`SECRET`)은 실패 값에 없다. owner 취소 중이면 미확인으로 남는다. 판정 함수는 취소 그룹과 `None`을 거부한다.
- 관련 시험 12개 파일(transport·HTTP·ingress·목록·launch·launcher 실제·stdio 로그·bridge 등록·조립·A138·A 아키텍처): 361 passed + subtests 7.
- `ruff check`·`ruff format --check`(바뀐 3개 파일), `mypy`(138개 소스), `lint-imports`(계약 7개 유지), `git diff --check` 통과.
- 전체 단위 시험은 돌리지 않았다.

고의 회귀 6개를 하나씩 넣고 해당 시험 노드만 돌렸다. 모두 시험이 실패했고, 매번 원래 바이트로 되돌린 뒤 SHA-256 일치를 확인했다.

| 고의 회귀 | 잡은 시험 |
| --- | --- |
| 새 판정을 쓰지 않음 | worker 대역, 실제 조립 `exits`, 실제 등록 두 경우, launcher 대역 확인 성공 |
| 잎이 본문 오류인지 보지 않음 | worker 대역 `another error`·`a copy` |
| 취소도 본문 오류로 인정 | 판정 함수 시험 |
| owner 취소 중 검사 제거 | owner 취소 시험 |
| 중첩 그룹을 풀지 않음 | worker 대역 `stream ended`·`error reply` |
| 잎이 연결 끊김 오류면 같은 객체가 아니어도 인정 | worker 대역 `error reply`·`a copy` |

## 남은 한계

- 판정은 mcp 1.29.0 세션과 이 저장소 파이프 어댑터가 정리를 `finally`에서 하고 실패를 그룹에 남긴다는 데 기댄다. mcp를 올릴 때 다시 확인하도록 A144 재검증 목록에 넣었다.
- 시작하지 못한 서버를 화면에 따로 알리지는 않는다. 등록된 서버면 지금처럼 이번 실행 차단으로 보인다.
- HTTP 서버가 initialize에서 실패한 경우는 바꾸지 않았다(A132 판정 그대로).
- B·C 코드와 공통 문서는 바꾸지 않았다.
