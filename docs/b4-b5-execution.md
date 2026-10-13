# B4·B5 실행·응답·승인 구현과 연동 조건

기준은 최종 [기술설계서](technical-design.md)의 6·7·8·13장이다.
2026-10-11 B 브랜치에서 해시·스냅샷·예외 경계, GuardService, ResponseGate,
ApprovalService와 담당 단위·성질 테스트를 구현했다.
실제 MCP·AGT·SQLite·UI 연결과 C의 계약·통합·결함 주입 검증은 별도 인수 대상이다.
2026-10-13 문서 갱신에서는 2026-10-12 사용자 결정에 따른 SQLite 담당 변경을 반영했다.
현행 배정은 [협업 규칙](team-workflow.md)을 따른다. SQLite·영속 순번·감사 저장·시작 복구는
[B6](tasks/b-sqlite-store.md)가 구현하고 C가 리뷰·독립 검증한다. API 세부 합의·B6 구현은 미완료다.

## 구현 파일과 실행 입구

| 파일 | 구현 의미 |
| --- | --- |
| `security/digest.py` | strict JSON 정규화 바이트와 SHA-256 |
| `core/snapshot.py` | 요청 사본, 승인 맥락 해시, UTC·monotonic 만료 |
| `security/failclosed.py` | 예약 전 거부, 예약 후 격리·결과 불명, 격리 기록 실패 처리 |
| `services/guard.py` | Task 잠금, 판정·예약, 비공개 transport 호출, 오류 감사 연결 |
| `services/response_gate.py` | 형식·크기·출처 검사, 원자 커밋 연결, 커밋 후 공개 |
| `services/approvals.py` | 사람 제어 채널, 재평가, 승인 소비, 원문 사본 폐기 |

일반 실행은 `await GuardService.submit(task_id, ProposedCall)`로 받는다.
Transport 객체와 예약 후 dispatch 메서드는 Guard의 비공개 필드·메서드다.
승인 서비스에는 내부 협력 객체만 실행 권한을 전달한다.
SDK import나 직접 SQLite·파일·URL·프로세스 연산은 추가하지 않았다.
`core/snapshot.py`도 domain·표준 라이브러리만 import하며 해시 함수는 주입받는다.

구성 시 Store·Registry·Catalog·Transport·AccessChecker와 다음 신뢰 입력이 필요하다.

| 입력 | 의미 / 실제 연결 담당 |
| --- | --- |
| `profile_id`, `policy_version`, `operator_id`, `taint_mode` | 검증된 운영 설정. 모델·도구 출력에서 받지 않는다 |
| `request_id_factory`, `approval_id_factory` | C의 ID 공급 함수 |
| `next_call_seq(task_id)` | B6 저장소의 Task별 영속 순번 공급. 재시작·기존 Task도 고려해야 한다 |
| `failure_audit(FailureEvent)` | B6 저장소의 내구성 감사 연결. Task·요청 ID, 단계, reason, 예외 타입만 받는다 |
| `utc_now`, `monotonic` | UTC 감사/DB 만료와 프로세스 내부 deadline |
| `approval_ttl_seconds`, `max_result_bytes`, `access_timeout_seconds` | 승인 만료·결과 크기·접근 검사 제한 설정 |

기존 B1 Store 포트에는 ID·순번·예약 전 예외 감사의 공급 API가 없어서,
당시 B1–B5 구현에서는 B 서비스의 구성 callback으로 분리했다. 저장소·C 보안 모듈을
구현하거나 `ports.py`를 합의된 계약으로 변경하지 않았다. 이후 B6 이관으로 영속 순번·
감사 저장은 B 담당이며 안전 ID 모듈은 C 담당이다. 정식 API와 배치는 A·C와 검토한다.
callback 공급자는 ID·순번 충돌과 감사 내구성을 실제 구현으로 보장해야 한다.

## Guard와 예약 전후 실패

Task 잠금은 상태 조회부터 판정·예약·전송·응답 커밋이 끝날 때까지 유지한다.
ASK는 WAITING_APPROVAL과 메모리 사본을 만든 뒤 반환한다. 사람을 기다리는 동안
잠금을 보유하지 않으며, WAITING 상태의 일반 submit은 거부한다.
다른 Task는 별도 잠금을 사용한다.

정지·결과 불명·종료 Task는 기존 Task에서 RUNNING으로 복구하지 않는다.
Profile·policy_version 불일치는 Task를 정지시키고 해당 서비스의 살아 있는 승인을
무효화한다. 실제 설정 변경 알림·Registry의 일괄 승인 무효화·프로세스 시작 시
기존 승인 정리는 A의 시작 실행 게이트·C Registry·B6 저장소 연결 경로에서도 집행해야 한다.

로컬 전제조건·경계의 DENY는 AGT에 원문을 전달하기 전에 결정한다.
접근 검사 결과는 기존 순수 정책에 합성하며 AGT 허용은 ASK를 완화하지 않는다.
접근 검사 제한 시간 초과는 AGT_ERROR로 거부한다.
검사 중 등록 상태·지문이 바뀌어도 현재 값으로 다시 판정한다.
예약 후 실제 transport 호출 직전에도 Task와 현재 Catalog 요청을 대조한다.

| 실패 위치 | 반환 / 상태 |
| --- | --- |
| 예약 전 해석·판정·예약 | Denied. 원문 없는 오류 메타데이터를 감사 callback에 전달 |
| 예약 전 저장 오류·취소 또는 오류 감사 실패 | 실행하지 않고 global halt로 이후 판정 차단 |
| 예약 후 예상 밖 READ 오류·취소 | Withheld, Task HALTED |
| 예약 후 예상 밖 SEND 오류·취소 | OutcomeUnknown, Task UNKNOWN_OUTCOME |
| 예약 후 격리 기록·오류 감사도 실패 | 미공개·결과 불명 반환, global halt |

넓은 예외 처리는 `fail_closed`와 `after_dispatch` 안에만 추가했다.
CancelledError도 예약 전후 단계에 따라 처리하며 쓰기 재전송 fallback은 없다.
실제로 예약된 후의 오류를 Denied로 되돌리는 경로는 두지 않는다.
오류 문자열·traceback·인자·원문을 UI나 로그로 전달하지 않는다.

## 응답 게이트

`RawResult`를 재검증하고 격리 자료에서 텍스트·structuredContent만 검사한다.
TextContent의 문자열과 structured JSON의 UTF-8 바이트 합이 설정 상한을 넘으면
미공개 처리한다. Image·Audio·EmbeddedResource·ResourceLink·알 수 없는 형식은
지원하지 않는다. 추가 SDK 메타데이터나 링크를 따라가는 처리는 없다.

정상 READ와 본문이 있는 isError READ는 모두 Catalog로 분류한다.
자원형 READ는 structuredContent.resource_id를 기대 ID와 대조하고,
도구 단위 READ는 등록된 빈 경로 규칙만 사용한다.
서버가 본문에 주장하는 label은 신뢰하지 않는다.
미분류 READ는 commit_observation(None)으로 HALTED·Unclassified를 기록하고
Withheld로 반환한다.

commit_observation이 반환하는 TaskState도 검증한다. Task·epoch, 상태·미분류,
high-watermark·last_read_label을 확인하고 Guard가 제공한 이전 상태와 보안 맥락을
대조한다. 잘못된 반환이나 커밋 실패는 STATE_COMMIT_FAILED로 미공개 처리한다.
오류 격리 상태의 저장도 실패하면 global halt로 전달한다.
이는 반환 DTO 검증이며 DB가 실제로 커밋됐다는 독립 증명은 아니다.

바이트가 없는 알려진 READ 전송 실패와 빈 isError READ는 등급을 병합하지 않고
FAILED/RUNNING으로 기록한다. 바이트가 들어온 뒤의 예상 밖 변환 오류는 HALTED다.
SEND 성공은 commit_send_outcome만 호출한다. SEND 오류·불명은 UNKNOWN_OUTCOME으로
처리하며 READ 등급 병합을 호출하지 않는다.

공개 결과 ReleasedResult는 형식·출처 검사와 커밋이 성공한 뒤에만 구성한다.
RawResult·격리 버퍼를 모델·UI에 전달하는 API는 추가하지 않았다.
A transport의 stderr·progress·late 결과 처리와 실제 로그 유출 방지는 A·C 인수 항목이다.

## 승인 사본과 사람 제어

canonical_json은 key 정렬·공백 없는 JSON·UTF-8을 사용한다. float·NaN·순환 값·
비 JSON 값·잘못된 UTF-8은 거부하고 유니코드 NFC 변환을 하지 않는다.
승인 사본은 기술설계서 8.1의 모든 필드를 해시에 포함하고, 동일 ID의 경계 tier/cap,
도구 종류·자원/목적지 설정·기대 자원 ID·Profile도 함께 고정한다.
인자 원문은 사본의 불변 DTO와 정규화 바이트로 메모리에만 보관한다.
Store에는 ApprovalRecord의 해시·승인자·UTC 시간 등 메타데이터만 전달한다.

status·row_revision은 승인 전이에 따라 바뀌므로 사본 해시에서 제외한다.
last_read_label은 effective_label로 고정하며, monotonic deadline은 메모리에만 둔다.
UTC 만료와 monotonic deadline 중 하나라도 만료하면 소비할 수 없다.
재평가 I/O가 끝난 뒤와 소비 직전에도 만료를 다시 검사한다.
소비 ACK 대기 중 만료될 수 있으므로 소비 성공 후에도 실제 transport 호출 직전에
양시계를 검사한다. 이때 이미 예약됐으면 READ HALTED 또는 SEND UNKNOWN_OUTCOME의
예약 후 경계로 처리하고 전송하지 않는다.

`guard.human_control(사전등록 operator_id)`는 A의 인증된 사람 제어 큐에 전달할
HumanControl을 만든다. approve·reject·expire·cancel·emergency_halt를 제공하며,
이 명령은 ToolView나 ProposedCall 필드에 들어가지 않는다.
이 객체 분리는 OS·UI의 운영자 인증 구현을 대신하지 않는다.
A가 모델 실행 큐에 객체를 노출하지 않고 인증된 제어 큐에만 연결해야 한다.

승인 시 같은 Task 잠금 안에서 현재 등록·도구·목적지로 재해석하고 AGT·정책을
재평가한다. ASK와 사본 해시가 모두 같아야 consume_and_reserve를 호출한다.
ALLOW로 바뀐 승인도 무효화한다. 상태·보안 버전·지문·정책·메타데이터 변경,
만료·사본 유실·승인 소비 충돌은 실행으로 이어지지 않는다.
소비 성공 뒤에는 원문 사본을 제거하고, 같은 잠금을 유지한 채 Guard의 예약 후
전송·응답 커밋 경로로 진입한다. 종착 상태의 승인 재전달은 실행하지 않는다.
재시작 후에는 DB 해시만으로 원문·실행 권한을 복원하지 않는다.

거절·만료 후의 Task 재개는 현재 WAITING 상태와 사본의 보안 맥락이 같을 때만
가능하다. 취소·긴급 차단은 Task를 정지시키며 정지/결과 불명 Task를 재개하지 않는다.

## Store 초안에서 확인한 연동 조건

현재 포트는 Task WAITING 전이와 승인 생성을 별도 메서드로 둔다.
ApprovalService.open은 WAITING 반환 상태를 먼저 저장하고 그 보안 버전으로 사본을
만든 뒤 승인·요청 WAITING을 생성한다. 중간 실패는 정상 Pending으로 반환하지 않고
global halt로 처리한다. Task 전이와 승인 생성의 원자 묶음 API는 B가 변경안을 제공하고
A·C와 검토할 대상이다. [C Store 요구](tasks/c-store-requirements.md)와
[처리 제안 v1](tasks/c-store-proposal.md)은 검토·독립 인수 기준이며 API 확정 기록이 아니다.

WAITING 전이의 security_version 증가 여부가 B1 초안에서 미확정이라 실제 반환
상태를 사본에 고정한다. 소비 후 WAITING→RUNNING은 버전 유지 또는 1 증가를
허용하되 나머지 사본의 보안 맥락은 그대로여야 한다. 설계 SQL은 소비 시
row_revision만 증가시키므로 최종 A·B·C Store 계약에서는 한 의미로 확정해야 한다.

consume_and_reserve의 True는 승인·요청·Task·감사가 한 트랜잭션으로 내구성 커밋된
뒤 반환되어야 한다. False와 저장 오류는 전송으로 이어지지 않는다.
B의 단위 대역에서 소비·호출 횟수를 세어도 실제 DB 원자성·wire·수신 부작용을
검증한 것으로 취급하지 않는다.
Store는 큐·I/O 대기 후 실제 트랜잭션 실행 시점의 UTC 만료를 검사해야 한다.
reserve/consume의 내구성 커밋과 취소·ACK 순서도 C의 계약 검증 대상이다.
호출자가 공급한 과거 now_utc만으로 대기 이후의 만료를 통과시키면 안 된다.

## 검증과 인수

담당 단위·성질 검증은 T04·T05·T06·T13·T25·T38, T09–T12·T20·T29·T35–T37의
서비스 의미 부분과 I1–I9의 해당 반환·호출 순서를 다룬다.
서비스 경쟁 테스트도 메모리 대역을 사용하며 C의 통합 테스트를 대신하지 않는다.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/unit -q
& '.\.venv\Scripts\python.exe' -m ruff check .
& '.\.venv\Scripts\python.exe' -m ruff format --check .
& '.\.venv\Scripts\python.exe' -m mypy
& '.\.venv\Scripts\python.exe' -m pip check
```

2026-10-11 최종 검증 결과:

- 전체 단위·성질 테스트 **837개 통과**. 기존 B1–B3 521개와 이번 추가 316개다.
- 추가 검증: 해시·스냅샷 101개, 예외 경계 15개, 응답 게이트 84개,
  Guard 44개, 승인 72개.
- Ruff 검사 통과, 포맷 검사 41개 파일 통과, mypy 소스 24개 파일 통과.
- 오프라인 editable 설치와 설치된 GuardService import 성공, pip check 통과.
- Python 3.12.15 가상환경의 33개 패키지가 requirements.lock과 일치한다.
- 독립 검토에서 찾은 커밋 반환 검증·잠금 대기 취소·목적지 제거 후 고아 상태·
  소비 ACK 타입·소비 대기 중 만료를 실패 테스트로 재현하고 보완했다.
- 원본 technical-design.md는 변경하지 않았다. 실제 A·C 연결·SQLite·wire·
  receiver_effect·GUI·CI 검증은 실행하지 않았다.

남은 인수는 A의 transport·AGT·Runner·제어 큐 연결, C의 안전 ID·Registry·Scanner·
안전 경로, B6의 SQLite·영속 순번·오류 감사·복구 구현, A·C의 공통 계약 합의와 담당 리뷰다.
C가 실제 승인 소비·host dispatch·wire tools/call·receiver effect 계수와
T07·T19·T36 등의 결함·유출 시나리오를 검증해야 한다.

2026-10-12 인수 준비에서는 긴급 차단 시 Store가 반환한 Task ID를 상태 변경 전에
확인하도록 보완했다. 다른 Task의 RUNNING·HALTED·CLOSED 반환을 거부하는 단위 테스트
3개를 추가했으며 전체 **840개 통과**, Ruff·mypy·pip check 통과를 확인했다.
연결 위치·구성 예시·최신 검증 결과와 남은 합의는 [B 인수 안내](b-handoff.md)에 정리했다.

## 승인 식별자 후속 수정과 추가 계약 요청

2026-10-12 `f3c2d2b`에서 승인 조회 직후 Task ID·snapshot epoch를 먼저 검사하고,
승인에 따른 Task 정지 직전에도 기대 epoch를 대조하도록 보강했다. 조회가 다른 Task나
교체된 epoch를 반환하면 해당 Task 행을 변경하지 않고 승인 종결·사본 폐기·global halt로
처리한다. 일반 submit의 trusted caller 기대 epoch 요청은 이 승인 수정과 별도이며 미합의다.

24개 추가 회귀 케이스를 수정 전에 실패로 재현했다. 후속 수정 후 B의 전체 단위·성질
테스트는 **864개 통과**했고 Ruff·mypy·pip check를 확인했다. C도 `f3c2d2b`의 864개·
Ruff·mypy를 별도 Ubuntu 환경에서 재현했다. 상세 근거는
[C 후속 수정 검토](tasks/c-b-review.md#후속-수정-f3c2d2b-확인)다.
앞의 837·840개 결과는 각 시점의 기록으로 유지한다. 후속 결과도 대역 검증이며 실제
SQLite·wire·receiver_effect의 검증을 뜻하지 않는다. 이번 문서 갱신에서 새 코드 시험은 실행하지 않았다.

A 브랜치 `5598009`에는 transport·AGT mock·모델 어댑터·Runner·bridge·Qt UI 구현이 있고
실제 제품 연결은 남아 있다. A는 일반 submit의 lock 내부 `expected_epoch` 비교,
인증된 UI의 동일 메모리 승인 사본 조회, 권위 있는 승인·Task 상태와 revision·종결 여부,
제어 명령 접수/적용 결과를 요청했다. 근거는
[A 계약 검토](https://github.com/hsminu/MCP-Runtime-Guard/blob/55980091fc49871903d72e8ba1e657ab7682035b/docs/tasks/a-port-review.md#7-b-head-f3c2d2b-연동-중-드러난-호출제어-계약-질문)다.
이 요구와 C의 승인 생성·종결 원자성·버전·ACK/커밋 불명·만료·복구 제안을
B6 계약 변경안으로 대조하고, 실제 합의 후 구현·B 단위 검증·C 독립 검증·A 연결 순서로 진행한다.
