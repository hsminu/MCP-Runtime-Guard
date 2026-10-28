# A51·A53 · 동기 접근 판정 포트와 AGT 제거 리뷰

2026-10-28. 두 항목 모두 B의 AGT 제거 [PR #47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47)(B2.9·B2.11)로 끝났다. 이 기록은 A가 한 몫을 남긴다. 코드 변경은 #47에 있고, 이 기록에는 없다.

## 한 일

- **A51 동기 포트 전환:** `AccessChecker.check_access`가 동기 포트가 됐다(ACCESS-01). A의 `adapters/access_rules_checker.py`도 #47의 커밋 `36d4d45`에서 동기로 바뀌었다. 2026-10-17 결정대로 B가 A 소유 파일을 고쳤고 A가 리뷰했다.
- **A53 A 소유 파일 리뷰:** 2026-10-27 #47에 A 리뷰를 게시하고 승인했다.
  - 포트 문안에 A가 동의했다. #20의 ACCESS-01 A(10-18)·C(10-27) 동의와 함께 구현된 포트 문안까지 A·B·C 합의가 기록됐다.
  - `AccessRulesChecker`는 I/O·대기·스레드 없이 (서버, 도구) 집합만 조회한다. `check_access`를 직접 부르는 곳은 Guard 하나다.
  - `__main__`·bridge·ui·examples의 AGT 흔적이 `Reason.AGT_*` 키와 표시 문구만 남은 것을 확인했다.

## 검증(리뷰 때 #47 head `21b68da`)

- ruff·format·mypy 통과, 단위 4,679개 통과·3개 skip, 계약 41개 통과
- `examples.verify_stdio_spike`·`verify_http_spike` 시나리오 6개와 `deny-send`·`deny-all` 계수가 PR 본문과 같았다.
- `python -m guard_app --demo --agt-mode mock`이 종료 코드 2로 거부된다.

## 남은 것

- `Reason.AGT_*` 이름 정리는 B2.10(AGT-04) 합의 뒤다.
- 시연 앱의 AGT 호환 코드는 [A123 기록](A123-ui-demo-agt-option.md)에서 지웠다.

관련: A123, B2.9, B2.10, B2.11, ACCESS-01. PR: [#47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47)(B, 병합됨).
