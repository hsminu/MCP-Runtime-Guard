# A177 · #94 F-02: 도구 설정 화면의 도구 단위 READ 등급 안내

2026-10-30 · `wip/next`(`feat/a-application` head `9da5868` 기준). PR: 다음 A PR(아직 없음)
관련: #94 F-02

## 문제

`resource_arg`가 없는 도구 단위 READ는 `(server_id, tool_name, "")` 규칙 하나로 모든 결과의 등급을 정한다. 여러 출처를 섞는 도구를 낮은 등급으로 설정하면 높은 등급 자료도 낮게 분류되지만, 도구 설정 화면에는 이를 알리는 안내가 없었다.

## 한 일

- `src/guard_app/ui/tool_settings.py`: 위쪽 안내의 기존 셋째 줄("보내기는 목적지 인자만 설정함. …") 바로 앞에 다음 문구를 한 줄로 추가했다.
  - 읽기 결과 등급은 그 도구가 돌려주는 모든 결과에 같이 붙음. 여러 출처를 섞어 돌려주는 도구는 그중 가장 높은 등급을 고르세요.
- 기존 안내의 내용·순서와 자원형 READ의 읽기 전용 표시는 유지했다. 등급 기본값·미선택 저장 차단·분류 계약은 바꾸지 않았다.
- `tests/unit/test_a155_tool_settings.py`: 기존 화면 시험에서 안내의 셋째 줄을 문구 전체와 정확히 비교하도록 단언을 추가했다.
- `docs/a-status.md`: A176 바로 아래에 A177 완료 줄을 추가했다.
- 기존 파일의 CRLF를 유지하고 새 완료 기록도 CRLF로 작성했다.

## 검증

Windows, 로컬 `.venv` Python 3.12, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `QT_QPA_FONTDIR=C:/Windows/Fonts`, `PYTHONDONTWRITEBYTECODE=1`. pytest에는 `-q -p no:cacheprovider --basetemp=.cache/codex-a177/pt-<n>`을 붙였다. 임시 파일과 검사 캐시는 `.cache/codex-a177/` 안에 두었다.

- 지정 4개 파일: 82 passed(`pt-1`)
  - `test_a155_tool_settings.py`·`test_a105_activation.py`·`test_ui_display_escaping.py`·`test_a_architecture.py`
- 고의 회귀: 안내 문구만 잠시 지우자 수정한 화면 시험이 새 단언에서 1 failed, pytest 종료 코드 1(`pt-2`).
- `tool_settings.py`를 원래 바이트로 복원하고 SHA-256 일치를 확인했다. 복원 뒤 해당 화면 시험은 1 passed(`pt-3`).
  - 회귀 전·복원 후 SHA-256: `0181B9F6C8E147E0F8183B4554370C2E28F9AE9E5F148B1665CB23F7804FF20E`
- `ruff check`·`ruff format --check`: 바뀐 Python 2개 통과. 최초 줄 길이 초과는 소스 문자열만 두 줄로 나눠 수정한 뒤 재검사했다. 화면 문구는 한 줄이다.
- `mypy src`: 140개 소스 파일 통과.
- `lint-imports --config tools/ci/importlinter.ini`: 7 kept, 0 broken.
- `git diff --check`: 통과.

## 남은 것

- 요청 범위의 미완료 작업 없음. 전체 시험과 실제 창의 육안 확인은 수행하지 않았다.
- 도구 단위 READ 차단이나 등급 하한은 B 계약 결정 사항으로 이번 범위에 포함하지 않았다.
