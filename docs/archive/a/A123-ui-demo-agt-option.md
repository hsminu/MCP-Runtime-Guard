# A123 · 화면 시연의 AGT 호환 코드 제거

2026-10-28. [A123 첫 기록](A123-agt-display-removal.md)에서 남긴 일이다. `RuntimeIdentity.agt_mode`와 그 생성 코드는 B AGT 제거([PR #47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47), B2.11)가 `src`에서 지웠다. #47 병합 전후 모두 돌도록 `examples/ui_demo.py`에 넣어 둔 호환 코드만 남아 있었다.

## 한 일

- `examples/ui_demo.py`의 `synthetic_backend()`가 `inspect.signature`로 `bootstrap`의 `agt_mode` 인자를 찾던 코드를 지웠다. 이제 `bootstrap("ask")`만 부른다.
- 쓰는 곳이 없어진 `import inspect`를 지웠다.
- 남은 AGT 흔적은 `Reason.AGT_DENY`·`AGT_ERROR` 키와 그 표시 문구, 시연 데이터의 사유 값뿐이다. 이름 정리는 B의 AGT-04(B2.10) 합의 뒤에 한다.

## 검증

- `tests/unit/test_ui_demo.py` 통과. 전체 결과는 [A44 기록](A44-demo-without-test-doubles.md)과 같은 실행이다.
- `rg "agt_mode" src examples`: 결과 없음.

관련: A53, B2.10, B2.11. PR: A 브랜치([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)에 더할 예정).
