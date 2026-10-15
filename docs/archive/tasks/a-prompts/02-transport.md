# 2단계 · MCP transport

제품 경로는 `src/guard_app/` 기준이다. A09–A16은 같은 모듈을 공유하므로 변경을 겹쳐 실행하지 않는다. [공통 규칙](README.md)을 적용한다.

## A09

### 서버별 worker와 세션 수명 구현

```text
A09를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 서버 하나당 장수 asyncio task 하나가 transport·ClientSession의 진입/사용/종료를 소유하게 구현한다.
선행 조건: A02 API 근거, A03의 합의된 Transport 계약과 실제 B 타입. 계약이 없으면 설계 제안까지만 수행한다.
수정 범위: adapters/mcp_transport.py와 A 소유 단위 테스트. ports/domain을 만들거나 바꾸지 않는다.

작업:
1. 실제 계약에 맞춰 worker 시작·준비 완료·명령 수신·종료의 수명을 구현해. 외부 호출자는 세션이 아닌 명령 큐만 사용하게 해.
2. 세션 context enter/use/exit가 같은 task에서 수행되고, 같은 서버에 중복 세션 소유자가 생기지 않게 해.
3. 준비 실패, 준비 전 명령, 중복 시작, 종료 후 호출의 결과를 계약에 맞춰 명확히 해. Task 보안 상태는 worker가 결정하지 마.
4. SDK 생성부를 교체 가능한 경계로 분리하되 관리 대상 도구가 Guard 밖에서 실행되는 공용 API를 노출하지 마.

완료 기준: 서버별 소유 task가 분명하고 session 객체가 UI/Runner로 새지 않는다. 독립 서버 worker의 수명도 서로 간섭하지 않는다.
검증: 세션 enter/use/exit task identity, 중복 시작 방지, 초기화 실패, 정상 종료를 의미 있는 단위 테스트로 확인해. 실제 SDK 수명은 A07/A08 근거와 대조해.
연결: 설계 9.1절, I1·I6, T08의 구조 요구. C의 정적/통합 T08은 별도다.
```

## A10

### 모든 명령 future의 확정·취소·종료 처리

```text
A10을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 성공·오류·취소·종료의 어떤 경로에서도 명령 future가 무기한 남지 않게 한다.
선행 조건: A09, B와 합의된 명령 결과/오류/취소/종료 계약.
수정 범위: adapters/mcp_transport.py와 해당 단위 테스트.

작업:
1. 큐에 들어간 명령별 결과 또는 예외가 정확히 한 번 확정되도록 소유권을 정리해. 취소된 future에 중복 set_result/set_exception을 하지 마.
2. 큐 대기 중 취소와 실행 중 취소를 구분해. 실행 중 SEND의 호출자 취소가 원격 부작용 취소를 보장한다고 처리하지 마.
3. 정상 종료·초기화 실패·세션 단절 때 새 명령 수락, 대기 명령 정리, 진행 명령의 불확실성 전달을 계약대로 구현해.
4. 명령 한 건의 변환 오류/McpError가 worker를 불필요하게 종료시키지 않도록 해. 안전하게 회복 불가능한 세션은 실패 상태로 전환하고 다음 명령을 명시적으로 거절해.

완료 기준: 미확정 future, 중복 확정, 종료 뒤 새 실행, 취소를 성공으로 오인하는 경로가 없다. broad catch는 설계상 허용된 ServerWorker._execute 경계를 넘지 않는다.
검증: 성공 뒤 다음 명령, 명령 오류 뒤 다음 명령, 호출자 취소, 종료와 enqueue 경합, 초기화 실패에 남은 명령, 이미 취소된 future를 단위 테스트로 확인해. 세부 예외/결과 의미는 A13과 맞춰.
연결: 설계 9.1–9.3절, I6·I8, T36의 worker 생존 요구.
```

## A11

### stdio 연결과 C launcher 연동

```text
A11을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 검증된 등록 설정으로만 stdio 프로세스를 시작하고 worker가 세션을 관리하게 한다.
선행 조건: A04/A07/A09/A10, 실제 C launcher와 설정 타입. 미구현 launcher를 A가 복제하지 않는다.
수정 범위: adapters/mcp_transport.py와 해당 단위 테스트. C security/launcher.py와 등록 설정 원본은 소유권 없이 수정하지 않는다.

작업:
1. C 검증 결과에서 실행 파일 절대 경로·인자 배열·허용된 환경만 받아 SDK에 전달해. shell 문자열로 합치거나 부모 환경 전체를 상속하지 마.
2. 최소 환경 전달 후 SDK가 기본 환경을 추가 병합하는지도 설치 코드를 확인해. 미허용 비밀 환경이 자식으로 들어가는지 합성 표지로 확인 가능한 지점을 마련해.
3. stdout은 MCP 프로토콜로 유지하고 stderr는 A14의 격리 경계에 연결할 수 있게 해. 프로세스 시작 실패에 전체 command/env/예외 원문을 노출하지 마.
4. 프로세스·세션의 정상 종료와 시작 도중 실패 정리를 worker 소유 수명에 연결해.

완료 기준: 등록되지 않은 실행 파일이나 인자는 C 검증을 우회하지 못하고, stdio라는 이유로 internal 신뢰를 부여하지 않는다. 환경 이름 허용과 비밀 값 로그 비노출이 분리되어 있다.
검증: C launcher 대역으로 전달값·거절 전 무실행을 확인하고, C fixture로 실제 프로세스 수명·환경 격리를 검증해. C의 S01 경로 검증을 UI 문자열 검사로 대체하지 마.
연결: 설계 9.1·10.1·13장, I7·I9, T19·T27, S01·S04.
```

## A12

### HTTP 전송의 redirect·retry·reconnect 제어

```text
A12를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: Streamable HTTP에서 SEND가 숨은 경로로 재전송되거나 미승인 URL로 전달되지 않게 한다.
선행 조건: A08–A10, C endpoint 검증, 합의된 HTTP Transport 계약과 실제 SDK 지원 근거.
수정 범위: adapters/mcp_transport.py와 단위 테스트. 공격 fixture/통합 테스트는 C에 요청한다.

작업:
1. 검증된 endpoint와 합의된 client factory를 사용해 redirect와 write retry를 명시적으로 끄고, SDK 내부 재시도 계층도 확인해.
2. 307/308 응답, 세션 재수립, 네트워크 오류, 인증 갱신 후 replay 가능성을 구분해. MVP에서 무인증만 검증했다면 인증 경로를 검증된 것으로 열지 마.
3. reconnect가 진행 중인 SEND를 큐에 다시 넣거나 자동 호출하지 않게 하고, 전송 여부가 불명확하면 B가 OutcomeUnknown으로 처리할 정보를 전달해.
4. SDK 제약으로 요구를 보장하지 못하면 해당 경로를 사용 가능으로 선언하지 말고 근거와 계약 변경 제안을 남겨.

완료 기준: 설정만 있는 것이 아니라 C의 wire 계수로 검증 가능한 구조와 실제 검증 결과/미실행 항목이 있다. 새 session/request ID를 만드는 방식으로 불명 SEND를 우회 재전송하지 않는다.
검증: A는 client 설정·재호출 금지 단위 검증을 수행한다. C가 작성한 307/308·응답 유실·reconnect 검증을 실행해 미승인 수신처 0과 추가 tools/call 0을 확인한다. C 자료가 없으면 전체 인수는 대기다.
연결: 설계 9.3절, I2·I8, T14–T16.
```

## A13

### 전송 단계별 오류와 결과 불명 전달

```text
A13을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 전송 전 실패, 자료 없는 READ 실패, dispatch 후 예상하지 못한 오류, SEND 결과 불명을 정확히 전달한다.
선행 조건: A10–A12와 B의 원시 결과/예외 계약. 최종 Denied/Halted/OutcomeUnknown은 B 책임이다.
수정 범위: adapters/mcp_transport.py와 해당 단위 테스트.

작업:
1. 예외가 발생한 단계와 요청 종류, 결과 수신 여부 등 계약상 필요한 증거를 보존해. 원문 결과를 오류 메타데이터로 유출하지 마.
2. 자료를 반환하지 않은 READ 전송 실패와 수신/변환 후 예상하지 못한 READ 오류를 구분해 B의 응답 게이트로 전달해.
3. dispatch 후 SEND timeout·연결 종료·McpError·결과 변환 실패가 '안전하게 차단됨'으로 변환되지 않게 해. 전송 여부를 알 수 없으면 보수적으로 불명 정보를 전달해.
4. 오류 보고에는 합의된 코드/단계/예외 종류만 사용하고 원래 예외 메시지·인자·본문은 기본 로그에 넣지 마. worker의 다음 명령 수명은 A10과 맞춰.

완료 기준: A가 정책 판정이나 등급 병합을 복제하지 않으며, B가 단계별 올바른 결과를 결정할 정보가 보존된다. SEND 응답이 READ 등급 경로로 들어가지 않는다.
검증: 각 단계의 timeout/closed/McpError/conversion error를 단위 테스트로 입력해 계약 결과와 다음 명령 생존을 확인해. 실제 Task 상태·미공개·계수는 C의 T25/T35/T36 관련 검증으로 확인한다.
연결: 설계 6.2·6.4·9.2절, I4·I5·I6·I8·I9, T25·T35·T36.
```

## A14

### stderr·logging·progress·알림 격리

```text
A14를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 도구 결과 외의 서버 출력도 출처 검증·허용 경로를 거치기 전에 공개되지 않게 한다.
선행 조건: A11–A13, 설치 SDK callback 근거, C의 격리 sink/redaction/보존 계약.
수정 범위: adapters/mcp_transport.py와 A 단위 테스트. C redactor나 보안 저장소를 별도로 구현하지 않는다.

작업:
1. stdio stderr, MCP logging/progress/message/notification, SDK 디버그 로그의 모든 유입 경로를 목록화해.
2. 서버 원문을 모델 대화·UI 상태 문자열·기본 logger·예외 문자열로 직접 전달하지 않고 합의된 격리 sink/안전한 메타데이터 경로로 연결해.
3. 알림 유형만 필요해도 원시 payload를 감사 상세에 통째로 넣지 마. 도구 정의 변경 알림은 A15의 Registry 흐름과 연결해.
4. sink 실패·닫힘·대량 출력 처리에서 원문을 fallback 로그로 출력하지 않게 해. 용량/보존/실패 정책이 미정이면 C에 정확한 계약 요구를 남겨.

완료 기준: 모든 출력 경로의 목적지와 공개 조건이 설명된다. 마스킹된 예외라도 미검증 원문을 먼저 기본 로그로 보내는 경로가 없다.
검증: 합성 비밀 표지를 모든 callback/stderr 경로에 주입한 A 단위 검증을 수행해 공개 sink가 받지 않는지 확인해. C의 T19/S04 실제 공격 검증은 별도 실행·기록해.
연결: 설계 9·11.4·13장, I5·I9, T19, S04.
```

## A15

### 도구 정의·지문 확인과 Registry 변경 알림

```text
A15를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 연결/재연결에서 관측한 도구 정의가 등록 정보와 다르면 Registry의 재검토 경로로 넘긴다.
선행 조건: A04/A09, C의 지문 계산·Registry 구현, B의 사용 가능 도구/실행 계약.
수정 범위: adapters/mcp_transport.py와 해당 단위 테스트. C 지문 계산이나 상태 전이 코드를 복제하지 않는다.

작업:
1. 연결 시 tools/list 정의를 C의 검증 경로로 전달하고 name/description/inputSchema/annotations가 지문 대상이라는 실제 계약을 확인해.
2. tool list 변경 알림과 reconnect 시 재확인을 연결해. 지원되지 않는 알림 방식은 지원된다고 가정하지 마.
3. 불일치가 발견되면 서버·관측 세대·안전한 식별 정보를 Registry에 전달하고, 검증되지 않은 도구 호출이 계속 허용되지 않도록 합의된 게이트와 연결해.
4. PENDING 전환·승인 무효화는 C/B가 수행한 확정 이벤트를 사용해. transport는 ACTIVE를 선언하거나 스캔 완료를 추측하지 마.

완료 기준: 일치/불일치/검증 실패 경로가 구별되고, 이전 연결의 늦은 결과가 최신 지문 상태를 덮어쓰지 않는다. ACTIVE 등록 상태와 실제 연결 상태가 별도로 유지된다.
검증: 일치·정의 변경·도구 삭제/추가·검증 오류·늦은 연결 결과를 단위 검증해. 실제 승인 무효/실행 차단은 C의 T10/T17 연계 검증으로 확인해.
연결: 설계 10.1·10.2절, I6·I10, T10·T17.
```

## A16

### 전송 계수 연결과 transport 인수

```text
A16을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: A transport가 C의 계약 검증과 실제 전송 계수에 연결되는지 인수한다.
선행 조건: A11–A15, A04에서 합의한 request ID/계수 계약, C의 Fake·실제 transport 계약 테스트와 계수 fixture, 통합 시 B Guard.
수정 범위: adapters/mcp_transport.py의 A 관측 연결·수정, A 단위 테스트, docs/integration-notes.md. C 테스트/계수 수신기를 대신 만들지 않는다.

작업:
1. 합의된 의미대로 host_dispatch를 worker _execute 진입에서 요청과 연결해 기록하고 프로토콜 유지 메시지와 구별해.
2. approval_consumed는 B/C 승인 저장 경로, wire_tools_call과 receiver_effect는 C 수신 경로에서 가져오도록 연결해. 클라이언트 로그를 wire 계수라고 부르지 마.
3. Fake와 실제 transport에 동일 C 계약 테스트를 실행해 성공·오류·취소·종료·재연결의 차이를 조사하고 A 구현 결함만 수정해.
4. B Guard가 준비됐으면 ALLOW/ASK/DENY의 계수를 맞춰 보고, 없으면 adapter 인수와 제품 통합 미실행을 구분해.

완료 기준: 관측 지점·자료 출처·ID 대응·누락 경로가 문서화되고 C가 재현할 수 있다. 측정 불가 경로가 0으로 기록되지 않는다.
검증: SEND 기준 ALLOW는 0/1/1/1, 승인 전 ASK·DENY는 0/0/0/0, 승인 1회는 1/1/1/1을 실제 해당 시나리오에서 확인해. 승인 소비가 해당하지 않는 harness는 N/A로 남겨. 결과 유실의 추가 전송 0은 A39에서 재확인해.
연결: 설계 9.4·14장, I1·I2·I3·I8, T01–T03·T12·T14–T16·T36.
```
