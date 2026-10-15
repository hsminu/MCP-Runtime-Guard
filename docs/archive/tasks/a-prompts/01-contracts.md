# 1단계 · 현황, 계약, 호환성

각 프롬프트를 독립적으로 복사해 사용할 수 있다. 공통 규칙과 의존성은 [색인](README.md)을 따른다.

## A01

### 현재 구현·잔여 작업·소유권 확정

```text
A01을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: A의 현재 작업 트리를 증거로 조사하고 남은 작업의 실행 순서를 확정한다.
선행 조건: 저장소 접근. 다른 담당의 전체 진행 상태를 추정하지 않는다.
수정 범위: A의 진행 기록 docs/tasks/a-progress.md를 필요할 때 만들고, A 역할 문서의 상태 설명을 실제 확인 범위에서 정정한다. 제품 소스·패키징은 만들지 않는다.

작업:
1. 브랜치, git status, tracked/untracked 파일, 기존 사용자 변경을 확인해 보존할 변경을 구분해.
2. A 역할 체크리스트를 환경 준비, HTML 시안, 실제 제품 구현, 실제 검증, 리뷰로 나누고 각 항목에 근거 파일·검증 기록을 연결해.
3. 계약 합의 기록, 실제 domain/ports, Guard/Catalog, C Registry/fixture의 현재 작업 트리 내 존재 여부를 확인해. 없는 것을 다른 브랜치에서도 없다고 보고하지 마.
4. A01–A42마다 완료 증거, 독립 착수 가능 여부, 필요한 담당·산출물을 기록해. 과거 문구와 실제 상태가 다르면 작성 시점 차이도 적어.

완료 기준: 이미 수행한 시안 작업을 다시 구현 대상으로 잡지 않고, 미구현 제품 경로와 미실행 검증을 분리한 실행 목록이 있다. B·C 동의나 테스트 통과를 추정한 체크 표시가 없다.
검증: 상태표의 각 사실을 실제 파일/출력과 대조한다. 이 작업은 런타임 테스트가 아니며 I/T/S 통과를 선언하지 않는다.
보고: 공통 완료 보고 형식으로 바로 시작할 작업 ID와 구체적인 외부 의존성을 남겨.
```

## A02

### 설치된 개발환경·SDK API 확인

```text
A02를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: transport·Qt 구현에 사용할 현재 환경과 API 근거를 확인한다.
선행 조건: A01의 경로/상태 확인. 과거 설치 기록은 출발점이며 현재 상태 증거가 아니다.
수정 범위: docs/integration-notes.md의 조사 기록. 이 작업에서 패키지 업그레이드나 lock 확정은 하지 않는다.

작업:
1. 실제 사용하는 Python 실행 경로·버전·아키텍처, 가상환경, MCP SDK·httpx·Pydantic·PySide6의 설치 버전과 의존성 충돌 여부를 확인해. 환경변수 값이나 비밀을 출력하지 마.
2. 설치된 코드에서 ClientSession, stdio_client, Streamable HTTP 생성 함수, logging/message/progress callback, errlog, HTTP client factory의 시그니처와 수명 관리 방법을 확인해.
3. redirect, transport retry, 인증 갱신 후 replay, reconnect의 제어 위치를 찾아 지원/미지원/미확인과 코드 근거를 구분해. 함수 이름만 있다는 이유로 동작이 검증됐다고 하지 마.
4. Qt↔asyncio 연결에 필요한 현재 API와 실행 가능한 검사 도구를 확인해. 문서만으로 불명확한 외부 API는 공식 자료를 확인하고 버전·근거를 남겨.

완료 기준: 실제 실행 환경, API 근거, 아직 왕복으로 확인해야 하는 항목, 의존성 충돌 여부가 기록되어 있다. 설치/import 성공과 기능 검증 성공이 구별된다.
검증: 실제 버전·import·의존성 검사와 안전한 시그니처 조회만 실행하고 출력 요약을 기록한다. 네트워크 부작용 검증은 A07/A08/A12/A16으로 넘긴다.
연결: 설계 9·12장/부록 A, I8·I9 지원 여부 조사이며 불변식 통과 판정은 아니다.
```

## A03

### B의 도메인·포트·Guard 계약 검토

```text
A03을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: A 구현에 필요한 B 소유 계약의 누락·충돌·판정 경계를 검토한다.
선행 조건: A01. B 초안/코드가 없으면 A의 요구사항 문서까지 작성하고 실제 계약 검토는 대기로 남긴다.
수정 범위: docs/tasks/a-port-review.md의 A 검토/제안. domain, ports, Guard를 대신 구현하거나 미합의 계약을 확정하지 않는다.

작업:
1. Transport, AccessChecker, ModelClient, ProposedCall/ResolvedRequest, ToolView/ModelTurn, Guard 결과 타입을 실제 초안과 대조해 입력·출력·오류·취소·수명 요구사항 표를 작성해.
2. Denied/Pending/Released/Withheld/OutcomeUnknown 각각의 UI/모델 공개 필드, 예약 전 오류와 dispatch 이후 READ/SEND 오류 차이를 확인해.
3. 신뢰 Task/승인자 출처, extra forbid, 승인 사본 접근 수명, 제어 API, Task별 직렬화, ToolView 생성 책임을 확인해. Guard 외부에서 transport가 필요해지는 계약이면 문제를 지적해.
4. 항목마다 '합의 확인/수정 제안/미확정'을 표시하고 B·C가 답할 수 있는 구체적인 타입·상태·오류 질문을 정리해. 동의는 실제 기록이 있을 때만 참조해.

완료 기준: A가 별도 도메인 모델을 만들 필요 없이 구현할 수 있는 계약과 미확정 경계가 드러난다. 실제 전체 메서드/반환형의 존재와 미정 여부를 구별한다.
검증: 설계 3·6.5·7–10·12장과 대조하고 I1·I3·I5·I6·I7·I8, T08·T09·T12·T21·T36·T37의 요구가 표현 가능한지 확인한다. 테스트 실행으로 오인할 표현을 쓰지 않는다.
보고: A의 검토 완료와 공동 계약 확정 여부를 별도로 보고해.
```

## A04

### C의 Registry·launcher·fixture·계수 계약 검토

```text
A04를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: A transport/GUI가 C의 실제 보안 기반을 사용할 인터페이스와 검증 자료를 정리한다.
선행 조건: A01. C 자료가 없으면 필요한 요구사항과 fixture 인수 조건을 작성한다.
수정 범위: docs/tasks/a-platform-review.md. C의 구현·fixture·테스트는 만들지 않는다.

작업:
1. 서버 레코드의 ID/표시 이름/transport/config/tier/max_label/등록 상태와 연결 상태, 검사 상태, 저장 지문의 출처를 확인해.
2. C launcher의 검증된 실행 파일·인자·최소 환경 전달 방식, endpoint 검증 결과, stderr/로그 격리와 redaction 인계, Registry 지문 불일치 알림 API를 확인해.
3. stdio READ·HTTP SEND 합성 fixture의 실행법·동일 SDK·초기화/정리·request ID 대응·오류 주입 지점을 요청해. 실서버를 우회 호출하는 호환성 시험 경로를 만들지 마.
4. approval_consumed, host_dispatch, wire_tools_call(=wire_post), receiver_effect 각각의 책임·관측 지점·집계 범위를 정리해. initialize/tools/list와 SEND 부작용을 분리하고 READ 계수 의미도 확정 대상으로 적어.
5. DB 단일 writer의 수명/종료 계약과 설정·인증정보 표시의 마스킹 책임을 확인해.

완료 기준: 'A가 전달할 값 → C가 검증/저장할 것 → A가 받을 결과'가 명확하다. 관측할 수 없는 stdio 내부 외부 통신 등은 검증 불가로 구별한다.
검증: 설계 9.4·10.1·10.2·11·13·14.2절, I8·I9·I10, T16·T17·T19·T36, S01·S02·S04 요구와 대조한다.
보고: 즉시 가능한 호환 조사와 C fixture/API가 있어야 가능한 왕복 검증을 구분해.
```

## A05

### UI 명령·이벤트·조회 계약 제안

```text
A05를 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 이미 만든 HTML 시안과 실제 Qt 화면에 필요한 backend 계약을 빈틈없이 제안한다.
선행 조건: A03/A04 요구사항, ui-preview와 사용자 화면 요청. 실제 계약 확정은 B·C와 별도 근거가 필요하다.
수정 범위: docs/tasks/a-ui-contract-proposal.md. 임의의 이벤트를 확정 API로 구현하지 않는다.

작업:
1. 현황·서버 목록/추가/검사·작업 실행·승인 대기/이력·호출 로그/상세에서 필요한 읽기 모델과 사람 명령을 표로 정리해.
2. 각 메시지에 신뢰 주체, ID, 타임스탬프와 시간대, 상태/리비전 순서 근거, 공개 가능한 필드, 에러/누락 표현, 중복/지연 도착 처리 요구를 적어. 설계의 이벤트명은 예시임을 표시해.
3. 승인 대기 누적을 위해 검색·정렬·페이지/커서·총건수·선택 요청 조회·만료/무효화·처리 이력·재시작 시 미복원 요구를 구체화해. 페이지 크기나 오래된 대기 표시는 표시 설정이며 승인 만료 정책이 아님을 명시해.
4. 활성 서버/승인 대기/차단 호출의 집계 기간과 기준, 등록 ACTIVE와 세션 연결의 구분, 확인 항목의 중복 제거·해결·대상 이동을 정의해.
5. 실행 큐와 사람 제어 큐, 요청 접수와 처리 적용의 응답, snapshot/이벤트 재동기화, 감사 상세의 안전한 필드 목록을 제안해.

완료 기준: 화면마다 필드의 공급자·원천·누락 처리·명령 결과가 있다. UI가 DB/transport를 직접 조회하거나 시간을 보고 승인을 만료시키는 요구가 없다.
검증: 사용자 요구를 A28–A36에 매핑하고 I1·I3·I5·I7·I9·I10, T10–T12·T18·T20·T29를 계약 수준에서 대조해.
보고: 구현 가능한 확정 항목과 B·C에게 필요한 결정만 구분해서 남겨.
```

## A06

### 설정·패키징·모델 환경의 결정 항목 정리

```text
A06을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: 구현 도중 임의 선택하기 쉬운 의존성·설정·실제/mock·모델 환경의 담당과 결정 상태를 정리한다.
선행 조건: A02–A05의 조사/제안. 이미 기록된 사용자 결정이나 공동 합의는 재사용한다.
수정 범위: docs/tasks/a-environment-decisions.md의 A 제안. config/pyproject/lock는 이 작업만으로 A 소유가 되지 않는다.

작업:
1. config/Profile 로더, pyproject, lock, 모델 제공자/모드 설정의 현재 편집 담당과 리뷰어를 확인하고 미정 항목을 분리해.
2. A 연결 필드, B 정책 의미, C 위험 입력/비밀 검증 책임을 나눠 설정 요구를 적어. UI 표시용 상태와 보안 정책 값을 섞지 마.
3. AGT 실제 판정 전용/명시적 mock, 모델 실제/대역, Scanner 별도 환경 가능성을 비교하고 결정에 필요한 실제 호환 근거를 적어. 자동 fallback으로 보안 검사가 느슨해지는 설정을 제안하지 마.
4. 모델 제공자, 승인된 모델 환경의 확인 주체/증거, 합성 데이터 시험 범위, Task별 캐시 격리 요구를 적고 T22 시작 차단 계약의 담당을 분명히 해.
5. lock는 stdio/HTTP 왕복과 AGT·Scanner 호환 결과를 근거로 확정하고, 새 환경 재설치 검증을 A41에서 수행하도록 조건을 남겨.

완료 기준: 미정 결정마다 담당·필요 근거·영향받는 작업 ID가 있다. 사용자나 담당자가 결정하지 않은 제공자/버전/권한을 확정했다고 쓰지 않는다.
검증: 설계 6.5·10.3·10.4절/부록 A, T21·T22와 대조한다. 이 문서는 연동 시험 결과가 아니다.
보고: A가 지금 결정할 수 있는 구현 선택과 공동 계약/실제 환경 지정이 필요한 선택을 분리해.
```

## A07

### 동일 SDK로 stdio READ 호환 왕복

```text
A07을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: Python 3.12와 같은 MCP SDK 조합에서 stdio READ의 실제 진입·호출·응답·종료를 검증한다.
선행 조건: A02/A04, C가 제공하거나 합의한 격리 합성 fixture/harness와 launcher 사용 조건. 없으면 요구사항/실행 절차를 준비하고 왕복은 미실행으로 남긴다.
수정 범위: A 소유 호환성 클라이언트가 필요한 경우 합의된 위치에 한정하고 docs/integration-notes.md에 기록한다. C fixture와 제품 Guard 우회 경로는 만들지 않는다.

작업:
1. 클라이언트/서버 실제 SDK·Python 버전, 승인된 합성 resource, 실행 파일·인자·환경 전달 계약을 확인해.
2. 같은 소유 task 안에서 transport와 ClientSession을 열고 initialize, 도구 정의 확인, 합성 READ, 세션 종료를 수행해.
3. 구조화 결과, resource_id, 정상 종료/프로세스 정리, stdout 프로토콜과 stderr 분리를 관측해. 원문 내용을 로그에 남기지 마.
4. 검증 수단이 제공된 범위에서 startup failure·invalid result·timeout의 SDK 표면을 기록하되 최종 Guard 판정 검증으로 부르지 마.

완료 기준: 실제 왕복 재현 명령, 버전, 결과 구조, 수명 관리, 미해결 SDK 제약이 기록된다. import만 성공했거나 대역만 쓴 경우 완료하지 않는다.
검증: C 관측 지표로 요청/응답을 대응하고 프로세스·세션 잔존 여부를 확인한다. receiver_effect를 READ에 어떻게 해석했는지 적는다.
연결: 설계 9·14.2절/부록 A, I1·I5·I9. 실제 Guard I5 검증은 A38/A39에 남겨.
```

## A08

### 동일 SDK로 HTTP SEND 호환 왕복

```text
A08을 실행해줘. docs/tasks/a-prompts/README.md의 공통 실행 규칙을 적용해.

목표: C의 합성 Streamable HTTP 수신 fixture와 같은 SDK로 SEND 1회의 실제 왕복을 확인한다.
선행 조건: A02/A04, C의 격리 fixture/harness·endpoint 검증·wire/부작용 계수. 실제 외부 서비스에 쓰기 요청을 보내지 않는다.
수정 범위: 합의된 A 호환 클라이언트와 docs/integration-notes.md. 제품의 관리 도구 직접 호출 기능이나 C 테스트를 만들지 않는다.

작업:
1. SDK·HTTP client factory·세션·URL 검증 방법을 설치 코드와 대조하고, MVP의 합의된 무인증 fixture 범위에서 실행해.
2. 자동 redirect/retry를 끌 수 있는 설정과 인증 갱신 replay 경로 유무를 확인하고 실제 합성 SEND 1회를 수행해.
3. tools/call 한 건의 wire 계수와 receiver_effect를 확인해. initialize/tools/list 등의 HTTP 요청 수를 SEND 수와 섞지 마.
4. 정상 결과·세션 종료·오류 표면과 아직 시험하지 않은 307/308, 응답 유실, reconnect 항목을 기록해. 미관측 경로를 안전하다고 단정하지 마.

완료 기준: 같은 SDK 조합에서 합성 SEND 1회와 실제 수신 부작용 1회의 근거, 재현 절차, client 설정이 있다. 이 결과만으로 재전송 방지 전체를 통과 처리하지 않는다.
검증: C의 계수/관측 결과와 A의 호출을 요청 식별자로 대응한다. 호환 harness라 승인/Guard 계수가 해당하지 않으면 N/A와 이유를 쓰고 제품 통합 계수로 바꾸지 않는다.
연결: 설계 9.3·9.4·14.2절, I8. T14–T16은 A12/A39에서 실제 공격 조건까지 확인한다.
```
