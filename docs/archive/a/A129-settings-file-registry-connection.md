# A129 · 설정 파일 읽기·저장과 Registry 연결(CFG-01, C4.10)

2026-10-28. 사용자 요청: B·C 구현 가운데 지금 연결할 수 있는 것만 연결한다. 이미 있는 것은 B3.11 설정 바이트 변환, C2.14 설정 파일 읽기·교체, C4.10 `ServerRegistry`와 B의 SQLite Registry 저장이다. b-status의 B3.12와 c-status의 C4.10이 A 연결을 기다리고 있었다. 계약(`domain/*`·`ports.py`)과 B·C 소유 파일은 바꾸지 않았다.

## 한 일

- **시작 때 설정 파일 읽기(`services/product_settings.py` 새 모듈, `__main__.py`).** CFG-01·C2.14가 정한 순서를 따른다.
  1. 앱 폴더 잠금(A102) 뒤, 창을 열기 전에 C `read_settings_file`로 읽는다. 상한은 B의 `SETTINGS_MAX_BYTES`를 넘겨 한 곳에만 둔다.
  2. 읽은 바이트는 B `parse_settings_bytes`로 봉투를 벗긴다.
  3. 결과에 따라 이렇게 처리한다.
     - 파일 없음: 첫 실행으로 보고 앱을 연다. 빈 설정을 저장하지 않는다.
     - 읽기 실패(C 코드)나 형식 오류(B 코드): 고정 문구와 코드만 stderr에 쓰고 잠금을 푼 뒤 앱을 시작하지 않는다. 파일은 그대로 둔다.
  - `CodexProcessPreparation.folders`를 추가해 잠금을 잡은 앱 폴더를 넘긴다.
- **Profile 확인(`bootstrap_product(settings=...)`).**
  - 백엔드에서 B 저장소 위에 C `ServerRegistry`를 만들고 `load`(C4.10)한 뒤, 그 Registry로 `load_profile_settings`를 검사한다.
  - 상태 코드는 `SETTINGS_LOADED`·`SETTINGS_FILE_MISSING`·`SETTINGS_PROFILE_INVALID`·`REGISTRY_UNAVAILABLE`이다.
  - Profile이 없으면 새 Task 거부 코드가 `GUARD_PROFILE_UNAVAILABLE` 대신 이 상태 코드가 된다(`TaskPolicy.missing_code`). Profile이 있으면 새 Task 행이 파일의 정책 버전을 쓴다.
- **정책 저장(`SettingsFileSaver`, A81·B3.12).**
  - B `serialize_settings_bytes(change.document.to_dict())` 뒤 C `replace_settings_file`을 부른다. 마지막으로 읽거나 쓴 세대와 대조하고, `asyncio.to_thread`로 정책 잠금 안에서 하나씩 부른다.
  - 결과 처리(C2.14 표를 따름)

    | 결과 | 처리 |
    | --- | --- |
    | 성공 | 세대를 바꾼다 |
    | 쓰기 실패·외부 변경·대조 중 읽기 실패 | `POLICY_SAVE_FAILED`, 적용하지 않음 |
    | 결과 불명(`SETTINGS_FILE_WRITE_UNKNOWN`) | `POLICY_SAVE_UNKNOWN`. 적용 실패(A104)와 같이 재시작을 요구하고 Guard를 닫는다 |
    | 저장 중 취소 | 재시작 요구만 걸고 취소를 다시 올린다(삼키지 않음) |

- **연결 직후 목록 기록(`assemble_guard`, C4.10·I10).**
  - Registry가 C `ServerRegistry`이면, 지문 대조 뒤 선택된 서버마다 `observe`로 실제 목록을 B 저장소에 기록한다. 정의가 바뀌면 ACTIVE가 PENDING으로 바뀌어 남는다.
  - 기록하지 못했거나(StoreError) Registry가 받지 않은(RegistryError) 서버는 이번 실행에서 막는다.
  - 대조를 기록보다 먼저 한다. 기록이 대조 기준 등록을 바꾸기 때문이다.
- **화면(`ui/window.py`, `ui/presentation.py`, `ui/policy.py`, `services/bridge.py`).**
  - `BackendBindings.settings_status`와 `settings_changed` 신호를 추가했다.
  - 설정 화면 맨 위에 "설정 파일" 상태와 고정 위치(`%LOCALAPPDATA%\MCPRuntimeGuard\config\settings.json`)를 보인다.
  - 파일이 없으면 시작 화면(대화)에서만 설정 화면으로 옮긴다. 사용자가 고른 화면은 그대로 둔다.
  - 새 거부 코드와 `POLICY_SAVE_UNKNOWN`의 고정 문구를 추가했다.

## 검증

- 새 테스트 `tests/unit/test_product_settings.py` 25개. Windows 실제 앱 폴더를 쓰고, C 파일 연산의 실패 코드는 주입한다.
  - 읽기: 파일 없음은 첫 실행이고 저장하지 않음, 세대와 함께 파싱, 형식 오류는 고정 코드이고 파일 유지, C 실패 코드 전달
  - 저장: 문서 저장과 세대 추적(두 번 연속), 외부에서 바뀐 파일은 덮어쓰지 않음, 실패 3종은 미저장, 결과 불명은 예외
  - 시작 상태 4종(빈 SQLite·잘못된 Profile·Registry 읽기 실패 포함), 제품 시작의 상태 코드와 새 Task 거부, 파일 없이 시작한 실행은 None
  - 저장 결과 불명이면 같은 코드로 변경·새 세션을 거부하고, 다시 저장하지 않으며, Codex를 새로 띄우지 않음
  - 목록 기록: 같은 목록은 ACTIVE 유지, 바뀐 목록은 PENDING과 새 지문으로 저장되고 서버를 막음, 기록 실패는 같은 목록이어도 이번 실행에서 막음, 등록과 다른 경계는 B Catalog가 조립을 거부(`PROFILE_TOOL_INVALID`)
  - 설정 화면 문구와 화면 이동, `__main__`의 형식 오류 시작 거부와 잠금 해제, 파일 없이 시작해 첫 실행 상태 전달
- 보정: `test_codex_preparation.py`의 `_run` 대역 인자, `test_a_architecture.py`에 새 A 모듈 등록.
- 전체 테스트(`QT_QPA_PLATFORM=offscreen`, `tests` 전체): 4,843 통과, 4 skip, subtest 20, 실패 없음. ruff check·format, mypy(100개 소스 파일)는 통과했다.
- 파일이 없는 상태의 설정 화면을 오프스크린 캡처해 확인했다.

## 남은 것(B·C 대기)

- **제품 Guard 조립:** `ProductInputs`의 영속 `next_call_seq`·`failure_audit`가 B6.8에 있다. 그때까지 제품 모드는 Guard를 만들지 않는다. 그래서 정책 저장(`policy_access`)과 목록 기록은 제품 화면에서는 아직 돌지 않고, 테스트에서만 돈다.
- **stdio 서버 실행:** EXE-01의 B 설정 필드(`command_sha256`·`command_size`)와 C 지문 계산·서버 실행 입구(C2.6)가 없다. C launcher는 크기·SHA-256 없이 실행하지 않는다.
- **HTTP 서버 URL 검증:** C2.5(PR #50)가 main에 들어와야 한다.
- **서버 추가 화면의 실제 등록(A128):** 위 실행 입구와 Scanner(C2.10)가 있어야 `register` → 확인 → `activate`를 잇는다.
- 등록과 설정의 경계가 다르면 지금은 B Catalog가 조립 전체를 거부한다. 서버 하나만 막을지는 B·C와 정할 일이다.
- 미설정 상태의 화면 제한("설정 화면만 열고")은 시작 화면 이동과 새 Task 거부까지만 했다. 다른 화면을 막지는 않는다.

관련: A24–A27, A81, A86, A104, A105, CFG-01, C2.14, B3.11, B3.12, C4.10, I9, I10. PR은 아직 없다(A 브랜치 작업).
