# A116 · UI 화면 조립·진입점 정리

2026-10-29. `wip/a116`의 전용 worktree에서 수행. PR: 미정.

## 한 일

- `ui/window.py`: 생성자를 셸·사이드바·메뉴·페이지·활동 패널·설정·셸 연결로 나눴다. runtime 부착과 signal 연결도 분리했다. 각 조립 호출 순서, 초기 미연결 상태, 포커스, scanner saver/tail 주입 인자는 유지했다.
- `ui/sessions.py`: 메시지 turn과 버블은 `session_transcript.py`, 모델 선택은 `session_model.py`, 목록 행과 delegate는 `session_list.py`로 옮겼다. 기존 이름은 sessions에서 재수출한다. Task/epoch 라우팅·부분 응답 교체/종결·초안·포커스 소유권은 SessionWorkspace에 남겼다.
- `session_projection.py`: 전송 가능 여부·상태 안내 우선순위·승인 띠·목록 상태를 Qt 위젯 변경 없는 함수로 분리했다. 새 권한 판정은 만들지 않았다.
- `__main__.py`: CLI를 `_parse_args()`로 분리하고, Qt 창/타이머/정리 관찰을 `ui/application.py`로 옮겼다. 제품·시연 bridge 조립은 `_run()`에 남겨 UI가 security나 다른 services를 직접 가져오지 않게 했다. `main()`의 첫 호출은 계속 C 로그 가림 설치다. 조립 모듈도 실행 시점까지 Qt import를 지연한다.
- A147 handler 생성·연결·닫기와 잠금 해제 조건, 시작 실패 코드, A110 정리 미확인 종료는 유지했다. UI 조립에는 준비된 bridge와 안전한 키 콜백만 전달한다.
- 시험 수정은 `test_ui_icons_style.py`의 accent 사용 파일명을 `sessions.py`에서 `session_list.py`로 옮긴 한 줄뿐이다. 단언 강도·소유 범위 밖 파일·현황 문서는 바꾸지 않았다.

## 정적 측정

기준은 이 worktree의 `git show HEAD:<경로>`다. 줄 수는 splitlines, 큰 함수는 AST end_lineno-lineno+1 > 200이다. 비공개 접근은 `_`로 시작하고 `__`로 시작하지 않는 Attribute 중 수신자가 self/cls가 아닌 것을 센 보수적 대용값이다. 실제 객체 소유권을 판별한 수치는 아니다. 남은 두 곳은 `os._exit`, runtime panel의 `_clear_review`이며 새 접근은 없다.

| 파일(src/guard_app 기준) | 줄 수 전 → 후 | 200줄 초과 함수 전 → 후 | 비공개 접근 대용값 전 → 후 |
| --- | ---: | ---: | ---: |
| `__main__.py` | 340 → 306 | 0 → 0 | 1 → 1 |
| `ui/window.py` | 768 → 797 | 1 → 0 | 1 → 1 |
| `ui/sessions.py` | 1168 → 727 | 0 → 0 | 0 → 0 |
| `ui/application.py` | 신규 → 75 | 0 → 0 | 0 → 0 |
| `ui/session_transcript.py` | 신규 → 210 | 0 → 0 | 0 → 0 |
| `ui/session_model.py` | 신규 → 113 | 0 → 0 | 0 → 0 |
| `ui/session_list.py` | 신규 → 149 | 0 → 0 | 0 → 0 |
| `ui/session_projection.py` | 신규 → 112 | 0 → 0 | 0 → 0 |

전체 줄 수 감소나 성능 향상을 주장하지 않는다. 생성자의 긴 조립 순서와 파일별 책임을 나눈 작업이다. 독스트링을 제외한 한국어 문자열 AST 멀티셋은 원래 3파일과 새 모듈 포함 8파일에서 108개 출현으로 동일했다(추가/삭제 0).

## 실제 검증

모든 명령의 cwd는 `C:/Project/sogaeding/MCP-Runtime-Guard/.cache/a-work/wt-A116`이다. Git Bash에서 다음 환경을 사용했다.

```bash
A116_PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
export PYTHONPATH=src PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen
export TMPDIR=$PWD/.cache/a-work/A116-tmp-env
export TEMP=$TMPDIR TMP=$TMPDIR
```

처음 import 경로를 출력해 이 worktree의 src/guard_app임을 확인했다. 아래 pytest마다 `-q -p no:cacheprovider`를 사용했다.

```bash
$A116_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A116-tmp-1  tests/unit/test_ui_*.py tests/unit/test_product_entry_logging.py  tests/unit/test_product_key_and_log.py tests/unit/test_sqlite_app.py  tests/unit/test_a138_product_startup.py tests/unit/test_a151_l2_startup.py  tests/unit/test_a_architecture.py
# 222 passed, 41 subtests passed (40.87초)
$A116_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A116-tmp-2  tests/unit/test_codex_preparation.py tests/unit/test_product_settings.py  tests/unit/test_mcp_launcher_pipes.py tests/unit/test_application_ids.py  tests/unit/test_bridge_registration.py tests/unit/test_codex_browser_login.py  tests/unit/test_server_add.py
# 121 passed, 133 subtests passed (7.41초)
$A116_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A116-tmp-3  tests/unit/test_ui_sessions.py tests/unit/test_product_entry_logging.py  tests/unit/test_a138_product_startup.py
# 회귀 복원 뒤 78 passed (11.21초, 위 묶음과 중복)
$A116_PY -m mypy
/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
$A116_PY -m ruff check src/guard_app/__main__.py src/guard_app/ui/{sessions,window,session_transcript,session_model,session_list,session_projection,application}.py tests/unit/test_ui_icons_style.py
$A116_PY -m ruff format --check src/guard_app/__main__.py src/guard_app/ui/{sessions,window,session_transcript,session_model,session_list,session_projection,application}.py tests/unit/test_ui_icons_style.py
git diff --check
```

- 서로 다른 최종 시험 343개·하위 검사 174개 통과. Pydantic settings의 lifespan 미해결 forward reference 경고 1건이 있었다.
- mypy 128 source files 성공, import-linter 7 kept/0 broken, Ruff와 format 9파일 통과, diff 공백 검사 통과.
- tests/와 examples/에서 sessions/window/진입점 및 이동 이름을 rg로 검색했다. `examples/ui_demo.py` 사용 시험은 `test_ui_demo.py`이며 첫 묶음에 포함된다. 화면 문자열/색상 감사는 `test_ui_icons_style.py`와 별도 AST 문자열 비교로 확인했다.
- 전체 단위 시험은 실행하지 않았다. 실제 Codex·DPI·보조 기술·C 독립 dispatch/wire/수신 부작용 인수는 미실행이다. SQLite 합성 transport와 T18 시험의 기존 계수 단언은 위 묶음에서 실행했다.

## 회귀 대조

worktree `.cache/a-work/A116-tmp-0/mutations.py`에서 각 변형을 하나씩 적용하고 해당 시험을 별도 프로세스로 실행했다(상한 90초). 매번 finally에서 원래 바이트를 복원하고 동일성을 확인했다.

| 경계 | 넣은 회귀 | 검출 |
| --- | --- | --- |
| 셸 연결 | 활동 signal 두 번 연결 | 활동 dock 시험 단언 실패 |
| runtime 연결 | 부분 응답 연결 누락 | 스트리밍 교체 시험 단언 실패 |
| 메시지 위젯 | 기존 turn의 최종 텍스트 갱신 누락 | 스트리밍 교체 시험 단언 실패 |
| 모델 선택 | 시작한 Task 모델 잠금 해제 | 모델 chip 시험 단언 실패 |
| 목록 재수출 | 기존 `_ROW_ROLE` 이름 제거 | 기존 import 수집 오류(exit 4) |
| 상태 투영 | 승인 띠 항상 숨김 | 승인 띠 시험 단언 실패 |
| CLI 경계 | 로그 가림 전에 파싱 | A146 설치 순서 단언 실패 |
| 앱 조립 | 시작 실패에도 Qt 코드 반환 | A138 시작 실패 코드 8개 단언 실패 |

추가 변형 하나는 검출하지 못했다. transcript 내부 text는 갱신하지만 실제 QLabel의 `last.body.setText(text)`를 생략하면 기존 스트리밍 시험은 통과했다. HEAD의 원래 sessions.py에도 같은 변형을 넣어 1 passed를 확인했다. 원래 존재하는 검사 공백이며 새로 생긴 결함으로 보고하지 않는다. 이번 지시가 시험 변경을 import/대역 수준으로 제한하므로 단언은 추가하지 않았다. C 리뷰 때 실제 QLabel 최종 내용 단언 보완을 제안한다. 신설 시험은 없다.

## 남은 한계·리뷰

- C 리뷰: A47 일반 텍스트, A75 Task별 격리·포커스, A131 승인 띠, signal 단일 연결, A146 import 순서, A147 로그 수명, A110 잠금 유지와 종료 확인. 아직 리뷰 요청/수신하지 않았다.
- B 계약 변경 요청 없음. domain/ports 및 공통 문서는 변경하지 않았다.
- 원래 checkout의 src/tests/docs는 읽거나 고치지 않았다. Git add/commit/push 등 상태 변경 명령도 실행하지 않았다. a-status 반영 문안은 별도 보고에만 남겼다.

관련: I1·I5·I6·I7·I8·I9·I11, T08·T18·T21·S04·S05, 설계 0.4·0.5·2·12, REC-01·REC-05. 보안 판정·이벤트 계약 변경 없음.
