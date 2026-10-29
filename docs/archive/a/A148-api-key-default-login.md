# A148 · API 키 기본 로그인

2026-10-29. 사용자 결정: C6.18 권고 두 가지를 반영한다. 제출·시연 기본 로그인은 API 키이며 ChatGPT 로그인은 보조 방법으로 유지한다. OpenAI 제휴·공식 앱을 암시하지 않는다. PR: 미정.

## 한 일

- `ui/model_settings.py`에서 API 키 입력·전송 행을 ChatGPT 로그인 안내와 버튼보다 위에 배치했다. 생성 순서도 같아서 키 입력이 먼저 키보드 포커스를 받는 순서다. 실제 최초 포커스를 강제하지는 않는다.
- `ui/presentation.py`에 로그인 제목 `Codex 로그인`(카드가 로그인하는 대상을 적는다. 작업자 초안의 `MCP Runtime Guard 로그인`은 앱 자체 계정처럼 읽혀 리뷰에서 되돌렸다), API 키 기본 안내, ChatGPT 보조 방법 안내를 모았다. 기기 코드·브라우저 버튼에 ChatGPT를 명시하고 기존 bridge 명령 연결을 유지했다.
- `BROWSER_OPEN_FAILED`는 “로그인 페이지를 열지 못해 요청을 취소함. API 키 또는 기기 코드로 로그인하세요.”로 바꾸고 기존 브라우저 실패 시험의 기대 문구도 바꿨다. 기기 코드 제한 안내도 API 키를 먼저 제시한다.
- Codex가 로그인을 처리하고 자격증명을 저장·관리한다는 사실로 안내했다. 기존 “Windows 자격증명 관리자에 직접 저장”은 개발문서 10.4절의 암호문·keyring 분리를 정확히 설명하지 못해 단순화했다.
- UI 전체 21개 Python 파일을 대상으로 문구·창 제목·아이콘·스타일 이름을 검색했다. 기존 제휴 암시 문구는 발견되지 않았다. 주 창 제목은 `MCP Runtime Guard`, 브랜드 아이콘은 자체 shield 선 그림이다. `OpenAI API 키`라는 입력 대상 설명은 제휴 표시가 아니므로 유지했다. 외부 로고 자산은 UI 폴더에 없다.
- `test_ui_icons_style.py`의 정적 검사는 UI 하위 Python 파일 전체(스타일 포함), 소스와 AST 문자열을 검사한다. 공백 제거·대소문자 무시로 아래 목록을 금지하며 인접 문자열 연결도 검사한다.

금지 목록: `공식`, `OpenAI와`, `OpenAI 제공`, `OpenAI 제휴`, `OpenAI 파트너`, `OpenAI 로고`, `official`, `powered by OpenAI`, `OpenAI partner`, `OpenAI logo`, `openai_logo`, `openaiLogo`.

## 실제 검증

Windows 로컬 `.venv`, Git Bash, Qt offscreen. 아래 명령은 저장소 루트에서 실행했다. A149와 같은 변경 묶음이다.

```bash
mkdir -p .cache/a-work/A148-tmp
export QT_QPA_PLATFORM=offscreen TMPDIR=$PWD/.cache/a-work/A148-tmp TEMP=$PWD/.cache/a-work/A148-tmp TMP=$PWD/.cache/a-work/A148-tmp
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A148-tmp-2 tests/unit/test_ui_model_settings.py tests/unit/test_ui_sessions.py tests/unit/test_ui_icons_style.py tests/unit/test_codex_browser_login.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_demo.py
.venv/Scripts/python.exe -m ruff check src/guard_app/ui/model_settings.py src/guard_app/ui/presentation.py src/guard_app/ui/sessions.py tests/unit/test_ui_model_settings.py tests/unit/test_ui_sessions.py tests/unit/test_ui_icons_style.py tests/unit/test_codex_browser_login.py
.venv/Scripts/python.exe -m ruff format --check src/guard_app/ui/model_settings.py src/guard_app/ui/presentation.py src/guard_app/ui/sessions.py tests/unit/test_ui_model_settings.py tests/unit/test_ui_sessions.py tests/unit/test_ui_icons_style.py tests/unit/test_codex_browser_login.py
.venv/Scripts/python.exe -m mypy
.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

- 최종 관련 시험 **93 통과**, 11.02초. 전체 단위 시험은 실행하지 않았다.
- Ruff check·format check 7개 파일 통과, mypy 114개 소스 통과, import-linter 7개 계약 유지, diff check 통과. 첫 포맷 검사에서 로그인 파일 1개의 줄바꿈을 지적해 포맷 후 다시 통과했다.
- 배치는 실제 Qt 좌표로 키 입력 하단이 두 ChatGPT 버튼 위인지 확인했다. 두 버튼을 클릭해 각각 `account_login_device`·`account_login_browser` 명령이 전달되는지 확인했다. 기존 브라우저 가짜 app-server 정상·실패·취소 시험도 통과했다.
- 별도 해체 금지 전용 감사 시험은 검색에서 찾지 못했다. 새 문구를 읽어 안내는 하세요체, 사실은 음슴체로 확인하고 핵심 문구를 정확한 문자열로 고정했다. 기존 색·간격·아이콘 감사 시험도 함께 통과했다.

## 회귀 대조

`.cache/a-work/A148-mutations.py`로 제품 소스에 회귀를 한 건씩 넣고 해당 시험 노드만 실행했다(`python -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A148-mutation-<번호> <노드>`). 매번 `finally`에서 원래 바이트를 복원하고 일치 확인했다. A149를 포함해 **21/21건**에서 pytest 종료 코드 1과 assertion 실패를 확인했다. 준비 스크립트의 CRLF·출력 인코딩 문제는 수정 후 전부 재실행했으며 그 준비 오류를 검출 성공으로 세지 않았다.

| 고의 회귀 | 검출 시험 | 결과 |
| --- | --- | --- |
| API 키 행을 ChatGPT 버튼 아래로 이동 | `test_api_key_precedes_chatgpt_controls_and_is_the_default_guidance` | 좌표 검사 실패 |
| API 키 기본 안내 제거 | 위 시험 | 문구 검사 실패 |
| 기기 코드 signal 연결 제거 | `test_both_chatgpt_buttons_still_route_account_commands` | 명령 목록 검사 실패 |
| 브라우저 signal 연결 제거 | 위 시험 | 명령 목록 검사 실패 |
| 금지 목록 12개를 각각 로그인 제목에 삽입 | `test_ui_sources_do_not_imply_openai_affiliation` | 12건 각각 실패 |
| 브라우저 열기 실패 안내를 기기 코드 우선으로 복원 | `test_browser_open_failure_offers_device_and_api_key_without_url` | 안내 검사 실패 |

## 근거와 남은 한계

- 개발문서 0.4·0.5·2·10.4·12절, 사용자 결정 2026-10-29, A89·A109·A125. I9·I11(키 처리 주체·bridge 경로 유지), I6(실패 안내)의 A UI 보조 검증이다. T19·T43·S04의 실제 비노출·정리 검증을 대체하지 않는다. UI 기본 순서·브랜드 금지 목록에 직접 대응하는 T/S ID는 없다.
- 실제 로그인·브라우저 열기·모델 호출·키 유효성 확인·wire/부작용 계수 측정·보안 스캔은 미실행이다. 로그인 버튼 시험은 bridge 명령 및 가짜 app-server 범위다. 정적 금지 목록은 모든 제휴 암시나 그림 로고를 판별하는 완전한 검사가 아니다.
- 지정 C6.18 기록은 로컬에 없었다. #78 C 리뷰는 GitHub 연결 도구 404, 두 원문을 요청한 gh는 네트워크 거부, 웹 #78·#79 조회는 cache miss였다. 원문을 확인했다고 보고하지 않는다. 사용자가 제공한 권고·결정·리뷰 요약과 현황 문서를 기준으로 구현했다.
- 현행 공통 문서에는 A148 결정이 아직 없고 기술 설계의 로그인 열거는 기기 코드·API 키 순이다. 최신 사용자 결정을 적용했으며 공통 문서 반영은 사용자 몫이다. 계약/API 변경, B/C 신규 구현 요청은 없다. UI 독립 리뷰는 C에게 남는다.
- 기존 사용자 변경인 `docs/a-status.md`·`.claude/`는 수정하지 않았다. Git add·commit·push·PR·댓글 게시도 하지 않았다.
