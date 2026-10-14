# B·C에게 전달할 계약 요청 초안

아래 두 프롬프트는 **아직 보내지 않은 초안**이다. A03–A06의 실제 결과를 첨부해서 사용한다. 전체 B/C 업무를 배정하는 프롬프트가 아니라 A가 의존하는 계약·검증 자료를 요청하는 프롬프트다.

사용자가 직접 담당 채팅에 붙이거나 명시적으로 전달을 요청했을 때 사용한다. 이 파일을 읽었다는 이유로 채팅을 만들거나 메시지를 보내지 않는다. 받는 담당자는 자신의 작업 디렉터리·역할 지침을 확인하고 기존 변경을 보존한다.

## H-B · B에게 계약 초안과 리뷰 요청

```text
MCP Runtime Guard에서 B 담당으로, A 애플리케이션 구현에 필요한 계약과 리뷰 자료를 준비해줘.
먼저 현재 브랜치·작업 트리와 AGENTS.md, AGENT.md, B 역할 문서, docs/contracts.md, 개발문서 0.4·0.5·2·3·6–10·12장을 확인해. 여러 담당이 작업 중이므로 다른 사람의 변경을 되돌리지 마.

입력: A가 제공한 a-port-review.md, a-ui-contract-proposal.md, a-environment-decisions.md 또는 해당 요구사항. 자료가 없으면 그 사실을 표시하고 기존 계약부터 확인해.

요청:
1. ProposedCall/ResolvedRequest/ToolView/ModelTurn, Guard의 Denied/Pending/Released/Withheld/OutcomeUnknown, Transport/AccessChecker/ModelClient의 실제 메서드·입출력·오류·취소·수명을 제안하거나 현재 합의 코드를 연결해줘.
2. 예약 전 fail-closed, READ 자료 없음과 수신/변환 후 실패, SEND 결과 불명, 승인 원자적 소비와 Task lock, Released 공개 경계의 의미를 명확히 해줘.
3. UI 사람 명령·불변 이벤트·snapshot 조회·페이지/집계·중복/역순 이벤트·고정 모델 응답의 담당과 데이터 공개 범위를 A·C 요구와 대조해줘. 특히 Guard.submit의 Task lock 내부 expected_epoch 검증, APPROVAL_RACE 및 TASK_NOT_RUNNING 반환 뒤 권위 있는 승인/Task 상태 조회, 접수와 적용을 구분하는 명령 결과를 검토해줘.
4. T22의 승인된 모델 환경 시작 조건, 설정/Profile/패키징/lock 담당 미정 항목을 결정 제안으로 분리해줘.
5. A가 리뷰할 Guard/Catalog의 대상 버전과 관련 테스트를 제공하고, A의 transport/AGT/model/Runner를 B가 리뷰할 기준을 제시해줘.

범위: B 소유 domain/ports/Guard/Catalog/정책 경계만 담당하고 A adapter/UI·C storage/Registry/fixture를 대신 구현하지 마. 새 계약은 실제 A·B·C 합의 또는 사용자 결정 없이 확정 기록을 만들지 마.

완료 보고: 현재 확정 코드/계약, 제안과 미확정 항목, 변경 영향, 관련 I/T/S, 실제 검증, A가 바로 구현할 수 있는 부분, 추가 합의 조건을 분리해줘. 도구 인자 원문이나 인증정보는 보고에 넣지 마.
```

## H-C · C에게 플랫폼·fixture·검증 자료 요청

```text
MCP Runtime Guard에서 C 담당으로, A transport·GUI 연결에 필요한 플랫폼 계약과 재현 가능한 검증 자료를 준비해줘.
먼저 현재 브랜치·작업 트리와 AGENTS.md, AGENT.md, C 역할 문서, docs/contracts.md, 개발문서 0.4·0.5·2·9–14장을 확인해. 여러 담당이 작업 중이므로 다른 사람의 변경을 되돌리지 마.

입력: A의 a-platform-review.md, a-ui-contract-proposal.md, 연동 기록 또는 해당 요구사항. 없는 자료/구현은 미제공으로 구분해.

요청:
1. Registry 레코드·등록/검사/활성화/차단·지문 확인/변경 알림·세션 상태 인계, launcher의 검증된 실행 설정/최소 환경, endpoint 검증, 격리/redaction 경로를 실제 API와 연결해줘. MCP SDK callback 밖 root logger에 남는 잘못된 알림 원문까지 앱 전체 공개 logging handler에서 격리하는 방법도 포함해줘.
2. 같은 MCP SDK의 합성 stdio READ·Streamable HTTP SEND fixture/harness를 제공해줘. 실제 외부 서비스 쓰기 없이 실행/초기화/정리할 수 있어야 하고 Guard 우회 기능이 제품에 들어가면 안 돼.
3. 요청별 approval_consumed / host_dispatch / wire_tools_call(=wire_post) / receiver_effect의 책임·관측 지점·ID 연결·프로토콜 메시지 분리·READ 계수 의미를 합의안으로 작성해줘.
4. 정상/ASK/DENY, 승인 중복/변조/만료, 307/308·응답 유실·reconnect·변환/McpError·원시 로그·commit 실패의 기존 계약/통합/공격/결함 주입 테스트와 실행법을 제공해줘. 없는 것은 작성 범위와 의존성을 구체화해줘.
5. 승인 목록/이력/집계/감사 상세의 안전한 조회와 보존 계약, 단일 DB writer 시작/종료 순서, Scanner 의존성/별도 환경 및 T17 결과를 A와 맞춰줘. Task/승인 snapshot revision·terminal 여부, Task ID 재사용/epoch 단조성, 명령 ID와 확정 상태 연결도 제시해줘.
6. A가 쓸 오류 주입 방법·계수 자료 형식·CI 실행법을 제공하고, A bridge/UI/시작·종료를 C가 리뷰할 기준을 제시해줘. C 소유 테스트의 B 리뷰 필요 여부도 기록해줘.

범위: C 소유 구현/fixture/검증을 담당하고 A transport/UI나 B 도메인·포트를 임의로 정의하지 마. 실제 A·B·C 동의가 없으면 공통 계약 합의를 완료로 표시하지 마.

완료 보고: 실제 제공한 API/fixture/검증 명령/버전과 결과, 미실행·관측 불가 경로, A의 선행 작업, B와 합의할 항목을 구분해줘. 화면 표시만으로 전송 차단을 통과 처리하지 마.
```
