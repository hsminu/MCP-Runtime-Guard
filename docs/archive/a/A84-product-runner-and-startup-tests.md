# A84 · 제품 Runner·시작 경로·아이콘/스타일 테스트 보강

2026-10-18. 테스트만 추가했고 제품 코드는 바꾸지 않았다.

이전에는 `tests/unit/test_runner.py`가 `examples.legacy_runner`만 대상으로 해서, 제품의 `CodexTaskRunner`를 직접 시험하는 테스트가 없었다. 제품 시작 경로(`--demo` 없음)와 `ui/icons.py`·`ui/style.py`도 직접 테스트가 없었다.

## 추가한 테스트

- **`test_codex_task_runner.py` 16개.** 제품 Runner의 다음 동작을 확인한다.
  - 입력을 한 번에 하나만 받는다.
  - 빈 입력이나 문자열이 아닌 입력을 거부하고, 입력 없이 진행하면 거부한다.
  - 응답이 문자열이 아니거나 65,536자를 넘으면 정지하고 닫힌다. 65,536자 정확히는 받는다.
  - 인증·세션 실패는 고정 코드로 정지한다.
  - 분류되지 않은 예외는 실패를 한 번만 보고하고 예외를 전파한다.
  - 전송 중 닫히면 늦게 온 답을 버리고 실패를 보고하지 않는다.
  - 승인 재개는 계약 전이라 거부한다.
- **`test_session_runtime.py` 2개.**
  - C 구성요소 없이 제품 bootstrap이 시작되는지 확인한다: `human_control=None`, `session_blocker`가 `CODEX_CONFIG_UNAVAILABLE`, 생성 거부.
  - 시작 준비가 실패하면 Store를 닫고 오류를 낸다.
- **`test_ui_icons_style.py` 21개.**
  - 고정 아이콘 모두가 보통·비활성 두 상태에서 실제로 그려진다.
  - 모르는 이름은 거부한다.
  - 아이콘 그림과 스타일시트에 외부 참조(`href`·`url(`·`http`)가 없다.

## 검증

새 테스트 39개가 통과했다. 해당 파일 ruff·format 통과. 전체 실행 결과는 다음 단계 커밋 기록에 함께 남긴다.

관련: I7·I8·I9(Runner 실패 처리), T18(표시), T21(Task별 Runner).
