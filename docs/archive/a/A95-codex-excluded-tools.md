# A95 · Codex에서 빠진 도구를 이유와 함께 표시

2026-10-19. PR #31(contracts 2026-10-18 Codex 이름 규칙 결정)의 "A는 빠진 도구를 이유와 함께 화면에 보여준다" 부분이다.

## 한 일

- **`adapters/codex_protocol.py`.** `build_dynamic_tools`가 뺀 도구마다 고정 이유 코드를 붙인다(`ToolExclusion`).
  - `NAMESPACE_NAME`: 서버 ID가 이름 규칙·길이(64자)·`mcp` 접두에 걸림
  - `NAMESPACE_RESERVED`: Codex 예약 namespace
  - `TOOL_NAME`: 도구 이름이 이름 규칙·길이(128자)·`mcp` 접두에 걸림
  - `DUPLICATE`: 같은 `server__tool`이 이미 있음
  - 기존 `excluded`(도구 참조 목록)는 그대로 둔다.
- **`services/policy_view.py`.** `PolicyView.codex_excluded`에 (도구 참조, 이유 코드)를 담는다. Codex에 실제로 넘기는 것과 같은 계산(`assembled.tool_views`)을 쓴다.
- **정책 화면(`ui/policy.py`).** 빠진 도구가 있을 때만 "Codex에서 쓸 수 없는 도구" 표를 보인다.
  - 이유는 코드별 고정 문구로 보여준다. 모르는 코드는 일반 문구로 보여준다.
  - 도구 이름은 규칙을 어긴 이름이라 제어 문자나 닮은꼴 문자가 섞일 수 있다. 그래서 `untrusted_display_text`로 바꿔서 보여준다(200자 제한).

## 검증

- 새 테스트
  - `test_codex_protocol.py`: 경우별 이유 코드 7개, 중복
  - `test_ui_policy.py`: 표 표시·숨김, 이유 문구, bidi 문자 표시
- 전체 테스트(로컬 A 브랜치, PR #28 병합 상태): 3,734개 통과, 1 skip, 1 실패.
  - 실패한 것은 B 소유 `test_access_rules.py::test_decision_and_normalization_are_deterministic`(hypothesis 성질 테스트)이다. A가 바꾸지 않은 파일이고, 단독으로 세 번 다시 돌리면 모두 통과했다. 실패 원인은 기록하지 못했다.
- ruff·format·mypy 통과.

## 남은 것

- 세션 화면에는 지금처럼 "도구 없는 대화" 표시만 있다. 빠진 도구 목록은 정책 화면에서 본다.
- 로컬 A 브랜치에만 커밋했다. PR #28 병합 뒤 main을 합쳐 PR #29에 올린다.

관련: I11, T18.
