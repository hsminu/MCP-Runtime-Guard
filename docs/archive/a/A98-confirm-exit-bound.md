# A98 · 종료 확인(`confirm_exit`) 상한을 정리 상한과 분리

2026-10-26. PR #35(C2.12 launcher) A 리뷰에서 찾은 A 쪽 불일치다.

## 한 일

- `services/session_runtime.py`: `confirm_exit`는 `CONFIRM_EXIT_TIMEOUT_S`(12초)까지 기다린다. interrupt·`aclose`·`cleanup_task`는 전처럼 `SESSION_CLEANUP_TIMEOUT_S`(0.5초)다.
  - C2.12 `LaunchedProcess.close()`는 stdin을 닫고 Codex가 스스로 끝나기를 최대 5초 기다린다. 그래도 남으면 Job을 종료하고 최대 5초 더 기다린다. 12초는 이 합에 여유 2초를 더한 값이다.
  - 전에는 0.5초 안에 확인이 끝나지 않으면 미확인으로 처리했다. 그러면 앱을 다시 시작할 때까지 새 Task를 받지 않았다. `close()`는 shield 안에서 계속 돌아 나중에 True를 내도 A는 그 결과를 받지 못했다.
- 확인 상한을 넘기면 전과 같이 처리한다. 미확인으로 보고 Task 폴더를 지우지 않으며 새 Task를 막는다.
- `services/session_preparation.py`의 모듈 설명에 이 상한을 적었다.

## 검증

- `test_session_runtime.py`에 테스트 2개를 넣었다.
  - 정리 상한(0.01초)보다 오래(0.1초) 걸린 확인이 성공으로 처리된다. 자원이 정리되고 다음 Task를 만들 수 있다.
  - 확인 상한을 넘기면 미확인이 된다. 자원은 남고 새 Task는 막히며, 확인은 다시 부르지 않는다.
  - 수정 전 코드에서 둘 다 실패한다.
- PR #35 브랜치(`22fb46d`)에서 python.exe를 대신 띄워 C `launch_process`와 맞물려 봤다.
  - stdin EOF 뒤 3초 버티는 자식은 0.5초 상한에서 미확인이 됐다.
  - `close()`는 2.6초 뒤 True로 끝났다.
  - 실제 codex.exe의 유휴 종료는 C 측정으로 0.06초다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15): 단위 4,202개 통과·1개 생략, contract 41개 통과, Ruff·포맷·mypy(82개 파일) 통과.

## 남은 한계

- 취소·긴급 정지 명령은 bridge의 urgent 레인에서 차례로 처리된다.
  - Codex가 늦게 끝나면 다음 정지 명령은 앞 명령의 종료 확인을 기다린다. 앞 명령 하나가 최대 약 13.5초까지 걸릴 수 있다(interrupt·close·정리 각 0.5초와 확인 12초). 전에는 약 2초였다.
  - 정지 대상 Task의 Guard 정지는 종료 확인보다 먼저 끝난다.
- 정책 변경으로 여러 Task를 정지할 때도 `_policy_lock` 안에서 한 Task씩 확인을 기다린다.
- turn 진행 중 stdin EOF를 받았을 때 Codex가 끝나는 시간은 아직 측정하지 않았다(PR #35 리뷰에서 C T21·C6.15에 요청). 측정값이 나오면 상한, 또는 C 어댑터의 `close(grace_s, kill_s)` 값을 다시 정한다.
- 앱 종료는 bridge `shutdown_timeout`(3초)이 전체를 묶는다.
  - 그 안에 확인이 끝나지 않으면 정리 미확인으로 끝난다.
  - 앱 프로세스가 끝나면 Job 핸들이 닫히고, C2.12의 `KILL_ON_JOB_CLOSE`로 Codex도 함께 끝난다.
- 실제 launcher 연결은 C의 `SessionPreparation` 어댑터(C2.13·C6.14 뒤) 이후다.

관련: I9, T21. PR은 아직 없다(A 브랜치 작업).
