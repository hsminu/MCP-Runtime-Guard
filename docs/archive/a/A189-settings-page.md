# A189 · 설정 화면을 정책·Codex 인증·Scanner 키 세 카드로 정리

2026-10-30 · `claude/a-ui-ux-revamp-e419b9`(A188 `fd4eb2b` 다음). PR: 아직 없음(로컬 커밋).
관련: 사용자 UI·UX 전면 개선 요청 4절. I9(인증정보 비보관·비노출), T18·T19·T43 화면 범위. 백엔드 명령·저장 계약은 바꾸지 않음.

## 한 일

- 새 `ui/settings_page.py`(`SettingsPage`): 최대 760px, 카드 세 개(Guard 정책 · 설정 파일 / LLM · Codex 인증 / Scanner API 키). 정책 카드는 설정 파일 상태를 알약(확인 전·정책 확인됨·등록 저장됨·설정 파일 없음·정책 오류·등록 기록 오류·시연 · 읽지 않음)과 기존 문장으로 보이고, "정책 · 경계 열기"로 정책 화면에 간다. 파일 위치는 접힌 자세히 안에 있다.
- `window.py`: `_assemble_settings`가 `SettingsPage`를 만든다. `settings_file_status`·`scanner_key_form`·`codex_login_info` 속성 이름, `set_settings_status` 본문, `SETTINGS_FILE_*` 문구, 첫 실행 이동 규칙은 그대로다.
- `model_settings.py`(`CodexLoginInfo`): `SectionCard`로 바꾸고 상태 알약을 붙였다. 로그인 전에는 API 키 입력(가림·표시 전환, 420–560px, 같은 줄 버튼, 600px 미만이면 세로)과 ChatGPT 로그인 방법(브라우저·기기 코드)을 보인다. 진행 중에는 취소, 로그인 뒤에는 현재 방식·상태와 로그아웃만 보이고 다른 방법은 펼치지 않는다. 입력 중("입력됨 · 아직 보내지 않음")과 보낸 뒤를 구분한다. 목적·기기 코드 도움말·모델 ID는 접힌 자세히로 옮겼다.
- `scanner_key.py`(`ScannerKeyForm`): `SectionCard`, `SecretLineEdit`, 알약(저장 기능 미연결·저장된 키 없음·저장됨 · 끝 4자리·저장 실패)과 입력 상태 줄(입력됨 · 저장 전 → 저장 중… → 저장됨 / 저장 실패 / 결과 미확인)을 나눴다. 키가 있으면 버튼이 "새 키로 교체"다. 키는 저장 호출 전에 입력칸에서 지우고, 저장 함수가 예외를 내면 예외를 삼키지 않은 채 "결과를 확인하지 못함"과 실패 알약을 남긴다.
- 검수에서 고친 점: 자세히 안의 "자격증명은 Codex가 저장·관리함."이 목적 문구와 겹쳐 "Guard는 입력한 API 키를 따로 저장하지 않음."으로 바꿨다. 저장된 키 표시가 알약으로 옮겨져 `test_product_key_and_log.py`의 시작 시 확인을 알약으로 바꿨다(시연 모드의 미연결 안내는 상태 줄 그대로).

## 검증

2026-10-30, Windows / Python 3.12 / 메인 checkout `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| 새 `test_ui_settings_page.py`(32건: 인증 상태별 표시, 입력·요청 중·결과, 취소·미확인, Scanner 끝 4자리 검증과 저장 결과, 복사·잘라내기 차단, 숨길 때 키 삭제, 설정 고정 문구와 이동, 너비·반응형) + `test_ui_model_settings.py`·`test_ui_scanner_key.py`·`test_ui_icons_style.py`·`test_ui_keyboard.py`·`test_ui_sessions.py`·`test_ui_session_manage.py`·`test_ui_responsive_detail.py`·`test_ui_demo.py` | 146 passed |
| 속성을 쓰는 다른 시험 `test_bridge_registration.py`·`test_codex_browser_login.py`·`test_product_key_and_log.py`·`test_product_settings.py` | 처음 4 failed(시작 시 키 상태를 옛 상태 줄에서 찾음) → 시험 수정 뒤 `test_product_key_and_log.py` 13 passed, 나머지는 처음부터 통과 |
| `ruff check`·`ruff format --check`, `mypy src`, `lint-imports` | 통과, 7 kept |
| 캡처(1260×850 라이트·다크, 520×850) | 카드 세 개, 같은 줄 입력·버튼, 좁으면 세로, 가로 스크롤 없음 |

## 한계

- 실제 Codex 로그인과 Scanner 키 저장(Windows 자격증명 관리자)은 돌리지 않았다. 합성 콜백과 offscreen 화면만 확인했다.
- Scanner 저장 함수는 동기라 오래 걸리면 화면이 멈춘다(기존 계약 유지). "저장 중…"은 호출 직전에 그린다.
- 설정 파일 알약은 고정 문장을 코드로 되짚어 고른다. 표에 없는 문장은 "상태 미확인"이 된다.
