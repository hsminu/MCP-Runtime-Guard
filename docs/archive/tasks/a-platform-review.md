# A04 · C 플랫폼 계약 검토와 인수 요구사항

2026-10-12 `feat/a-application` 작업 트리 기준 A 측 검토다. [A04 실행 지시](a-prompts/01-contracts.md#a04), [설계](../../technical-design.md) 9.4·10.1·10.2·11·13·14.2절, [공통 계약 체크리스트](../../contracts.md)를 대조했다. **C의 API 제안이나 A·B·C 합의 기록은 아직 확인되지 않았다.** 아래 메서드·필드 이름은 설계의 후보 이름 또는 A의 요청이며 확정 인터페이스가 아니다.

## 확인 범위와 판정

`rg --files .`, `git branch --show-current`, `git status --short`로 현재 체크아웃을 확인했다. 이 작업 트리에는 `src/guard_app/`의 Registry·launcher·endpoint·storage·redact 구현, `fixtures/`, `tests/contract`·`tests/integration`·`tests/attacks`·`tests/faults`와 계수 구현이 없다. 추가로 `git ls-remote origin refs/heads/feat/c-security-platform`에서 원격 HEAD `0f30f25`가 로컬 추적 ref와 같은 것을 확인했고, `git ls-tree`에 C 제품/fixture 소스가 없었다. `docs/contracts.md`의 합의 표도 비어 있다. [연동 기록](../integration-notes.md)의 2026-10-11 설치·import·시그니처 결과는 당시 환경의 조사 기록이며, 이번 작업에서 패키지 설치 상태나 실제 stdio/HTTP 왕복을 다시 실행하지 않았다.

| 대상 | 현재 체크아웃의 제공 상태 | 동작 판정 |
| --- | --- | --- |
| Registry·Scanner·등록/지문 API | 구현 미제공 | 상태 전이·조회·불일치 처리 미검증, 합의 대기 |
| launcher·endpoint·redact·SQLite | 구현 미제공 | 입력 거부·격리·단일 writer 종료 미검증, 합의 대기 |
| 합성 stdio READ·HTTP SEND fixture/harness | 미제공 | 동일 SDK 왕복과 오류 주입 미실행 |
| 요청별 계수와 C 계약·통합·공격 테스트 | 미제공 | `host_dispatch`/wire/수신 효과 검증 불가 |

`미제공`은 **이 체크아웃에서 파일을 찾지 못했다**는 뜻이고, `미검증`은 기대 동작을 실제 실행으로 증명하지 못했다는 뜻이다. 설계 코드 블록이나 UI 시안의 상태값은 구현·실측 증거가 아니다.

## A → C → A 계약 제안

모든 행은 C에게 요청할 **입력·검증·결과**다. B가 정의할 `domain/*`·`ports.py`, 정책 의미, Guard 결과 타입을 이 문서에서 확정하지 않는다. 입력 실패 시에도 원문 인자·환경값·예외 메시지를 화면/기본 로그로 반환하지 않는 오류 코드가 필요하다.

| 경계 | A가 전달할 값 또는 요청 | C가 검증·저장할 것 | A가 받아야 할 결과·상태 |
| --- | --- | --- | --- |
| 서버 등록 | 사람 전용 명령의 `server_id`, 표시 이름, transport별 설정, `tier`, `max_label`, 도구 사용 정의와 인증정보 **참조** | ID 중복/형식, 신뢰 설정의 권한·허용 범위, stdio와 HTTP 설정의 상호 배타성, 안전한 저장; 신규 등록은 `PENDING` | 안정된 서버 ID, 저장된 공개 설정의 마스킹된 조회 모델, 등록 상태와 `row_revision`, 거부 시 안전한 reason code. 표시 이름은 ID와 별개 |
| stdio 실행 설정 | 등록된 절대 실행 파일 경로, 인자 **리스트**, 전달 허용 환경변수 이름과 필요 값의 안전한 참조 | `security.launcher`의 실행 파일·인자 허용 규칙, 최소 환경 구성, 누락된 비밀값 fail closed. 부모 환경 전체 전달 금지 | A transport가 그대로 SDK에 넘길 수 있는 **검증 완료 설정** 또는 거부; 검증 후 설정 변경을 식별할 버전/핸들. 실제 호출/생성 시점과 실패 타입은 C가 명시 |
| HTTP endpoint | 등록 URL과 인증정보 참조 | `security.endpoints`의 scheme/host/port/path 정규화, userinfo·인코딩·IDN 함정 거부/정확한 비교, 허용 endpoint 고정 | 정규화된 endpoint/식별자와 안전한 거부 이유. A는 redirect나 재연결이 새 수신 경계로 바뀌는 것을 묵인하지 않음 |
| 연결·도구 확인 | 세션 연결 시 같은 SDK `tools/list`로 받은 정의의 이름·설명·`inputSchema`·`annotations`, 서버 ID | 등록 레코드의 **저장 지문**과 현재 정의의 정규화 SHA-256 비교; 불일치 시 즉시 `PENDING`, 기존 승인 무효화·재검사 필요 상태를 B 경로에 원자적으로 전파 | 서버별 저장 지문, 현재 관측 지문, 불일치 reason code와 상태/리비전 이벤트. A transport는 직접 `ACTIVE`를 선언하지 않음 |
| 검사·활성화·차단 | 사람 제어의 검사/재검토/차단 명령, 검사 대상 도구 metadata | Scanner에 본문·인증정보 없이 정의만 전달. 필수 분석기 완료·skipped·failed, 위험도, `fingerprint_set` 일치를 검사. 검사 성공 뒤에도 관리자 확인 없이는 `ACTIVE` 금지 | 등록 상태 `PENDING`/`ACTIVE`/`BLOCKED`, 검사 상태와 시각/버전/안전한 요약, 현재 지문과 일치 여부. **세션 연결 상태는 등록/검사 상태와 별도** |
| 공개 조회·GUI | 서버 목록/상세·집계/감사 조회 요청 | Registry/Store 소유의 저장 상태와 권한·마스킹 정책으로 필드 공급. UI가 DB를 직접 읽지 않음 | ID·표시 이름·transport·마스킹된 endpoint/실행 설정·`tier`·`max_label`·등록/세션/검사/지문 상태를 출처·갱신 시각/리비전과 함께 전달. `tier`는 transport/localhost에서 추론하지 않음 |
| 격리·로그 | A transport의 stdio `errlog`, SDK logging/progress/알림, 오류 타입; 세션 ID | C의 격리 저장 위치·수명·접근 통제 및 `security.redact`의 안전한 메타데이터 규칙. 원문을 일반 감사/CSV/UI/모델로 복사하지 않음. MCP SDK가 callback을 거치지 않고 root/named logger에 원문을 기록하므로 앱 전체 공개 handler의 allowlist 정책도 필요 | 격리 핸들 또는 수신 API, 앱 시작 전 공개 logging handler 정책, 마스킹된 상태/오류 코드만. 서버 로그는 신뢰할 수 없는 데이터로 취급 |
| MCP 수신 예산 | A worker가 허용할 구조화 결과·content 블록 수와 B `ResponseGate`의 공개 크기 상한 | C stream factory에서 SDK의 `CallToolResult` 역직렬화 **전** stdio/HTTP 수신 바이트·메시지 수 제한을 적용하고, 초과를 원문 없는 오류로 전달. 전송 이후 초과는 READ 미공개/정지, SEND 결과 불명/재전송 금지로 처리 | 검증된 수신 상한 설정·초과 오류의 dispatch 전후 구분과 합성 대형 결과/빈 블록 다량 fixture. A의 객체 변환 전 검사는 wire 단계 상한을 대체하지 않음 |
| DB 수명 | A backend 시작·부분 실패·종료 신호, 조회/명령의 완료 대기 | 백엔드 스레드 단일 writer가 연결·트랜잭션·WAL/foreign keys/synchronous FULL을 소유; 승인/요청/감사 원자성 유지 | 준비 완료/시작 실패, 안전한 종료 완료·미완료 신호와 호출 큐가 더는 접수하지 않는 시점. Qt 스레드에서 연결을 직접 닫거나 SQL 실행하지 않음 |

설계 `registrations` 예시 스키마에는 표시 이름·transport 설정·세션 상태가 보이지 않는다. **어느 저장 모델/조회 모델이 이를 공급하는지 미확정**이다. `tier`·`max_label`은 등록된 신뢰 계약의 값이어야 하며 서버가 주장한 값, UI 시안 입력, 접속 방식만으로 확정하면 안 된다. 등록 권한, 외부 서버의 `max_label` 상한, 지문 계산의 정규화 방식과 동시 변경 리비전은 B·C와 합의가 필요하다. `status()`·`fingerprint()`는 설계상 이름일 뿐 실제 시그니처·오류 타입은 미제공이다.

## 격리 합성 fixture/harness 인수 조건

C가 제공할 때 A07/A08이 **제품 Guard 우회 경로를 추가하지 않고** 같은 MCP SDK 조합의 호환성만 확인할 수 있어야 한다. A 전용 클라이언트 시험은 C가 합의한 격리 합성 fixture 안에서만 실행한다.

1. **재현 정보:** Python/클라이언트·서버 MCP SDK의 정확한 버전과 잠금 근거, OS별 실행 명령, 경로·임시 디렉터리·무인증 loopback 사용 조건, 초기 계수 0 확인, 시작/준비 확인, 종료/프로세스·파일 정리 명령을 제공한다. 테스트 사이 계수와 데이터가 새 요청에 섞이지 않아야 한다. 현재 기록의 `mcp 1.29.0`은 과거 설치 후보일 뿐 확정 lock이 아니다.
2. **stdio READ:** 등록된 검증 완료 launcher 설정으로 합성 `public/`·`company/`·`secret/` 자료만 읽는다. `initialize` → `tools/list`/지문 확인 → `tools/call` → 구조화 결과의 `resource_id`/요청 경로 대응 → 세션·자식 프로세스 종료를 관측한다. stdout은 프로토콜 전용, stderr/logging/progress는 격리한다. 경로 탈출/심볼릭 링크 거부(S01), 출처 불일치와 변환 실패 주입 지점을 문서화한다.
3. **HTTP SEND:** `127.0.0.1`의 합성 Streamable HTTP 서버에서 `initialize` → `tools/list` → `tools/call` → 종료를 재현한다. 부작용은 fixture 내부의 임시 저장에만 남기며 수신 횟수와 무해한 결과 식별자만 테스트에 제공한다. 307/308, 처리 후 응답 유실, reconnect, SDK/변환/McpError, 기밀 표지 로그를 주입할 수 있어야 한다. 주입 모드는 테스트마다 독립적으로 설정·초기화한다.
4. **요청 대응:** Guard `request_id`/`approval_id`/Task·epoch·`call_seq` ↔ A worker의 dispatch ↔ SDK/JSON-RPC 메시지 ID ↔ fixture 수신·효과를 어떻게 연결할지 C가 안전한 sidecar 또는 harness 규칙을 제안한다. JSON-RPC ID를 Guard의 신뢰 ID로 간주하지 않는다. 하나의 테스트가 여러 세션·동시 요청을 가질 때도 요청별 계수가 구별되어야 한다. 인자·본문·인증값·raw HTTP body는 계수 파일에 저장하지 않는다.
5. **실패 판정:** fixture 시작 불가, 버전 불일치, 계수 파일 누락/파싱 실패, request ID 매핑 불명, 주입 실패는 `0회`나 테스트 통과로 처리하지 않는다. 오류 지점별로 dispatch 전/후와 수신 전/후를 구분해 결과를 남긴다.

위 조건 중 실행법·동일 SDK 서버·계수가 없는 현재 상태에서는 A가 설치된 SDK 시그니처와 lifecycle을 조사할 수 있지만 stdio/HTTP 왕복(A07/A08), 전송·재전송 검증(A11/A12/A16)은 **미실행**이다. 실제 외부 서비스 직접 호출로 빈 fixture를 대신하지 않는다.

## 요청별 계수 계약과 판정

설계 9.4절의 `wire_post`와 14.2절 fixture의 `wire_tools_call`은 **HTTP POST 중 JSON-RPC `tools/call`의 같은 계수**라는 계약 제안이다. C의 계약 테스트로 동일함을 확인해야 한다. 이름이 같아져도 `initialize`, `tools/list`, 알림, HTTP 핸드셰이크는 이 계수에 넣지 않고 `wire_protocol` 등 별도 계수로 기록한다.

| 계수 | 소유·관측 지점 | 요청별 인수 조건 |
| --- | --- | --- |
| `approval_consumed` | B 승인 전이, C Store의 `approvals` `CONSUMED` 영속 전이 | 승인 ID와 request ID, 원자적 소비의 실제 전이 수를 센다. 클릭·승인 버튼 이벤트 수가 아니다. ALLOW에는 0 |
| `host_dispatch` | A `ServerWorker._execute` 진입 | Guard가 예약한 request ID별 진입 수. A가 관측 신호를 내고 C harness가 검증할 형식·중복 처리 방식 합의 필요. enqueue/호출 제안 수가 아니다 |
| `wire_tools_call` = `wire_post` | C HTTP fixture 앞단 ASGI 미들웨어 | HTTP POST body의 JSON-RPC method가 `tools/call`인 수. 수신 서버의 효과 수와 별도이며 원문 body를 저장하지 않음. 파싱 불명은 측정 오류로 별도 표시 |
| `receiver_effect` | C HTTP SEND fixture 도구 내부 | **실제 저장 부작용**을 만든 횟수. 응답을 받았는지와 독립적으로 증가하며 재시작/재전송 때 중복 효과를 검출 |

HTTP SEND의 단일 요청에 대한 기본 기대값은 아래와 같다. `(approval_consumed, host_dispatch, wire_tools_call, receiver_effect)` 순서다. 동시 실행에서는 요청 ID별로 먼저 집계하고 전체 합계를 확인한다.

| 상황 | 기대 계수 | 추가 판정 |
| --- | --- | --- |
| 정상 ALLOW | `(0, 1, 1, 1)` | 등록·정책·Guard 경유 확인 |
| ASK 승인 전/거절/만료, DENY | `(0, 0, 0, 0)` | 승인 대기/화면 문구만으로 0을 대신하지 않음 |
| ASK 승인 1회 또는 더블클릭/이벤트 재전달 | `(1, 1, 1, 1)` | 승인 소비·dispatch·수신 효과 모두 1회 이하가 아닌 **정확히 1회** |
| 승인 후 응답 유실 | 승인 요청이면 `(1, 1, 1, 1)` | 결과는 `UNKNOWN_OUTCOME`, 뒤이은 자동/새 호출 ID 재전송 0. 비승인 ALLOW면 첫 값 0 |
| 전송 전 검증/예약 실패 | `(0, 0, 0, 0)` | 실패 위치가 실제로 dispatch 전임을 별도 관측 |
| 307/308 redirect·reconnect | 단계별로 별도 기록 | **미승인 redirect 대상의** `tools/call`·효과 0, 진행 SEND 재전송 0. 원래 endpoint에 도달한 POST가 있으면 전체 wire를 0으로 쓰지 않음 |

T36의 dispatch 이후 변환 예외/McpError에서는 `host_dispatch=1`이어도 wire와 효과가 주입 위치에 따라 0 또는 1일 수 있다. C는 각 주입점에서 정확한 기대값을 선언하고 READ의 HALTED·미공개, SEND의 UNKNOWN_OUTCOME과 worker 생존을 함께 확인해야 한다. `wire_tools_call`은 HTTP fixture의 관측이다. stdio READ에서는 C가 별도 `stdio_tools_call_received`/`read_invocation` 같은 계수의 정의와 안전한 수집 위치를 합의해야 한다. READ는 저장 부작용이 없으므로 `receiver_effect=1`을 자동 기대하지 않는다. stdio 서버 **내부의 추가 외부 통신**을 fixture가 관측하지 못하면 그 경로는 `검증 불가`다.

## C에게 필요한 결정과 재개 조건

| 질문/산출물 | 필요한 담당 | 재개할 A 작업과 인수 기준 |
| --- | --- | --- |
| 등록·검사·차단·지문 불일치·세션 상태의 실제 명령/조회/이벤트 타입, 공개 필드, 리비전·오류 타입. 등록 설정과 표시 이름의 저장 위치 | C 제안, B의 Registry/도메인 계약 확인, A UI 검토 | A05·A15·A30–A32: UI가 저장 상태를 재현하고 지문 변경 시 `PENDING`·승인 무효가 관측됨(I10, T17) |
| launcher 검증 완료 설정 생성/사용 수명, Windows/Linux 절대경로·인자·최소 환경 규칙; endpoint 정규화·redirect 방어; stderr 격리 핸들 및 SDK/root/httpx 공개 logging handler 정책 | C 구현/테스트, A transport 사용, B 리뷰 | A11–A14: S01·S02·S04, T16·T19의 입력 거부와 비공개를 실제 계수/로그로 확인(I8·I9) |
| stdio/HTTP stream의 SDK 역직렬화 전 수신 상한과 대형 결과/빈 content 블록 다량 주입, 초과 시 요청별 계수·READ 미공개/SEND 결과 불명 판정 | C stream/fixture 구현, A transport 객체 단계 제한, B Guard 결과 검토 | A13·A16·A39: SDK 단계 메모리 상한과 전송 후 오류 의미를 실제 계수로 검증(I5·I8, T36) |
| 동일 SDK 합성 stdio/HTTP fixture의 실행 명령·주입 방법·정리 절차, 요청 ID 대응과 READ 관측 정의 | C fixture/계약 테스트, A 리뷰 | A07·A08·A16: 정상 왕복·초기화/종료·request별 정확한 계수·오류 시 미공개가 재현됨(T16·T36) |
| DB writer 준비/종료·부분 실패 계약, 마스킹된 서버/감사 조회 | C Store, B 포트·원자성 계약, A bridge 검토 | A05·A27·A35: UI 직접 DB 접근 없이 안전한 스냅샷/이벤트와 종료 완료 신호 사용 |

이 문서는 A의 요구사항 작성까지 완료한 것이다. **C 구현 검토, 공통 계약 합의, I8·I9·I10 및 T16·T17·T19·T36/S01·S02·S04 통과는 아직 아니다.** C가 실제 소스·fixture·계수 결과를 제공하면 그 버전과 실행 증거를 대조해 이 판정을 갱신한다.
