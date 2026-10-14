# A → B · 2026-10-13 SQLite 앱 연결 리뷰 요청

기준: A `feat/a-application` **`7ed173d`**(B `6115fa6` 병합 `4388298` 위의 SQLite 앱 연결).
[팀 협업 규칙](../team-workflow.md)의 리뷰 배정에 따라 B가 리뷰할 A 변경과 병합 해결을 모았다.
기능 요청은 [SQLite 후속 요청](a-b-sqlite-requests-2026-10-13.md)의 B-SQL-01–05에 따로 있다.
이 문서는 리뷰 요청이며 B 동의나 A·B·C 공동 계약 합의를 대신하지 않는다.

> **임시 요청 문서.** B는 각 항목에 승인·수정 요청·기각과 근거 커밋을 남긴다.
> 모두 정리되면 [A 진행 현황](a-progress.md)의 SQLite 절을 갱신하고 이 문서를 정리한다.

A 측 검증은 다음과 같다. 전체 테스트 **2,158 passed, 0 failed**를 2라운드 QA와 커밋 전 재실행에서 각각 확인했다.
ruff check·format과 `git diff --check`도 통과했다. C 독립 인수와 보안 스캔은 실행하지 않았다.

## 리뷰 항목

### R-B-01 · `TaskRunner.pending_approval_id` 추가

- 위치: `src/guard_app/services/runner.py:191`. A 소유이며 리뷰는 B 담당이다.
- 변경: 읽기 전용 속성이다. 이 Runner가 Guard에서 `Pending`으로 받은 `approval_id`만 반환한다.
  실행 권한이나 승인 상태의 근거로 쓰지 않는다.
- 사용처: `examples/synthetic_app.py:227`의 snapshot이 이 ID로 공개 `get_approval`을 조회한다.
  PENDING일 때만 UI에 대기 승인으로 표시한다. 메모리 대역 전용 `store.approvals`를 직접 조회하던 코드는 제거했다.
- 확인 요청: Runner가 `Pending`을 관찰한 뒤 Guard·ApprovalService가 승인을 종결하는 경로에서 오래된 ID가 남는지 확인한다.
  남더라도 `get_approval`의 PENDING 검사가 표시를 막는다. 이 처리가 B 승인 수명 규칙과 맞는지 판단한다.

### R-B-02 · A 조립에서의 `SQLiteStore` 사용 방식

- 위치: `examples/synthetic_app.py:85-106, 189-195`
- 사용 방식:
  - 생성자는 `SQLiteStore(path, audit_id_factory=UUID)`로 부른다. busy timeout과 `utc_now`는 B 기본값을 쓴다.
  - Store를 열면 새 UUID Task를 `create_task(created_at_utc=datetime.now(UTC))`로 만들고 `load_task`로 같은지 확인한다.
    이 확인을 마친 뒤에야 AGT·MCP·Guard를 만든다.
  - `close()`는 AsyncExitStack에 가장 먼저 등록한다. 그래서 같은 백엔드 스레드에서 마지막에 닫힌다.
  - `next_call_seq`는 실행마다 메모리 카운터다(B-SQL-01 보류). `failure_audit`는 no-op다(B-SQL-04).
- 확인 요청:
  1. 생성자 인자 사용과 `create_task`의 시각 공급이 B 의도와 맞는지 확인한다.
     `created_at_utc`에 Store 내부 시계 대신 호출자 시계를 넣었다.
  2. 실행마다 새 Task를 만들면 `(task_id, call_seq)` 유일성이 깨지지 않는다는 A 판단을 확인한다.
  3. 이전 행을 조회·수정하지 않는 임시 정책이 B-SQL-02 시작 복구 설계와 충돌하는지 확인한다.

### R-B-03 · `StoreError` 하위 타입 `BackendCleanupError` (참고)

- 위치: `src/guard_app/services/bridge.py:38`. 주 리뷰는 C지만, 기반 타입이 B 소유 `domain/errors.py`의 `StoreError`라서 B에도 알린다.
- 용도: A 자원 정리에서 Store close가 실패했다는 것을 bridge에 알린다. Guard·ApprovalService 경로에서는 발생하지 않는다.
- 확인 요청: B 서비스가 `StoreError`를 넓게 잡아 예약 전후 결과로 바꾸는 경로가 있다. 이 하위 타입이 그 경로에 들어갈 가능성이 있는지 판단한다.
  들어갈 수 있다면 `StoreError`와 무관한 A 전용 예외로 바꾸도록 요청한다.

### R-B-04 · 병합 `4388298`의 B 소유 문서 충돌 해결

- 대상: `docs/contracts.md`(B 소유). 같은 병합에서 `README.md`와 `.gitignore`도 충돌을 해결했다.
- 해결 내용(`git diff 6115fa6 4388298 -- docs/contracts.md`):
  - 머리에 A 배너 한 줄을 남겼다.
  - 합의 기록 절 앞에 A 동기화 기록 3줄을 넣었다.
  - 끝에 "2026-10-13 A 리뷰 수정 연결" 절을 남겼다.
  - B 본문은 바꾸지 않았다.
- 확인 요청: B가 공통 계약 문서의 배치와 문구를 확인한다. 고칠 부분이 있으면 B 브랜치에서 정리한다.
  A는 다음 병합에서 B 쪽을 따른다.

### R-B-05 · `test_catalog_schema` 성질 테스트의 health check 실패

- 위치: `tests/unit/test_catalog_schema.py:221, 238`(B 소유).
  대상 테스트는 `test_compiled_acceptance_always_satisfies_original_constraints`다.
- 관측: 다른 프로세스와 동시에 실행한 병합 전·후 전체 실행 2회에서 Hypothesis `FailedHealthCheck(too_slow)`로 실패했다.
  파일만 따로 돌린 실행(35 passed)과 이후 전체 실행 3회에서는 통과했다.
- 판단: 부하에 따른 타이밍 flaky로 보인다. 원인은 미확정이다.
- 요청: `suppress_health_check=[HealthCheck.too_slow]`나 생성 전략 축소 같은 안정화가 필요한지 판단한다.

## 함께 볼 문서

- [SQLite 후속 요청 B-SQL-01–05](a-b-sqlite-requests-2026-10-13.md): 영속 call_seq(사용자 결정으로 B 보류), 시작 복구, 목록 조회, 실패 감사, AGT 감사 맥락.
- [A 진행 현황 SQLite 절](a-progress.md): 실행 명령과 라운드별 검증 결과.
