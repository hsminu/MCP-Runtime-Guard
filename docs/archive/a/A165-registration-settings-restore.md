# A165 · A128·A105 설정 저장이 끝나지 않은 PENDING 등록의 복구 화면

2026-10-29 · `wip/next`(A164 커밋 `1a369d7` 위). PR: 아직 없음(로컬 커밋).
관련: A128, A105, A163, I6·I9, EXE-01, C2.22.

## 문제

등록은 Registry에 PENDING 행을 먼저 쓰고 설정 파일 저장을 그다음에 한다. 설정 저장이 실패하면(`REG_SETTINGS_SAVE_FAILED`) Registry 행만 남는다. 다시 시작하면 그 행은 설정 파일에 없어 실행되지 않는다. 같은 서버를 다시 등록하려 해도 `REG_SERVER_EXISTS`로 거부된다. 화면에는 이 상태가 따로 보이지 않았고, 고칠 방법도 없었다.

반대 경우(설정 파일에만 있고 Registry 행이 없음)는 A163의 서버별 설정 확인과 시작 때 C `observe`가 다룬다. 이번 항목에서는 바꾸지 않았다.

## 한 일

서비스(`services/bridge_registration.py`, `bridge_types.py`)

- `RegistrationRow.in_settings`를 더했다. 목록의 각 행이 설정 파일에 있는지를 담는다.
- `RegistrationRequest.restore`를 더했다. `prepare`·`prepare_file`에서만 쓰고, 확인 대기 값에 등록·재확인·복구 중 어느 것인지를 함께 둔다.
- 복구 미리보기
  - 거절 조건: Registry 행이 없거나, PENDING이 아니거나, 이미 설정 파일에 있으면 `REG_RESTORE_UNAVAILABLE`이다. 입력한 신뢰 경계·최대 수신 등급이 등록과 다르면 `REG_RESTORE_BOUNDARY_DIFFERS`다.
  - stdio는 일반 등록처럼 EXE-01 지문을 재고, 확인 화면을 띄운다.
- 복구 확인(사람이 확인한 뒤). 아래 순서로 확인하고, 어느 단계에서 거절되면 아무것도 쓰지 않는다.
  1. Registry를 `refresh`하고 같은 거절 조건을 다시 본다. 미리보기와 확인 사이에 차단되거나 저장된 행도 걸러진다.
  2. 서버 목록을 수집한다. 연결하지 못하면 `REG_SERVER_UNREACHABLE`이다.
  3. 목록 지문을 C의 `listing_fingerprints`로 계산한다. 그 결과가 등록에 저장된 도구별 지문과 다르면 `REG_RESTORE_TOOLS_DIFFER`다.
  4. 모두 같으면 `policy.save_registration(updating=False)`로 설정 파일에만 저장하고 `REG_SETTINGS_RESTORED`로 답한다. Registry에는 쓰지 않으므로 행은 PENDING 그대로다.
- 일반 등록(`restore` 없음)의 `REG_SERVER_EXISTS` 거부는 그대로다.

화면(`ui/servers.py`, `server_add.py`, `window.py`, `models.py`)

- 서버 목록·상세
  - 목록의 등록 상태 열에 " · 설정 없음"을 붙인다.
  - 상세의 설정 확인 줄은 "설정 파일에 이 서버가 없음. 등록만 저장되고 설정 저장은 끝나지 않았음. 서버 목록에서 설정 다시 저장을 누르세요."다.
  - 설정 파일에 없는 행에서는 활성화·도구 설정 버튼이 꺼진다.
- 설정 파일에 없는 PENDING 행을 고를 때만 "선택한 서버 설정 다시 저장" 버튼과 안내가 보인다. 누르면 서버 추가 화면이 복구 모드로 열리고, ID·신뢰 경계·최대 수신 등급이 그 행 값으로 채워진다.
- 복구 모드
  - 같은 ID를 중복으로 보지 않는다.
  - 확인 버튼은 "확인한 설정 저장"이다.
  - 상태 줄은 "설정 다시 저장 · 도구 목록이 등록과 같을 때만 설정 파일에 저장함. 등록 상태는 PENDING으로 남음."이다.
  - ID를 바꾸거나 재확인을 누르거나 등록 흐름을 닫으면 복구 모드가 끝난다.
- 새 응답 코드 네 개의 문구(음슴체·하세요체)
  - `REG_SETTINGS_RESTORED`: 완료와 PENDING 유지, 검사·활성화 전에는 실행되지 않음.
  - `REG_RESTORE_UNAVAILABLE`: 복구할 등록이 아님.
  - `REG_RESTORE_BOUNDARY_DIFFERS`: 목록에 보이는 경계 값으로 고르세요.
  - `REG_RESTORE_TOOLS_DIFFER`: 도구 목록이 등록과 달라 저장하지 않음.

A163 시험 수정(`tests/unit/test_ui_render_projection.py`)

- A163에서 `status_display`가 TTL 인자 대신 저장된 만료 시각(`expires_at`)을 쓰게 바뀌었다. 그런데 이 파일은 A163 시험 묶음에 없었다. 그래서 #92 head(`97d3534` 이후)에서 이 시험 하나가 실패한다.
- 시험 행에 `expires_at`을 넣고 TTL 인자를 뺐다. 단언 값은 그대로다.

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- 실제 SQLite Store·C `ServerRegistry`·`TaskPolicy` 위 시험 5개(`tests/unit/test_bridge_registration.py`)
  - 설정 저장 실패 뒤 같은 Store로 다시 만든 상태에서 다음을 확인했다.
    - 행이 `in_settings=False`로 보이고, 일반 등록은 `REG_SERVER_EXISTS`다.
    - 복구 미리보기는 목록을 수집하지 않는다.
    - 확인하면 설정 파일에 그 서버만 저장되고, 프로필 서버 목록은 비어 있다.
    - Registry 행(상태·도구·`row_revision`)은 바뀌지 않는다.
  - 거절 경우: 다른 경계, 없는 ID, 다른 도구 목록, 연결 실패, 미리보기와 확인 사이 차단. 모두 설정 파일이 없는 그대로이고, 경계·ID 거절은 목록도 수집하지 않는다. 사이 차단 뒤 행은 BLOCKED 그대로다.
  - 화면: 복구 버튼은 설정 파일에 없는 PENDING 행에만 보인다. 그 행에서는 활성화·도구 설정이 꺼진다. 폼은 `restore=True`로 보내고 모드별 문구를 쓴다. 창은 복구 모드로 추가 화면을 연다.
- 바뀐 모듈을 가져오는 시험 71개 파일: 1177 passed, 22 skipped, subtests 162.
- `ruff check`·`ruff format --check`(바뀐 8개 파일), `mypy`(138개 소스), `lint-imports`(계약 7개 유지), `git diff --check` 통과.
- 전체 단위 시험은 돌리지 않았다.
- 창 확인: `GuardWindow` 1280×860을 offscreen(글꼴 `C:/Windows/Fonts`)과 실제 Windows 플랫폼 두 번 띄워 `QWidget.grab`으로 찍었다. 눈으로 확인한 것은 다음이다.
  - 목록의 " · 설정 없음"과 상세의 설정 확인 줄
  - 복구 버튼과 안내
  - 복구 모드 추가 화면의 채워진 값, 상태 줄, 버튼 이름

  목록 행은 화면 시험용으로 직접 넣었다. 실제 설정 저장 실패를 데스크톱에서 일으킨 것은 아니다.

고의 회귀 17개를 하나씩 넣고 해당 시험 노드만 돌렸다. 모두 시험이 실패했고, 매번 원래 바이트로 되돌린 뒤 SHA-256 일치를 확인했다.

| 고의 회귀 | 잡은 시험 |
| --- | --- |
| PENDING이 아닌 행도 복구 허용 | 거절 시험 |
| 이미 설정 파일에 있는 행도 복구 허용 | 복구 시험 |
| 경계 비교 제거 | 거절 시험 |
| 도구 지문 비교 제거 | 거절 시험 |
| 확인 때 다시 거절 조건 보지 않음 | 거절 시험(사이 차단) |
| 복구도 Registry에 등록 | 복구 시험 |
| `in_settings` 항상 참 | 복구 시험 |
| `restore` 요청 무시 | 복구 시험 |
| 상태와 무관하게 복구 버튼 표시 | 화면 시험 |
| 설정 없는 행에서도 활성화 허용 | 화면 시험 |
| 복구 버튼이 신호를 내지 않음 | 화면 시험 |
| 폼이 `restore`를 보내지 않음 | 폼 시험 |
| 복구 ID를 중복으로 봄 | 폼 시험 |
| 복구 완료 뒤 모드를 끝내지 않음 | 폼 시험 |
| ID를 바꿔도 복구 모드 유지 | 폼 시험 |
| 창이 복구 모드로 열지 않음 | 창 시험 |
| 창이 `in_settings`를 넘기지 않음 | 창 시험 |

## 남은 한계

- Registry 등록에는 실행 파일 경로·지문이나 URL이 없다. 그래서 같은 서버인지는 도구별 지문과 신뢰 경계로만 확인한다.
  - 다른 실행 파일이나 URL이 같은 도구 목록을 내면 막지 못한다.
  - 저장되는 연결 정보는 사람이 확인 화면에서 본 값이다. 행은 PENDING이라 검사·활성화 전에는 실행되지 않는다.
- 복구는 설정 파일에 없는 PENDING 행만 다룬다. ACTIVE·BLOCKED 행이 설정 파일에 없는 경우는 지금처럼 실행되지 않고 " · 설정 없음"으로만 보인다.
- B·C 코드와 공통 문서는 바꾸지 않았다. C의 `listing_fingerprints`는 공개 함수를 그대로 썼다.
