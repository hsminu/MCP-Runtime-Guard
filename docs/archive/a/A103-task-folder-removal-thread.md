# A103 · Task 폴더 삭제를 작업 스레드로 옮김

2026-10-26. PR #35 B 리뷰(`security/paths.py` 4번 A 연결 메모)를 A에 반영했다.

## 한 일

- `services/codex_preparation.py`: `_cleanup_task`가 C2.13 `AppFolders.remove_task_folder`를 `asyncio.to_thread`로 부른다.
  - Task 폴더에는 Codex 로그 DB가 있다. 이벤트 루프에서 지우면 그동안 다른 Task의 도구 호출과 답변 처리가 멈춘다.
  - 폴더 만들기, 그리고 실행이 거부됐을 때 남는 거의 빈 폴더 지우기는 루프에 그대로 둔다.
- `services/session_runtime.py`: 폴더 삭제(`cleanup_task`)에 `TASK_CLEANUP_TIMEOUT_S`(5초)를 따로 둔다. interrupt와 `aclose`는 전처럼 0.5초다.
  - 전에는 루프에서 동기로 지워서 0.5초 상한이 삭제를 끊을 수 없었다.
  - 스레드로 옮기면 0.5초를 넘긴 삭제는 성공해도 미확인이 된다. 그러면 앱을 다시 시작할 때까지 새 Task를 받지 않는다. 그래서 상한을 따로 뒀다.
  - 이 PC에서 20MB SQLite DB(WAL)와 작은 파일 51개가 든 폴더를 지우는 데 약 10ms 걸렸다. 5초는 느린 디스크나 파일 검사를 위한 여유다.
- 상한을 넘기면 전과 같이 미확인으로 보고 새 Task를 막는다.
- `session_preparation.py`·`codex_preparation.py`의 모듈 설명을 고쳤다.

## 검증

- `test_codex_preparation.py`: 실제 앱 폴더·잠금과 python.exe 대역 프로세스로 확인했다. 폴더 삭제가 이벤트 루프와 다른 스레드에서 실행되고, 폴더가 사라진다.
- `test_session_runtime.py`
  - 정리 상한(0.01초)보다 오래(0.1초) 걸린 삭제가 성공으로 처리되고, 다음 Task를 만들 수 있다.
  - 삭제 상한을 넘기면 미확인이 된다. 자원은 남고 새 Task는 막히며, 삭제를 다시 부르지 않는다.
- 새 테스트 3개는 수정 전 코드에서 모두 실패한다.
- 전체 검사(A 브랜치, Windows 11, Python 3.12.15): 단위·contract 4,344개 통과, 1개 생략(기존 경고 5개). Ruff·포맷·mypy(85개 파일) 통과.

## 남은 한계

- 5초는 실제 Codex Task 폴더로 잰 값이 아니다. 실제 실행(C6.14·C6.15) 뒤 다시 본다.
- 상한을 넘긴 뒤에도 스레드는 삭제를 계속할 수 있다. 그 결과는 반영하지 않고 미확인으로 남긴다. 남은 폴더는 다음 앱 시작 때 C2.13 `prepare_app_folders`가 지운다.
- 정리 하나가 최대 5초 더 걸릴 수 있다. A98 기록의 "정지 명령 하나가 약 13.5초까지"는 약 18초가 된다. 실제로는 삭제가 느릴 때만 그렇다.

관련: I9. PR은 아직 없다(A 브랜치 작업).
