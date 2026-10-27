# A106 · 제품 DB를 앱 폴더 고정 경로로 옮기고 시연이 그 경로를 열지 못하게 함

2026-10-27. PR #35 B 리뷰 1번의 A 몫이다. C2.17(`AppPaths.db_file`)이 main에 병합돼 막힘이 풀렸고, 사용자 지시로 A 브랜치에서 했다.

## 배경

- #35에서 A·B·C가 합의했다. REC-01의 DB 단일 소유는 `app.lock`과 앱 폴더 안의 고정 DB로 본다(C2.17 기록).
  - 같은 사용자의 두 번째 실행은 `app.lock`이 막는다.
  - 다른 사용자는 앱 폴더 DACL이 막는다.
- 지금까지 제품 모드는 `--db-path`를 꼭 받았다. 그래서 같은 DB를 앱 폴더 밖 경로로 열 수 있었고, 이 합의가 성립하지 않았다.
- 시연(`--demo`)은 잠금을 잡지 않는다. 시연이 제품 DB를 열면 실행 중인 제품과 같은 DB를 함께 쓰게 된다.

## 한 일

- `services/codex_preparation.py`
  - `CodexProcessPreparation.db_file` 속성을 추가했다. claim한 앱 폴더의 `paths.db_file`(`data\guard.sqlite3`)을 돌려준다.
  - claim 전이나 close 뒤에 읽으면 `CODEX_FOLDERS_UNCLAIMED`다. 잠금 없이는 DB 경로도 없다.
- `__main__.py`
  - 제품 모드는 `codex.db_file`을 `bootstrap_product`에 넘긴다. 경로는 claim 뒤, bridge를 만들기 전에 정한다.
  - `--db-path`를 시연 전용 옵션에 넣었다. 제품 모드에서 주면 다른 시연 전용 옵션처럼 "--demo와 함께만" 오류(종료 코드 2)로 끝난다. 조용히 무시하지 않는다.
  - 시연의 `--db-path`는 `_outside_app_folder`가 앱 폴더 밖이라고 확인한 경우만 받는다.
    - 경로를 `Path.resolve()`로 풀어(링크·junction 포함) 앱 폴더 안이면 거부한다. Windows 경로 비교라 대소문자는 구분하지 않는다.
    - 파일이 이미 있으면 제품 DB와 같은 파일인지(`samefile`, 하드 링크 포함)도 본다.
    - 앱 폴더를 구하지 못하거나 경로를 확인하지 못하면(`AppPathError`·`OSError`·`ValueError`) 거부한다.
    - Windows가 아니면 앱 폴더와 제품 모드가 없으므로 받는다.
    - 오류 문구에는 경로를 싣지 않는다.
- README의 시연 명령(`--store sqlite --db-path <파일>`)은 그대로 쓸 수 있다. 제품 실행 명령은 README에 없다.

## 검증

- `tests/unit/test_codex_preparation.py`(Windows, 실제 폴더·DACL·잠금)
  - `db_file`은 claim 전·close 뒤에 `CODEX_FOLDERS_UNCLAIMED`이고, claim 중에는 `<root>\data\guard.sqlite3`이며 부모 폴더가 있다.
  - `main`이 bridge를 만들 때 잠금이 잡혀 있고 DB 파일은 아직 없다. bridge의 bootstrap이 여는 경로가 앱 폴더의 고정 DB다.
  - 시연 거부 5가지: 제품 DB, 앱 폴더 안 다른 파일, 대문자로 바꾼 제품 DB 경로, junction을 거친 앱 폴더 안 새 파일, 제품 DB의 하드 링크. 모두 종료 코드 2, 고정 문구, 출력에 경로 없음, bridge 미생성.
  - 앱 폴더 밖 파일(이름이 `app-`로 시작하는 형제 폴더 포함)은 받는다. 앱 폴더를 구하지 못하면 거부한다.
  - 기존 A102·A110 `main` 테스트는 `--db-path` 없이 돌고, 고정 DB가 생기지 않았는지 본다.
  - 변이 확인: `samefile` 검사를 끄면 하드 링크 사례가, `resolve`를 끄면 junction 사례가 실패한다. 각 분기를 사례 하나 이상이 잡는다.
- `tests/unit/test_sqlite_app.py`
  - 제품 모드의 `--db-path`가 시연 전용 옵션 거부 사례에 들어갔다.
  - `--codex-exe` 상대 경로·claim 거부 테스트에서 `--db-path`를 뺐다.
- 전체 검사(A 브랜치 `6a2918c` 위, Windows 11, Python 3.12.15)
  - 단위 4,670개 통과·1개 생략(main 병합 직후 4,662개에서 이번 테스트 8개가 늘었다), contract 41개 통과
  - Ruff·포맷(저장소 추적 파일 224개)·mypy(98개 파일) 통과
  - 경고 5개는 A102 기록에 적은 기존 경고(AGT 패키지 4개, `pydantic_settings` 1개)와 같다.

## 남은 한계

- C의 `AppFolders.db_path()`(#37 C 회신, 다음 C PR)가 나오면 그것으로 바꾼다. 그 함수는 DB·`-wal`·`-shm`이 링크가 아닌 일반 파일인지 확인한다. 지금은 경로만 계산하므로, 앱 폴더 안에 누가 미리 둔 링크 파일은 막지 못한다. 앱 폴더는 사용자·SYSTEM 전용이라 같은 사용자 권한이 있어야 가능하다.
- 시연 검사는 시작 시점 한 번이다. 검사 뒤 시작 전에 경로를 바꿔치기하는 경합은 막지 않는다(같은 사용자 권한 필요).
- 기존에 `--db-path`로 쓰던 제품 DB는 새 위치로 옮기지 않는다. 제품은 새 위치의 빈 DB로 시작한다. 이관은 만들지 않았고, 필요한지는 미확정이다.
- GUI를 실제로 띄워 보지는 않았다.

관련: REC-01, I9, C2.17, PR #35(B 리뷰 1번), #37, A101, A102. 계약 변경 없음(C2.17 합의를 따름). PR은 아직 없다(A 브랜치 작업).
