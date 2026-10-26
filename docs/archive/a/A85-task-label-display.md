# A85 · Task 등급·미분류 표시

2026-10-18. Store의 `TaskState.high_watermark`·`unclassified`를 화면에 보이도록 A 소유 projection을 넓혔다. B·C 소유 파일과 공통 계약은 바꾸지 않았다.

- `TaskProjection`(bridge, A)에 `label: Label | None`·`unclassified: bool`을 추가했다. 기본값은 "미제공"이다.
- 제품 `SessionRuntime.snapshot`과 시연 `examples/synthetic_app.py`가 Store에서 읽은 값을 그대로 넣는다. 모델이나 도구 출력에서 추론하지 않는다(I7).
- 세션 머리글과 결과 화면 요약에 다음처럼 표시한다.
  - "Task 등급: 내부 · INTERNAL"
  - 미분류 표시가 있으면 " · 미분류 자료 표시됨"을 덧붙인다.
  - 값이 없으면 "미제공"이라고 쓴다.

UI 이벤트 계약(B1.5)이 확정되면 같은 필드를 그 DTO로 옮긴다.

검증: 새 테스트 4개(화면 3, 제품 snapshot 1)와 관련 4파일 163개 통과. ruff·mypy 통과. 관련: I7, T18.
