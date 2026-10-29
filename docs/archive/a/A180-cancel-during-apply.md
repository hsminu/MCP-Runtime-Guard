# A180 · #101 제안: 저장 성공 뒤 B 적용 중 취소에도 재시작 요구·Guard 닫기

2026-10-30 · `wip/a179`(head `48e0b36` 기준). PR: 다음 A PR(아직 없음)
관련: #101 제안, A155·A156. I1·I6·I10, T10·T29·S05, 개발문서 6.6·12.2절.

## 문제

B는 #101에서 저장 성공 뒤 B 적용 중 취소를 주입하는 A 시험을 제안했다. 그 경우가 `POLICY_APPLY_FAILED`·실행 차단·재시작 안내로 이어지는 것은 코드 추적으로만 확인했다고 적었다. 파일 저장이 True로 끝난 뒤 B 적용 호출에 취소를 주입한 A 시험이 결함을 찾았다. 정책 규칙·SEND 목적지 두 경로에서 파일은 새 문서인데 재시작 코드는 None, Guard 닫기 호출은 0회였고 세션 접수·추가 변경이 열려 있었다. Guard가 옛 규칙 또는 일부 적용된 규칙을 가진 채 실행을 계속할 수 있는 상태였다.

코드 추적과 실제 결과가 다른 이유: saver 취소에는 `POLICY_SAVE_UNKNOWN` 처리가 있고 적용 거부(`PolicyChangeError`)에는 `_apply`의 `POLICY_APPLY_FAILED` 처리가 있었다. 저장 성공 뒤 `guard.apply_policy_change`의 취소는 둘 어디에도 걸리지 않았다. 도구 설정·활성화·차단·등록·갱신은 바깥 `finally`가 재시작 요구와 Guard 닫기를 보장했지만, 정책·목적지의 공통 `_save_change` 경로에는 같은 보호가 없었다.

## 한 일

- A 소유 session_policy.py의 _apply에서 guard.apply_policy_change 호출에 CancelledError 분기를 추가했다. 파일 저장이 이미 성공했으므로 POLICY_APPLY_FAILED로 재시작을 요구하고 Guard를 닫은 뒤 같은 취소를 다시 올린다. 넓은 예외 처리는 추가하지 않았다. change_server_access의 B3.10 docstring에도 적용 취소의 같은 처리와 취소 전파를 적었다. domain·ports 계약 변경은 없다.
- 기존 A180 시험의 access·destinations 기대 코드를 POLICY_APPLY_FAILED로 고쳤다. 나머지 다섯 경로의 최종 코드는 그대로다. SessionAdmission.require_restart는 마지막 코드를 보존하므로 기존 바깥 finally가 자기 코드를 다시 설정하는 것도 확인한다.
- 일곱 경로 모두 같은 취소 객체의 전파, 성공 반환값 없음, 파일의 새 문서, 이전 적용 정책의 화면 투영, 변경 불가, 재시작 요구, 새 세션 접수 게이트·추가 변경 거부를 단언한다. Guard의 실제 aclose 호출·전역 정지와 새 submit의 Denied(INTERNAL_ERROR), 합성 peer 호출 0도 확인한다. Guard 닫기는 정책·목적지에서 1회, 기존 바깥 finally가 있는 다섯 경로에서 2회다.
- Registry.apply_settings → saver → Guard.apply_policy_change 순서를 유지한다(A173). saver의 True ACK와 디스크의 새 문서를 확인한 뒤 B 적용 입구에서 취소한다. Registry 적용 자체의 취소는 기존 A155 시험 범위다.

## 검증

지정된 가상환경 Python을 사용했고 PYTHONPATH=src, QT_QPA_PLATFORM=offscreen, QT_QPA_FONTDIR=C:/Windows/Fonts, PYTHONDONTWRITEBYTECODE=1, PYTHONIOENCODING=utf-8을 설정했다. 모든 pytest에는 `-q -p no:cacheprovider --basetemp=.cache/codex-a179/pt-f<n>`을 붙였다. 전체 시험은 실행하지 않았다.

| 실행 | 대상 | 결과 | 종료 코드 |
| --- | --- | --- | --- |
| pt-f1 | test_a180_cancel_during_apply.py 단독 | 7 passed, 35 subtests passed | 0 |
| pt-f2 | 새 except 분기만 제거, 같은 파일에 `-k 'access or destinations' --tb=short` | 2 failed, 5 deselected | 1 |
| pt-f3 | 지정 관련 7개 파일, 복구 뒤 한 번 실행 | 150 passed, 42 subtests passed | 0 |

관련 파일은 test_a180_cancel_during_apply.py, test_a155_tool_settings.py, test_a156_destinations.py, test_session_collaborators.py, test_policy_change.py, test_bridge_policy_access.py, test_ui_policy.py다(모두 tests/unit). 없는 파일은 없었다.

고의 회귀에서 access·destinations 모두 재시작 코드가 None이라 실패했다. 시험 직전 바이트를 보관하고 finally에서 그대로 복구했다. session_policy.py의 회귀 전·복구 후 SHA-256은 모두 `70D15B79394C633E286CE9040161FDC467D3161F71260BEC0ED0449B7B254281`로 일치했다. 이후 pt-f3은 복구된 제품 코드로 통과했다.

| 경로 | 취소 뒤 최종 재시작 코드 | Guard 닫기 호출 |
| --- | --- | --- |
| 정책 규칙(access) | POLICY_APPLY_FAILED | 1회 |
| SEND 목적지(destinations) | POLICY_APPLY_FAILED | 1회 |
| 도구 설정(tools) | REG_TOOLS_UNKNOWN | 2회 |
| 서버 활성화(activate) | REG_ACTIVATION_UNKNOWN | 2회 |
| 서버 차단(block) | REG_ACTIVATION_UNKNOWN | 2회 |
| 등록 추가(register) | POLICY_SAVE_UNKNOWN | 2회 |
| 등록 갱신(update) | POLICY_SAVE_UNKNOWN | 2회 |

일곱 사례 모두 성공 반환값 없이 취소가 전파되고 파일은 새 문서를 유지했다. 화면 투영은 이전 적용 정책을 유지하면서 change_available=False였다. Guard 새 호출·세션 접수·추가 변경은 거부됐고 합성 peer 호출은 각각 0이었다. 도구 설정의 후속 preview도 REG_TOOLS_UNKNOWN이었다.

- `ruff check --no-cache`·`ruff format --check --no-cache`: 이번에 바꾼 Python 2개 통과, 각각 종료 코드 0.
- `python -m mypy src`: 140개 소스 파일 통과, 종료 코드 0. MYPY_CACHE_DIR은 지정 작업 캐시 안으로 설정했다.
- `lint-imports --config tools/ci/importlinter.ini --cache-dir .cache/codex-a179/import-linter`: 199 files, 1200 dependencies, 7 kept, 0 broken, 종료 코드 0.
- `git diff --check`: 종료 코드 0.
- 기존 A179 관련 6개 파일은 작업 전후 SHA-256 일치, a-status의 A180 외 바이트 동일, 바꾼 네 파일의 bare LF 0을 확인했다.

### A 검수에서 다시 잰 것

- 새 `except` 분기를 지우고 A180 파일 전체를 돌리면 7개 모두 실패했다(12 failed, 20 subtests passed). 정책·목적지는 재시작 코드가 None이고, 나머지 다섯은 Guard 닫기가 1회뿐이다. 원래 바이트로 되돌린 뒤 SHA-256 앞자리 `70d15b79394c633e`가 일치했다.
- 관련 묶음 한 번: 위 7개 파일과 A179의 `test_a179_ready_exception_lock.py`·`test_product_key_and_log.py`, 182 passed, 42 subtests passed, 종료 코드 0.

## 남은 것

- 등록·갱신은 저장 성공 뒤 적용 취소에도 POLICY_SAVE_UNKNOWN을 쓰는 이름 문제를 바꾸지 않았다. 바깥 finally의 기존 코드를 유지했다.
- A180의 세션 검증은 실제 세션 전체 생성 대신 생성 전 admission 게이트를 검사한다. 취소 뒤 화면 상태는 PolicyView·RegistrationView 경계이며 실제 사용자 화면 조작·취소 표시는 확인하지 않았다.
- 실제 B 내부 각 await의 취소, 정리 중 재취소, 네트워크 wire·원격 부작용, 프로세스 재시작 후 새 문서 적용은 확인하지 않았다. A가 B 적용 입구의 취소를 받는 경우를 검증했다.
- 전체 시험·보안 스캔·성능 측정·B/C 독립 인수는 실행하지 않았다. 네트워크·인증정보·사용자 앱 데이터 폴더를 사용하지 않았다.
