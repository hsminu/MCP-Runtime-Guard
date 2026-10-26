# A57 · tools/list 수집의 시간 기준·반복 cursor·중복 이름·수집 중 종료 보정

A09·A10 수집을 다시 손본 작업이다. A09·A10 기록은 고치지 않고 이 기록을 새로 쓴다. QA 검토(독립 에이전트 2명)에서 나온 지적을 C 운영 설정 추천안 v1 원문(`archive/tasks/c-scanner-operating-proposal.md`)과 대조한 뒤 반영했다.

## 한 일

- **시간 기준**
  - 추천안: 초기화 10초는 "서버 연결·initialize 완료까지, 대기 시작부터", 목록 수집 30초는 "초기화 시작부터 모든 목록 페이지 수신 완료까지"다.
  - A09·A10은 `initialize()`만 10초로 묶었고, 30초는 initialize가 끝난 뒤부터 쟀다.
  - 이제 수집을 켜면 stream 열기와 `initialize`를 함께 10초로 묶는다. 수집 마감은 같은 시작점에서 30초다.
  - `collect_tool_definitions`에 절대 마감 시각 `deadline`을 추가했다. 시작 대기 전체는 `max(초기화, 수집) + 1초`다.
- **반복 cursor와 중복 이름** (추천안 "입력·실행·오류 처리" 표)
  - 이미 쓴 cursor가 다시 나오면 즉시 `TOOLS_LIST_CURSOR_REPEATED`로 끝낸다.
  - 같은 목록에 같은 도구 이름이 다시 나오면 `TOOLS_LIST_DUPLICATE_NAME`으로 끝낸다.
  - 도구 수는 중복을 제거하기 전에 센다.
- **수집 중 종료**: 시작 도중(초기화·수집) `close()`가 owner를 취소하는 건 close 자신의 동작이다. 그런데 이것을 `WorkerOwnerFailed`로 보고하던 결함을 고쳤다. 시작 시간초과로 취소할 때도 같다.
- **깊이 상한**: `max_json_depth`는 64를 넘길 수 없게 했다. 64를 넘기면 `FrozenJsonObject` 자체 상한에 먼저 걸려서, 깊이 초과가 아니라 형식 오류로 잘못 보고되기 때문이다.

## 실제 검증

- `tests/unit/test_mcp_tool_listing.py` 24개 통과. 새 테스트는 다섯 가지다.
  - 반복 cursor·중복 이름
  - 깊이 65 설정 거부
  - 느린 연결이 초기화 상한에 포함됨
  - 수집 마감을 초기화 시작부터 셈
  - 수집 중 close가 정상 종료됨
- 새 테스트 5개(파라미터 포함)는 수정 전 코드에서 모두 실패함을 확인했다.
- 페이지 상한·시간초과 기존 테스트 2개는 같은 cursor를 반복하는 방식이어서, 고유 cursor로 바꿨다. 반복 cursor 규칙이 먼저 적용되기 때문이다.
- `ruff check`·`ruff format --check`·`mypy` 통과. 전체 `pytest -q` 결과는 같은 커밋 메시지에 남긴다.

## 남은 한계

- 빈 문자열 `nextCursor`는 다음 페이지로 본다. 반복 규칙 때문에 두 번째에 미완료로 끝나므로 보수적이다.
- 1MiB·8MiB 바이트 상한은 그대로 막혀 있다(C stream ingress, #21 5번).
- 추천안의 동작 규칙(반복 cursor·중복 이름)이 10-15 결정의 "값" 범위에 포함되는지는 사용자 확인이 필요하다. 그래서 a-status A56에 함께 둔다.

## 관련

- I6, I10 기반, T17, contracts 2026-10-15 Scanner 운영 상한 결정, C 운영 설정 추천안 v1.
- 커밋: 이 기록과 같은 커밋. PR 없음.
