# A170 · #89의 `GUARD_CLOSED` 사유를 화면 문구로

2026-10-29 · `wip/next`(main 병합 `72513a7` 위). PR: [#92](https://github.com/hsminu/MCP-Runtime-Guard/pull/92)에 올릴 예정.
관련: #89(B 합의 전 제안), I9.

## 문제

main의 #89가 사유 `GUARD_CLOSED`를 더했다. 앱이 끝날 때 `Guard.aclose`가 전역 정지에 쓰는 사유이고, B 설명으로는 결함이 아니다. 그런데 화면 사유 표(`ui/presentation.py` `REASONS`)에는 이 코드가 없었다. 그래서 호출 로그의 표·CSV·상세와 현황에서 "미확인"으로 보였다. 정상 종료가 알 수 없는 문제처럼 읽힌다.

## 한 일

- `REASONS`에 `"GUARD_CLOSED": "Guard 정상 종료로 실행하지 않음"`을 더했다(음슴체).
- 시험 `test_call_logs.py::test_every_reason_a_row_can_carry_has_its_own_text`를 더했다.
  - 사유 표가 `Reason` 전체에서 요청·승인 행에 오지 않는 사유를 뺀 집합과 같은지 본다. 빠지는 사유는 `OK`와 Codex 세션 사유 7개(`SESSION_*`)다.
  - 다음에 사유가 늘었는데 문구가 없으면 이 시험이 실패한다.
  - 호출 로그 CSV에 새 문구가 나오는지도 본다.
- `SESSION_*` 사유는 지금 어느 서비스도 요청·승인 행에 쓰지 않는다. 모델 문구(`core/model_messages.py`)를 고르는 데만 쓰인다. 그래서 화면 문구는 더하지 않고 시험의 제외 목록에 이유와 함께 적었다.

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- 사유 표를 쓰는 화면 시험 17개 파일: 271 passed, subtests 9.
- 고의 회귀: 새 줄을 빼면 새 시험이 실패했다. 원래 바이트로 되돌린 뒤 SHA-256 일치를 확인했다.
- `ruff check`·`ruff format --check`(바뀐 2개 파일), `mypy`(138개 소스), `git diff --check` 통과.
- 전체 단위 시험은 돌리지 않았다.

## 남은 것

- `GUARD_CLOSED`는 B의 합의 전 제안이다. 이름이나 뜻이 바뀌면 문구도 따라 바꾼다.
- 이 사유가 실제로 요청·승인 행에 기록되는지는 B Store 동작이다. 이번에는 실제 종료 흐름을 화면으로 확인하지 않았다.
- B·C 코드와 공통 문서는 바꾸지 않았다.
