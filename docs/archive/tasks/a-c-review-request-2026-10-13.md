# A → C · 2026-10-13 SQLite 앱 연결 리뷰·독립 검증 요청

기준: A `feat/a-application` **`7ed173d`**(B `6115fa6` 병합 `4388298` 위의 SQLite 앱 연결).
[팀 협업 규칙](../../team-workflow.md)에 따라 `services/bridge.py`와 `__main__.py`의 리뷰는 C가 맡는다.
저장소 독립 계약·통합·결함 주입도 C가 맡는다. 이 문서는 리뷰와 검증 요청이며 C 인수나 공동 계약 합의를 대신하지 않는다.

> **임시 요청 문서.** C는 각 항목에 승인·수정 요청·기각과 실행한 검증, 대상 커밋을 남긴다.
> 모두 정리되면 [A 진행 현황](../../a-status.md)의 SQLite 절을 갱신하고 이 문서를 정리한다.

A 측 검증은 다음과 같다.
- 전체 테스트는 **2,158 passed, 0 failed**다.
- 새 `tests/unit/test_sqlite_app.py`는 **30 passed**다. 실제 SQLite 임시 파일과 실제 bootstrap을 사용한다.
- ruff check·format이 통과했다.

C 독립 인수, 보안 스캔, 정적 강제 규칙(Semgrep·import-linter) 실행, mypy는 하지 않았다.

## 리뷰 항목

### R-C-01 · `BackendCleanupError(StoreError)`와 시작 정리 판정

- 위치:
  - `src/guard_app/services/bridge.py:38`: 예외 정의
  - `src/guard_app/services/bridge.py:743-744`: `_discard_bootstrap`
  - `examples/synthetic_app.py:87-94`: `close_store`
- 변경:
  - Store close가 `StoreError`로 실패하면 원문 없이 `BackendCleanupError`로 바꿔 전파한다.
  - bootstrap이 이 예외로 끝나면 `_discard_bootstrap`이 정리 미확인(`False`)을 반환한다.
    그 결과 상태는 `STARTUP_CLEANUP_UNCONFIRMED`다.
  - 정상 종료 중 close가 실패하면 `CLEANUP_UNCONFIRMED`다.
  - 그 밖의 bootstrap 예외는 기존처럼 정리 완료(`STARTUP_FAILED`)로 본다.
- 확인 요청:
  1. A 정리 실패를 B 소유 `StoreError`의 하위 타입으로 둔 선택이 적절한지 판단한다.
     넓은 `StoreError` 처리 경로에 섞일 위험이 있다면 A 전용 예외로 바꾸는 안을 낸다.
  2. 현재는 Store가 아닌 정리 콜백(transport close 등)의 실패를 정리 완료로 본다.
     이 판정 차이를 그대로 둘지 판단한다. 이 차이는 이번 변경 전부터 있던 동작이다.
  3. 다른 bootstrap 예외를 처리하는 도중 close가 실패하면, `from None`이 원래 예외를 가린다.
     이것이 시작 실패 분류에 영향을 주는지 확인한다.

### R-C-02 · CLI `--store`·`--db-path`

- 위치: `src/guard_app/__main__.py:27-39`
- 변경:
  - `--store {memory,sqlite}` 옵션을 추가했다. 기본값은 memory다.
  - sqlite일 때만 `--db-path`를 받는다. 조합이 어긋나면 `parser.error`(exit 2)로 거부한다.
  - 창 제목에는 Store 종류만 넣고 DB 경로는 넣지 않는다.
  - 부모 디렉터리는 만들지 않는다.
- 확인 요청: 신뢰 설정 입력의 범위가 적절한지 확인한다. 경로 정규화나 심볼릭 링크 처리는 하지 않는다.
  안전 경로 모듈을 거쳐야 하는지 판단한다.

## 독립 검증 요청

| ID | 대상 | 관련 불변식·시험 | 비고 |
| --- | --- | --- | --- |
| V-C-01 | SQLite 앱 경로의 stdio·HTTP 독립 wire `tools/call` 계수 | I1·I3, T01–T03·T12 | A는 host dispatch와 수신 서버 계수만 쟀다. wire 계수는 검증 불가로 기록했다 |
| V-C-02 | 시작 게이트 결함 주입: 열기, 스키마, 새 Task 저장·재조회, close 실패에서 AGT·transport·Guard 생성 0 | I1·I6 | 직접 대응하는 T/S ID는 없다. A 단위 테스트와 독립적인 대조가 필요하다 |
| V-C-03 | 재시작 시 과거 승인 미복원과 재전송 0 | I3·I4·I7·I8, T11 | A는 이전 PENDING 승인 1건만 시험했다. APPROVED·DISPATCHING·UNKNOWN_OUTCOME 행이 남은 DB의 재시작은 B-SQL-02 이후로 미실행이다 |
| V-C-04 | 오류·경로 비노출 | I9, T19 | A는 고정 문구와 DB 경로 비노출을 확인했다. 로그 수집 경로 전체는 확인하지 않았다 |
| V-C-05 | 정적 강제 규칙 | SQL 실행 위치 제한 | A 코드와 테스트에 `sqlite3` import나 SQL 실행은 없다. `examples/synthetic_app.py`가 `guard_app.storage.sqlite_store`와 기존 `tests.unit.test_guard` 대역을 import한다. 규칙상 허용되는지 판단한다 |
| V-C-06 | CLI 시작 실패의 GUI 표시 | 승인 렌더링·상태 표시 | 부모 경로가 없는 DB로 실행하면 콘솔 출력 없이 계속 실행된다. 창의 FAILED 표시는 GUI를 확인할 수 없어 검증 불가였다. bridge `STARTUP_FAILED`는 시험으로 확인했다 |
| V-C-07 | `test_sqlite_app.py` docstring의 T/S·I 매핑 | 문서 추적성 | 2라운드에서 T07·T08 오매핑을 고쳤다. 대응 ID가 없는 테스트에는 "직접 대응 T/S ID 없음"이라고 표시했다. C 시험 목록과 대조해 정정이 필요하면 알린다 |

## 함께 볼 문서

- [B 리뷰 요청](a-b-review-request-2026-10-13.md)
- [SQLite 후속 요청 B-SQL-01–05](a-b-sqlite-requests-2026-10-13.md): 시작 복구가 들어오면 V-C-03을 다시 실행해야 한다.
- [A 진행 현황 SQLite 절](../../a-status.md)
