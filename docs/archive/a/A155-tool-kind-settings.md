# A155 · 등록 도구 종류·READ 등급·SEND 목적지 인자 설정

2026-10-29. `wip/a155`, `.cache/a-work/wt-A155`에서 작업했다. 원래 checkout의 src·tests·docs는 읽거나 고치지 않았다. B·C 소유 파일, 공통 문서와 a-status는 변경하지 않았다. 보고만 원래 checkout의 `.cache/a-work/A155-report.md`에 남겼다.

## 구현과 판단

- bridge의 `tool_settings_preview`·`tool_settings_save` 명령을 추가했다. 불변 DTO는 A bridge_types에 두며 UI는 bridge 재노출만 사용한다. 원격 이름·인자를 담는 DTO는 repr을 숨긴다. 새 `session_tool_settings`는 등록 사본과 B 설정의 조회·검증·문서 변환만 수행한다.
- 미리보기는 서버 ID, row revision, Registry 종류, 설정 종류·목적지 인자·빈 prefix 등급, 최상위 `properties`의 `type == "string"` 인자 이름, 읽기 전용 여부를 반환한다. 배열형 type·중첩 속성·숫자 속성은 후보가 아니다. 빈 prefix 규칙이 여러 개면 B의 최고 등급 판정에 맞춰 최댓값을 표시한다.
- 저장 시 PENDING/ACTIVE, 정수 revision의 정확한 일치, 등록된 도구 이름, 중복·요청 형태, 종류, READ 등급, SEND 문자열 인자를 백엔드에서 검사한다. `resource_arg` 설정 도구는 읽기 전용이며 요청에 포함되면 거부한다. OTHER는 B `ToolSettings.check_arguments`가 허용하는 명시적 `kind: other` 항목으로 저장한다. deny의 도구 참조를 보존하기 위해 항목을 삭제하지 않는다.
- READ는 자원 인자가 없는 도구 단위형만 만든다. 편집한 도구의 `prefix: []`만 교체하고 다른 서버·다른 도구·비어 있지 않은 prefix는 보존한다. 종류가 달라진 도구의 allow만 제거하고 deny는 보존한다. B가 최종 문서 전체를 다시 검증한다. 기존 비어 있지 않은 출처 규칙이 남은 도구를 SEND/OTHER로 바꾸려는 경우 B가 출처 참조 오류로 거부하며, 규칙을 임의로 삭제하지 않는다.
- 서버를 `profile.servers`에 추가한다. 일반 등록은 출처 규칙 없이 선택 목록에서도 빠지므로 이 단계가 없으면 다음 시작의 `_record_listings`가 해당 서버에 `observe`하지 않는다. A49처럼 PENDING 서버를 선택해도 Guard와 Catalog의 ACTIVE 검사는 유지된다. 선택 추가가 실행 권한을 부여하지 않는다.
- `TaskPolicy.lock`을 얻은 뒤 최신 문서를 읽는다(A117). 검증·B `prepare_policy_change` → ACTIVE의 C `request_review` → saver → `_apply`·세션 종료 → 재시작 latch·Guard 종료 순서다. 준비에는 조립된 live Registry와 기존 resource resolver를 전달한다. 실행 중 정의·transport를 교체하지 않는다. 내용 해시가 같은 저장도 기존 사람 제어 API로 Task를 정지한다.
- ACTIVE를 먼저 PENDING으로 내려 종류 변경 도구가 재검토 없이 기존 권한을 쓰지 못하게 한다. ACTIVE 여부는 `registry.status()`가 아니라 저장 사본(`entry`)으로 본다. 이번 실행에서 대조를 기록하지 못한 서버는 `status()`가 PENDING이지만 Store에는 ACTIVE로 남기 때문이다(Opus 확인 뒤 수정). 이 쓰기에서 ACK를 확인하지 못하면 A128처럼 등록 쓰기 `_unknown` latch도 세운다. 취소는 finally에서 재시작 요구·Guard 종료를 수행한 뒤 그대로 전파한다. 넓은 예외 처리는 추가하지 않았다. 결과 불명 쓰기는 재시도하지 않는다.
- 서버 목록의 PENDING/ACTIVE 행에 도구 설정 입구를 추가했다. 종류별 등급·목적지 인자 선택을 켜고 끄며, 문자열 인자가 없으면 SEND 항목을 비활성화한다. 변경한 도구만 요청에 담고 읽기 전용 도구는 보내지 않는다. 등급·목적지의 임의 기본값을 선택하지 않는다.
- 도구 이름은 A47 `untrusted_display_text`와 PlainText QLabel로, 인자 이름은 같은 변환과 일반 QComboBox 항목으로 표시한다. 원래 이름은 선택 데이터로 보존한다. 저장 전 Task 정지·재시작·재활성화·목적지 미등록 시 전송 거부를 알린다. 결과와 새 세션 차단 안내에도 고정 재시작 문구를 연결했다.
- A105 활성화 미리보기는 변경하지 않았다. 그 응답의 `kind_fixed=False`만으로는 미설정 OTHER와 저장 후 미재시작을 구별할 수 없으므로, 활성화 창에 미재시작이라고 단정하는 문구는 추가하지 않았다. 저장 결과에는 재시작 뒤 활성화하라고 명확히 안내한다.

## 순서와 부분 실패

Registry와 설정 파일은 단일 트랜잭션이 아니다. 검증 거부 외에 쓰기 절차가 시작된 뒤 발생한 실패는 모두 재시작을 요구한다.

| 중단/완료 지점 | Registry | 설정/실행 정책 | Guard 판정과 안내 |
| --- | --- | --- | --- |
| 이름·종류·등급·인자·resource_arg·상태·revision 거부 | 불변 | 파일·정책 불변 | `REG_TOOLS_INVALID`/`REG_TOOLS_STALE`. 쓰지 않음 |
| B 문서 준비 거부 | 불변 | 파일·정책 불변 | `REG_TOOLS_INVALID`. 쓰지 않음 |
| ACTIVE `request_review` 실패/결과 불명/취소 | 이전 ACTIVE 또는 PENDING 미확정 | 아직 파일을 쓰지 않음 | Guard 종료, `REG_TOOLS_UNKNOWN`, 등록 쓰기 불명 latch. 성공으로 표시하지 않음 |
| PENDING 확인 또는 review 성공 후 saver False | PENDING | 기존 파일 | `POLICY_SAVE_FAILED`, Guard 종료·재시작 요구 |
| saver 외부 파일 변경 감지 | PENDING | 외부 파일 보존 | `POLICY_SAVE_FILE_CHANGED`, Guard 종료·재시작 요구 |
| saver ACK 유실/취소 | PENDING | 이전/새 파일 미확정 | `POLICY_SAVE_UNKNOWN` 또는 취소 전파. Guard 종료·재시작 요구, 재저장 금지 |
| 저장 성공 뒤 적용 실패 | PENDING | 새 파일, 실행 정책 적용 미확정 | `POLICY_APPLY_FAILED`, Guard 종료·재시작 요구 |
| 모두 성공 | PENDING, 도구 정의는 기존 사본 | 새 파일·정책, 기존 Task 정지 | `REG_TOOLS_RESTART_REQUIRED`, Guard 종료·새 세션 거부 |
| 재시작 후 같은 listing을 observe | 새 설정의 도구 정의, PENDING | 새 설정으로 시작 | 종류 대조가 맞아 활성화 허용 선택 가능. 사람 활성화 전 실행 불가 |

실패 후 추가 명령은 admission의 `REG_TOOLS_UNKNOWN`으로 먼저 막힌다. Registry 불명 latch도 유지된다. 저장 성공 후 `_rows`는 조회만 하며 추가 쓰기를 하지 않는다. 실제 외부 wire·부작용의 경쟁 인수는 C 범위로 남긴다.

## 검증

최초 `PYTHONPATH=src` import가 지정 worktree의 `src/guard_app/__init__.py`임을 확인했다. 첫 Git Bash `python -c`는 인용 전달 오류로 실패했고 PowerShell에서 같은 환경으로 확인했다. 실행 시험·정적 검사는 Git Bash로 했다. `PY`는 원래 checkout의 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`다. `PYTHONPATH=src`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`, TMPDIR/TEMP/TMP는 worktree `.cache/a-work/A155-tmp-1`로 설정했다.

```bash
PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A155-tmp-7 tests/unit/test_a155_tool_settings.py tests/unit/test_a105_activation.py tests/unit/test_bridge_registration.py tests/unit/test_server_add.py tests/unit/test_a49_registration.py tests/unit/test_session_runtime.py tests/unit/test_product_assembly.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_policy.py tests/unit/test_ui_display_escaping.py tests/unit/test_ui_responsive_detail.py tests/unit/test_ui_read_only_pages.py tests/unit/test_ui_sessions.py tests/unit/test_ui_icons_style.py tests/unit/test_account_runtime.py tests/unit/test_a_architecture.py tests/unit/test_bridge.py tests/unit/test_session_collaborators.py tests/unit/test_bridge_policy_access.py --tb=short
```

최종 **304 passed, 140 subtests passed, 22.04초**. 전체 단위 시험은 실행하지 않았다. 새 A155 시험은 7개 함수·매개변수 확장 30개이며 A105 harness를 재사용한다. 실제 B SQLite·C Registry·C 설정 파일 저장을 사용하고, 목록과 MCP peer는 합성이다.

- 부적합 요청 16가지에서 설정 파일·영속 등록 사본 불변, 재시작 latch 없음 확인.
- READ·SEND·OTHER 문서 저장, 출처 보존·등급 교체, 선택 서버 추가, allow 제거·deny 보존, 동일 정책 버전에서도 Task HALTED 확인.
- ACTIVE review 선행, 파일 실패·ACK 유실·취소·외부 변경·review 커밋 전후 실패·적용 거부에서 고정 코드, 재시작 요구, Guard close 호출, 추가 저장 없음 확인.
- 새 Registry load 후 같은 listing observe를 거쳐 OTHER→READ·PENDING과 활성화 선택 가능 확인. observe 전 선택 불가도 확인.
- 합성 peer의 도구 호출 수신은 위 경로에서 0. 외부 wire·OS 부작용 전체의 계수로 일반화하지 않는다.
- Qt offscreen에서 일반 텍스트·bidi 가시화, 원래 이름 보존, 종류별 활성 상태, 읽기 전용·SEND 비활성, 실제 bridge action 접수, revision·payload·결과 문구를 확인했다.

변경 Python 10개에 `PY -m ruff check <변경 파일>` 통과, `PY -m ruff format --check <변경 파일>` 10 files already formatted. `PYTHONPATH=src PY -m mypy` 133 source files 통과. `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini` 7 kept, 0 broken. `git diff --check` 통과.

### 회귀 대조

아래 변형을 하나씩 제품 파일에 넣고 지정 A155 시험을 실행했다. 매번 finally에서 원래 바이트로 복구하고 SHA-256 일치를 확인했다. 구문·수집 오류는 검출로 세지 않았다. 임시 스크립트·출력은 worktree `.cache/a-work/A155-tmp-1/`에 있다.

| 변형 | 대상 시험 요약 | 최종 검출 |
| --- | --- | --- |
| 정책 공유 잠금 대신 새 독립 잠금 | waits_for_policy_lock | 1 실패 |
| SEND 스키마 후보 검증 생략 | rejects_without_any_write | 4 실패 |
| resource_arg 읽기 전용 검사 생략 | 같은 거부 시험 | 1 실패 |
| revision 검사 생략 | 같은 거부 시험 | 2 실패 |
| profile.servers 추가 생략 | document_and_restart | 1 실패 |
| ACTIVE request_review 생략 | active_review_precedes | 7 실패 |
| request_review를 저장 뒤로 이동 | 같은 순서·실패 시험 | 7 실패 |
| 다른 도구의 빈 prefix 규칙도 삭제 | preserves_unedited | 1 실패 |
| 종류 변경 allow 보존 | 같은 보존 시험 | 1 실패 |
| READ 등급을 PUBLIC으로 고정 | document_and_restart | 1 실패 |
| OTHER 저장을 READ로 바꿈 | other_and_identical | 1 실패 |
| 동일 정책 해시일 때 Task 정지 생략 | 같은 동일 정책 시험 | 1 실패 |
| 최종 Guard close 생략 | active_review_precedes | 7 실패 |
| Registry 쓰기 불명 latch 생략 | 같은 실패 시험 | 2 실패 |
| 원격 도구 이름 가시화 생략 | ui_plain_names | 1 실패 |
| UI가 revision 0 전송 | 같은 화면 시험 | 1 실패 |
| ACTIVE 판단을 `registry.status()`로 되돌림 | unrecorded_active_server_is_still_reviewed | 1 실패 |

마지막 행은 리뷰 뒤 `entry` 판단으로 고치면서 추가한 시험과 대조다. 수정 뒤 `test_a155_tool_settings.py`는 31 passed다.

처음 대조에서 동일 정책 시험은 도구 순서 차이로 내용 해시가 달랐고, Guard 종료 시험은 Registry 거부만 관찰해서 변형을 놓쳤다. 동일 해시 단언과 `aclose()` 직접 관찰로 보강한 뒤 재검출했다. 잠금 시험도 저장 호출 계수를 직접 확인하도록 보강했다. 표의 대조는 29개 사례 시점 결과이며, 이후 적용 거부 사례와 전역 재시작 안내 단언을 추가한 최종 정상 회귀는 위 304개 결과다.

## 한계·인계

- 목적지 경계 `destinations[]`·`profile.destinations` 편집은 A156이다. 이번에는 SEND 목적지 인자만 정하므로 목적지가 없는 Profile의 SEND는 `UNKNOWN_DESTINATION`으로 거부된다.
- 일반 서버 자원형 READ, 등록과 동시에 종류 정하기, Scanner·검토된 설명(A47·SCAN-01·DESC-01)은 범위 밖이다. 비어 있지 않은 출처 규칙과 SEND/OTHER가 충돌하면 설정 파일을 자동 정리하지 않고 거부한다.
- 실제 Codex·외부 MCP 프로세스·wire·OS 부작용·사람이 조작하는 화면의 제품 종단 인수는 미실행이다. UI 검증은 offscreen 단위 시험이다. 성공 뒤 재시작·목록 대조·사람 활성화가 필요하다.
- B·C API/계약 변경 요청은 없다. B에는 정책 준비·allow/deny 보존·부분 저장 처리 리뷰, C에는 Registry review/observe 순서·독립 T10/T29 및 wire 인수를 요청한다. 이번 A 시험이 C의 독립 검증을 대체하지 않는다.

관련: I1·I6·I7·I8·I9·I10, T10·T18·T29·T38의 A 연결 범위, 설계 0.4·0.5·2·4.2·4.3·10.1·10.3, 2026-10-18 접근 규칙 결정·ACCESS-01·CFG-01, A47·A49·A81·A91·A105·A117·A128·A130.

PR: 미정.
