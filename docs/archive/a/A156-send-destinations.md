# A156 · SEND 목적지 경계 등록·Profile 선택

2026-10-29. 기준 `wip/a156`의 `97382ce`(A155 후속). 지정 worktree `wt-A156`에서만 문서·제품·시험을 읽고 고쳤다. 원래 checkout에는 `.cache/a-work/A156-report.md`만 썼다.

## 한 일과 설계 판단

- `TaskPolicy.destinations`로 목록 조회와 추가·변경·삭제·Profile 사용 선택을 연결했다. 수정은 정책 공유 잠금 안에서 최신 문서를 읽고, 재시작 latch 확인 → 입력 검사 → B `prepare_policy_change` → saver → `_apply` → 멈춘 세션 종료 순서다. 접근 규칙 변경과 `_save_change`를 공유한다. 내용 해시가 같으면 저장·적용·세션 종료를 하지 않는다.
- B `_Configuration`은 `profile` 하나와 최상위 `destinations`를 가진다. `load_profile_settings`는 선택된 ID의 경계만 새 Catalog에 넣고, `Guard.apply_policy_change`가 그 Catalog를 교체한다. 따라서 목적지 변경은 transport·도구 정의·Registry 변경 없이 즉시 적용된다. 실제 Guard 판정과 새 세션 생성으로 재시작 불필요를 확인했다.
- **삭제**는 최상위 경계와 Profile 참조를 모두 지운다. **선택 해제**는 최상위 등록을 보존하고 Profile 참조만 지운다. 사용 안 함 행과 재선택을 지원하기 위한 구분이다. 지시문의 ‘함께 정리’는 삭제에는 양쪽 제거, 선택 해제에는 참조의 일관성 유지로 해석했다. 리뷰가 지시문과 다르다고 지적했고, Opus가 이 구분을 유지하기로 했다. Catalog는 Profile이 선택한 목적지만 받으므로 선택 해제된 등록으로는 보낼 수 없다(`UNKNOWN_DESTINATION`). 사용자 결정은 아니다.
- `session_destinations.destination_document`는 입력을 정규화하지 않는다. 빈 ID, 앞뒤 공백, Unicode Cc/Cf/Cs 및 줄·문단 구분자, 256자 초과, 중복 추가, 없는 대상 변경·삭제, 잘못된 종류·등급·사용 여부를 거부한다. 256자는 기존 `bridge_ingress.bridge_id` 식별자 상한을 재사용했다. 기존 B 목적지 ID에는 별도 길이 상한이 없다. 256자 한국어 ID의 저장·정확 일치 호출도 확인했다.
- 외부 경계에는 `CONFIDENTIAL`을 허용하지 않는다. A 입력 검사 뒤 B가 완성 문서의 경계·중복·Profile 참조를 다시 검증한다. A 문서 변환을 대역으로 우회한 시험에서도 B가 잘못된 외부 경계를 거부했다. 등급·경계는 오직 사용자 설정에서 온다.
- `DestinationEdit`, `request_policy_destinations(edit=None)`를 기존 control 명령 경로에 추가했다. `None`은 조회이고 변경 성공은 `policy_changed(PolicyView)`로 새 목록을 보낸다. 새 이벤트 종류를 만들지 않았다. `PolicyView.destinations`는 등록된 전체 목록과 선택 여부를 담는다. 저장 후 적용 실패에서는 마지막 적용 문서의 목록을 보여 주므로 새 파일과 옛 실행 정책을 섞지 않는다.
- 정책 화면에 ID·경계 종류·최고 수신 등급·사용 여부 목록, 추가·변경·삭제·새로 고침을 넣었다. 기존 `TIER_NAMES`와 Task 정지 확인 문구를 재사용한다. 목적지 ID 표시는 `plain_label`이고, 외부 경계 선택지는 `PUBLIC`·`INTERNAL`만 제공한다. ID 정확 일치·상한·모델에게 ID를 대화로 알려 주는 방법도 안내한다. UI는 bridge를 통해서만 명령을 보낸다.
- A155 도구 설정 창에 ‘목적지는 정책 화면에서 등록하세요’를 추가하고 해당 시험을 보완했다. 신규 서비스 모듈을 A 아키텍처 검사 대상에 추가했다.
- A155 보완(`97382ce`)이 서버 경계가 등록과 다른 경우까지 서버별 차단으로 바꿔 `test_product_settings`의 "등록과 다른 설정은 조립을 멈춤"(A129) 시험이 실패했다. 작업자는 시험 기대값을 바꿨으나, Opus가 시험을 되돌리고 `product_assembly.py`를 좁혔다. 막힌 서버 중 경계(tier·최고 등급, B `_ProfileRegistry`가 도구보다 먼저 비교하는 값)가 등록과 같은 서버만 도구 목록 계산에서 빼고(A155 종류 편집이 아직 대조되지 않은 경우), 경계가 다르면 예전처럼 `PROFILE_TOOL_INVALID`로 조립을 멈춘다. 경계 불일치는 설정 파일이 앱 밖에서 바뀐 신호일 수 있어 더 보수적인 쪽을 유지했다.

## 저장 실패와 중단

| 상황 | 실행 정책과 후속 동작 |
| --- | --- |
| 입력·B 준비 거부 | `POLICY_DESTINATION_INVALID`, 파일·정책·Task 불변 |
| saver False | `POLICY_SAVE_FAILED`, 적용 없음, 기존 실행 유지 |
| 외부 파일 변경 | `POLICY_SAVE_FILE_CHANGED`, 외부 파일 보존, 기존 실행 정책 유지 |
| 저장 ACK 불명 | `POLICY_SAVE_UNKNOWN`, Guard 종료·재시작 latch, 추가 저장 거부 |
| 저장 중 취소 | 같은 불명 처리 후 `CancelledError` 전파 |
| 저장 성공 후 적용 거부·실패 | `POLICY_APPLY_FAILED`, Guard 종료·재시작 latch, 적용 전 목록 유지 |
| 이미 재시작 필요 | 해당 고정 코드로 저장 전 거부 |
| 같은 내용 해시 | 쓰기·정지 없음 |

기존 접근 규칙의 취소 처리 코드를 공통 함수로 옮겼으며 새 넓은 예외 경계는 만들지 않았다. 실행 중 Task 상태 변경은 B `_apply`에 맡기고 A는 반환된 Task의 모델 세션을 종료한다.

## 검증

처음 `PYTHONPATH=src` import가 `wt-A156/src/guard_app/__init__.py`임을 확인했다. 모든 검사 cwd는 worktree다. `PY`는 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`. Git Bash에서 `PYTHONPATH=src`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`, TMPDIR/TEMP/TMP는 worktree의 `.cache/a-work/A156-tmp-1`로 설정했다.

```bash
$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A156-tmp-3 tests/unit/test_a156_destinations.py tests/unit/test_ui_policy.py tests/unit/test_bridge_policy_access.py tests/unit/test_b28_integration.py tests/unit/test_a155_tool_settings.py tests/unit/test_a105_activation.py tests/unit/test_session_runtime.py tests/unit/test_product_assembly.py tests/unit/test_ui_*.py tests/unit/test_a_architecture.py tests/unit/test_bridge.py tests/unit/test_bridge_registration.py tests/unit/test_session_collaborators.py tests/unit/test_product_settings.py --tb=short
```

최종 **372 passed, 66 subtests passed, 27.14초**. 전체 단위 시험은 돌리지 않았다. 새 A156은 매개변수 확장 38개다.

- 실제 B SQLite·Guard·설정 파일 저장, 합성 MCP 바이트 peer를 사용했다. Registry는 기존 A 조립 시험 대역이다. 별도 제품 세션 시험은 기존 `policy_product`와 대본형 Codex app-server를 재사용했다.
- 추가·변경한 목적지로 PUBLIC 외부 ALLOW, INTERNAL 외부 ASK, 외부 최고 등급 초과 DENY, 내부 CONFIDENTIAL ALLOW를 확인했다. 허용은 transport `call` 진입·peer `tools/call` 수신 각 1회, 거절·승인 전은 각 0회, 승인 후 각 1회다. 삭제·선택 해제·대소문자 불일치는 `UNKNOWN_DESTINATION`이며 추가 dispatch가 없다.
- 잘못된 입력 19가지, 중복·없는 대상, 변경 없음, 저장 실패·결과 불명·취소·외부 파일 변경·적용 실패, 최신 문서 잠금 직렬화·재시작 거부를 확인했다. 실패 뒤 목록이 저장 문서를 잘못 보여 주지 않는 것도 확인했다.
- 정책 변경 뒤 기존 Task HALTED·세션 `POLICY_CHANGED`·모델 사용 불가, 같은 실행에서 새 세션 생성 가능을 확인했다. 뒤이은 접근 규칙 변경도 목적지 목록을 보존한다.
- Qt offscreen에서 목록·PlainText·경계별 등급 선택지·추가/변경/삭제 요청·고정 결과 문구를 확인했다. 실제 bridge 조회·변경·거부도 확인했다.
- 변경 Python 16개 Ruff check·format check 통과. 전체 mypy **135 source files** 통과. import-linter **7 kept, 0 broken**. `git diff --check` 통과. 한국어 파일 인코딩도 확인했다.

### 회귀 대조

변형을 하나씩 넣고 지정 시험을 실행한 뒤 `finally`로 원래 바이트를 복원하고 SHA-256 일치를 확인했다. 구문·수집 오류는 검출로 세지 않았다. 스크립트와 출력은 worktree `.cache/a-work/A156-tmp-1/`에 있다. 잠금 시험은 실패 시에도 대기 작업을 수거하도록 보완했다.

| 제품 변형 | 잡는 시험 | 검출 결과 |
| --- | --- | --- |
| 준비에 변경 문서 대신 현재 문서 전달 | b_preparation_revalidates_document | 1 실패 |
| A 백엔드 입력 검사 생략 | invalid_edit_never_writes_or_applies | 10 실패·9 통과(B 검증이 일부 차단) |
| 외부 CONFIDENTIAL 입력 검사 생략 | document_builder_rejects_external_confidential | 2 실패 |
| 정책 공유 잠금 대신 독립 잠금 | lock_reads_latest_document_and_restart_refusal | 1 실패 |
| 삭제 시 최상위 경계 유지 | add_update_selection_delete_and_noop | 1 실패 |
| 삭제·해제 시 Profile 참조 유지 | 같은 시험 | 1 실패 |
| 세션 정지 콜백 생략 | 같은 시험 | 1 실패 |
| Guard 적용 생략 | live_catalog_matrix_and_dispatch_counts | 5 실패 |
| 변경 없는 문서도 저장 | add_update_selection_delete_and_noop | 1 실패 |
| 실패 후 Guard close 생략 | failure_semantics_and_applied_view | 3 실패·2 통과 |
| 적용 실패 화면에 저장 문서 표시 | 같은 시험 | 1 실패·4 통과 |
| 재시작 latch 검사 생략 | lock_reads_latest_document_and_restart_refusal | 1 실패 |
| 사용 여부를 무시하고 선택 | exact_id_limit_unselected_add_and_unavailable | 1 실패 |
| 외부 선택지에 CONFIDENTIAL 허용 | ui_plain_id_choices_commands_and_fixed_results | 1 실패 |
| ID를 AutoText QLabel로 표시 | 같은 시험 | 1 실패 |
| bridge가 변경 대신 조회 전달 | bridge_destination_read_change_and_refusal | 1 실패 |
| runtime의 세션 종료 연결 생략 | runtime_binding_stops_session_and_allows_new_session | 1 실패 |
| A155 정책 화면 연결 문구 제거 | A155 화면 시험 | 1 실패·30 통과 |
| (Opus) 막힌 서버의 경계 비교 생략 | settings_that_differ_from_the_registration_stop_the_assembly | 1 실패 |
| (Opus) 경계가 같은 막힌 서버도 도구 목록에 포함 | a_blocked_server_whose_settings_are_ahead_of_its_registration_is_left_out | 1 실패 |

## 한계와 인계

- URL 목적지 해석·URL-01 정규화, 여러 Profile은 구현하지 않았다. 목적지는 오직 불투명 ID의 정확 일치다. URL처럼 보이는 문자열도 URL 의미로 해석하거나 신뢰하지 않는다.
- 모델 도구 설명은 DESC-01 전이라 비어 있다. 등록 ID를 모델에게 자동으로 알려 주지 않으므로 사용자가 대화에서 ID를 알려 줘야 한다.
- 선택 해제는 등록을 보존하고 삭제는 양쪽을 정리한다. 해제 시 등록도 지워야 한다면 사용자·B 리뷰에서 정한다.
- 실제 외부 MCP 프로세스·네트워크 wire·OS 부작용·실제 모델·사람이 조작하는 화면의 종단 인수는 미실행이다. peer 수신을 외부 부작용 계수로 일반화하지 않는다.
- B·C 소유 파일과 domain/ports 계약은 바꾸지 않았다. B에는 정책 변경·실패 경로 리뷰, C에는 경계 판정·차단 계수·화면 독립 인수를 요청할 수 있다. 이번 A 시험은 C 독립 검증을 대체하지 않는다.
- `docs/a-status.md`와 공통 관리 문서는 고치지 않았다. 반영 문안은 별도 보고에 적었다. Git add·commit·push는 하지 않았다.

관련: I1·I2·I6·I7(추가 I3·I8·I9), T01–T06·T18·T29의 A 연결 범위, 개발문서 0.4·0.5·2·4.3·5.1·10.3절, 2026-10-18 접근 규칙 결정·ACCESS-01·CFG-01, A81·A91·A104·A117·A155.

PR: 미정.
