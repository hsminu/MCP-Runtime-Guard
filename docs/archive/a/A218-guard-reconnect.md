# A218 · 등록 변경 뒤 앱 재시작 없이 Guard 다시 연결

2026-10-30 · `wip/next`(#113 head `6af7a4d` 위). PR: 다음 A PR(아직 없음). 출처: 사용자 요청("서버 활성화, 서버 도구 설정 하나 할때마다 껐다가 켜야하는데 이거 개선방안없음?"), 지시("1안 진행", "A: 교체 때만 정지 생략", "걍 해버려 b 동의받음").
관련: I1·I3·I6·I10, T10·T11·T29, A105(Task 행 잠금), A208(서버 삭제).

## 왜

- 서버 활성화·차단, 삭제, 도구 설정 저장, 실행 파일 지문 재확인은 저장에 성공해도 재시작 필요 표시를 세우고 Guard를 닫았다.
  - 결과 코드: `REG_ACTIVATION_RESTART_REQUIRED`, `REG_DELETED`, `REG_TOOLS_RESTART_REQUIRED`, `REG_PIN_SAVED`
  - 이유: `McpTransport`가 만들 때 연결할 서버를 고정한다.
- 그래서 설정을 하나 바꿀 때마다 앱을 껐다 켜야 했다.
- 사용자가 고른 1안: 앱 안에서 Guard를 다시 조립한다.

## 한 일

### 다시 연결 순서 (`services/session_runtime.py` `_reconnect_guard`)

등록 저장이 성공하면 `TaskPolicy`가 정책 잠금 안에서 런타임의 다시 연결을 부른다.

1. 세대 번호를 올린다.
2. 진행 중인 세션을 `POLICY_CHANGED`로 정지하고, 진행 중인 작업이 끝나기를 기다린다.
3. 이전 조립을 닫는다: transport → settle → Guard 순서이고, Guard를 닫을 때만 교체 종료로 표시한다.
4. 아래가 모두 맞을 때만 다시 조립한다. 하나라도 어긋나면 다시 조립하지 않는다.
   - 정지 기록 확인됨
   - 정리 미확인 없음(생성 중인 세션 자원은 제외)
   - admission 거절 없음
   - Store 실행 권한 열림
5. `product_startup._guard_rebuilder`가 Registry를 다시 읽고 `assemble_guard`로 새 Guard·transport를 만든다. 새 `ApprovalOutcomes`와 Codex 세션 팩토리도 이때 만든다.
6. 새 조립으로 바꾸고 세대를 한 번 더 올린다.
   - 바꾸는 것: 승인 결과, 이벤트, 도구 참조, 세션 팩토리, 시스템 정지 채널
   - `TaskPolicy.adopt_guard`로 정책도 새 Guard에 붙인다.
7. 실행 권한과 admission을 다시 확인한 뒤 bridge에 새 `GuardBindings`를 알린다.

어느 단계에서든 끝나지 않으면 지금까지처럼 재시작 필요다.

- 새 조립을 닫는다.
- 이전 조립의 보류된 정지를 저장소에 넘긴다.
- `TaskPolicy`가 원래 재시작 코드로 admission을 막는다.
- 취소일 때도 위 정리를 한 뒤 `CancelledError`를 다시 던진다.

저장 결과가 불명인 경로는 다시 연결하지 않고 재시작 필요 그대로다.

### 계획된 교체 종료의 저장소 정지 보류 (`services/product_assembly.py` `GlobalHaltRelay`)

- B의 `GuardService.aclose()`는 `_halt_globally(GUARD_CLOSED)`를 부른다. 이것이 `on_global_halt` = `store.halt_execution`으로 이어진다.
  - 그 결과 Store가 FAULTED·`execution_enabled=False`·`requires_restart=True`가 된다.
  - 따라서 이전 Guard를 닫으면 다시 조립이 늘 실패했다(Codex 첫 구현의 시험 24개 실패).
- B 파일은 그대로 두고, A 조립 계층이 저장소 함수 앞에 전달기를 둔다.
  - **평소:** 모든 사유를 그대로 넘긴다. 앱 종료와 실패 정리도 같다.
  - **교체 종료:** `AssembledGuard.aclose(replacing=True)`가 Guard의 `aclose()`를 부르는 동안만 `GUARD_CLOSED` 하나를 보류한다. 다른 사유는 그때도 넘긴다.
  - transport 종료·settle 단계는 아직 표시 전이라, 그때 난 정지는 그대로 저장소로 가고 다시 조립하지 않는다.
  - **성공하면:** 보류를 버린다. 저장소는 READY·실행 권한 열림이다.
  - **실패·취소면:** 보류한 `GUARD_CLOSED`를 한 번 넘긴다. B의 전역 정지는 한 번만 동작하므로, Guard를 다시 닫는 것으로는 저장소가 닫히지 않는다. 그래서 명시적으로 넘겨야 한다.
- 주입 구성(전역 정지 None)은 지금처럼 None이다.
- 근거: 사용자가 이 방식을 골랐고 B 동의를 받았다고 전했다. GitHub 기록 링크는 없다.

### 세션 생성과 교체 (`_create_session`)

- Codex 실행은 정책 잠금 밖이다. 잠금은 Task 행을 쓰는 부분만 쥔다(A105 범위 그대로).
  - Codex 첫 구현은 세션 생성 전체를 잠금 안에서 돌렸고, 그동안 설정 저장이 멈췄다. 이를 되돌렸다.
- 생성자는 팩토리와 세대 번호를 같은 동기 구간에서 읽는다.
- 행을 쓰는 잠금 구간에서 세대가 바뀌었으면 `SESSION_UNAVAILABLE`로 거절한다. 이미 띄운 세션은 기존 실패 정리(`_discard`)로 닫힌다.
- 교체 뒤 새로 시작한 생성은 새 팩토리로 진행한다.

### bridge와 화면

- **bridge 연결**
  - `bridge_types.GuardBindings`: Guard에 묶인 바인딩 5개다.
  - `BindingsOwner.change_guard`: 쓸 수 없는 바인딩이면 `ValueError`로 거절하고 기존 값을 유지한다.
  - `bridge_host`: 바꾼 뒤 정책 이벤트를 다시 보낸다.
- **결과 코드:** 다시 연결에 성공하면 새 코드다.

  | 변경 | 다시 연결 성공 | 실패(기존) |
  | --- | --- | --- |
  | 활성화·차단 | `REG_ACTIVATION_RECONNECTED` | `REG_ACTIVATION_RESTART_REQUIRED` |
  | 삭제 | `REG_DELETE_RECONNECTED` | `REG_DELETED` |
  | 도구 설정 | `REG_TOOLS_RECONNECTED` | `REG_TOOLS_RESTART_REQUIRED` |
  | 지문 재확인 | `REG_PIN_RECONNECTED` | `REG_PIN_SAVED` |

- **화면 문구:** 상태라 음슴체다. 예: "서버 설정 저장 완료 · 다시 연결함. 진행 중이던 Task는 정지됨."
  - 지문 재확인이 다시 연결되면 설정 화면에도 저장 완료로 보인다.
- **세션 팩토리를 주입한 구성:** 다시 연결을 연결하지 않고 기존 재시작 경로를 쓴다.

## Codex 결과에서 바꾼 것

- Codex가 쓴 기록과 현황 줄을 다시 썼다.
  - 기록에 "커밋하지 않음", "공통 문서를 고치지 않음"이 있었는데 사실과 다르다. 이 기록과 같이 커밋하고, 계약·개발문서도 같은 변경에서 고친다.
  - 현황 줄이 빈 줄 뒤에 들어가 있어 A217 바로 아래로 옮겼다.
- 코드는 Codex 두 번째 결과(spec2) 그대로다.

## 기존 시험을 고친 이유

- `test_a208_server_delete.py`: 화면 문구 표에 새 코드 3개를 더했다. 기존 행은 그대로다.
- `test_bridge_registration.py`: 지문 재확인 시험을 `REG_PIN_SAVED`·`REG_PIN_RECONNECTED` 두 경우로 돌린다. 기존 단언은 그대로다.

## 검증

Windows / Python 3.12.15, offscreen, `QT_QPA_FONTDIR` 없음.

| 확인 | 결과 |
| --- | --- |
| 새 시험 `test_a218_guard_reconnect.py` | 84 통과 |
| 관련 시험 37개 파일(새 시험 포함. 다음 이름을 쓰는 시험 파일 전부: `assemble_guard`·`bootstrap_product`·`set_server_active`·`save_tool_settings`·`delete_server`·`save_registration`·`GuardBindings`·`BindingsOwner`·`create_session`·`AssembledGuard`) | 658 통과, 21 건너뜀, 경고 1, subtest 77 |
| 바꾼 Python 15개 `ruff check`·`ruff format --check` | 통과 |
| `mypy` | 147개 파일 통과 |
| import-linter | 7 kept, 0 broken |

되돌림 검사는 `test_a218_guard_reconnect.py`만 돌렸고, 6건 모두 잡혔다.

| 되돌림 | 결과 |
| --- | --- |
| 전달기가 교체 중 `GUARD_CLOSED`도 넘김 | 29 실패 |
| `release_held_halt`가 아무것도 안 함 | 21 실패 |
| 생성 쪽 세대 검사 없음 | 2 실패 |
| 교체 뒤 bridge 알림 없음 | 3 실패 |
| 이전 조립을 닫지 않음 | 24 실패 |
| 새 조립 인수 때 세대를 올리지 않음 | 2 실패 |

- 건너뛴 21건은 통과로 세지 않았다. 경고는 `test_access_rule_counts.py` http 경우의 Pydantic `IncompleteFieldDefinitionWarning`으로, 이번 변경과 무관하다.
- 전체 시험은 돌리지 않았다.

## 남은 것

- 실제 앱에서 활성화 → 다시 연결 → 새 대화로 도구 호출까지는 확인하지 않았다.
  - 시험은 실제 SQLite·Registry·Guard와 메모리 byte peer를 쓴다.
  - Codex 실행은 대역이다.
- 교체 중 dispatch 0과 옛 승인 미실행은 A 단위 시험에서 확인했다. 독립 검증(T10·T29)은 C 범위다.
- 정지 보류의 B 동의는 사용자 전달이다. PR에서 B 확인을 받는다.
