# A130 · #52 B·C 리뷰 후속(저장 중 취소, 앱 밖 설정 파일 변경, `turn/start` 키 집합)

2026-10-28. [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)의 B 리뷰 1·2와 C 리뷰가 병합을 막지 않는 작은 제안 세 개를 냈다. 사용자 요청으로 그 셋을 반영했다. 계약(`domain/*`·`ports.py`)과 B·C 소유 파일은 바꾸지 않았다.

## 한 일

- **저장 중 취소도 Guard를 닫음(`services/session_policy.py`, B 리뷰 2-2).**
  - A129는 저장 중 취소(`CancelledError`)에 재시작 요구만 걸고 Guard는 열어 두었다. 그래서 실행 중인 세션의 도구 호출이 계속될 수 있었다.
  - 취소된 쓰기도 결과 불명과 같이 파일에 어느 문서가 있는지 모른다. 그래서 같은 `_require_restart`(재시작 요구 + `guard.aclose()`)를 부른 뒤 취소를 다시 올린다.
  - `GuardService.aclose()`는 첫 await 전에 전역 정지를 세운다. 그래서 닫는 중에 다시 취소돼도 Guard는 정지 상태다.
- **앱 밖에서 바뀐 설정 파일은 재시작 안내(`services/product_settings.py`, C 리뷰 A129-2, C2.14 "A 연결" 표).**
  - C의 `SETTINGS_FILE_CHANGED`·`SETTINGS_FILE_INVALID`·`SETTINGS_FILE_READ_FAILED`는 쓰기 전에 지금 파일을 대조하다 난 것이고, 쓴 것은 없다.
    - 이 셋은 새 `SettingsFileChangedError`로 올린다.
    - 정책 변경은 `POLICY_SAVE_FILE_CHANGED`로 거부하고, 화면은 "설정 파일이 앱 밖에서 바뀌었거나 읽을 수 없어 저장하지 않음. 앱을 다시 시작해 파일을 다시 읽으세요."를 보인다.
  - Guard는 지금 규칙을 그대로 돌리고 재시작 요구는 걸지 않는다(C 표의 "False, 재시작 안내"). 뒤의 저장도 같은 세대 대조로 계속 거부된다.
  - `SETTINGS_FILE_WRITE_FAILED`·`SETTINGS_FILE_DATA_INVALID`·`APP_FOLDERS_CLOSED`는 그대로 `POLICY_SAVE_FAILED`다.
- **`turn/start` 키 집합 고정(`tests/unit/test_codex_protocol.py`·`test_codex_session.py`, B 리뷰 1-1·C 리뷰).**
  - `turn/start`는 모델·샌드박스·승인 정책도 턴 단위로 덮어쓴다. 그런 키가 실리면 L1 고정 설정을 우회한다.
  - 그래서 `"effort" not in params` 대신 키 집합 전체를 단언한다. 고르지 않으면 `{threadId, input}`, 고르면 `effort`가 더해진다.
  - 가짜 app-server가 받은 wire에서도 같은 단언을 한다. 코드 변경은 없다.

## 검증

- `tests/unit/test_product_settings.py`
  - 저장 중 취소: 옛 세션의 READ가 닫힌 Guard에서 거부되고(dispatch 0), 새 세션·재저장은 `POLICY_SAVE_UNKNOWN`으로 거부됨. 고치기 전 코드로 되돌리면 이 테스트가 실패함(READ가 나감)을 확인했다.
  - 앱 밖 변경: 두 번 모두 `POLICY_SAVE_FILE_CHANGED`, 새 Task 거부 없음, 새 세션의 READ는 바뀌기 전 규칙대로 실행됨.
  - 저장기: 실제 앱 폴더에서 바깥이 고친 파일을 두 번 연속 덮어쓰지 않음, C 코드 3종은 `SettingsFileChangedError`, 나머지 3종은 미저장(False).
  - 정책 화면이 새 코드에 재시작 안내 문구를 보임.
- 전체(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`):
  - 단위 4,849개 통과, 4개 skip, subtest 20개, 실패 없음
  - contract 41개 통과
  - ruff check·ruff format --check(231개 파일)·mypy(100개 소스 파일) 통과

## 남은 것

- C 리뷰의 다음 단계 제안(Registry 인스턴스 하나로, 승인 사본의 옆 패널 폭·`\n` 표시, "내부 코드" 접기, 첫 실행 때 화면에서 첫 파일 만들기)은 이번 범위 밖이다.
- 제품 모드에서는 B6.8 전까지 Guard를 설정 파일로 조립하지 않는다. 그래서 위 저장 경로는 테스트에서만 돈다.

관련: A81, A104, A120, A129, B3.10, B3.12, C2.14, I11, L1.
