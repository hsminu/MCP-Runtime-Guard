# A105 · 새 Task 행을 정책 잠금 안에서 쓰기(B3.12의 A 몫)

2026-10-27. #33에서 A가 맡은 "새 Task 생성 직렬화"를 반영했다.

## 한 일

- `services/session_runtime.py` `_create_session`: Store에 Task 행을 쓰는 단계(`create_task`·`load_task`)를 정책 변경과 같은 `_policy_lock` 안에서 한다.
  - 정책 변경은 저장 → 적용 → 옛 세션 정지까지 이 잠금을 쥔다. 그래서 바뀌는 중인 규칙으로 행이 써지지 않는다.
  - 잠금 안에서 그때의 `GuardSettings`(Profile ID·`policy_version`)로 TaskState를 다시 만들어 쓴다. 세션 생성 함수는 `task_id`·epoch만 쓰므로 세션은 그대로 둔다.
  - 잠금을 얻은 뒤 생성 허용을 다시 확인한다. 기다리는 동안 정책 적용이 실패했으면(A104) `POLICY_APPLY_FAILED`로 거부된다.
- 잠금은 행 쓰기에만 쥔다. Codex 실행과 세션 준비는 잠금 밖이라 여러 Task를 동시에 만들 수 있다.

## 고친 문제

- 전에는 세션을 시작할 때 읽은 `policy_version`으로 나중에 행을 썼다.
- 정책 변경 중에 만든 세션은 옛 버전 행이 됐다.
  - 적용 전에 써지면 정책 변경이 그 Task를 정지시켰다.
  - 적용이 끝난 뒤 써지면 옛 버전으로 남았다. 그 Task의 호출은 Guard가 정책 버전 불일치로 거부하지만, 화면에는 실행 중으로 보였다.

## 검증

- `test_b28_integration.py`(실제 Guard·SQLiteStore·대본형 app-server)
  - 정책 저장 중에 세션을 요청한다.
  - 세션은 정책 잠금에서 기다린다. 행은 변경이 끝난 뒤 새 `policy_version`으로 써지고, Task는 정지되지 않고 실행 중이다.
  - 수정 전 코드에서는 저장 도중에 행이 써져 실패한다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15): 단위·contract 4,345개 통과, 1개 생략(기존 경고 5개). Ruff·포맷·mypy(85개 파일) 통과.

## 남은 한계

- 서버 활성화(`activate_server`)를 같은 잠금으로 묶는 일은 남았다. A에 아직 활성화 흐름이 없다(C Registry 활성화 C4.10 대기).
- 정책 변경이 여러 Task를 정지하는 동안 새 세션의 행 쓰기는 그만큼 기다린다(A98 기록의 정지 시간).
- C의 "거부·누락 쌍 전송 0" 독립 검증(B3.12의 C 몫)은 C가 한다.

관련: I2·I6, #33. PR은 아직 없다(A 브랜치 작업).
