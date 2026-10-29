# A162 · 실제 실행 확인과 표시 후속

2026-10-29 · `wip/next`(#87 head `9047e5d` 위).
번호: 커밋 `5c856b3`에서는 A160이었다. 올리기 전에 원격의 A160(Codex HTTP 401)과 겹쳐 A162로 옮겼다.
관련: A128·A151·A109·A142, EXE-01, I6·I9·I11, T19·T22·T43·S04의 A 화면 범위.

사용자가 직접 할 실제 확인(A128·A151·A109) 중 인증정보·로그인 없이 되는 부분을 Codex 작업자 둘에게 맡겼다. 거기서 나온 A 화면 문제 4건을 고쳤다.

## 실제 실행 확인(작업자, 추적하지 않는 작업 폴더)

모두 임시 앱 폴더(`prepare_app_folders(<작업 폴더>)`)를 썼고, 실제 `%LOCALAPPDATA%\MCPRuntimeGuard`는 고정 codex.exe 설치 위치만 썼다. 인증정보·자격 증명 관리자·로그인 완료·외부 네트워크는 쓰지 않았다. 창은 offscreen이고 화면은 `QWidget.grab()`으로 남겼다.

- **A128 실제 stdio 서버 등록·EXE-01: 확인됨(offscreen).**
  - 실제로 쓴 것: 제품 창, bridge, `bootstrap_product`, `RegistrationCommands`, C launcher, C Registry·설정 저장, B SQLite.
  - 서버: `test_mcp_launcher_pipes.py`의 Python stdio 서버를 별도 프로세스로 띄웠다.
  - 흐름: 등록 → PENDING → 실행 파일 바꿈 → "지문 재확인 필요" → 재확인 → 새 지문 저장 → PENDING 유지. 파이프의 `tools/call` 쓰기는 0회였다.
  - SQLite 행에는 지문이 없다(EXE-01대로 설정 파일에 있음). 그래서 상태는 Store에서, 지문은 설정 파일에서 대조했다.
- **A151 실제 L2: 부분 확인.**
  - C `test_real_codex_passes_l2`를 고정 0.162.1로 3회 돌려 모두 통과했다.
  - 제품 시작 경로(`CodexProcessPreparation` → `_run()`)는 8회 돌렸다. 7회 통과했고, 1회는 `L2_PROBE_CALLS_UNEXPECTED`로 실패했다.
    - 그 회차는 내부 관찰을 남기지 않아 어느 판정 조건(검사 도구 호출 순서·겹침, 결과 불일치)인지 모른다. 원인은 미확정이다.
    - 실패 때도 세션만 거부되고 앱은 시작했다.
  - 8회 모두 세 지문이 일치했다. 종료 확인, tasks 폴더 비움, 띄운 PID 종료도 확인했다.
  - 시간(로그아웃 상태의 새 CODEX_HOME): L2 1.3–2.1초, 시작 전체 1.6–2.7초, 종료 0.02–0.1초.
- **A109 `BROWSER_OPEN_FAILED`: 확인됨(열기 실패 경로).**
  - 실제 app-server와 C URL 검사를 썼고, 열기만 `make_login_opener(shell=항상 False)` 대역이다.
  - URL 검사는 통과했고 열기만 실패했다(shell 1회). 취소 요청 1회 뒤 계정 프로세스는 유지됐고(A142), 앱 종료 때 끝났다.
  - 이벤트·라벨·로그에 URL이 없었고, 인증 파일은 생기지 않았다.

## 고친 것

- **지문 재확인 화면의 같은 ID 경고(A128):**
  - 등록된 서버의 "기존 실행 파일 다시 확인"을 누르면 그 ID를 재확인 대상으로 기억한다.
  - 같은 ID인 동안에는 새 등록용 "현재 목록에 같은 ID가 있음" 경고를 숨긴다. 미리보기는 "기존 서버의 실행 파일 지문만 다시 확인함. 다른 등록 내용은 바뀌지 않음."이다.
  - ID를 고치거나 새 등록 확인을 누르면 재확인이 끝난다. 같은 ID를 다시 입력해도 경고가 다시 보인다. `REG_RECHECK_UNAVAILABLE`도 재확인을 끝낸다.
  - 새 등록은 지금처럼 막힌다(초안 없음, 입력 확인 버튼 꺼짐).
- **재확인에 "등록" 문구 재사용(A128):** 재확인 중에는 다음 문구로 바꾼다.
  - 확인 버튼: "확인한 지문 저장"
  - 안내: "표시된 지문을 확인한 뒤 지문 저장 버튼을 누르세요."
  - 진행: "확인 중 · 아직 지문 저장 결과가 아님."
- **L2 진행 표시(A151):**
  - 지금까지 시작 진행(Store 단계·`CODEX_L2_CHECK`)은 숨겨진 활동 패널에만 보였다.
  - 이제 창의 생명주기 연결이 STARTING 이벤트의 `LIFECYCLE_MESSAGES` 문구를 세션 화면 안내에도 넘긴다.
  - Task가 없는 첫 화면에서는 명령 안내·세션 차단 안내 다음, 기본 안내 앞에 보인다. READY·FAILED·CLOSING이면 지운다.
- **브라우저 열기 실패 안내의 중복 줄(A109):**
  - FAILED 상태에 다음 할 일을 담은 안내가 붙으면, 일반 줄 "Codex 계정 요청을 처리하지 못함. 잠시 뒤 다시 확인하세요." 대신 안내만 보인다.
  - 대상: `BROWSER_OPEN_FAILED`, `LOGIN_REQUEST_UNCONFIRMED`, `LOGIN_CANCEL_UNCONFIRMED`, `LOGOUT_REQUEST_UNCONFIRMED`.
  - 다음 할 일이 없는 `ACCOUNT_READ_UNCONFIRMED`·`BROWSER_LOGIN_UNAVAILABLE`은 일반 줄을 둔다.

바꾼 파일: `ui/server_add.py`, `ui/window.py`, `ui/sessions.py`, `ui/session_projection.py`, `ui/model_settings.py`, 시험 3개(`test_bridge_registration.py`, `test_a151_l2_startup.py`, `test_codex_browser_login.py`).

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- 시험 27개 파일을 돌려 371 passed + subtests 106이었다. 대상은 다음과 같다.
  - 등록: 서버 추가, bridge 등록, A49, A155, A105
  - 계정: 브라우저 로그인, 계정, 계정 runtime
  - 시작·bridge: A151, bridge, A 아키텍처, 제품 키·로그
  - `test_ui_*.py` 15개
- 새 시험은 2개다(재확인 화면, 시작 진행 표시). 기존 A109 시험 2개에는 단언을 더했다.
- 바뀐 Python 8개 파일은 `ruff check`와 `ruff format --check`를 통과했다.
- `mypy` 135개 소스, `lint-imports`(계약 7개 유지), `git diff --check`를 통과했다.
- 전체 단위 시험은 돌리지 않았다.

고의 회귀 5개를 하나씩 넣고 해당 시험 노드만 돌렸다. 매번 원래 바이트로 되돌리고 SHA-256 일치를 확인했다.

| 고의 회귀 | 잡은 시험 | 결과 |
| --- | --- | --- |
| 재확인 중 같은 ID 경고를 숨기지 않음 | `test_pin_recheck_is_not_shown_as_a_duplicate_registration` | 1 실패 |
| 재확인 확인 버튼을 "확인한 내용으로 등록"으로 고정 | 같은 시험 | 1 실패 |
| ID를 고쳐도 재확인 유지 | 같은 시험 | 1 실패 |
| 시작 진행 문구를 세션 화면에 넘기지 않음 | `test_startup_progress_shows_on_the_default_session_screen` | 1 실패 |
| FAILED 안내 대체를 끔 | `test_browser_open_failure_offers_device_and_api_key_without_url`, `test_an_unconfirmed_account_command_names_what_was_not_confirmed` | 2 실패 |

## 남은 한계

- A151 L2 간헐 실패는 C `run_l2` 판정 쪽이라 고치지 않았다. C에 알려 원인 확인이 필요하다(현황에 따로 둠).
- 사람이 볼 것:
  - 로그인한 CODEX_HOME의 시작·정리 시간
  - 실제 브라우저 로그인 완료
  - 실제 데스크톱의 글꼴·DPI·포커스
  - 재시작 뒤 연결 거부 화면
- A128 재확인 흐름은 대역 bridge로 화면만 시험했다. 실제 등록 흐름은 위 작업자 확인이 덮는다. 이 고침 뒤 실제 창으로 다시 찍지는 않았다.
- B·C 코드와 공통 문서는 바꾸지 않았다.
