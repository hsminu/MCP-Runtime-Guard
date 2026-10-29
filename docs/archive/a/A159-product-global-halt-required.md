# A159 · 제품 조립은 전역 정지 없이 시작하지 않음

[#83 B 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/83#issuecomment-6493403531) 1번의 참고 의견을 #83에서 고쳤다.

## 한 일

- `assemble_guard`가 Store의 `halt_execution`을 한 번 꺼내 `GuardService(on_global_halt=)`에 넘긴다.
  - 제품 경로(`ProductInputs.folders`가 있음)에서 그 값을 호출할 수 없으면 `ProductAssemblyError("GLOBAL_HALT_UNAVAILABLE")`로 조립을 거부한다. 전에는 `getattr(store, "halt_execution", None)`이 조용히 전역 정지를 빼고 조립했다.
  - 검사는 stream 구성과 서버 존재 확인 뒤, transport를 만들기 전이다. 서버에 연결하지 않는다. 모호한 입력은 지금처럼 `PRODUCT_INPUTS_INVALID`가 먼저 거부한다.
  - 주입 조립(folders 없음, 시험·데모)은 지금처럼 None을 허용한다.
- 화면: `bootstrap_product`에서 올라온 조립 오류는 bridge가 `STARTUP_FAILED`("백엔드를 시작하지 못함 · 앱을 다시 시작하세요.")로 바꾼다. 새 문구는 넣지 않았다. 제품 Store(`SQLiteStore`)와 Store 포트(`StoreLifecycle.halt_execution`)에는 이 메서드가 있어서 정상 시작에서는 이 거부가 나지 않는다.
- B 리뷰 3번(같은 내용 재활성화로 멈춘 Task가 감사에 "사람의 긴급 정지"로 남음)은 사용자가 누른 활성화로 생긴 정지라 지금 기록으로 둔다. B `apply_policy_change` 선택지는 요청하지 않는다.

## 실제 검증

2026-10-29, Windows 11, Python 3.12, 로컬 `.venv`.

- 새 시험 `test_product_refuses_a_store_without_global_halt`(`test_mcp_launcher_pipes.py`):
  - 실제 `SQLiteStore`를 감싸 `halt_execution`을 없애거나 호출할 수 없는 값으로 바꾼 Store로, 실제 앱 폴더와 launcher stream 구성의 제품 조립을 하면 `GLOBAL_HALT_UNAVAILABLE`로 거부된다. launcher 실행 0회, 도구 호출 0회.
  - 같은 Store로 주입 조립(folders 없음)은 성공하고, Guard의 전역 정지 콜백은 None이다.
- 고의 회귀 3개를 하나씩 넣고 되돌렸다. 모두 새 시험이 잡았다.

  | 고의 회귀 | 결과 |
  | --- | --- |
  | 검사 삭제(전처럼 조용히 None) | 실패 |
  | folders 조건 삭제(주입 조립도 거부) | 실패 |
  | 호출 가능 대신 None만 확인 | 실패 |

- 관련 시험 14개 파일(조립·설정·시작·launcher·등록·A105·A138·A143·A151·Codex 제품 흐름·로그): 193개 통과, subtests 28개.
- 조립을 쓰는 C 계수 시험 3개 파일(접근 규칙·승인·Guard 경로): 64개 통과.
- `ruff check .`, `ruff format --check .`(306개), `mypy`(118개 소스), `lint-imports --config tools/ci/importlinter.ini`(계약 7개 유지), `bandit -r src -ll`, `git diff --check` 통과.
- 전체 시험은 이 기록에서 다시 돌리지 않았다.

## 남은 한계

- 거부 코드는 화면에서 `STARTUP_FAILED`로 묶여 따로 보이지 않는다. 정상 제품 Store에서는 나지 않는 경로라 문구를 따로 두지 않았다.

관련: I6, SEQ-02, REC-01, A138, [A157](A157-pr83-c-review-followups.md).

PR: [#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83)
