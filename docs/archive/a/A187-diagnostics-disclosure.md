# A187 · epoch·dispatch·상태 코드를 기본 화면에서 진단 정보로 옮김

2026-10-30 · `claude/a-ui-ux-revamp-e419b9`(A186 다음). PR: 아직 없음(로컬 커밋).
관련: 사용자 UI·UX 전면 개선 요청 2절(개발 용어는 자세히·진단으로, 제약·오류는 숨기지 않음), 기술 설계 12.4. 백엔드 값과 계산은 바꾸지 않음.

## 한 일

- 대화 화면 머리(`ui/sessions.py`): `epoch n`·Task ID·상태 문장 라벨을 빼고 상태 알약과 등급 태그(`LabelPill`)만 둔다. Task ID·epoch·상태 코드는 상태 알약 툴팁으로 옮겼다.
- 활동 패널 요약(`ui/runtime.py`): 상태 이름, 등급, 모델(모델 ID가 없으면 모델 모드), 다음 할 일 한 줄만 보인다. Task ID·epoch·상태 코드·호스트 dispatch·수신 부작용·모델 모드는 접힌 "진단 정보"(`Disclosure`) 안에 PlainText로 둔다. Task가 없으면 진단 칸을 숨기고 비운다.
- 승인 대기면 "승인 사본에서 수신처와 인자를 확인하세요.", 진행 가능하면 "실행 버튼을 눌러 다음 모델 단계를 진행하세요."를 더했다. 종료 상태 설명과 기존 안내 문장은 그대로다.
- 내부 오류 코드 토글("내부 코드 ▸")은 그대로 두었다.

## 검증

2026-10-30, Windows / Python 3.12 / 메인 checkout `.venv`, `QT_QPA_PLATFORM=offscreen`.

| 확인 | 결과 |
| --- | --- |
| `test_runtime.py`·`test_ui_sessions.py`·`test_ui_approval_t18.py` | 57 passed |
| 진단 정보가 접혀 있다가 열면 Task ID·epoch·상태 코드·dispatch 수를 보임, `None` 문자 없음 | 통과(`test_results_summary_never_prints_none_counters`) |
| 숨은 문자가 섞인 Task ID가 진단 칸에 PlainText로만 보임 | 통과(`test_ui_approval_t18.py`) |
| `ruff check`·`ruff format --check`·`mypy src`·`lint-imports` | 통과 |

## 한계

- 진단 정보를 클립보드로 내보내는 기능은 만들지 않았다.
- 실제 Codex Task로는 확인하지 않았다. 가짜 TaskProjection과 mock 모델 시험만 돌렸다.
