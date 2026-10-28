# A106 · 제품 DB 경로를 C `AppFolders.db_path()`로 교체

2026-10-28. [A106 첫 기록](A106-product-db-path.md)은 제품 DB를 앱 폴더의 고정 경로(`data\guard.sqlite3`, C2.17)로 옮겼다. 그때 C의 DB 파일 검사 입구가 없어서 경로만 썼다. C2.19(`AppFolders.db_path()`, [PR #41](https://github.com/hsminu/MCP-Runtime-Guard/pull/41))가 main에 들어와 교체했다.

## 한 일

- `CodexProcessPreparation.db_file`이 `self._folders.paths.db_file` 대신 `self._folders.db_path()`를 돌려준다.
  - C가 DB와 `-wal`·`-shm` 파일이 있으면 앱 폴더 권한 안의 일반 파일인지 본다. 링크·reparse point·폴더, 하드링크가 둘 이상인 파일, 사용자·SYSTEM 밖에 권한이 있는 파일은 `AppPathError("APP_DB_INVALID")`다.
  - 잠금(`claim`) 전에는 지금처럼 `CODEX_FOLDERS_UNCLAIMED`다.
- `__main__.main()`이 잠금과 설정 파일 읽기 바로 뒤에 `db_file`을 한 번 확인한다.
  - 거부되면 잠금을 풀고 고정 문구 "제품 DB 파일이 링크이거나 권한이 달라 앱을 시작하지 않음(APP_DB_INVALID)"을 남긴 채 종료 코드 1로 끝난다. 백엔드와 창은 만들지 않는다. 경로는 문구에 넣지 않는다.
  - `_run`은 백엔드를 만들기 직전에 같은 검사를 한 번 더 거친다.

## 검증

- `tests/unit/test_codex_preparation.py`
  - 새 테스트: DB 파일에 하드링크가 하나 더 있으면 `db_file`이 `APP_DB_INVALID`다.
  - 새 테스트: 같은 상태에서 `main()`이 창을 열지 않고 1을 돌려주며, 오류 출력에 고정 문구가 있고 임시 경로가 없다. 그 뒤 같은 앱 폴더를 다시 잡을 수 있다(잠금이 풀림).
  - 기존 테스트: 새 앱 폴더에서는 `data\guard.sqlite3`를 그대로 돌려주고, 백엔드가 그 경로를 연다.
- `tests/unit/test_paths.py`(C)의 `db_path()` 테스트도 통과한다.
- 전체 결과는 [A44 기록](A44-demo-without-test-doubles.md)과 같은 실행이다.

## 남은 것

- 검사와 SQLite가 경로로 다시 여는 사이의 틈은 남는다. C 설명대로 끼어들려면 사용자·SYSTEM 전용인 `data` 폴더에 쓸 수 있어야 한다.
- `_run`의 두 번째 검사에서 거부되면 고정 문구 없이 예외로 끝난다. 잠금은 풀리고 앱은 시작하지 않는다. 첫 검사 직후 DB 파일이 바뀐 경우뿐이다.
- 이관 전 백업(`begin_backup`, SEQ-03)은 B6.8 몫이라 이 항목에서 연결하지 않았다.

관련: A102, C2.17, C2.19, REC-01. PR: A 브랜치([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)에 더할 예정).
