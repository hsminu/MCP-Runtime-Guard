# A105 · 서버 활성화와 정책 저장 직렬화

2026-10-29. `wip/a105`, `.cache/a-work/wt-A105`에서 작업했다. 원래 checkout의 src·tests·docs는 읽거나 고치지 않았다. A 소유 코드·단위 시험과 이 기록만 변경했다. 공통 문서·a-status·B/C 구현은 변경하지 않았다.

## 구현과 판단

- 서버 목록의 PENDING 행 → 활성화 확인 → bridge `RegistrationRequest` → `RegistrationCommands` → `TaskPolicy.set_server_active` → B 정책 준비·C 설정 저장·B 적용·C Registry 활성화를 연결했다. ACTIVE/PENDING 행의 차단도 같은 경로로 연결했다. UI는 `services.bridge`의 명령·이벤트만 사용한다.
- `TaskPolicy.lock`을 얻은 뒤 최신 문서를 읽는다(A117). 같은 잠금이 설정 저장, 정책 적용, 기존 세션 정리, Registry 쓰기, 재시작 요구까지 유지된다. 정책 변경·Task 생성·활성화가 앞 변경을 덮어쓰지 않는다. 성공 뒤 재시작이 필요하므로 기다리던 두 번째 활성화/정책 변경은 저장 없이 거부한다.
- 활성화는 Profile 선택 서버에 ID를 추가하고, B `prepare_server_access_change`로 전체 READ/SEND 체크 목록을 검증한다. 체크 해제는 명시 거부에 들어간다. 겹친 allow/deny, OTHER·알 수 없는 이름·빠진 지원 도구는 거부한다. `prepare_policy_change`에는 조립된 `LiveCheckedRegistry`를 넘겨 실행 중 지문 검사를 유지한다.
- `expected_row_revision`을 화면 확인 사본과 결속하고 잠금 안에서 검사한다. 실제 저장의 최종 revision 검사는 C/B API에 맡긴다. `reviewed_by`는 `RegistrationCommands` 생성 시 백엔드가 정한 운영자 ID다(제품 기본 `local-user`). 요청 DTO에 확인자 입력은 없다. `reviewed_scan_id=None`을 명시한다. Scanner는 호출하지 않았다.
- 정책 내용이 바뀌면 B 적용으로 policy_version이 바뀌고 기존 Task가 HALTED가 된다. 내용이 같은 재활성화는 B의 내용 해시를 그대로 유지하며, 기존 `HumanControl.emergency_halt`로 Task를 정지하고 세션을 닫는다. 같은 내용인데 새 버전을 만들어 내지 않는다.
- `McpTransport`는 생성 시 `_workers`를 구성하고 동적 추가 API가 없다. 성공 뒤 admission에 재시작 요구를 기록하고 Guard를 닫는다. 따라서 새 세션에 연결되지 않은 서버 도구가 제공되지 않는다. 성공 응답의 최신 정책 보기·등록 목록을 화면에 다시 발행한다.
- 차단은 C `block`을 먼저 수행하고 Profile 선택·해당 서버 접근 규칙·그 선택에 종속된 출처 규칙을 제거해 저장·적용한다. 서버 설정과 등록 도구 자체는 남는다. BLOCKED를 다시 PENDING으로 돌리는 별도 UI는 이번에 만들지 않았다.

### 도구 종류와 첫 파일

종류는 B `ServerSettings.tools`의 `ToolSettings.kind`에서 정한다. C Registry `_tool_specs`는 이 값을 복사하며 목록에만 있는 도구를 OTHER로 기록한다. B `_ProfileRegistry`는 Registry와 설정의 종류·인자를 대조한다. 모델/서버 annotations에서 종류를 추정하지 않는다.

이번 화면에는 종류 편집을 넣지 않았다. SEND에는 `destination_arg`가 필요하고 READ는 출처·등급 규칙까지 필요하다. 종류 선택만 추가하면 등록 사본과 신뢰 설정이 어긋나므로, 이들은 설정·Registry 대조를 함께 다룰 후속 편집 흐름이 필요하다. 이미 종류가 일치하는 READ/SEND는 기본 체크, 미정 도구는 체크 해제·비활성으로 표시한다. A128 신규 등록의 OTHER 도구는 활성화해도 호출 권한이 생기지 않는다.

A135의 현황 줄 및 A08·A130·A135 기록에 있는 #52 C 리뷰 요약은 첫 파일을 **등록 흐름**에서 만들도록 안내한다. A128의 첫 등록 파일 생성 후 Guard 없이도 활성화를 저장하는 시험을 추가했다. 설정 화면에 중복 생성 버튼은 필요하지 않다. C `SettingsFileSaver`의 generation 대조를 유지하며 외부 변경 파일을 덮어쓰지 않는다. #52 원문을 새로 조회한 것은 아니며 worktree 기록의 요약을 근거로 판단했다.

### 대조 미기록

C 코드에서 `REG_SERVER_UNRECORDED`는 `observe` 저장 실패가 남긴 `_unrecorded` 표시다. 이전 실행에 등록한 모든 PENDING 서버를 뜻하지 않는다. `load`는 이 표시를 비우고, `refresh`/같은 실행의 재대조는 풀지 않는다. 따라서 이 오류를 고정 문구로 거부하고 재시작을 안내한다. 자동 대조 재시도나 표시 해제 우회는 없다. 설정을 먼저 저장한 뒤 해당 오류가 나면 아래 부분 실패 규칙으로 Guard를 닫는다. 다음 시작의 실제 연결·목록 대조는 기존 제품 조립이 담당한다.

## 저장 순서와 부분 실패

두 저장은 원자적 트랜잭션이 아니다. 시작 상태는 활성화=PENDING, 차단=PENDING/ACTIVE다. 알려진 저장 실패도 보수적으로 재시작을 요구한다. 실패·취소·결과 불명 쓰기는 이 실행에서 재시도하지 않는다.

| 경로/중단 지점 | Registry | 설정 파일/실행 정책 | Guard 및 재시작 후 판정 |
| --- | --- | --- | --- |
| 활성화: revision·선택 검증 거부 | PENDING 유지 | 쓰지 않음 | `REG_NOT_ACTIVE` 또는 Profile 범위 거부. 재확인 가능 |
| 활성화: 설정 저장 실패/외부 변경 | PENDING | 기존/외부 파일 유지 | Guard 닫음·재시작 요구. 이후에도 Registry가 실행을 막음 |
| 활성화: 설정 저장 결과 불명/취소 | PENDING | 이전/새 문서 중 미확정 | Guard 닫음. 새 허용 규칙만 남아도 `REG_NOT_ACTIVE` |
| 활성화: 설정 저장 성공, B 적용 실패 | PENDING | 새 파일, 실행 정책 적용 미확정 | Guard 닫음. 재시작해도 Registry 관문 유지 |
| 활성화: 저장·적용 성공, Registry 거부/미기록 | PENDING | 새 선택·규칙 | Guard 닫음. `REG_NOT_ACTIVE`, 자동 재시도 없음 |
| 활성화: Registry 쓰기 결과 불명 | DB는 PENDING/ACTIVE 미확정, C 캐시는 보수적 PENDING | 새 파일·새 정책 적용 완료 | 이번 실행 Guard 닫음. 재시작 후 DB ACTIVE이고 실제 지문 대조가 맞아도 체크 해제 도구는 접근 거부 |
| 활성화: 모두 성공 | ACTIVE | 새 선택·체크 규칙 | 기존 Task 정지·새 세션 거부. 재시작 후 연결·지문 대조·접근 규칙 적용 |
| 차단: Registry 쓰기 실패/결과 불명 | DB는 이전/BLOCKED 미확정, ACTIVE 캐시는 PENDING 취급 | 아직 설정을 쓰지 않음 | Guard 닫음. 재시작으로 결과 확인 필요; 차단 성공이라고 보고하지 않음 |
| 차단: Registry 성공, 설정 실패/불명 | BLOCKED | 이전/새 문서 미확정 | Guard 닫음. 재시작 후에도 `REG_NOT_ACTIVE`; 이전 allow만으로 실행 불가 |
| 차단: 모두 성공 | BLOCKED | Profile 선택·서버 접근/출처 규칙 제거 | Task 정지·재시작 요구, Registry와 Profile 모두 실행 거부 |

Registry부터 활성화하면 기존 Profile/allow가 있던 PENDING 서버가 설정 저장 실패 시 실행 가능해지는 틈이 생긴다. 이 순서는 채택하지 않았다. 반대로 차단은 권한을 먼저 내리는 Registry 우선 순서다. 결과 불명의 Registry 쓰기가 실제로 커밋되었다면, 활성화 쪽은 설정도 이미 저장된 것이므로 ‘DB만 성공’ 상태가 아니다. 커밋 전후 오류를 각각 시험했다.

## 화면

A91 `ToolAllowDialog`의 체크박스를 재사용한다. 확인 화면에 다음을 고정 표시한다: 검사 결과 미연결·검사 없이 확인하는 단계, 미정 종류 선택 불가, 저장하면 실행 중 Task 정지, 저장 후 재시작. 같은 설정의 재활성화는 내용 해시 정책 버전이 그대로일 수 있으므로 버전 변경을 단정하지 않는다. 활성화 결과의 `POLICY_SAVE_FAILED`는 정책 화면 문구 대신 Guard 정지·재시작 문구로 바꿔 보인다(활성화 쓰기 실패는 재시작 latch를 세운다). 이 두 문구는 리뷰 지적으로 고쳤고 `test_a105_activation.py`에 단언과 문구 회귀 1건(검출)을 더했다. 검토된 설명 입력은 없다. 응답 command_id가 다른 사본은 무시한다. 440×400 offscreen 캡처에서 3개 도구·안내·확인/닫기 버튼이 잘리지 않음을 확인했다. 캡처 프로세스에만 Windows 맑은 고딕을 로드했고 제품 폰트는 변경하지 않았다. 캡처는 worktree `.cache/a-work/A105-tmp-1/activation.png`에 있다.

## 검증

공통 환경: 지정 worktree, Git Bash, `PYTHONPATH=src`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`. `PY`는 원래 checkout의 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`다. 최초 import 경로가 worktree src임을 확인했다. TMPDIR/TEMP/TMP는 worktree `.cache/a-work/A105-tmp-1`이다.

```bash
PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A105-tmp-40 tests/unit/test_a105_activation.py tests/unit/test_bridge_registration.py tests/unit/test_server_add.py tests/unit/test_session_runtime.py tests/unit/test_product_assembly.py tests/unit/test_a138_product_startup.py tests/unit/test_bridge.py tests/unit/test_a_architecture.py tests/unit/test_ui_policy.py tests/unit/test_bridge_policy_access.py tests/unit/test_session_collaborators.py tests/unit/test_product_settings.py tests/unit/test_b28_integration.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_responsive_detail.py tests/unit/test_ui_display_escaping.py tests/unit/test_ui_sessions.py tests/unit/test_ui_overview.py
```

결과: **310 passed, 172 subtests passed, 23.00초**. 전체 단위 시험은 실행하지 않았다. 새 A 단위 시험은 11개 함수·매개변수 확장 21개다. 실제 B SQLite·C Registry·C 파일 저장, Task HALTED, DB 재열기 후 체크 해제 도구의 Guard 거부, 합성 MCP peer의 tools/call 수신 0을 확인했다. 외부 파일 변경·커밋 뒤 ACK 소실·취소·양쪽 저장 순서·동시 활성화·정책 변경을 포함한다. 재시작은 새 SQLiteStore와 새 transport를 만들어 확인했다.

- 변경 Python 9개에 `PY -m ruff check <files>` / `PY -m ruff format --check <files>` 통과(9 files already formatted).
- `PYTHONPATH=src PY -m mypy`: 118 source files 통과.
- `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini`: 7 kept, 0 broken.
- `git diff --check` 통과. 이름 이동·제거는 없고 정책 변경·서버 화면 직접 의존 시험을 검색해 포함했다.

### 회귀 대조

제품 코드에 한 번에 하나씩 주입하고 지정 시험을 실행한 뒤 finally로 원래 바이트를 복구했다. SHA-256 일치도 확인했다. 수집 오류·구문 오류는 탐지 성공으로 세지 않았다. 최종 10개 변형 모두 실제 assertion 실패로 탐지했다. 새 11개 시험 함수 모두 아래 대조에 포함된다.

| 변형 | 시험 핵심 이름 (`test_a105_activation.py`) | 결과 |
| --- | --- | --- |
| 공유 정책 잠금을 새 개별 Lock으로 대체 | policy_edit_precedes_activation / overlapping_activations | 2 실패 |
| 체크 해제를 허용으로 바꿈 | activation_saves_applies_then_activates | 1 실패 |
| reviewed_by를 화면 값으로 대체 | activation_saves_applies_then_activates | 1 실패 |
| 오래된 revision 검사 제거 | stale_revision_and_unrecorded | 1 실패 |
| 부분 실패 재시작 latch 제거 | settings_failure_orders / registry_failure_orders | 12 실패 |
| Profile 서버 선택 추가 생략 | activation_selects_profile / first_registration_file | 2 실패 |
| C block 호출 생략 | block_revokes_registry | 1 실패 |
| UI가 확인한 revision 대신 0 전달 | activation_ui_uses_bridge | 1 실패 |
| 동일 정책의 Task 정지 생략 | identical_activation_stops_tasks | 1 실패 |
| Registry 활성화를 설정 저장 앞으로 이동 | activation_saves_applies_then_activates / settings_failure_orders | 5 실패, 4 통과 |

## 남은 의존성과 인계

- A47·SCAN-01·DESC-01은 계속 미완료다. 이번 사용자 지시에 따라 Scanner·검토된 설명은 연결하지 않았다.
- B의 현재 worktree는 접근 거부를 여전히 기존 `AGT_DENY` 값으로 반환한다. contracts의 2026-10-28 ACCESS 코드 결정과 구현 시차가 있다(B2.10 후속). 새 AGT 코드는 추가하지 않았고 B 파일도 변경하지 않았다. 위 표의 ‘접근 거부’는 실제 거부·수신 0을 뜻하며 `ACCESS_DENY` 문자열 이행 완료를 주장하지 않는다.
- B: 두 저장의 순서·내용 동일 시 사람 정지·정책 적용 리뷰. C: Registry 미기록/결과 불명·차단 및 활성화 UI 리뷰와 독립 T/S 인수. 공통 domain/ports 변경 요청은 없다.
- 신규 OTHER 도구의 종류·출처·목적지를 정하는 편집 UI와 BLOCKED 재검토 UI는 후속이다. 많은 도구가 있는 확인 창의 스크롤 개선은 기존 ToolAllowDialog의 한계로 남는다.
- 실제 외부 서버·Codex·네트워크 수신·OS 부작용·Scanner·C 공격/통합 시험은 미실행이다. 합성 peer 0회를 외부 부작용 전체 0으로 일반화하지 않는다.

관련: I1·I2·I6·I7·I8·I9·I10·I11, T10·T18·T29의 A 연결 단위 범위, 설계 0.4·0.5·2·6.6·10.1·10.3·12, B3.10·B3.12, CFG-01·ACCESS-01·AGT-04·EXE-01, A04·A81·A91·A105·A117·A128·A130·A135·A138.

PR: 미정.
