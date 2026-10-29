# A184 · 설정 파일이 없는 첫 실행도 기본 정책으로 바로 Task를 시작함

2026-10-30 · `wip/next`(main `f0c2643` 위). 사용자 결정(2026-10-30): 설정 파일이 없어도 바로 Task를 만들 수 있어야 한다.
관련: CFG-01(계약 문구 변경 필요), I6·I11, A129·A135.

## 문제

- 실제 제품을 처음 켜면 설정 파일이 없어서 Profile을 확인하지 않았다. 그래서 Guard를 조립하지 않았고 Codex 세션 생성기도 연결하지 않아 새 Task를 만들 수 없었다.
- 새 Task가 막힌 사유는 `CODEX_SESSION_UNAVAILABLE`로 보였다. `creation_blocker`가 세션 생성기 없음을 정책 사유보다 먼저 돌려주기 때문이다. 실제 원인은 설정 파일이 없다는 것이었다.
- 이 동작은 CFG-01(2026-10-26)의 "파일이 없으면 설정 화면만 열고 Task 시작을 막으며 빈 초안을 자동 저장·적용하지 않는다"를 따른 것이다. 사용자가 이번에 이 부분을 바꿨다.

## 결정과 한 일

- **기본 문서:** 내장 기본 Profile `default_settings_document()`는 `{"profile": {"id": "local", "servers": []}, "servers": []}`다.
  - 선택 서버·도구·목적지가 없고 정책 값은 기본값이다.
  - 부를 때마다 새 객체를 돌려준다.
  - 등록 화면의 첫 등록 문서(`TaskPolicy.registration_document`)도 같은 함수를 써서 정의가 한 곳뿐이다.
- **`SettingsFile.profile_document`:** 파일이 없으면 기본 문서를 돌려준다. `document`는 그대로 None이어서 "파일 없음"의 뜻이 남는다.
- **`load_settings_profile`:**
  - 파일이 없어도 같은 경로로 Registry를 로드하고 Profile을 확인한다. 이때 `SETTINGS_FILE_DEFAULT`를 돌려준다.
  - `SETTINGS_FILE_MISSING` 코드는 없앴다.
  - Registry 실패와 Profile 오류는 기존 코드(`REGISTRY_UNAVAILABLE`·`SETTINGS_PROFILE_INVALID`)를 그대로 쓴다.
- **`bootstrap_product`:**
  - 기본 문서로 Guard를 조립하고 Codex 세션 생성기를 연결한다. 시작할 때는 아무것도 쓰지 않는다.
  - 정책 저장기(`SettingsFileSaver`, 세대 None)는 첫 저장 때 C `replace_settings_file`로 파일을 만들고, 그다음부터 그 세대를 따른다.
- **새 Task가 막히는 순서:** 시작 준비 → 로그아웃 중 → 정책(Profile) 사유 → 세션 생성기 없음. `creation_blocker`와 `_create_session` 모두 이 순서다.
- **화면:**
  - 상태 문구는 "설정 파일이 없어 기본 정책(서버·도구 없음)으로 실행 중. 서버를 등록하거나 정책을 저장하면 설정 파일이 만들어짐."이다.
  - 첫 실행 때 설정 화면으로 옮기던 동작과 `SETTINGS_FILE_MISSING` 막힘 문구는 뺐다.
- **바뀌지 않은 것:**
  - 읽을 수 없거나 형식이 틀린 파일은 지금처럼 앱 시작을 거부하고 덮어쓰지 않는다(#33).
  - 기본 정책에는 서버·도구가 없어서 모델에 주는 도구는 0개다(`dynamicTools == []`, I11).

바꾼 파일:

- 제품 코드: `product_settings.py`, `product_startup.py`, `session_policy.py`, `session_runtime.py`, `ui/presentation.py`, `ui/window.py`. `__main__.py`는 주석만 바꿨다.
- 시험: `test_product_settings.py`, 새 `test_a184_default_settings.py`.

## 검증

2026-10-30, Windows / Python 3.12 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 대상 시험 15개 파일(설정·세션 런타임·제품 시작·조립·등록·도구 설정·목적지·Codex 제품 흐름·UI 세션·UI 스타일, 새 시험 12개 포함) | 309 passed, 27 subtests passed |
| `ruff check`·`ruff format --check`(바꾼 9개 파일), `mypy src`(140개), import-linter(계약 7개 유지) | 통과 |
| 되돌림: `creation_blocker` 순서를 원래대로 | 4 failed(정책 사유 대신 `CODEX_SESSION_UNAVAILABLE`) |
| 되돌림: `_create_session`의 정책 확인 삭제 | 4 failed |
| 되돌림: 파일이 없으면 `SETTINGS_FILE_MISSING`, None | 5 failed |
| 되돌림: 시작이 `settings.document`를 넘김 | 2 failed |
| 되돌림: 기본 문서를 공유 객체 하나로 | 1 failed |
| 되돌림: 첫 실행에 설정 화면 이동 복원 | 1 failed |

되돌릴 때마다 원래 파일 바이트와 `git diff`가 대조 전과 같은지 확인했다.

새 시험이 확인하는 것:

- **첫 실행:** 실제 Windows 임시 앱 폴더·SQLite·합성 L2·가짜 app-server(`answer_only`)로 확인한다.
  - 설정 파일 없이 막힘 사유가 없다.
  - Task를 만들어 한 턴을 끝낸다(`dynamicTools == []`).
  - 끝난 뒤에도 설정 파일이 없다.
- **첫 저장:** 목적지를 저장하면 C 저장 함수가 세대 없이 파일을 만든다. 두 번째 저장은 그 세대를 따른다. 다음 시작은 `SETTINGS_LOADED`로 같은 정책을 읽는다.
- **막힘 순서:** Profile 오류·Registry 실패·Profile 미연결이 세션 생성기 없음보다 먼저 나온다. 이때 Task 행은 0개다.

## 한계·남은 것

- 실제 codex.exe·로그인·모델 호출은 돌리지 않았다. 실제 제품 첫 실행 확인은 남아 있다.
- 전체 시험은 돌리지 않았다.
- **계약 문구:**
  - `docs/contracts.md`의 CFG-01 행(2026-10-26)과 계약 상태 표는 아직 "파일이 없으면 Task 시작을 막는다"·"미설정 화면"으로 되어 있다.
  - 사용자 결정이 우선하고, 공통 문서라 사용자가 반영한다.
  - B3.12(시작/저장/적용·Task 생성 직렬화 검증)의 첫 실행 기대값도 이 결정에 맞춰야 한다.
- 공통 문서와 B·C 소유 파일, 사용자 앱 데이터 폴더는 수정하지 않았다.
