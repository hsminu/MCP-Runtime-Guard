# A200 · 등록 뒤 Scanner 검사·기록·활성화의 검사 ID 연결

2026-10-30 · `claude/a-scanner-desc-546649`(`f0c2643` 기준). PR: 다음 A PR(아직 없음)
관련: A128·A47·A105, C4.13·C4.14·C4.17, B1.9(#106), 2026-10-13·2026-10-15 Scanner 결정. I7·I9·I10, T10·T17·T18·T29, 개발문서 10.4절.

## 문제

C의 `ScanService`(C4.14)와 Registry `record_scan`이 main에 있었지만 A 제품 흐름은 한 번도 부르지 않았다. 등록은 PENDING 저장에서 끝났고, 활성화는 늘 `reviewed_scan_id=None`이었다(A105). 사용자가 어떤 검사 결과를 보고 활성화했는지 Registry에 남지 않았다.

## 한 일

- 새 A 서비스 `services/registration_scans.py`의 `RegistrationScans`가 제품 루프에서 `ScanService` 하나를 감싼다. 검사는 명령 레인 밖의 Task로 돈다. 제어 레인 작업자 4개와 등록 명령의 90초 제한·잠금을 10분짜리 검사가 붙잡지 않게 하려는 것이다.
  - 등록 확인(`confirm`, 재확인 제외)이 끝나면 이미 모은 도구 목록으로 바로 검사를 넣는다. 서버를 다시 띄우지 않는다.
  - 수동 검사(`scan`)는 PENDING이고 설정 파일에 있는 서버만 받는다. 목록을 다시 모으고(90초), 세션 시작 실패·시간 초과는 `SCAN_SERVER_UNREACHABLE`, 수집 시간 초과는 `SCAN_COLLECT_TIMEOUT`이다. 진행 중이면 `SCAN_ALREADY_RUNNING`.
  - 상태는 `queued` → `running`(`on_started`) → `done`/`failed`로 `scan_changed` 신호에 실린다(`BackendBindings.scan_events` → `BackendHost` → `BackendBridge.scan_changed`). 실패 코드는 `ScanError`의 고정 코드만 쓰고, 형식이 다르면 `SCAN_FAILED`, 그 밖의 예외는 `SCAN_UNCONFIRMED`, 취소는 `SCAN_CANCELLED`다. 완료 콜백에서 `Task.exception()`을 고정 코드로 바꾸는 2026-10-18 허용 패턴이고 넓은 except는 더하지 않았다.
  - `SCAN_RECORD_UNKNOWN`은 `registry.refresh` 뒤 실패로 알린다(다시 검사 안내).
  - 보여 줄 보고서는 Registry 캐시의 `last_scan`에서만 읽는다. 보고서 지문 집합이 지금 등록 지문과 같을 때만 보이고, 다르면 `outdated=True`로 보고서를 숨긴다.
  - 앱 종료 때 진행 중 검사 Task를 먼저 취소하고 기다린 뒤 `ScanService.close()`를 부른다(#59 A 확인 순서). 닫힌 뒤 신호는 내보내지 않는다.
- `RegistrationView.scan`(`ScanState`)을 `activation_preview`·`scan`·`scan_status`·등록 결과에 실었다. `scan_status`는 실행 차단 중에도 읽을 수 있다. 새 DTO 없이 B의 `ScanReport`를 그대로 실었다(B1.9).
- 활성화: `RegistrationRequest.reviewed_scan_id`를 받아 `set_server_active(..., reviewed_scan_id=)` → `registry.activate`로 넘긴다. 문자열이 아니면 `POLICY_CHANGE_INVALID`, 차단에 값이 있으면 `POLICY_CHANGE_INVALID`, 지금 보이는 검사 ID와 다르면 쓰기 없이 `REG_ACTIVATION_STALE`이다. 검사가 미리보기와 확인 사이에 끝나면 `record_scan`이 올린 `row_revision`으로 기존 CAS가 거부한다. 검사 없이(`None`) 활성화는 그대로 된다. Scanner 경고는 활성화를 막지 않고 Guard 판정도 바꾸지 않는다(2026-10-13 결정).
- 승인 창용 `ApprovalReview`에 `tool_name`과 `scan`(`scan_for_review`)을 더했다. 활성화 때 검토한 검사가 Registry의 마지막 검사와 같으면 그 보고서, 검토 없이 활성화했으면 `SCAN_NOT_REVIEWED`, 검토한 검사가 마지막 검사가 아니면 `SCAN_REVIEWED_UNAVAILABLE`이다.
- 조립: `product_startup._registration_scans`가 `ScanService(registry, cisco_connector())`를 만든다. 키는 검사 때마다 C 코드가 Windows 자격증명 관리자에서 읽고, 없으면 C가 LLM 분석기를 `SCAN_LLM_KEY_MISSING_OR_INVALID`로 빼고 YARA만 돈다(C4.14). 등록과 검사가 같은 `collect`를 쓴다. 제품 `close`는 `scans.aclose()` 뒤 런타임을 닫는다. A184 범위(설정 파일 없음 경로, `creation_blocker`)는 건드리지 않았다.
- 화면은 이번 항목에 없다. 경고 표시는 A201, 검토된 설명은 A202다.

## 검증

지정 가상환경 Python, `PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8`, pytest는 `-q -p no:cacheprovider --basetemp=<.cache 아래>`. 전체 시험은 돌리지 않았다. 검사기는 가짜와 고정 `ScanReport`이고 실제 LLM·Scanner 키·Cisco SDK는 쓰지 않았다.

| 실행 | 대상 | 결과 |
| --- | --- | --- |
| Codex 보고 | 아래 6개 파일 + `test_a105_activation.py` | 180 passed, 35 subtests passed |
| A 재실행 1 | 같은 7개 파일 | 180 passed, 35 subtests passed |
| A 재실행 2(격리 수정 뒤) | `test_bridge_registration.py`의 제품 bridge 시험 단독 | 1 passed |
| A 재실행 3 | `test_a200_registration_scans.py`, `test_bridge_registration.py`, `test_a180_cancel_during_apply.py`, `test_a_architecture.py`, `test_approval_review.py`, `test_bridge.py` | 157 passed, 35 subtests passed |

- `test_a200_registration_scans.py`(25개): 실제 SQLite·Registry·설정 파일. 등록 → 기록 1회 → 미리보기의 검사 ID → 활성화 뒤 Store의 `reviewed_scan_id`, 틀린 ID의 무쓰기 거부, 검사 없는 활성화, 실패 코드와 원문 비노출(`"secret text"`가 상태 어느 필드에도 없음), 대기·실행·완료 신호와 중복 검사, 수동 검사의 수집·대상 거부, 수집 실패 4종, 종료 때 취소 → close 순서와 늦은 신호 없음, 지문이 바뀐 보고서 숨김, `SCAN_RECORD_UNKNOWN`의 refresh, 입력 검사 3종, 실행 차단 중 `scan_status`, bridge 신호 중계, 승인 검토의 도구·검사, 복원 경로가 모은 목록을 씀, 수집 시간 초과.
- `test_bridge_registration.py` 제품 시험: `bootstrap_product` → `RegistrationCommands` → `RegistrationScans` → `BackendHost` 신호까지 `queued → running → done`, 검사 1회, 종료 때 close 1회, 재시작 뒤 추가 검사 없음. `cisco_connector`는 `scanner`·`product_startup` 양쪽에서 부르면 실패하도록 막았다.
- `ruff check`·`ruff format --check`: 바꾼 Python 11개 통과. `mypy src`: 141개 소스 파일 통과. `lint-imports --config tools/ci/importlinter.ini`: 계약 7개 유지, 0 broken. `git diff --check` 통과, 바꾼 파일 모두 CRLF.

### A 검수에서 다시 잰 것

- Codex 첫 시험에 `scope="session", autouse=True` 픽스처가 있어 `product_startup._registration_scans` 교체가 뒤 모듈까지 새었다. 제품 시험을 단독으로 돌리면 실제 `ScanService(cisco_connector())`를 탈 수 있었다. 픽스처를 지우고 제품 시험 안에서만 가짜 검사기를 넣고 Cisco 호출을 막게 고쳤다.
- 고의 회귀: 작업 사본 밖 복사본의 `session_policy.py`에서 `reviewed_scan_id=None`으로 바꾸고 `-o pythonpath=<복사본>/src`로 A200 파일을 돌리면 활성화 시험 1개가 `reviewed_scan_id`가 None이라 실패했다(1 failed, 24 passed). 처음에는 pytest 설정의 `pythonpath = ["src"]`가 원래 `src`를 먼저 잡아 회귀가 적용되지 않았다(25 passed). 원본 파일은 바꾸지 않았다.

## 남은 것

- 등록 화면·승인 창의 경고 표시(C4.13)는 A201, 검토된 설명 입력(DESC-01)은 A202.
- 승인 창은 활성화 때 검토한 검사가 마지막 검사와 다르면 그 보고서를 보여 주지 못한다(`SCAN_REVIEWED_UNAVAILABLE`). B·C에 `scan_id`로 저장된 보고서를 읽는 공개 API가 없다. 필요하면 B 계약 제안.
- 진행 정보는 대기·실행·끝뿐이다. 분석기별 진행률은 `ScanService`가 내지 않는다.
- 실제 Cisco SDK·LLM 키·YARA 규칙으로 도는 검사, 10분 예산·대기 8건 초과, 실제 앱 종료 때의 취소는 확인하지 않았다. 가짜 검사기로 A 연결만 확인했다.
