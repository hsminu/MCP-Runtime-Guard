# A208 · MCP 서버 삭제

2026-10-30 · `wip/next` · PR #110. 출처: 사용자 요청("mcp 서버 삭제 기능 추가", "요청없이 그냥 내가 다 구현").
관련: I2·I6·I8·I9, T10·T18·T29, S03·S04, A105(차단 흐름), A200(등록 검사), A206.

## 범위와 소유

- 사용자가 B(`ports.py`·`storage/sqlite_store.py`)와 C(`services/registry.py`) 부분까지 A가 구현하라고 정했다. B·C는 PR에서 리뷰한다.
- `docs/contracts.md` 결정 표에 넣을 줄은 사용자에게 초안으로 넘긴다. 공통 문서는 고치지 않았다.
- 구현은 Codex(`gpt-6-astra`, high)가 했고 검토 뒤 아래 세 곳을 직접 고쳤다.

## 한 일

- Store `delete_server(server_id, *, expected_row_revision, at_utc) -> None`(`RegistryStore` 프로토콜에 하나 더함)
  - BLOCKED만 받는다. 아니면 `Reason.REG_NOT_ACTIVE`이고 revision은 CAS로 확인한다.
  - 한 트랜잭션에서 `last_scan_id`·`reviewed_scan_id`를 NULL로 바꾼 뒤 `scan_reports`·`registration_tools`·`registrations` 순서로 지운다(외래 키 순서).
  - 같은 트랜잭션에 원문 없는 `REGISTRY_DELETED` 감사(서버 ID, revision, 도구 수)를 쓴다. 호출·요청·승인·기존 감사 행은 남긴다.
  - 새 Reason은 없다. 같은 ID로 다시 등록할 수 있다.
- Registry `delete`: 다른 쓰기와 같이 결과 불명이면 캐시를 stale로 표시하고, 성공하면 캐시에서 뺀다.
- 정책 `delete_server` 순서: 설정 문서 준비·검증 → PENDING·ACTIVE면 차단 → 설정 파일에서 서버 기록·Profile 선택·출처·접근 규칙 제거 → 적용(Task 정지) → Registry 삭제 → 재시작 필요(`REG_DELETE_RESTART_REQUIRED`, 끝까지 못 가면 `REG_DELETE_UNKNOWN`)와 Guard 닫기. 이미 설정에 없으면 파일을 쓰지 않는다. 쓰기 뒤 실패는 다시 시도하지 않는다.
- bridge `delete` 요청
  - 진행 중 등록 검사를 취소·정리하고(`RegistrationScans.forget`), 대기 중인 등록 확인도 비운다.
  - 결과는 `REG_DELETED`(남은 행 포함) 또는 `REG_DELETE_UNKNOWN`이다.
- `activation_preview`는 Store에서 행을 다시 읽어 최신 revision을 준다. 설정 기록이 없는 등록 행도 미리보기를 준다. 삭제가 중간에 멈춰 설정만 지워진 서버를 다음 실행에서 다시 지울 수 있게 하려는 것이다.
- 서버 목록 화면
  - 위험 표시 "선택한 서버 삭제" 버튼을 둔다. 미리보기 → "서버 삭제 확인" 창 → `delete` 순서이고, 기본 포커스는 취소다.
  - 결과는 고정 음슴체 문구로 보인다.

## 검토에서 고친 것

- 정책: Codex 구현은 차단을 먼저 쓰고 나서 설정 문서를 검증했다. `set_server_active`처럼 문서 준비·검증을 첫 쓰기 앞으로 옮겼다. 이제 문서가 잘못되면 아무것도 쓰지 않고 Guard도 열어 둔다.
- bridge: 정책의 `SessionRuntimeError`를 `REG_ACTIVATION_STALE`만 빼고 전부 `REG_DELETE_UNKNOWN`으로 바꾸고 있었다. 그래서 쓰기 전 거절(이미 재시작 필요, 설정 저장 불가, 문서 무효)도 "삭제가 끝났는지 확인하지 못함"으로 보였다. 이제 쓰기 뒤 저장 실패(`POLICY_SAVE_FAILED`·`POLICY_SAVE_UNKNOWN`·`POLICY_SAVE_FILE_CHANGED`)만 바꾸고, 나머지는 원래 코드로 돌려준다.
- 화면: 삭제 버튼은 Registry 등록 상태(pending·active·blocked)가 있을 때만 켠다.
- 시험 셋을 더했다(문서 무효 시 차단 안 함, 쓰기 전 거절 코드 유지, 등록 없는 서버 버튼 꺼짐). 확인 창 위험 표시 단언도 하나 더했다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| `test_a208_server_delete.py` | 35 passed |
| 대상 14개 파일(A208, registry_store, server_registry, registry_settings_change, a105_activation, bridge_registration, product_settings, server_add, ui_read_only_pages, a206_server_detail, a_architecture, ui_icons_style, a200_registration_scans, sqlite_store) | 450 passed, 115 subtests passed |
| `ruff check`·`ruff format --check`(389개), `mypy src`(147개), import-linter 7개 계약, `git diff --check` | 통과 |

되돌림 검사(전부 복원 뒤 바이트 같음):

| 되돌림 | 잡은 시험 |
| --- | --- |
| Store BLOCKED 확인 제거 | 2 실패 |
| Store CAS 제거 | 4 실패 |
| 정책에서 차단 건너뜀 | 7 실패 |
| 설정 파일 서버 기록 유지 | 4 실패 |
| 검증 전에 차단 쓰기 | 14 실패 |
| 쓰기 전 거절도 불명으로 | 1 실패 |
| 삭제 버튼 등록 조건 제거 | 1 실패 |
| Registry 캐시 유지 | 1 실패 |
| 검사 행 남김 | 1 실패 |
| 확인 창 위험 표시 해제 | 3 실패 |
| finally 재시작 표시 제거 | 9 실패 |

## 한계

- 계약 초안에는 "다시 등록하면 revision 0부터"라고 적었지만 스키마가 `row_revision >= 1`을 강제한다. 다시 등록해도 새 등록과 같이 1부터다. 초안을 고쳤다.
- 쓰기 전 거절 중 이미 재시작이 필요한 경우는 그 재시작 코드의 문구가 보인다(차단 흐름과 같다).
- 실제 제품 창에서 눌러 보지는 않았다. 전송 dispatch는 A105 peer 계수로만 봤고 외부 wire는 주장하지 않는다.
- B·C 리뷰 전이다.
