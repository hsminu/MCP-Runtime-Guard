# A123 · 화면의 AGT 표시 제거

2026-10-28. AGT 제거(2026-10-15 결정) 뒤에도 화면 상단과 실행 컨트롤에 "AGT: 미설정"이 남아 있다는 사용자 지적에 따라 화면 표시만 지웠다. AGT 코드 제거는 B의 [PR #47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47)(B2.11)이고, 이 기록을 쓴 시점에 아직 병합 전이다.

## 한 일

- 바꾼 곳은 #47의 같은 위치와 같은 변경이다. #47이 병합될 때 충돌을 줄이려는 것이다.
  - `ui/presentation.py`: `AGT_DENY`·`AGT_ERROR` 사유를 "접근 규칙 거절"·"접근 규칙 확인 실패"로, 차단 안내를 "Guard 정책 또는 접근 규칙"으로 바꿨다.
  - `ui/runtime.py`: 실행 컨트롤 요약 줄에서 AGT 줄을 빼고 모델 줄만 남겼다.
  - `ui/window.py`: 상단 표시를 "모델: …"만 보이게 바꿨다.
- `RuntimeIdentity.agt_mode` 필드와 `_sync_selected_task`의 생성 코드는 그대로 두었다. #47이 함께 정리한다.

## 검증

- `test_ui_overview.py`: 상단 표시가 "모델: 미설정", mock 모드에서 "모델: mock"이다.
- `test_runtime.py`: 실행 컨트롤 요약에 "AGT"가 없다.
- 전체 테스트(`QT_QPA_PLATFORM=offscreen`, `tests` 전체): 4,874 통과, 4 skip, subtest 20. 실패 1개는 C 소유 `test_paths.py::test_app_root_is_under_local_app_data`이고 이 변경 전부터 실패했다.

## 남은 것

- #47이 병합되면 받아서 `RuntimeIdentity.agt_mode`와 그 생성 코드를 정리한다.
- `ui/presentation.py`의 차단 안내 줄은 A125에서 문체를 바꿔 #47과 같은 줄이 달라졌다. 병합할 때 A125 문체 쪽으로 맞춘다.

관련: A53, B2.11. PR은 아직 없다(A 브랜치 작업).
