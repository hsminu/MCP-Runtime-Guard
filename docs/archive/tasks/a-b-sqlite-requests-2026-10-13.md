# A → B · 2026-10-13 SQLite 앱 연결 후속 요청

기준: A `feat/a-application`, 병합 HEAD **4388298**(B `6115fa6` 병합).
이번 사용자 스펙에 따라 A 합성 앱에 B SQLiteStore를 연결했다. B 소유 코드·테스트와 C 소유 검증·설정은 수정하지 않았다. 아래는 B 검토 요청이며 API 확정·A/B/C 합의·C 독립 인수나 다른 clone으로의 전달 완료를 뜻하지 않는다.

> **임시 요청 문서.** B는 해결하거나 기각한 항목의 처리 결과·근거 커밋을 남긴다. 모두 정리되면 [A 진행 현황](../../a-status.md)의 요청 표·링크를 갱신한 뒤 이 문서를 정리한다.

## 요청 ID

| ID | 구분 | 위치 | 요청 / 상태 |
| --- | --- | --- | --- |
| B-SQL-01 | 보류 기능 | Store 포트, Guard의 next_call_seq 주입 | 영속 호출 순번. 2026-10-13 사용자 결정으로 B 보류 |
| B-SQL-02 | 복구 계약·API 부족 | SQLiteStore, 앱 bootstrap 시작 게이트 | 이전 Task·승인·진행 요청의 시작 정리와 결과 불명 SEND 사람 확인 |
| B-SQL-03 | 조회 API 부족 | Store.load_task/get_approval, 앱 snapshot | Task·대기 승인·복구 대상 목록의 공개 조회 |
| B-SQL-04 | 감사 API 부족 | Guard.failure_audit, Store 포트 | 사전 예약 실패 감사의 영속 저장 연산 |
| B-SQL-05 | 감사 맥락 부족 | SQLite 감사, AGT-02 | agt_mode·규칙 버전 감사 필드·마이그레이션 |
| B-SQL-06 | 실행 회차 범위 방어 검토 | ApprovalService._cancel / HumanControl.cancel·emergency_halt | 새 실행의 capability로 옛 Task 행 정지 가능. A 입구 차단 완료, B 범위 계약 검토 요청 |

## B-SQL-01 · 영속 call_seq

- 부족한 기능/위치: `ports.Store`의 현행 12개 연산에 순번 할당이 없다. `GuardService.__init__`은 외부 `next_call_seq`를 요구하고 SQLite는 `(task_id, call_seq)` 유일성을 검사한다. 동일 Task를 재시작할 때의 순번 API는 미확정이다.
- 요청: B가 단일 writer의 영속 순번 연산, Task/epoch 범위·원자성·실패 의미를 제안한다. **2026-10-13 사용자 결정으로 이번 연결에서는 보류**하며 A가 대체 구현하지 않는다.
- A 임시 처리: SQLite 실행마다 UUID 기반 새 Task를 생성하고 그 실행에만 메모리 카운터를 쓴다. 이전 Task를 이어 실행하지 않으므로 Task/순번 쌍이 충돌하지 않는다. request·approval·audit ID도 UUID 기반이다. 일반적인 동일 Task 재개용 순번 구현으로 간주하지 않는다.
- 재현 근거: `test_restart_ids_do_not_collide_and_file_reopens`가 같은 파일을 두 번 열고 각 실행의 ASK 승인·SEND 1회 및 서로 다른 Task/승인 ID를 검사한다.

## B-SQL-02 · 시작 복구와 결과 불명 확인

- 부족한 기능/위치: SQLiteStore는 다시 열 때 스키마를 검증하지만 이전 실행의 Task·승인·요청을 확정하는 공개 복구 연산은 없다. 메모리 결속 소멸로 실행 권한은 차단되지만 행 정리는 하지 않는다. [B 수명 문서](../b6-sqlite-lifecycle.md)의 한계와 일치하며 새로 발견한 B 결함으로 단정하지 않는다.
- 요청: 이전 RUNNING/WAITING_APPROVAL Task, PENDING/APPROVED 승인, DISPATCHING 요청의 처리 규칙과 **A 시작 게이트가 호출할 단일 연산·결과 DTO·실패 의미**를 제안한다. 승인 EXPIRED/INVALIDATED, DISPATCHING→UNKNOWN_OUTCOME, Task HALTED 등은 검토할 선택지이며 이번 문서가 상태 전이를 확정하지 않는다. 기존 UNKNOWN_OUTCOME과 결과 불명 SEND의 수신측 사람 확인·종결 경로, 트랜잭션/커밋 불명과 복구 실패 시 실행 차단 의미도 필요하다.
- A 임시 처리: Store 열기·새 Task 저장·동일 상태 재조회가 모두 성공하기 전 Guard·AGT·MCP를 시작하지 않는다. 매 실행 새 Store·Guard·HumanControl·Task·Runner를 생성한다. 과거 Task를 재개하거나 과거 승인(PENDING/APPROVED 포함)을 실행 권한으로 복원하지 않으며 이전 DISPATCHING/UNKNOWN_OUTCOME 요청을 재전송·재시도하지 않는다. 앱 bootstrap은 기존 행을 조회·수정하지 않고 같은 DB의 새 Task만 시작한다.
- 한계: 과거 RUNNING/WAITING_APPROVAL Task와 DISPATCHING 요청은 그대로 남고 이번 화면에는 보이지 않는다. 결과 불명 SEND의 사람 확인 경로도 없다. 정상 종료에서도 Task를 임의로 HALTED/CLOSED로 확정하지 않는다. Task/요청 수명 정리 완료나 복구 지원으로 표시하지 않는다.
- 재현 근거: `test_restart_does_not_restore_old_approval_or_task`는 1회차 Pending 뒤 close, 2회차 review=None·approve=Denied·dispatch/effect=0, 새 Task RUNNING을 검사한다. 테스트에서만 공개 API로 이전 Task·승인 행의 불변을 확인한다. 이전 APPROVED·DISPATCHING·UNKNOWN_OUTCOME 잔존 행의 A 재시작 시험은 미실행이며 B 복구 API 이후 확장 대상이다.

## B-SQL-03 · 목록 조회

- 부족한 기능/위치: Store의 `load_task(task_id)`·`get_approval(approval_id)`는 알려진 ID 조회만 제공한다. SQLite에는 메모리 대역의 `approvals` 컬렉션이 없고 Task·대기 승인 목록 API도 없다.
- 요청: 현재/과거 실행 구분·Task 관계·상태·revision을 확인할 수 있는 공개 목록 조회와 페이지/필터 범위, 조회 실패 의미를 B6-05와 함께 제안한다. 조회가 실행 권한을 복원해서는 안 된다.
- A 임시 처리: `TaskRunner.pending_approval_id`로 이번 Runner가 받은 Pending의 ID만 관찰하고, 공개 `get_approval`의 ID 일치와 PENDING을 확인한 뒤 표시한다. 비공개 속성·별도 SQL은 쓰지 않는다. 전체 목록과 이전 실행 기록 표시는 연결하지 않았다.
- 재현 근거: `test_sqlite_scenario_uses_real_store_and_transport`의 stdio/http ASK 케이스와 재시작 시험이 이번 승인 표시·소비 후 제거·과거 승인 미표시를 검사한다. 메모리 기본 경로도 같은 공개 조회를 쓴다.

## B-SQL-04 · 사전 예약 실패 감사

- 부족한 기능/위치: Guard 생성자는 `failure_audit(FailureEvent)`를 별도 callback으로 요구하지만 Store의 현행 공개 연산에는 이 이벤트의 저장 연산이 없다. 앱의 `audit` callback은 기존 no-op다. SQLite의 판정·승인·응답 원자 감사가 이 callback의 모든 실패 감사를 대신한다고 볼 수 없다.
- 요청: 요청 ID가 아직 없는 예약 전 실패 등을 원문 없이 기록하는 영속 연산과 필수 메타데이터, 감사 실패 시 차단 범위·실패 전파를 정의한다. 예약 후 실패 저장과 추가 감사 순서도 유지해야 한다.
- A 임시 처리: callback을 기존 no-op로 유지하며 영속 실패 감사 완료라고 표시하지 않는다. B의 fail-closed·결과 미공개 경계는 그대로 사용한다. 별도 SQL·감사 저장소를 만들지 않았다.
- 재현 근거: `examples/synthetic_app.py`의 `audit`, `GuardService.__init__`의 주입 인자와 `ports.Store` 메서드 목록을 대조했다. 감사 DB 내용 직접 조회·새 실패 감사 내구성 시험은 미실행이다.

## B-SQL-05 · AGT 감사 맥락 (기존 AGT-02)

- 부족한 기능/위치: 앱은 real/mock 선택과 합성 rule version을 알고 있으나 현재 Store 감사 연산에 `agt_mode`·규칙 버전을 전달하는 공개 인자가 없다. v1 감사 스키마도 전용 필드를 제공하지 않는다.
- 요청: [공동 계약 AGT-02](../../contracts.md)의 필드·마이그레이션·기존 불명 행 처리·저장 실패 의미를 검토한다. 기존 기록의 모드/버전을 추정해서 채우지 않는다.
- A 임시 처리: 선택/초기화된 모드는 기존 TaskProjection·창 제목에 표시한다. DB 경로는 표시하지 않는다. mode/규칙 버전의 영속 감사는 미완료로 남기며 기존 `detail`에 임의 형식으로 넣지 않는다.
- 재현 근거: `synthetic_app.bootstrap`의 AGT 생성·snapshot, SQLite 공개 생성자/감사 관련 연산과 기존 AGT-02 요청을 대조했다. 이번 연결이 AGT 감사 계약을 새로 확정하지 않는다.

## 실제 검증과 범위

- 새 A 단위 파일: `tests/unit/test_sqlite_app.py`. 실제 SQLite 임시 파일·실제 bootstrap·stdio 및 로컬 HTTP 서버를 사용한다. 2라운드에서 정상 시나리오를 두 transport로 매개변수화했다.
- I1–I9 중 해당 A 경계와 T01/T02/T03/T11/T12/T13/T19의 부분 검증이다. T01–T03은 판정·승인 표시·수신 계수, T11은 재시작 시 과거 승인 실행 0, T12는 순차 중복 승인 후 CONSUMED·SEND 1회, T13은 같은 내용의 별개 정상 요청 각각 처리, T19는 오류·CLI 출력의 경로/표지 비노출만 연결한다. 전체 T/S 또는 C 독립 인수 통과를 뜻하지 않는다.
- 시작 게이트·설정 거부·종료 정리는 직접 대응 T/S ID가 없다(A 시작 게이트 I1/I6, 설정 I6/I7/I9, 자원 수명 I6). T07 실행 중 커밋 실패→HALTED, T08 정적 import 규칙, T21 대화·캐시 분리, T35 SEND 직후 다음 호출은 이 파일에서 검증하지 않아 매핑에서 제외했다.
- 다른 application_id/미래 schema DB는 B 공개 API로 생성할 수 없으므로 이번 A 시험에서 생략했다. A 테스트에 SQL 실행을 추가하지 않았다. B의 기존 스키마 거부 단위 검증과 구분한다.
- 2라운드 정상 stdio/HTTP의 host_dispatch·receiver_calls·receiver_effect를 실측했다. HTTP는 `spike.counts`와 `TaskProjection.host_dispatch/receiver_effect`를 사용했다. ASK 승인 전 전체 dispatch/receiver_calls/effect=1/1/0(READ만), 승인·중복 승인 후 2/2/1(SEND 1회), DENY는 1/1/0(SEND 0회), ALLOW는 2/2/1이다. 이 counts는 도구 함수 수신 계수이며 별도 ASGI wire tools/call 계수는 아니다. stdio/HTTP 독립 wire 계수는 이번 파일에서 검증 불가다. 시작 실패는 stream/counts 파일 구성 전 차단해 spawn·wire·수신 효과 경로가 만들어지지 않음을 확인한다.
- 이번 연결에서 새 B 코드 결함은 확인되지 않았다. 최종 명령·결과는 [A 진행 현황](../../a-status.md)의 SQLite 연결 절에 기록한다. 위 항목은 기능/공동 계약 부족이며 결함 재현 결과로 바꾸어 쓰지 않는다.


## B-SQL-06 · 새 실행 HumanControl의 옛 Task 제어 범위

- 추가 기준: HEAD `631e116` + W1–W4 작업 트리(2026-10-13). 이전 절의 "새 B 결함 없음"은 당시 SQLite 연결 기록이다.
- 위치: `services/approvals.py`의 `ApprovalService._cancel`(기준 554줄), `HumanControl.cancel/emergency_halt`(820/823줄).
  인증된 operator와 ID 형식은 검사하지만 현 실행 Runner 소유 관계는 검사하지 않는다. `request_stop` 뒤 공개 Store 조회·invalidate·halt가 진행된다.
- 재현: 같은 SQLite 파일로 실제 mock AGT/stdio bootstrap → Pending → close → 새 bridge/bootstrap READY.
  옛 Task ID로 run/cancel/emergency_halt를 차례로 보낸다. 수정 전
  `test_old_task_commands_are_rejected_before_model_control_and_projection`가 실패했다.
  run은 RUNNER_UNAVAILABLE이나 cancel/halt는 CONTROL_DENIED_QUERY_REQUIRED, B 제어 2회·discard 2회·옛 조회 6회·옛 projection 2회였다.
  정리 후 공개 load_task 비교도 실패해 옛 행 row_revision 2→3을 확인했다. 모델·dispatch·receiver_calls/effect는 0이었다.
- 원인 분류: A bridge의 소유 검사 누락과 A snapshot의 옛 행/현재 Runner 혼합은 A 결함으로 수정했다.
  B capability가 여러 Task를 다루는 현 계약 자체를 이번 요청만으로 B 회귀 결함이라고 단정하지 않는다.
  다만 REC-03의 옛 행 비변경 원칙을 B 경계에서도 보장할지, 허용 Task/실행 회차 범위를 누가 공급할지 검토가 필요하다.
- A 임시 처리: cancel/halt도 run과 같이 현재 TaskRunner·task_id 일치를 먼저 검사한다.
  실패하면 RUNNER_UNAVAILABLE, discard·B 제어·Store 조회·projection 모두 0이다.
  synthetic snapshot은 현재 Runner 밖 ID를 조회 전에 고정 ValueError("Task is unavailable")로 거부한다.
- 수정 후 증거: 같은 테스트에서 위 계수 전부 0, 옛 Task 전체 상태·epoch 불변, 새 Task ASK 승인 뒤 dispatch/receiver_calls/effect=2/2/1이다.
  B 파일·테스트·공동 계약은 바꾸지 않았다. B 검토·C 독립 인수·다른 clone 전달은 미완료다.

### 옛 approval ID · 2라운드 검증 (2026-10-14)

- test_old_approval_controls_preserve_restart_rows의 REVIEW/APPROVE/REJECT 3개 매개변수 사례는
  각각 실제 synthetic bootstrap·SQLite·mock AGT·stdio의 1회차를 Pending까지 진행해 얻은 ID를 사용한다.
  1회차 정리 뒤 새 SQLiteStore의 공개 get_approval/load_task로 기준 행을 읽고 닫은 다음 새 bridge를 시작한다.
- 최종 코드: REVIEW는 REVIEW_UNAVAILABLE, APPROVE와 REJECT는 CONTROL_DENIED_QUERY_REQUIRED.
  각 사례에서 모델 호출=0, host dispatch=0, receiver_calls/effect=0/0, 옛 Task projection=0이다.
- 2회차 정리 후 다시 새 SQLiteStore의 공개 API로 확인한 실제 결과: 세 사례 모두
  옛 승인 PENDING→PENDING, 옛 Task WAITING_APPROVAL→WAITING_APPROVAL,
  승인·Task row_revision 증가량 각각 0, 전체 DTO 동일이다. REJECT도 옛 승인 행을 변경하지 않았다.
- 처리: 행 불변을 그대로 단언하는 A 테스트로 남겼다. 추가 A 차단·B 소스/테스트·공동 계약 변경은 없다.
  기존 test_bridge.py 승인 테스트를 변경하지 않았고 정상 Runner 승인 경로를 포함한 지정 집중 시험은
  121 passed/0 failed, 전체는 2,264 passed/0 failed다. 상세 명령·D1 계수는 a-progress.md의 2라운드 기록을 따른다.
- 연결: REC-03·I3·T11 재시작 부분. 앞선 옛 Task 제어 범위의 B 검토 요청은 유지하며,
  이 관측을 모든 오래된 승인 상태·복구 상황에 대한 보장이나 C 독립 인수로 확대하지 않는다.
