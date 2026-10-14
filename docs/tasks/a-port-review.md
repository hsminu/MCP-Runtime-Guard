# A03 · A 관점의 B 도메인·포트·Guard 계약 검토

작성 기준: 2026-10-12, `feat/a-application`의 현재 작업 트리와 B 초안 커밋 `0c02b61` (`origin/feat/b-security-core`)의 읽기 전용 대조. 담당 범위는 A가 구현할 MCP transport, AGT 번역, 모델 클라이언트, Runner, bridge·UI가 B 소유 계약에 요구하는 사항이다. **계약 합의 문서가 아니다.**

## 확인 범위와 판정

- 확인한 자료: `AGENTS.md`, `AGENT.md`, `README.md`, `docs/team-workflow.md`, [공통 계약 체크리스트](../contracts.md), [A 역할 문서](a-application.md), [A03 프롬프트](a-prompts/01-contracts.md#a03), [기술 설계](../technical-design.md) 0.4·0.5·2·3·6.5·7–10·12·14.3장.
- 현재 체크아웃에 `src/guard_app/domain/*`, `src/guard_app/ports.py`, `src/guard_app/services/guard.py` 및 `ToolView`·`ModelTurn` 정의가 없다. B 커밋 `0c02b61`에는 해당 코드와 구현 인수 문서가 있다. 이 문서는 브랜치를 전환하지 않고 `git show 0c02b61:<경로>`로 읽었다. 양쪽 `docs/contracts.md`의 합의 표는 비어 있다.
- 아래 1–5절은 A 요구사항과 최초 질문이다. **B 초안에서 확인한 답·차이·잔여 질문은 6절이 우선한다.** `미확정`은 타입이 존재하지 않는다는 뜻이 아니라 A·B·C가 실제 계약으로 합의하지 않았다는 뜻이다. `합의 확인` 항목은 현재 0개다.
- 문서·코드 대조만 수행했다. 런타임 테스트, 수신 계수 측정, B·C 리뷰와 합의는 미실행이다. A03의 **A 측 초안 검토 완료**, 공동 계약 확정은 대기 상태다.

## 1. A가 소비하거나 구현할 계약

설계의 코드 블록은 참고안이다. 아래 `입력/출력`은 확정 시그니처가 아니라 A가 구현을 시작하려면 B의 타입에 표현돼야 하는 의미다. 특히 원시 MCP 결과·서버 로그는 이 표의 어떤 UI·모델 출력에도 직접 들어갈 수 없다.

| 대상·소유 | 입력과 신뢰 출처 | 필요한 출력 | 오류·취소·수명 요구 | 판정 / B에게 요청할 결정 |
| --- | --- | --- | --- | --- |
| `ProposedCall` · B 모델, A 변환 | 모델이 제안한 `tool_ref`, `arguments`, `model_call_id`만. 전부 비신뢰. `task_id`는 별도 backend 실행 문맥에서 전달 | frozen·strict·`extra="forbid"` 검증 결과 또는 명시적 검증 실패 | 미지 필드, `label`, `task_id`, `approver_id`, `boundary` 등이 들어오면 제출 전 거부. 모델 ID는 중복 탐지 참고일 뿐 권한 근거가 아님. 잘못된 제안의 후속 Runner 처리(안전한 고정 응답/Task 상태) 필요 | **미확정** · 실제 필드 타입, JSON 값 범위, 검증 오류의 Guard 진입 전후 책임을 제시해 달라. 설계 10.4절의 “버려진다(extra forbid로 실패)”는 조용히 제거와 검증 거부가 충돌하므로 **거부**를 명시해 달라. |
| `ResolvedRequest` · B 모델/Catalog | Guard가 생성한 request/task ID, epoch·순번, 등록된 `ToolSpec`·지문, 스키마 검증 인자, 신뢰 등록의 서버·목적지 경계, READ의 expected resource ID | 정책·AGT·transport에 전달할 불변 객체. SEND는 등록 목적지 필수, READ 출처는 Guard 계산 | 해석 실패·미등록·도구 종류 OTHER·스키마 실패·목적지 불명은 예약 전 DENY. 모델·SDK가 주장한 등급·신뢰·경계로 채우지 않음 | **미확정** · 실제 생성/소유 API, 인자의 불변 복사와 민감값 수명, `Transport.call`에 전달할 최소 정보/인증 핸들 형태를 정해 달라. |
| `ToolView` · B 계약, A Runner 사용 | C Registry의 ACTIVE 저장 레코드와 B Catalog의 고정 네임스페이스·등록 스키마·관리자 검토 설명 | 모델에 공개 가능한 불변 이름/스키마/설명 목록 | 지문 변경·PENDING/차단 시 다음 모델 단계에서 제외. 제어 도구는 목록에 없음. 이미 제안된 호출은 Guard가 다시 판정 | **미확정** · ToolView 타입, 생성·조회 책임(B Catalog/C Registry/A Runner), 목록 리비전과 갱신 이벤트를 정해 달라. A가 서버 `tools/list` 원본을 직접 모델에 넘기는 계약은 허용할 수 없다. |
| `ModelTurn` / `ModelClient.step` · B 포트/A 구현 | 해당 Task만의 대화·캐시/제공자 핸들, ACTIVE ToolView 목록. 모델 출력은 비신뢰 | 텍스트/도구 제안의 타입이 분리된 turn. 다중 tool call의 순서·ID가 보존되어 Runner가 `ProposedCall`로 엄격 변환 가능해야 함 | 제공자 오류·시간초과·취소를 원시 예외 메시지 없이 표현. 새 Task에서 대화·캐시·클라이언트의 Task 관련 세션 상태 분리. 취소 후 늦은 turn을 현재 Task에 반영하지 않음 | **미확정** · `Conversation`·`ModelTurn` 실제 구조, 여러 제안의 순서, 부분 스트림 공개/취소 방식, Task별 클라이언트 생성·폐기 API를 제시해 달라. |
| `Transport.call` · B 포트/A 구현 | Guard가 예약한 `ResolvedRequest` 또는 동등한 dispatch 전용 명령. C 등록 설정으로 열어 검증된 서버 세션만 사용 | SDK 원시 결과를 UI·모델에 공개하지 않는 `RawResult`/격리 핸들, 또는 민감 메시지를 제거한 분류 가능한 예외. 호출별 future는 반드시 성공/예외로 확정 | 예약 이후 전송 여부·바이트 수신 여부를 구분할 수 있어야 함. SEND timeout·끊김·재연결·취소 경합에 자동 retry/redirect/replay 금지. 변환 실패/McpError가 worker 루프를 죽이면 안 됨. 서버별 장수 task가 세션 진입·사용·종료 소유. 종료 시 대기 명령의 future도 확정 | **수정 제안** · 설계 9.2절 `RawResult.timeout(sent=True)`만으로는 “READ 자료 미수신”과 “일부 수신 후 실패”를 구분하기 어렵다. `received_any`/단계 또는 동등한 보수적 신호, 호출별 오류형, 큐 대기 중 취소/종료 결과를 정해 달라. |
| `AccessChecker.check_access` · B 포트/A 구현 | B가 구성한 `ResolvedRequest`와 `TrustedContext`. AGT 입력에는 필요한 최소 비밀만 전달하며 AGT는 dispatch 권한을 갖지 않음 | `AccessResult(allowed, reason, error)` 의미. `allowed=True, error=False`도 Guard의 ASK/DENY를 완화하지 않음 | 거절은 `allowed=False`; 예외·시간초과·형식 불명은 `error=True`로 DENY. 호출 취소·shutdown은 Guard가 기다리지 않도록 제한시간 필요. 모의/실제 모드 표식은 UI·감사에서 구분 | **미확정** · `TrustedContext` 구성·신뢰 출처, reason의 안전한 형식/길이, timeout 소유 및 AGT 실패가 `AccessResult`가 되는 정확한 경계를 정해 달라. |
| `GuardService.submit` · B 서비스/A Runner 소비 | backend가 생성·유효성 확인한 Task ID와 `ProposedCall`. Runner는 transport 접근 권한 없음 | `Denied / Pending / Released / Withheld / OutcomeUnknown` 중 하나 | 한 Task의 판정·dispatch·응답 커밋 직렬화. 승인 대기 중 일반 submit 거절. 호출자 취소는 이미 예약된 SEND를 “차단”으로 바꾸지 않음. 요청마다 고유 backend ID를 반환할 필요 | **미확정** · 실제 인자·반환 타입, 요청 ID가 해석 실패 때 없을 수 있는 경우, `submit` coroutine 취소와 backend StopRequest의 구분을 제시해 달라. |
| 사람 제어 API · B 승인/Guard, A bridge 소비 | 등록된 운영자 신원과 제어 큐의 승인·거절·취소·긴급 차단. 모델/도구 결과에서 도달 불가 | “접수됨”과 재평가·소비·적용 후 확정 상태를 구별하는 안전한 결과/이벤트 | 승인 대기에는 Task lock 미보유. 승인 후 재평가→소비→dispatch→응답 커밋은 동일 lock. 더블클릭/재전달은 1회 이하 소비·dispatch. 정책·지문·상태 변화와 재시작은 승인 무효 | **미확정** · API별 타입/오류/중복 결과, 운영자 신원 공급자, 제어 큐 ack와 적용 결과, stop/긴급 차단의 전송 경합 의미를 정해 달라. |
| 승인 사본 조회 · B 승인, A UI 소비 | 승인 ID와 인증된 로컬 운영자 문맥. 실행에 사용할 **같은 메모리 사본** | 수신처·전체 인자·기호가 보이는 승인 전용 평문 view와 만료/무효화 상태 | 원문 인자는 DB·일반 로그·감사·CSV에 없음. 사본 분실/재시작/만료 시 다시 조회하거나 승인할 수 없음. 표시용 치환은 실제 전송 바이트·해시를 바꾸지 않음 | **미확정** · UI가 일회성 view를 얻는 서비스 API, 메모리 사본 해제 시점, 열람 시 상태 재검사, 사본 누락 응답을 정해 달라. |

### 신뢰·상태 경계에서 확인할 사항

1. **Task**는 backend가 생성하고 상태에 묶는다. Runner의 모델 출력 속 Task ID나 도구 결과 속 ID로 선택하지 않는다. 새 Task는 `TaskState`와 대화·캐시·제공자 핸들·Task 관련 MCP 세션 상태를 함께 분리한다(6.5절, T21).
2. **승인자**는 사람 전용 제어 경로의 사전 등록 운영자 문맥에서 나온다. 버튼 payload나 모델의 `approver_id` 문자열만으로 신원을 확정하지 않는다. 로컬 단일 운영자는 설계상의 전제이지 인증 구현 완료 증거가 아니다(8.5절).
3. **도구 종류·등급·경계·목적지·지문**은 Catalog와 C의 저장 등록값에서 온다. `stdio`/localhost/도구 annotation/서버가 주장한 `resource_id` 또는 “internal”이라는 이름을 신뢰 근거로 쓰지 않는다. 모든 호출에 서버 경계를, SEND에 목적지 경계를 검사한다(1.3·4장).
4. **호출의 권한과 실행 경로**는 Guard만 연결한다. A Runner·UI·AGT는 `Transport` 참조나 관리 대상 `tools/call` 함수에 접근하지 않는다. 호환성 실험이 필요하면 C와 합의한 격리 fixture/harness를 제품 경로와 분리한다(I1, T08).
5. **결과의 신뢰**는 Guard의 response gate에서만 확정된다. READ는 격리→형식/크기·출처 검사→등급/감사 원자적 커밋→공개 순서다. SEND는 READ 등급 병합을 호출하지 않는다. 서버 stderr·progress·logging·알림도 원시 비신뢰 데이터다(I5·I9).

## 2. Guard 결과의 공개 범위 제안

아래 필드는 **A의 최소 공개 제안**이다. 실제 도메인 필드와 표시용 reason allowlist는 B가 정의하고 A·C가 검토해야 한다. UI의 안전한 감사 상세와 승인 전용 전체 인자 view는 별개다.

| 결과 | 모델에 전달 가능한 것 | UI/일반 이벤트에 전달 가능한 것 | 보내면 안 되는 것 / 상태 의미 | 판정 |
| --- | --- | --- | --- | --- |
| `Denied` | 고정된 짧은 차단 문구와 허용 목록의 공개용 reason_code만. 요청/경계 상세 없음 | backend request ID가 있으면 그 ID, 안전한 reason_code, 확정 상태·시각. 승인 행동 없음 | 인자·원시 예외·서버 응답 없음. 예약 전 실패이면 dispatch 0이어야 함 | **미확정** · reason allowlist와 해석 전 request ID의 nullability 필요 |
| `Pending` | 고정된 “사람 승인 대기” 문구. 모델에 승인 ID나 제어 기능 없음 | approval/request ID, 상태·만료 시각·요약. 전체 인자는 별도 메모리 view에서만 | ASK 자체는 전송·승인 소비 0. 승인 전 모델의 다음 일반 submit은 거절 | **미확정** · pending turn 처리와 UI 조회 타입 필요 |
| `Released` | gate가 커밋 후 내보낸 허용 텍스트/구조화 결과만 | 같은 공개 결과와 확정 request/Task 상태. 표시용 크기 제한 필요 | `RawResult`, 미분류/지원하지 않는 payload, 커밋 전 내용, stderr/로그를 혼입하지 않음 | **미확정** · READ label 표시와 SEND 응답의 공개 포맷 필요 |
| `Withheld` | 고정된 “결과를 제공할 수 없음” 문구 | 안전한 reason_code, Task가 HALTED인지, 결과 미공개 사실 | 원시 결과·부분 본문 없음. 미분류/형식 불명/READ post-dispatch 예외/커밋 실패는 미공개 | **미확정** · `Withheld`의 Task 상태·사유별 계약 필요 |
| `OutcomeUnknown` | 고정된 “실행 결과 확인 필요” 문구. 재시도 유도 금지 | SEND 요청 ID와 UNKNOWN_OUTCOME, 수신측 확인 필요, 자동 재전송 금지 | 성공/차단/재시도 가능으로 표시하지 않음. 원시 응답·예외 원문 없음 | **미확정** · 수신측 확인 후 종료 명령/이력 표현 필요 |

공개용 `reason_code`는 원시 AGT reason·예외 문자열·서버 텍스트를 그대로 쓰지 않는다는 의미다. 감사 상세도 문서 원문, 인증정보, 전체 인자, 원시 예외 메시지는 갖지 않아야 한다(I9). 승인 대상의 전체 인자는 위 `Pending` 일반 이벤트에 싣지 않고 운영자 전용 조회로 제한한다.

## 3. 예약·dispatch·응답 오류의 상태 구분

| 경계/상황 | 설계에서 요구하는 의미 | A transport/Runner가 알아야 할 것 | 판정 / B·C 확인 질문 |
| --- | --- | --- | --- |
| `DISPATCHING` 예약 이전: 제안 검증·Catalog·정책·AGT·Store 예약 실패 | `Denied`, dispatch 0. 판단 불가도 fail-closed | A는 Guard 결과만 소비. AGT 오류를 `AccessResult.error=True`로 변환 | **미확정** · 예약 트랜잭션 성공 여부와 안전한 reason_code는 누가 반환하나? C의 수신 계수로 0을 어떻게 연결하나? |
| 예약 이후 READ, 결과 **미수신이 확실한** transport timeout/끊김 | 실패 통보, 등급 변화 없음, Task RUNNING 유지(6.2·9.2절) | raw failure가 “자료 바이트 없음”을 증명해야 함. 늦은 결과는 폐기·감사만 | **수정 제안** · 일부 수신 후 timeout, SDK 변환 이전 수신 여부 불명인 경우도 단순 실패로 두면 I5가 깨질 수 있다. 보수적으로 미공개·HALTED 처리할 판별 신호를 정해 달라. |
| 예약 이후 READ, 수신/형식 확인·SDK→RawResult 변환 중 예상하지 못한 예외 또는 출처/형식 불명 | `Withheld`, HALTED, 원시 결과 미공개(7.1·T36). `resource_id` 불일치도 미분류 정지 | worker는 future를 예외로 확정하고 살아남음. Runner는 부분 텍스트를 보지 않음 | **미확정** · `RawResult.failed`와 post-dispatch 예외를 구분하는 타입/단계, 유실된 부분 데이터의 격리·폐기 책임 필요 |
| 예약 이후 SEND timeout·끊김·취소 경합·reconnect·변환/McpError 등 결과 불명 | `OutcomeUnknown`, Task UNKNOWN_OUTCOME, 자동 재전송 없음. 새 모델 호출 ID도 거절 | worker가 retry/redirect/인증 replay를 끄고 결과 불명 신호를 Guard에 전달 | **미확정** · 성공 응답 수신과 커밋 완료가 확인되기 전의 예외/취소는 무엇으로 분류하나? `StopRequest`가 늦게 도착하면 어떤 확정 이벤트를 내나? |
| 정상 READ/SEND 응답을 받았으나 분류·상태/감사 커밋 실패 | 공개 0, 안전한 Task 정지/전역 정지. 수신된 SEND를 DENY나 미실행으로 보고하지 않음 | Runner/UI에는 gate의 확정 결과만. 원시 응답은 노출 금지 | **수정 제안** · 6.3절의 공통 `_halt`와 7.1절의 SEND `OutcomeUnknown` 사이에서 **SEND 커밋 실패**의 최종 타입·Task 상태를 B가 하나로 확정해 달라. 보수적 제안은 `OutcomeUnknown`; 정지 기록도 실패하면 global halt 필요. |

취소는 “사용자가 중지를 요청했다”와 “실제 전송 전에 취소가 확정됐다”를 별개로 표현해야 한다. 이미 예약되거나 진행 중인 SEND의 coroutine/future가 취소됐다는 이유만으로 전송 0 또는 `Denied`를 표시하면 T14–T16과 I8을 검증할 수 없다. 종료 중 모든 대기 future를 해결하고, Guard가 Task 최종 상태를 결정하도록 해야 한다.

## 4. B·C에게 전달할 구체적인 결정 요청

| ID | 답변 주체 | 답이 필요한 타입·상태·오류 질문 | 영향받는 A 작업 |
| --- | --- | --- | --- |
| B1 | B, A·C 검토 | `ProposedCall`, `ResolvedRequest`, `ToolView`, `ModelTurn`, `Conversation`, `RawResult`, `TrustedContext`, 다섯 Guard 결과의 실제 필드·불변성·nullability·검증 오류는? 특히 모델의 extra 필드를 어떻게 거부하나? | A09–A10, A18–A22, A24–A26 |
| B2 | B, C | `Transport.call`이 받는 예약 증명은 무엇이며 Guard 이외 호출자가 이를 획득할 수 없게 하는 방법은? 전송 시작/바이트 수신/부분 수신/변환 실패를 어떤 결과형과 예외형으로 표현하나? | A09–A16, A21, A39 |
| B3 | B, C | 예약 전 실패, READ 무수신 실패, READ 수신 후 실패, SEND 결과 불명, READ/SEND 커밋 실패의 결과 타입·Task 상태·감사·global halt 조건은? | A10·A13·A21·A36·A39 |
| B4 | B | `AccessChecker`의 trusted context, 제한시간, `AccessResult.error` 번역과 AGT reason 공개 allowlist는? AGT 판정이 도구 실행/사람 승인으로 이어지지 않는다는 포트 보장은? | A17–A18, A21 |
| B5 | B, C | ToolView 목록의 유일한 공급자는 누구이며 ACTIVE·등록 지문·고정 스키마/설명·갱신 리비전을 어디서 검증하나? 같은 이름 도구의 네임스페이스 충돌은 어떻게 거부하나? | A15, A19–A21, A30 |
| B6 | B, C | 새 Task 생성/종료와 Task ID의 신뢰 출처, 모델 대화·캐시/핸들, MCP 세션의 Task 관련 상태 분리 계약은? 병렬 ModelTurn 호출은 어떤 순서로 Guard에 제출하나? | A20–A22, A27, A40 |
| B7 | B, C | 승인 사본 전용 조회·해제, 운영자 신원 공급, `Approve/Reject/Cancel/EmergencyBlock`의 접수/적용 응답, 중복 이벤트·만료·재시작/정책·지문 변경 처리 API는? | A25–A26, A33–A36 |
| C1 | C, B | Fake/실 transport의 동일 계약 테스트가 각 오류·취소·종료 경계를 어떻게 주입/관측하나? request ID별 `approval_consumed / host_dispatch / wire_tools_call / receiver_effect`의 자료형과 READ receiver_effect 정의는? | A16, A38–A39 |

위 요청은 기존 [전달용 초안](a-prompts/07-handoffs.md)을 구체화한 A 측 입력이다. 이 문서 작성만으로 B·C에게 메시지를 보냈거나 동의를 얻은 것은 아니다.

## 5. 설계·검증 연결과 재개 조건

| 요구 | 문서 수준에서 확인한 표현 | 실제 인수에 필요한 증거 |
| --- | --- | --- |
| I1 / T08 | Guard `submit`만 관리 호출 입구, UI·Runner의 transport 접근 금지 | B Guard/포트 코드와 C 정적·수신 0 검증 |
| I3 / T09·T12 | 같은 메모리 사본, 해시·버전 재평가, 승인 원자적 소비, 중복 제어 1회 이하 | B 승인 API·C Store 트랜잭션과 request별 소비/dispatch 계수 |
| I5·I6 / T36 | READ 원시 결과 격리·분류·커밋 후 공개, 수신 후 예외는 HALTED·미공개 | B gate/오류형, A worker 단위검증, C 결과 변환/McpError·커밋 결함 주입 |
| I7 / T21 | Task·승인자·등급·경계의 신뢰 출처 분리, 새 Task의 대화·캐시 격리 | B 모델/생성 계약, A Runner 구현, C 격리 통합 검증 |
| I8 / T36 | SEND 결과 불명은 UNKNOWN_OUTCOME, retry 금지 | A 실제 transport와 C wire/receiver 계수·오류 주입 |
| I4·I5 / T37 | 승인 재평가부터 READ 응답 커밋까지 같은 Task lock | B Guard/승인 코드와 C 동시 submit 검증 |

재개 조건: B의 `0c02b61` 초안은 6절에서 대조했다. B·C가 6절의 차이와 저장·Registry·계수 연계 질문에 답하고 A·B·C가 실제 계약을 확인한 뒤 `docs/contracts.md`에 합의를 기록한다. 그 전에는 A02의 환경/API 조사와 C의 격리 fixture 요구처럼 독립적인 작업을 진행할 수 있으나, 이 문서를 근거로 미합의 제품 포트를 구현하지 않는다.

## 6. B 커밋 `0c02b61` 실제 초안 대조

다음의 `확인`은 **해당 커밋에 코드가 존재한다**는 판정이다. 합의나 A 제품 연결의 완료 판정이 아니다. 표의 경로·줄 번호는 모두 커밋 `0c02b61` 기준이며 `git show`로 읽었다. B의 `0c02b61:docs/b-handoff.md`도 A·C 동의와 실제 MCP·SQLite 검증을 완료하지 않았다고 명시한다. 현재 A 체크아웃에는 B 소스가 없으므로 이 검토에서 import·테스트는 실행하지 않았다.

| A03 대상 | B 초안에서 확인한 실제 계약 | 판정과 남은 A·B·C 결정 | 코드 근거 |
| --- | --- | --- | --- |
| 모델 제안·해석 | `ProposedCall(tool_ref, arguments, model_call_id)`는 frozen/strict/extra forbid인 `Frozen`을 상속한다. `ResolvedRequest`는 Guard가 준 request/task ID, epoch/순번, 등록 ToolSpec, 인자, resource ID, 서버·목적지 경계를 담는다. `Catalog.resolve`가 등록 상태·지문·인자 스키마·목적지/자원을 검사한다. | **초안 확인 / 미합의.** A19는 제공자 출력의 추가 필드를 `ProposedCall` 검증 오류로 거부할 수 있다. `ResolvedRequest`에는 예약 증명 토큰이 없어 `Transport.call`의 호출 자격은 Guard가 객체 참조를 비공개로 보유하고 연결하는 구성에 의존한다. A·B·C가 transport 인스턴스의 공급·노출 범위와 C의 수신 계수로 I1을 확정해야 한다. | `src/guard_app/domain/models.py:55–87,90–136`; `src/guard_app/services/catalog.py:76–103,168–187`; `src/guard_app/services/guard.py:215–231,304–355` |
| Transport·원시 결과·오류 | 포트는 `async call(req: ResolvedRequest) -> RawResult`이다. `RawResult(payload, failed, sent, failure_reason)`에서 `failed=True`는 payload 없는 **확실한 무자료 실패**로 제한된다. 자료가 섞인 `isError`는 수신 결과다. 예상 밖 수신·변환 실패는 원문 없는 `DispatchError(exception_type)`로 전달한다. | **초안 확인 / 부분 충족.** A10·A13은 일부 수신 또는 수신 여부 불명 시 `failed=True`로 포장하지 말고 `DispatchError`로 보내야 B의 READ 정지·SEND 결과 불명이 유지된다. 초안에는 `received_any`·worker 종료/대기 future 확정 API·서버 연결 설정 조회가 없다. A worker의 오류 단계 기록과 C의 Fake/실 계약 시험으로 판별 가능성을 확정해야 한다. | `src/guard_app/ports.py:32–40`; `src/guard_app/domain/contracts.py:28–48`; `src/guard_app/domain/errors.py:24–34`; `src/guard_app/services/response_gate.py:107–115` |
| AGT 판정 | `AccessChecker.check_access(req, ctx) -> AccessResult`이고 `TrustedContext`는 `TaskState`와 사전 등록 `operator_id`를 담는다. `AccessResult`는 `allowed`, `reason`, `error`다. Guard는 로컬 거부를 먼저 판정하고 `asyncio.timeout`으로 AGT를 기다리며, 제한시간 초과를 `error=True`로 바꾼다. | **초안 확인 / 부분 충족.** A18이 AGT 예외·형식 불명을 `error=True`로 번역해야 한다. `reason`은 일반 문자열이므로 UI/감사에 직접 전달하지 않는 공개 규칙과 최대 길이가 필요하다. AGT에 Task 전체와 원문 인자를 주는 현재 포트의 최소 정보 원칙도 A·B가 확인해야 한다. | `src/guard_app/ports.py:43–50`; `src/guard_app/domain/models.py:155–161`; `src/guard_app/domain/contracts.py:101–105`; `src/guard_app/services/guard.py:233–285` |
| 모델 목록·turn | `ModelClient.step(conversation, tuple[ToolView, ...]) -> ModelTurn`; `ModelTurn.calls`는 `tuple[ProposedCall, ...]`이다. `Catalog.tool_views(server_ids)`는 ACTIVE·지문 일치·지원 종류만 반환하고 네임스페이스 이름을 만든다. | **초안 확인 / 수정 제안.** `ToolView` 타입은 존재하지만 Catalog가 모든 `description`을 빈 문자열로 만든다. 등록 시 관리자 검토 설명의 공급/저장 위치를 정해야 한다. 목록 revision·변경 이벤트·Task별 모델 클라이언트 생성/폐기·부분 스트림/취소 API는 없다. A20–A22가 제공자 상태 격리를 구현할 수 있도록 별도 계약이 필요하다. | `src/guard_app/ports.py:65–72`; `src/guard_app/domain/contracts.py:212–234`; `src/guard_app/services/catalog.py:125–152` |
| Guard 입구·결과 | 실제 입구는 `GuardService.submit(task_id, ProposedCall) -> Outcome`; 다섯 결과 DTO의 discriminator·필드가 정의됐다. `Denied.request_id`만 nullable이고, `Pending`은 request/approval ID·reasons, `Released`는 공개 result·label, `Withheld`·`OutcomeUnknown`은 request ID·reasons를 담는다. Guard가 Task lock 안에서 판정·예약·전송·커밋하고 ASK 때 반환한다. | **초안 확인 / UI 계약 미합의.** A21은 결과 종류별 안전한 고정 문구를 만들 수 있으나 이 DTO만으로 Task 최종 상태·발생/만료 시각·이벤트 revision·안전한 상세 조회를 표시할 수 없다. B·C의 조회/이벤트 모델과 공개 reason 문구 규칙이 필요하다. | `src/guard_app/services/guard.py:156–181,193–231`; `src/guard_app/domain/contracts.py:63–98` |
| 결과 게이트와 예약 후 구분 | `ResponseGate`는 TextContent·structured JSON과 크기를 검사하고, READ의 분류/커밋 후에만 `Released`를 만든다. `failed=True` READ는 FAILED/RUNNING, 자료/형식 불명 READ는 WITHHELD/HALTED, SEND 실패는 UNKNOWN_OUTCOME으로 기록한다. SEND 성공은 `commit_send_outcome`을 사용한다. `after_dispatch`는 취소·예상 밖 예외를 READ `Withheld`, SEND `OutcomeUnknown`으로 격리하고, 격리 기록 실패에는 global halt를 건다. | **초안 확인 / 실계약 검증 대기.** 3절에서 질문한 SEND 커밋 실패의 최종 타입은 초안상 `OutcomeUnknown`이다. 이것은 실제 SQLite 내구성·wire 0/1 증거가 아니며 C의 `commit_request_failure`·오류 감사 원자성, A worker의 늦은 결과 폐기와 재전송 금지를 확인해야 한다. | `src/guard_app/services/response_gate.py:66–146,177–226`; `src/guard_app/services/guard.py:287–355,401–437`; `src/guard_app/security/failclosed.py:28–41` |
| 승인 사본·사람 제어 | `human_control(operator_id)`로 받은 `HumanControl`은 `approve`, `reject`, `expire`, `cancel`, `emergency_halt`를 제공한다. 승인 사본은 `ApprovalService._memory_snapshots`에 있고 승인 후 재평가·소비·전송은 Task lock에서 이어진다. `cancel`과 `emergency_halt`는 현재 같은 `_cancel` 구현을 호출한다. | **초안 확인 / 수정 제안.** A34가 요구하는 **같은 메모리 사본의 전체 인자 열람 API가 없다.** 승인 전용 조회의 신원 확인·상태 재검사·수명·누락 응답을 B가 정의해야 한다. A25·A36에는 명령 접수 ack와 적용 결과를 구분할 bridge 계약, 취소/긴급 차단의 전송 경합 표시와 확정 이벤트가 필요하다. OS/UI 운영자 인증은 이 Python 객체만으로 성립하지 않는다. | `src/guard_app/services/guard.py:156–158`; `src/guard_app/services/approvals.py:121–129,211–247,439–453,585–611` |
| Task 생성·승인 저장 | `Store.create_task`는 있지만 Guard에는 Task 생성 API가 없다. 승인 생성과 Task WAITING 전이는 별도 Store 호출이며 B도 원자 묶음을 미합의로 남겼다. `consume_and_reserve`는 bool ACK와 내구성 커밋·만료 확인을 요구한다. | **초안 확인 / 미합의.** A22·A25는 신뢰 Task 생성 주체·호출 순서와 새 Task 대화/캐시/세션 분리를 정해야 한다. C는 WAITING 전이+승인 생성의 원자성, security_version 전이, DB commit ACK·취소/만료를 확정해야 한다. | `src/guard_app/ports.py:98–103,119–135,153–169`; `src/guard_app/services/approvals.py:131–196`; `src/guard_app/domain/models.py:139–153` |

**B·C에게 전달할 남은 결정:** (1) 승인 원문 view와 화면 조회·이벤트 DTO, (2) `ToolView.description`의 신뢰 공급자와 목록 revision, (3) Transport의 허가된 연결/세션 구성과 무자료·부분 수신·종료 오류 관측, (4) Task 생성·제어 ack·취소 경합, (5) C Store의 승인 원자성·보안 버전·commit ACK 및 request별 계수다. 4절의 B1–B7·C1 질문 중 초안에서 답한 시그니처는 위 표로 갱신됐고, 나머지는 공동 합의 전까지 열린 상태다.

## 7. B HEAD `f3c2d2b` 연동 중 드러난 호출·제어 계약 질문

현재 A 작업 트리는 사용자의 B 브랜치 활용 지시에 따라 B 소스 사본을 포함하며 `GuardService`와 `ApprovalService`는 `f3c2d2b`에서 갱신했다. 다음 두 항목은 A Runner/bridge 작업 중 실제 코드와 단위 재현으로 확인한 **추가 계약 요청**이다. B 소유 코드를 이 문서에서 바꾸거나 공동 합의 완료로 표시하지 않는다.

1. `TaskRunner`가 다중 도구 호출을 순서대로 제출할 때 이전 호출과 다음 호출 사이에 같은 `task_id`의 epoch가 바뀔 수 있다. A는 다음 제출 전 Store를 다시 읽어 관측 가능한 교체를 중지하지만, `GuardService.submit(task_id, call)`은 호출자의 기대 epoch를 받지 않아 조회와 Guard 내부 Task lock 획득 사이 경쟁을 원자적으로 닫지 못한다. **B 요청:** `submit`이 trusted caller의 `expected_epoch`(필요하면 incarnation/정책 버전)를 받아 Task lock 내부에서 현재 값과 비교해 불일치를 전송 예약 전에 거절하는 계약을 제시해 달라. C는 Task ID 재사용·epoch 단조 증가와 정확한 `host_dispatch=0`을 검증해야 한다. A의 사전 조회만으로 T21/I7 완전 통과라고 표시하지 않는다.
2. B의 사람 제어 `reject/expire`는 아직 유효한 승인에 대해 `Denied(APPROVAL_RACE)`를 반환할 수 있다. 이 값은 이미 완료된 중복 제어에서도 나타나므로 **종결 여부를 뜻하지 않는다**. A Runner는 이 사유가 오면 대기를 보존하도록 수정했지만, 이후 실제 승인 상태를 단독으로 확정할 수 없다. `cancel/emergency_halt`의 `Denied(TASK_NOT_RUNNING)`도 적용 후에 반환될 수 있어 그 결과형만으로 UI의 적용/거절을 구분하지 못한다. **B·C 요청:** 승인·Task의 권위 있는 조회, revision과 terminal 여부, 제어 명령의 접수/적용 결과 또는 연결 가능한 확정 이벤트 계약을 제공해 달라. A25/A26/A33/A36의 낙관적 완료 표시를 막는 선행 조건이다.

2026-10-12 `origin/feat/b-security-core`를 다시 fetch한 결과 HEAD는 `f3c2d2b` 그대로였다. A의 B 소유 소스 사본은 해당 ref와 파일 해시가 일치한다. B가 추가한 [계약 체크리스트](../contracts.md)·[B1 초안](../b1-contract-draft.md)·[B4/B5 구현 문서](../b4-b5-execution.md)·[인수 안내](../b-handoff.md)를 A 작업 트리에 원문대로 가져왔다. 문서에는 위 `expected_epoch`, 인증 UI snapshot/이벤트, 승인 원문 조회, 도구 목록 revision, 제품 bootstrap의 확정 API가 추가되지 않았고 공통 합의 기록도 비어 있다. 이 복사는 B 제안의 확인을 쉽게 하려는 것이며 A·C의 동의나 계약 동결이 아니다.
