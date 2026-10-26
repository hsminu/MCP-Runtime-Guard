# A81 · 정책 화면의 규칙 저장을 B3.10에 연결

2026-10-19. PR #28(B3.10)이 main에 병합된 뒤, 서버 활성화 대화상자의 도구 허용 선택을 정책 변경 명령에 연결했다. 실제 설정 파일 저장은 형식(B3.11/B3.12)이 미결이라 주입형으로 남겼다.

## 한 일

- **`SessionRuntime.change_server_access`.**
  - 순서는 `prepare_server_access_change` → 주입된 Profile 저장 함수 → 저장 성공 뒤 `apply_policy_change`다. 저장이 실패하면 적용하지 않는다.
  - Registry는 `AssembledGuard.registry`(`LiveCheckedRegistry`) 그 객체를 넘긴다.
  - 적용 뒤에는 Guard가 멈춘 `stopped_task_ids`의 Codex 세션을 A가 interrupt·종료한다.
  - 새 Task와 정책 화면은 적용된 Profile로 바꾼다(B 리뷰 4).
  - `task_source`는 `SQLiteStore.list_tasks`다.
  - 실패 코드: `POLICY_SAVE_FAILED`, `POLICY_CHANGE_INVALID`, `POLICY_APPLY_FAILED`(Guard 정지), `POLICY_CHANGE_UNAVAILABLE`
- **bridge.** `request_policy_access(server_id, allowed, denied)` 명령 하나를 추가했다. 성공하면 `policy_changed`로 새 정책 보기를 다시 보낸다. 형식이 틀린 입력은 백엔드로 가기 전에 거부한다.
- **정책 화면.** 대화상자의 선택을 이 명령으로 보내고, 결과는 코드별 고정 문구로만 보여준다. 저장 함수가 연결되지 않으면 지금처럼 저장 버튼이 꺼진다.

## 검증

- 실제 Guard·SQLiteStore로 확인(`test_b28_integration.py`).
  - 저장된 문서의 접근 규칙
  - 정책 버전 변경
  - 기존 Task 정지
  - 새 Task에서 거부된 READ가 서버로 실제 호출 0건
  - 저장 실패·잘못된 선택일 때 적용 없음
  - 저장 함수가 없으면 사용 불가
- bridge·화면(`test_bridge_policy_access.py`): 명령 1회 실행, 거부 코드 전달, 잘못된 입력 거부, 자기 명령 결과만 고정 문구로 표시.

## 남은 것

- 실제 Profile 저장 함수는 설정 파일 형식(B3.11/B3.12)이 정해지면 연결한다. 그 전까지 제품에서는 저장할 수 없다.
- 서버 목록의 활성화 흐름은 C Registry 활성화와 B 3차 PR의 `RegistryStore.activate_server` 순서를 기다린다.

관련: I2·I6·I10, T18.
