# A97 · MCP 도구 오류(`is_error`)를 Codex에 실패로 전달

2026-10-19. PR #32 A 리뷰에서 찾은 A 쪽 빈틈이다.

## 한 일

- `services/guard_relay.py`: Released 결과의 `is_error`가 True면 Codex 응답의 `success`를 False로 보낸다.
  - 공개된 본문(텍스트·구조화 결과)은 그대로 넘긴다.
  - 도구 오류는 Task 정지가 아니다.
- 바로 Released된 결과와 승인 뒤 executed로 나온 결과가 같은 변환(`_answer`)을 거치므로 둘 다 적용된다.
- 전에는 MCP 서버가 `isError`를 돌려줘도 `success=True`로 넘어가서, 모델이 실패한 호출을 성공으로 받았다.

## 검증

- `test_codex_session.py`에 바로 Released된 경우와 승인 뒤 나온 경우 테스트 2개를 넣었다.
  - `success=False`, 본문 유지, 세션은 정지하지 않음
  - 수정 전 코드에서 둘 다 실패하는 것을 확인했다.
- 전체 테스트: 3,735개 통과, 1 skip, 2 실패.
  - 실패한 2건은 이번에 바꾼 파일과 관계없는 테스트다: `test_access_rules.py::test_decision_and_normalization_are_deterministic`, `test_agt_real.py::test_burst_of_slow_but_healthy_sdk_calls_never_retires_or_closes`.
  - 둘 다 단독으로 세 번 다시 돌리면 모두 통과했다. 이번 전체 실행이 평소보다 느렸다(8분 49초). 원인은 확인하지 못했다.
- ruff·format·mypy 통과.

## 남은 것

- 로컬 A 브랜치에만 커밋했다.

관련: I7, T14.
