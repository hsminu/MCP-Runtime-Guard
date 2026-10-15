# B6 · B 내부 읽기 전용 시작 점검기

2026-10-13 사용자 요청은 **혼자 구현 가능한 부분부터 구현·검증·add**다.
시작 코드 기준은 B `6115fa6`이고, 직전 작성한
[순번·복구 상세 설계](b6-sequence-recovery-design.md)의 A/C 회신 8항목은 여전히 미합의다.
이번에는 공유 계약을 확정하지 않는 내부 사전 점검 기반을 구현했다.

## 범위 구분

| 구분 | 이번 구현 / 다음 단계 |
| --- | --- |
| B 내부 구현 | 기존 v1 스키마·형식·외래키 관계를 읽고, Task별 요청/승격 순번의 기록상 최댓값을 관찰 |
| B 내부 구현 | 같은 읽기 snapshot의 Task·요청·승인 연결 자료, frozen private 결과, 원문 없는 오류·연결 소유 검사 |
| A/C 합의 필요 | 영속 계수 구조·초기값/증가/할당·콜백/Store 연결·새 schema migration (SEQ-01–03) |
| A/C 합의 필요 | 복구 상태 표·epoch/버전 증가·감사/반복 적용·RecoveryReport·시작/실패 ACK (REC-02–05) |
| A/C 함께 연결 | 단일 앱 소유·이전 실행자 종료·앱 gate·Runner/UI·Registry/AGT·실제 wire/수신 검증 (REC-01, A/C 소유 모듈) |

SQLite/복구의 B 소유권과 공유 동작의 합의는 별개다. 실제 순번 발급·재시작 상태 정리는
새 스키마/상태 의미에 의존하므로 이번 사용자 범위에서는 확정/연결하지 않았다.
이번 결과는 그 전에 쓸 수 있는 **검증된 저장 사실 조회**다.

## 코드와 관찰값

- [`sqlite_store.py`](../../src/guard_app/storage/sqlite_store.py)의 private 동기
  `_inspect_startup()`가 지정 SQL 모듈 안에서 조회한다. 새 public Store 메서드가 아니다.
- [`startup_inventory.py`](../../src/guard_app/storage/startup_inventory.py)는 SQL 없는
  frozen private 자료다. storage의 공개 export·domain·ports·Reason은 변경하지 않았다.
- 기존 연결/스레드를 확인하고, 이미 진행 중인 트랜잭션이면 BEGIN 전에 거부한다.
  외부 caller의 쓰기를 조회 실패 처리로 rollback하지 않는다.
- `BEGIN` 읽기 트랜잭션 안에서 현재 정확한 v1 DDL/application_id/version과 기존 FK를 검사한다.
  이어 Task/승격 이력·요청·승인 연결을 같은 snapshot에서 읽는다.
- Task와 승인에는 기존 strict `TaskState`/`ApprovalRecord` decode를 사용한다.
  요청의 기존 문자열/정수/enum/시각/해시·존재하는 reasons 형식을 검사한다.
  깊은 손상 JSON의 RecursionError도 구체적으로 고정 StoreError에 정규화한다.
- 기존 UNIQUE/ID/FK 관계를 확인하지만, 상태 조합의 복구 가능 여부는 결정하지 않는다.
  WAITING인데 승인 생성 전인 상태·종결 승인 뒤 resume 전인 상태·U8 충돌도 관찰 자료로 남긴다.

| 내부 값 | 의미 |
| --- | --- |
| `request_sequence_max` | 해당 Task의 현재 requests에 실제 기록된 최대 call_seq. 없으면 None |
| `escalation_sequence_max` | 해당 Task의 실제 승격 이력 최대 at_seq. 없으면 None |
| `persisted_sequence_max` | 위 두 기록 중 최댓값. 둘 다 없으면 None. 기록 0과 빈 이력을 구분 |
| `stored_epoch/stored_kind/stored_row_revision` | v1 요청 테이블에 없으므로 모두 None. 현재 Task·메모리 결속·Registry로 채우지 않음 |
| 승인 연결 | 기존 ApprovalRecord와 해당 request가 가리키는 Task ID. 원문 snapshot이나 실행 권한이 아님 |

반환에 next/seed/배정 ACK·복구 목표 상태·READY 토큰은 없다.
기록상 MAX는 사라진 미기록 배정을 복원하거나 순번을 할당했다는 증거가 아니다.
NULL decision/reasons는 v1이 허용하는 미기록 값으로 관찰한다. 등록/Scanner JSON·감사 detail은
반환 자료로 읽지 않고, 기존 v1에서 FK가 아닌 관계를 새 제약으로 강제하지 않는다.

## 유지하는 경계

성공한 점검은 7개 테이블/버전·Task/요청/승인/감사·메모리 실행 맥락을 변경하지 않는다.
audit ID/utc_now callback도 호출하지 않는다. 오류의 원문을 공개하거나 형식 불명을
기본 PUBLIC/초기값/READ로 보충하지 않는다.

조회 SQL 오류는 기존 저장소 경계로 처리한다. COMMIT/rollback 확정 불가에서는 기존과 같이
연결/메모리 실행 맥락을 폐기할 수 있다. 성공 ACK·예전 실행 권한을 되살리지 않는다.
"읽기 전용"은 이 점검 메서드의 DML 비수행을 뜻한다. 기존 생성자의 빈 DB v0→v1 초기화는 그대로다.

점검기는 앱 시작에 자동 연결하지 않았고, 점검 성공이 전체 복구/제품 준비 완료를 뜻하지 않는다.
순번 저장·migration·복구 UPDATE·앱 gate는 합의 후 다음 단계다.
전체 snapshot을 메모리에 읽으므로 큰 DB의 시간/메모리 상한·지원 예산은 아직 측정하지 않았다.

## 검증 상태와 남은 작업

실제 파일 SQLite의 B 단위 테스트로 빈/0/Task별 순번 근거·보안 이력·반복 읽기·승인 관계·
v1 미기록 kind/epoch·split 중단 조합·손상 형식/FK/DDL·WAL snapshot·기존 TX 보존·
스레드/닫힘/SQL/불명 read ACK·원문 비노출을 검사한다.
새 `tests/unit/test_startup_inventory.py` **69개 통과(2.43초)**,
기존 SQLite 3개 파일의 **424개 통과(27.05초)**를 확인했다.
최종 전체 **1,683 passed(74.93초)**다. Ruff check, format **60개 파일**,
strict mypy **29개 소스 파일**, pip check도 통과했다.
Windows asyncio 검사에는 정상 실행 권한을 사용하고 cacheprovider를 끈 뒤
저장소 내부의 검증된 전용 temporary 경로를 사용했다.

관련 I4/I6/I7/I8/I9, T11/T19/S03의 B 저장소 단위 기반이다.
[검증 계획 50개](b6-sequence-recovery-validation.md)의 일부 사전 관찰 속성을 다루지만,
영속 배정/복구 API·프로세스 중단·앱 gate·실제 MCP wire를 실행한 것으로 표시하지 않는다.
C 독립 계약/결함·A 제품 연결은 별도다.

다음은 SEQ-01–03·REC-01–05 회신 대조 → [contracts.md](../contracts.md)의 실제 합의 기록 →
계수/schema·복구 구현 → A 연결/C 독립 인수다. 최초 변경은 기존 설계 문서와 함께 add로 준비했다.
이후 2026-10-13 사용자가 이 검증된 설계/점검기 묶음의 commit·push를 지시했다.
실제 공개 커밋은 B 브랜치 Git 이력으로 확인하며, 공개를 공유 계약 동의로 처리하지 않는다.
