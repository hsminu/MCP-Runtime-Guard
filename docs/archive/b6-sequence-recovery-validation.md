# B6 · 영속 순번·재시작 복구 검증 시나리오

기준일 2026-10-13, 작성 B. **아래는 검증 계획 50개이며 전부 미구현·미실행**이다.
후속 [B 내부 사전 점검기](b6-startup-preflight.md)의 조회 기반 단위 검사는 별도다.
그 검사에서 확인한 기록/형식/snapshot 속성을 아래 기능 시나리오 전체의 PASS로 표시하지 않는다.
현재 B `6115fa6`의 1,614개 단위/회귀 통과 기록은 이전 QA 검증이며 이 계획의 통과 근거가 아니다.
대상 설계와 미합의 항목은 [상세 설계안](b6-sequence-recovery-design.md)을 따른다.
시나리오 ID는 이 문서의 관리 번호이며 원본 T/S 번호나 새 테스트 완료 표시가 아니다.

## 1. 담당·관찰값·실행 전제

| 구분 | 담당 / 관찰 |
| --- | --- |
| 저장소·Guard 단위 | B 작성/실행, C 리뷰. 실제 파일 SQLite + 메모리 AGT/Transport로 할당·상태/감사·공개 경계 검사 |
| 독립 Store 계약·결함 | C 작성/실행, B 리뷰. 합의된 동일 Store/대역 계약·실제 잠금·프로세스 중단/재개방 검사 |
| 앱 시작·UI·Runner | A 연결, C 독립 인수. 모델 호출·제어 접수·준비/실패 projection·옛 대화/캐시 차단 |
| 실제 전송/부작용 | C fixture와 A transport. stdio/HTTP의 요청별 host_dispatch·wire tools/call·receiver_effect |

합의 전에는 실제 테스트 API/오류/스키마를 확정하지 않는다.
아래 기대값은 B 권장안이며, 회신으로 선택이 바뀌면 관련 시나리오를 함께 갱신한다.
B unit이 통과해도 C의 독립 계약·통합·공격 시험을 완료로 표시하지 않는다.

반드시 관찰할 값:

- Task ID/epoch·객체별 revision·security_version·status·등급/미분류/last_read·승격 이력.
- 독립 계수의 last_issued, 반환 순번, 요청/승인 상태·관계, 감사 event ID/고정 코드·행 수.
- startup barrier의 완료/실패 ACK, 모델 호출 수, 옛 승인 소비 수, 공개된 결과 수.
- 실제 전송 시험은 **중단 전 누적값과 복구 후 증가량을 구분**한다. 기존 SEND 부작용은
  0 또는 1일 수 있어도, 재시작 때문에 생기는 추가 SEND wire/effect는 0이어야 한다.
- DB·감사·공개 오류·stdout/stderr의 원문/인자/인증 sentinel 유출 0.
  허용된 정상 결과 공개와 로그 비저장을 구분하며, 미커밋/옛 결과 공개는 0이다.

각 시험은 신뢰 fixture가 생성한 새 DB를 사용한다. Task/요청 ID·숫자/상태만 barrier와
관찰 파일로 전달하며 production에 fault hook·raw logger를 추가하지 않는다.
제품 SQL은 B 지정 storage 모듈에 두고, C 독립 시험의 SQL/fixture는 소유 지침을 따른다.
지원 OS·고정 제품 lock·프로세스 종료 확인·임시 경로 범위를 검증한 뒤 실행한다.

## 2. 순번 할당 · 12개

기본은 B 단위 계획이며 동시 연결/프로세스는 C 독립 검증으로도 수행한다.

| ID | 준비 / 중단·입력 | 확인할 기대 | 합의 의존 / 근거 |
| --- | --- | --- | --- |
| SQV-01 | 빈 새 Task에 연속 배정, 연결 종료/재개방 | 신규 1부터 증가·커밋 계수 보존. 옛 종결 Task에 실행 권한이 생기지 않음 | SEQ-01/02, I6 |
| SQV-02 | 두 Task 생성/계수 초기화 뒤 교대로 배정·동일 model_call_id 사용 | 생성된 각 Task에 정확한 초기 계수, Task ID별 독립·request_id/model_call_id와 혼동 없음 | SEQ-01/03, T13 |
| SQV-03 | 순번 COMMIT 뒤 record_decision 실패 | 공백 허용·다음 순번 재사용 없음·요청 저장/dispatch 성공 오보고 없음 | SEQ-01/02, I6 |
| SQV-04 | 순번 COMMIT 뒤 Catalog 인자/지문 검사 실패 또는 호출 취소 | 배정은 남고 요청/전송 권한 없음. 새 인자/ID로 같은 배정을 재활용하지 않음 | SEQ-01/02, I3/I6 |
| SQV-05 | load_task → 배정 → ASK/승인 생성 | 배정 때문에 Task/security/revision/epoch/등급이 변하지 않으며 승인 CAS 자기 충돌 없음 | SEQ-01/02, B6-02·T12 |
| SQV-06 | 같은 Task 배정 경합, 독립 writer 연결 경쟁 | 성공 반환 값 중복 없음·영향 행 수 1·계수 유실 없음. 다중 앱 실행 허용과는 별개 | SEQ-02/03, I6 |
| SQV-07 | 배정 COMMIT 성공 뒤 반환 ACK 유실 | 순번 반환/dispatch 없음, 연결 폐기/실행 차단. DB의 배정값은 남고 복구로 옛 Task 재개 없음 | SEQ-02, B6-03 |
| SQV-08 | Task/계수 초기화·BEGIN/계수 UPDATE/필수 할당 감사 각각 실패 | 생성/배정의 각 TX에서 확인된 rollback이면 부분 Task/계수/감사 변경 없음·비공개 후보 미노출 | SEQ-02/03, I6/I9 |
| SQV-09 | 빈/없는 ID·bool/음수/비정수 계수·깨진 FK | 새 기본 Task/계수로 보충하지 않음·고정 오류·순번/전송 0 | SEQ-02/03, I6/I7·S03 |
| SQV-10 | 계수 상한 직전/상한·손상된 범위 | 허용 최댓값의 정확한 배정, 다음 증가 실패. wrap·음수·float 저장/성공 ACK 없음 | SEQ-01/03, I6 |
| SQV-11 | READY/WAITING/HALTED/UNKNOWN/CLOSED·미분류 Task 배정 요청 | 합의한 RUNNING 정상 Task만 배정. 불명 SEND에 새 순번/ID로 실행 권한 없음 | SEQ-02, I3/I8·T15 |
| SQV-12 | 같은 Task ID의 새 epoch 이관과 옛 요청 제출 | 계수 리셋·동일(task_id, seq) 재사용 없음, 옛 req epoch는 record_decision/예약에서 거부 | SEQ-01·REC-03, I3/I7·T11 |

## 3. 스키마·기존 DB · 9개

B는 migration 단위를 준비하고 C는 실제 중단/잠금/무결성 시험을 독립 수행한다.

| ID | 준비 / 중단·입력 | 확인할 기대 | 합의 의존 / 근거 |
| --- | --- | --- | --- |
| MGV-01 | 빈 DB·정확한 v1·새 지원 버전 재개방 | 합의한 DDL/application_id/version 확인. 다시 열어 계수·등급을 초기화하지 않음 | SEQ-03, I4/I6 |
| MGV-02 | 요청 최대 순번보다 높은 escalations.at_seq, 빈 이력 Task 포함 | 모든 보존된 순번 근거의 MAX로 seed, 빈 이력 0. 다음 후보는 seed보다 큼 | SEQ-03, I4 |
| MGV-03 | v1에서 배정했지만 기록 전 사라진 메모리 순번 | 잃어버린 배정을 복원했다고 보고하지 않음. 옛 Task는 정지·실행 재개/번호 권한 복원 없음 | SEQ-03·REC-02/03, T11 |
| MGV-04 | 잘못된 정수/승격 이력·FK 파손·불명 미래/다른 앱 스키마 | 이관/복구 성공 아님·기본 PUBLIC/유효 kind로 보충하지 않음·게이트 닫힘 | SEQ-03·REC-02, I4/I6 |
| MGV-05 | DDL·계수 seed·요청 메타데이터 이관·버전 태그 중단/오류 | migration 내부 변경은 전체 rollback 또는 전체 완료. 일부 DDL + 새 버전 ACK 없음 | SEQ-03, B6-03 |
| MGV-06 | v1 DISPATCHING의 kind/epoch 없음·현재 Registry 종류 변경 | Registry를 과거 종류 증거로 쓰지 않음. 기본은 복구 실패; 격리 대안 선택 시 합의대로만 처리 | SEQ-03·REC-02, I6/I8 |
| MGV-07 | runtime에서 계수 행 누락/기록 MAX보다 낮음 | 자동 MAX 재생성·순번 재사용 없음. 저장 무결성 실패로 실행 차단 | SEQ-03, I6 |
| MGV-08 | 높은 last_issued 유지, 낮은/일부 요청 이력만 남음 | counter를 현재 요청 MAX로 낮추지 않음. 이력 삭제 허용 절차는 별도 | SEQ-03, I6 |
| MGV-09 | migration COMMIT 뒤 시작 복구 실패 | 새 스키마/seed는 남을 수 있지만 Task 복구 ACK·앱 READY 없음. 다음 시작은 지원 새 스키마 검사부터 재개 | SEQ-03·REC-01/05, I6 |

임의의 오래된 백업을 외부 관리 절차 없이 같은 DB처럼 넣었을 때, DB 자체만으로 삭제된
최신 배정/소비 사실을 증명할 수는 없다. 이 경계는 별도 백업 절차/외부 증거의 검토 항목이다.
"모든 과거 백업 rollback을 자동 검출"하는 검증 완료 기준을 만들지 않는다.

## 4. 시작 복구 상태·원자성 · 21개

B 단위 계획 + C의 U1–U8 독립 계약/결함 검증이다. 표의 요청·Task 상태 쌍은 REC-02 합의 후 확정한다.

| ID | 준비 / 중단·입력 | 확인할 기대 | 합의 의존 / 근거 |
| --- | --- | --- | --- |
| RCV-01 | 빈 DB, 복구 대상 없음 | 원문 없는 변경 0 report. DB 성공만으로 Registry/AGT 준비 전 앱 READY를 만들지 않음 | REC-01/04, I6 |
| RCV-02 | 옛 READY/RUNNING/WAITING, 진행 요청 없음/종결 이력만 있음 | 비종결 Task HALTED 권장·대화/실행 복원 없음·과거 완료 요청 보존 | REC-02/03, T11 |
| RCV-03 | PENDING/APPROVED, UTC가 과거/미래·만료 전/후 | 원문/권한 복원 없이 폐기, 관련 미예약 요청 DENIED·비종결 Task HALTED. TTL 연장 없음 | REC-02/04, I3·T11 |
| RCV-04 | CONSUMED + DISPATCHING READ | 소비 보존·WITHHELD/HALTED 권장·옛 결과 공개/추가 dispatch 0 | REC-02, I3/I5·T11/T36 |
| RCV-05 | CONSUMED + DISPATCHING SEND, 원격 효과 0 또는 1 | 소비 보존·요청/Task UNKNOWN 권장·복구 후 추가 SEND 0 | REC-02, I3/I8·T14/T15 |
| RCV-06 | ALLOW 예약 DISPATCHING READ/SEND, 승인 없음 | 승인 유무와 별개로 예약 후 상태 처리, DENIED/미전송으로 단정하지 않음 | REC-02, I6/I8·T36 |
| RCV-07 | READ/SEND 결과 COMMIT 뒤 공개 전 중단 | SUCCEEDED/관측 이력 보존·원문 복원/옛 공개 없음·Task 자동 재개 없음 | REC-02, I4/I5·T07/T19 |
| RCV-08 | Task WAITING COMMIT 뒤 create_approval 전 중단 | 현재 split API의 정상 중간 상태로 구분. 미예약 요청 DENIED·Task HALTED 후보, 가짜 승인 생성 없음 | REC-02, B6-01·T11 |
| RCV-09 | 승인 REJECTED/EXPIRED COMMIT 뒤 Task resume 전 중단 | 종결 승인/요청 보존·WAITING Task HALTED 후보. 거절을 다시 승인/실행하지 않음 | REC-02, B6-01·T11 |
| RCV-10 | PENDING 승인 + DISPATCHING/CONSUMED 시각 모순 | 정상 crash로 추정 수정하지 않음·원자 실패·게이트 닫힘 | REC-02, U3/U7·I6 |
| RCV-11 | 정상 HALTED/UNKNOWN/CLOSED·종결 요청/승인 | 상태·등급·소비·row/security/epoch 보존, 반복 복구에서 무조건 +1 없음 | REC-02/03/04, I4/I8 |
| RCV-12 | HALTED/CLOSED Task + DISPATCHING SEND 잔존 | RUNNING 복귀/임의 종결 전이 없음. 기본 실패 또는 합의한 U8 격리, KIND 유실과 별도 보고 | REC-02, U8·I6/I8 |
| RCV-13 | 한 Task에 SEND 불명·READ/DECIDED/WAITING 여러 행 | 집합으로 목표 결정·SEND UNKNOWN을 뒤 HALTED 규칙으로 덮지 않음 | REC-02, I8 |
| RCV-14 | kind/epoch NULL, 지원하지 않는 종류·형식 | 과거 Registry/메모리로 채우지 않음, 성공 READY 없음. 명시 격리안을 선택하면 그 계약만 적용 | REC-02/03, I6/I7 |
| RCV-15 | 잘못된 request→task·approval→request 관계, CAS/행 수 오류 | 관계없는 Task 변경 0·전체 복구 성공 0·정상 초기값으로 보충하지 않음 | REC-02, U3·I6/I7 |
| RCV-16 | 높은 등급·last_read·unclassified=True·여러 승격 이력 | 복구 전후 보안 맥락 동일·미분류 해제/등급 하향/이력 유실 없음 | REC-02/03, I4·T05/T33 |
| RCV-17 | 여러 Task 복구의 각 쓰기·감사 실패 | 전체 TX 권장안에서는 Task/요청/승인/감사 모두 rollback·READY 없음 | REC-04/05, U4·T07 |
| RCV-18 | 복구 COMMIT 불명/성공 뒤 ACK 유실, 새 인스턴스 시작 | 첫 성공 ACK 없음·게이트 닫힘, 실제 DB 재검사·중복 적용/소비/전송 없음 | REC-04/05, U1/U7·T11 |
| RCV-19 | 동일 복구를 확인된 성공 뒤 두 번 실행 | 두 번째 실제 변경/행별 복구 감사 0·계수 하향/버전 재증가 없음. 시작 점검 감사는 별도 계약 | REC-04, I3/I6 |
| RCV-20 | 선택한 recovery epoch 방식·범위 초과·옛 명령/req 반환 | 과거 요청 epoch 보존·선택한 증가의 영속 증거·반복 증가 없음·옛 승인/요청 실행 0 | REC-03/04, I3/I7·T11 |
| RCV-21 | 복구 동안 UTC 역행/진행, 기존 decided/consumed 시각 | 승인 기록 순서 하한 유지·expires_at 불변·재시작 폐기를 시간 만료 대기로 미루지 않음 | REC-04, QA-01·T11 |

## 5. A 시작 연결·C 실제 프로세스 인수 · 8개

다음은 A·C 소유 구현/독립 시험 계획이다. B는 복구 API와 관찰 메타데이터·검증 요구를 제공한다.

| ID | 준비 / 중단·입력 | 확인할 기대 | 합의 의존 / 근거 |
| --- | --- | --- | --- |
| AIV-01 | 연결/migration/복구/Registry/AGT 각 준비 단계에서 실행·승인·취소 접수 | 합의된 읽기/고정 진행 표시만 가능, 준비 전 모델·배정·소비·dispatch/wire 0 | REC-01, A bootstrap·I1/I3/I6 |
| AIV-02 | 복구/등록/AGT 시작 실패·DB 잠금 대기·큰 DB | UI는 고정 실패/준비 표시·실행 계속 닫힘. 측정한 대기/취소 시간을 보고, 1000ms를 전체 I/O 상한으로 오보고하지 않음 | REC-01/05, QA-03/10·AGT-03 |
| AIV-03 | 동일 DB를 두 앱/실행자가 열려고 함 | 두 번째 실행 개방 없음·이전 실행자의 전송과 recovery 겹침 없음. 단순 BEGIN lock으로 전체 수명 소유를 주장하지 않음 | REC-01, I1/I8 |
| AIV-04 | 같은 프로세스 재초기화 중 늦은 AGT·MCP worker·대기 SEND·종료 미확인 | 이전 실행자 실제 종료 전 새 게이트 열림 0. 이전 Guard stop을 지워 재개하거나 요청을 새 실행자로 replay하지 않음 | REC-01/05, QA-09·AGT-03·T20 |
| AIV-05 | 아래 P0–P11에서 실제 프로세스 중단, stdio/HTTP 별도 | DB old/all-new 원자성·원문 공개 없음·옛 승인 소비 없음·복구 후 추가 SEND wire/effect 0 | REC-02/04/05, T07/T11/T14/T36 |
| AIV-06 | 복구 후 옛 승인 버튼/명령·epoch·같은 SEND의 새 모델 호출 ID | 옛 Task 실행/원문 조회 권한 없음·소비/재전송 0. 새 request ID가 잠금을 우회하지 않음 | REC-01/03, I3/I8·T12/T15 |
| AIV-07 | 사용자가 명시적으로 새 Task 시작 | A 대화/캐시/모델 세션 분리·신뢰 ID/epoch 제공·새 독립 순번. 옛 결과/요청 자동 이관 없음 | SEQ-01·REC-03, I4/I7·T21 |
| AIV-08 | RecoveryReport/권위 projection 오류·불명 COMMIT·원문 sentinel | 접수와 성공 ACK 구분·READY 오보고 없음·허용 ID/코드만 표시, DB/로그/오류 원문 0 | REC-04/05, I5/I9·T19/S04 |

### 실제 중단 지점

| 지점 | 중단 위치 | 핵심 판별 |
| --- | --- | --- |
| P0 | 순번 TX 전 | 배정/요청/전송 0 |
| P1 | 순번 COMMIT 뒤 ACK 전 | 커밋된 계수 보존·같은 배정 회수 없음·전송 0 |
| P2 | 요청 해석 뒤 판정 저장 전 | 계수 공백 가능·옛 실행/승인 권한 없음 |
| P3 | DECIDED COMMIT 뒤 예약 전 | 예약 전 요청 폐기 후보·전송 0 |
| P4 | WAITING Task COMMIT 뒤 승인 생성 전 | split API의 가능한 중간 상태 |
| P5 | APPROVED COMMIT 뒤 소비 전 | 승인 실행 권한 폐기·소비/전송 0 |
| P6 | 소비/예약 COMMIT 뒤 transport 전 | CONSUMED 보존·DISPATCHING이 미전송 증거는 아님·추가 전송 0 |
| P7 | 수신자가 SEND 부작용을 만든 뒤 결과 유실 | 기존 effect 1·복구 후 증가 0·UNKNOWN 권장 |
| P8 | READ 결과 수신 뒤 관측 COMMIT 전 | 옛 raw 공개 0·WITHHELD/HALTED 권장 |
| P9 | READ/SEND 결과 COMMIT 뒤 모델/UI 공개 전 | 저장 결과/등급 보존·옛 결과 재구성/재공개 없음 |
| P10 | 복구 쓰기/감사 중 COMMIT 전 | DB 전체 old 또는 합의된 완료 결과, 부분 성공/READY 없음 |
| P11 | 복구 COMMIT 뒤 앱 READY ACK 전 | 게이트 닫힘·재검사/중복 적용 방지·추가 소비/전송 0 |

이 지점을 fixture의 원문 없는 barrier로 관찰한 뒤 강제 종료한다.
단순 coroutine 취소·예외 주입을 실제 프로세스 중단/수신 부작용 검증으로 대신하지 않는다.
이미 원격에서 처리 중인 첫 SEND가 늦게 완료될 수 있다는 점과, **새 프로세스가 추가로
전송하지 않았음**을 구분해서 보고한다.

## 6. 인수 결과 기록 양식

각 실행 기록에는 시나리오 ID, 합의된 SEQ/REC 선택, 코드/lock/schema version, 담당·날짜·OS,
fault 지점, 기대/실측 DB diff·감사·모델/공개 수·중단 전/복구 후 전송 계수를 남긴다.
PASS/FAIL/미실행/관찰 불가를 구분하고, 원문은 붙이지 않는다.

합의 회신 → B 단위 구현 → C 독립 계약/결함 → A 연결 → C 실제 계수 순서로 인수한다.
이 문서의 **50개 기능/연결 계획은 미실행**이다. 최초 작성 때는 코드/fixture를 변경하지 않았고,
후속 사용자 지시로 읽기 전용 사전 점검기와 그 B 단위 테스트만 추가한다.
영속 배정·migration·복구 API·실제 프로세스/전송 검증은 합의 후다.
