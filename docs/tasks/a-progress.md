# A 작업 현황과 재개 조건

기준: 2026-10-12, `feat/a-application`. [A01 실행 프롬프트](a-prompts/01-contracts.md#a01)의 최초 조사와 사용자의 후속 **B 브랜치 활용 결정**에 따른 구현 현황이다. A 브랜치 커밋은 GitHub의 같은 브랜치에 push했다. 커밋 제목의 A ID 범위는 변경을 묶어 추적하기 위한 것으로 해당 범위의 모든 인수 조건 완료를 뜻하지 않는다. 개별 진행 판정은 A01–A42 표를 따른다.

## 로컬 커밋 추적

| A 작업 | 커밋 | 기록한 변경 |
| --- | --- | --- |
| A03 선행 | `7d0a6fb` | B `f3c2d2b`의 보안 코어 초안과 계약 문서 사본 |
| A09·A10·A13·A14 부분 | `b70d8c1` | 서버별 MCP worker, 종료·오류 경계와 단위 테스트 |
| A18 부분 | `e95fd54` | 명시적 AGT mock 판정 adapter |
| A19 | `169b7c2` | provider 중립 모델 도구 제안 검증 |
| A21·A22 부분 | `181677e` | Guard 경유 TaskRunner와 Task 격리 |
| A24·A25 부분 | `4a9b410` | Qt/백엔드 bridge와 bounded 제어 큐 |
| A28–A30 부분 | `ffb97a4` | Qt 현황·서버 읽기 화면 |
| A31 부분 | `07926d5` | 로컬 MCP 등록 초안 폼 |
| A34 부분 | `5dc4b66` | 승인 메모리 사본용 일반 텍스트 상세 |
| A36 부분 | `b026f3a` | 확정 Task 상태의 읽기 화면 |
| A28–A37 표시·검증 | `4096f6b` | Qt 창 구성, 탐색·키보드·표시 회귀 시험 |
| A03 | `afdd0d4` | B 포트 검토와 열린 계약 목록 |
| A04 | `88ceb3a` | C Registry·fixture 요구사항 |
| A05 | `e62fdfc` | UI 명령·snapshot 계약 제안 |
| A06 | `369dbce` | 환경·소유권 결정표 |
| A07·A08 준비 | `1518ec7` | 격리 stdio/HTTP 왕복 실행 준비서 |
| A23 | `9fb36b1` | B Guard·Catalog 소스 검토 |
| A28–A35 시안 | `4a99a34` | 기존 합성 HTML UI 시안의 별도 체크포인트 |
| A02 환경 | `047d1bc` | 기존 Python 버전 설정과 로컬 산출물 ignore |
| A01–A42 계획 | `a18c7ef` | 실행 프롬프트 42개를 담은 문서 묶음 |
| A02–A37 검증 기록 | `e526a70` | 환경·시안·adapter·Qt의 실행 근거와 한계 |
| A42 | `faa5089` | 개별 A 작업·커밋·미완료 조건 현황표 |
| A02 | `4d1fb1a` | 문서의 로컬 절대 경로를 레포 기준으로 일반화 |
| A32 부분 | `fce96e2` | Scanner·Registry·연결 상태 읽기 카드 |
| A33 부분 | `0333032` | 승인 대기/이력의 6건 페이지 조회·선택·오래된 응답 배제 |
| A35 부분 | `b47b322` | 공개 호출 로그·토큰 결속 상세·안전한 CSV 복사 |
| A32·A33·A35 연결 | `054919d` | Qt 창에 읽기 화면과 조회 요청 신호 연결 |
| A33 표시 | `3deba36` | 좁은 창의 승인 핵심 상태 열 표시 |
| A35 표시 | `b11ace9` | 좁은 창의 호출 결과 열 표시 |
| A32 표시 | `ab2f1be` | 서버 상세 상단에 검사 증거 배치 |
| A24 제한시간 | `c267a3f` | bridge 시작·종료 제한시간을 유한한 양수로 검증 |
| A37 키보드 | `475ac2e` | A31 Tab 결함 수정과 A31/A33/A34/A35 Windows Qt 키보드 회귀 시험 |
| A37 배율 | `de022b2` | 125%·150% Qt 배율 검증과 기본 창의 가용 화면 맞춤 |
| A41 보안 검토 | `fd2a699` | 소스 스캔 결과·아키텍처 경계와 ARTEX 실행 조건, B 정규식 발견 기록 |
| A04 수신 계약 | `8c65da7` | C의 MCP SDK 역직렬화 전 수신 상한·초과 계수 요구 |
| A10 결과 예산 | `c0e07b9` | MCP 결과의 복사 전 요소·바이트·중첩 상한 |
| A19 모델 예산 | `54a82ff` | 모델 응답 크기·호출 수 상한과 인자 분리 사본 |
| A37 창·현황 반응형 | `a84a6c4` | 320px 메뉴 탐색, 낮은 창 대응, 긴 ID 줄바꿈, 미해결 항목 우선 |
| A33·A35·A37 목록 반응형 | `e427da1` | 승인·호출 목록의 핵심 열, 필터·버튼 재배치, 공개 승인 요약 표시 보호 |
| A30·A31·A34·A37 상세 반응형 | `58a898a` | 서버 목록·등록 폼·상세 창 크기 적응과 긴 ID 회귀 검사 |

각 커밋의 실제 파일은 `git show --stat <커밋>`으로 확인할 수 있다. HTML 시안과 로컬 환경 설정은 제품 소스와 분리해 커밋했다.

## 확인한 출발점

| 구분 | 현재 작업 트리에서 확인한 근거 | 판정 |
| --- | --- | --- |
| 브랜치와 기존 변경 | 최초 조사 때 `feat/a-application`의 문서·`.gitignore`·`.python-version`·`ui-preview/` 변경을 확인했다. 이후 A 소스·단위 테스트와 B 초안 사본을 현재 트리에 추가했다 | 기존 변경을 보존했다. 브랜치를 바꾸거나 파일을 초기화하지 않았다. |
| 환경 준비 | `docs/integration-notes.md`의 2026-10-11 기록과 2026-10-12 A02 재조사: CPython 3.12.15, `mcp 1.29.0`, `PySide6 6.12.0`, 현재 106개 설치 패키지의 의존성 검사 | 설치·API 근거는 현재 확인했다. 실제 왕복과 AGT/Scanner 호환은 미실행이다. |
| HTML 시안 | `ui-preview/`와 `docs/integration-notes.md`의 2026-10-12 세 화면 기록 | 정보 구조·표시 요구의 시안이다. PySide6 제품 화면과 실제 backend 연결은 없다. |
| 시안 검증 | 기존 기록의 JS 문법·최소 DOM 대역 검사 | 실제 화면 렌더링·키보드·반응형 및 T18 전체 검증은 미실행이다. |
| 공통 계약 | 사용자의 B 활용 결정에 따라 `0c02b61`의 B 소유 `src/guard_app`를 가져왔고, B HEAD `f3c2d2b`에서 바뀐 Guard/approval 두 파일을 최신화했다. 2026-10-12 B ref를 다시 fetch해 같은 HEAD임을 확인하고 B의 `docs/contracts.md` 초안 및 B1–B5·인수 문서를 가져왔다. B 소스 사본의 파일 해시는 ref와 일치한다. 합의 표는 여전히 비어 있다 | A 구현은 이 실제 B 초안을 기준으로 한다. A·B·C 공동 계약 동결로 표시하지 않는다. |
| B·C 제품/검증 소스 | 현재 트리에는 B 소스 사본과 A adapter/Runner/Qt UI·A 단위 테스트가 있다. B 최신 원격 `f3c2d2b`, C 원격 `0f30f25`를 확인했다. C ref는 여전히 문서만 있으며 Registry/launcher/fixture·SQLite 구현은 없다 | B 코드와 A 코드는 단위 경계까지 연결했다. C 구현, 공통 패키징/lock, CI는 확인되지 않았다. |
| 실제 연동 | A 단위 테스트 201건과 Qt offscreen 표시·bridge 커널 시험, 현재 화면의 Windows 네이티브 Qt 렌더링·키보드 시험 및 125%·150% Qt 배율 시뮬레이션은 수행했다. MCP stdio/HTTP 왕복, 실제 AGT·모델·Scanner, C 계수, 앱 전체 구성과 lock 재설치는 미실행이다 | 대역·화면 결과를 실제 전송이나 전체 I/T/S 인수 통과로 취급하지 않는다. |

기술 설계의 I1–I10과 T/S 번호는 인수 목표다. 현재 단위 검사와 화면 검증으로 그 전체 인수 조건을 통과한 것으로 표시하지 않는다.

## A01–A42 실행 판정

`즉시`는 현재 트리만으로 A가 조사·제안을 시작할 수 있다는 뜻이다. `요구사항까지`는 실제 계약 검토나 실행 시험을 완료할 수 없음을 뜻한다. 선행 ID의 제목이 아니라 아래 적힌 실제 산출물을 확인한 후 구현한다.

| ID | 현재 증거·남은 A 산출물 | 착수/재개 조건과 담당 |
| --- | --- | --- |
| A01 | 이 현황표 작성, B/C 원격 HEAD와 해당 커밋 트리를 읽기 전용 확인 | 현황 대조 완료. 새 산출물 도착 시 파일·상태 재확인 후 갱신 |
| A02 | [현재 설치·SDK API 재조사](../integration-notes.md) 완료. HTTP 기본 redirect·stdio 환경 병합 등 정적 제약 확인 | 조사 완료. 실제 동작은 A07/A08/A12/A16에서 재현 필요 |
| A03 | [A 포트 요구와 B `0c02b61` 초안 대조](a-port-review.md) 작성. 최신 B `f3c2d2b` fetch·B 계약/인수 문서 사본과 대조했으나 새 A 연결 계약은 없음 | A 검토 완료. B/C의 계약 결정·합의 기록 필요 |
| A04 | 현재 트리와 원격 C HEAD에 Registry/launcher/fixture·계수 코드 없음. [A 연동 요구](a-platform-review.md) 작성 | A 요구사항 완료. 실제 검토는 C 초안·fixture 계약 필요 |
| A05 | `ui-preview/`를 바탕으로 [UI 명령·이벤트 제안](a-ui-contract-proposal.md) 작성 | A 제안 완료. 확정은 B·C 답변 필요 |
| A06 | [환경 결정표](a-environment-decisions.md) 작성. B `0c02b61`의 B 단독 lock과 A 후보 환경 구분 | A 제안 완료. 설정/패키징 담당·모델 승인 환경 공동 결정 필요 |
| A07 | [stdio READ 실행 준비서](a-compatibility-runbook.md) 작성, 실제 왕복 증거 없음 | C 격리 합성 fixture/harness와 launcher 조건 필요 |
| A08 | [HTTP SEND 실행 준비서](a-compatibility-runbook.md) 작성, 실제 왕복·수신 계수 없음 | C 격리 fixture, endpoint 검증, wire/effect 계수 필요 |
| A09 | [서버별 SDK 세션 소유 worker](../../src/guard_app/adapters/mcp_transport.py)와 A 단위 테스트 구현. 소유 task·초기화·종료 구조 검증 | B 초안 포트 기준 단위 완료. 실제 C launcher/HTTP endpoint와 SDK 왕복은 A11/A12 대기 |
| A10 | 같은 worker에 명령 future 정리, 대기/진행 취소 구분, 시작/호출/종료 timeout·close 처리 구현. 여러 서버 종료 중 다른 서버 신규 호출 수락 경합을 차단했다. 서버별 대기 명령을 기본 64건(설정 상한 1,024건)으로 제한하고 포화는 원격 호출 전에 `WorkerQueueFull`로 거절한다. MCP 결과는 `model_dump` 전 content 256개, 구조 4,096노드·깊이 64, 기본 복사 예산 512 KiB(설정 상한 8 MiB)로 검사한다 | 대역 단위 완료. SDK의 역직렬화 전 wire 상한, 실제 cancellation 및 세션 종료 경합은 C stream/fixture로 인수 필요 |
| A11 | stdio 제품 연결 없음 | A07·A09·A10 및 C launcher 필요 |
| A12 | HTTP 제품 연결·retry/redirect 시험 없음 | A08–A10 및 C endpoint 검증 필요 |
| A13 | `RawResult` JSON 사본과 원문 없는 `DispatchError` 전달 구현. timeout은 무자료 확증이 없어 오류로 보수 처리 | 부분 완료. 실제 stdio/HTTP 단계별 실패와 SEND 결과 불명은 C fixture·B Guard 통합 검증 필요 |
| A14 | 정상 MCP logging/message callback은 폐기하지만, MCP 1.29.0 메모리 스트림의 잘못된 알림 원문이 callback 밖 root logger에 남는 것을 합성 표지로 재현했다 | 부분 경계만 있음. C stderr 격리 파일·앱 전체 공개 logging handler allowlist와 실제 T19/S04 시험 전 stream factory 연결 금지 |
| A15 | 지문 변경 알림 코드 없음 | C Registry/지문 계약, A09 필요 |
| A16 | transport/계수 인수 근거 없음 | A11–A15 및 C 계약·수신 계수 검증 필요 |
| A17 | 사용자가 AGT 배포판을 아직 정하지 않았다고 답했다. 현재 설치 환경에도 AGT 후보 없음 | 실제 판정 전용 API/버전과 C Scanner 조합 자료가 정해져야 실제 호환성 검증 가능 |
| A18 | 명시적 규칙 기반 [mock AccessChecker](../../src/guard_app/adapters/agt.py)와 단위 테스트 구현. 규칙 없음 deny, 신뢰 Task 불일치 error. Qt 상단은 신뢰 bootstrap에서 제공한 실제/mock 모드만 표시하고 기본값은 미설정 | mock·UI 표시 골격 부분 완료. 실제 AGT 번역, 안전 규칙 파일 로딩, bootstrap·감사의 mode 연결과 C dispatch 0 인수 대기 |
| A19 | [provider 중립 제안 변환](../../src/guard_app/adapters/model_client.py) 구현. B Catalog ACTIVE ToolView의 닫힌 schema·이름·인자/추가 필드 검증. 정규화 모델 응답의 텍스트·호출·인자 노드/문자·전체 크기를 파싱 전 검사하고 가변 인자는 제한된 분리 사본으로 검증한다 | B 초안 기반 단위 완료. 실제 제공자 수신 상한·응답/API는 A20 대기. B 등록 스키마 정규식의 동기 매칭 위험은 별도 미해결 |
| A20 | 실제 제공자·모델·허용 데이터/인증 환경이 아직 지정되지 않았다 | 실제 API·환경 승인/egress 검증 대기. 대역을 실모델 결과로 표시하지 않음 |
| A21 | [TaskRunner](../../src/guard_app/services/runner.py)가 제안을 Guard.submit에만 내고 Released만 후속 대화에 삽입. Pending/Denied/Withheld/Unknown 구분. B의 비종결 `APPROVAL_RACE`에서 승인 대기와 후속 호출을 유지하고 확정 결과를 다시 받을 수 있게 수정 | B 결과 계약 기반 단위 완료. 사람 제어 이벤트·권위 있는 승인 재조회, 실제 C Store/전송 통합 대기 |
| A22 | Runner별 대화/호출 ID와 생성 시 `model_client_factory` 분리, 지연 응답/Task epoch·정책 변화 단위 검증. 연속 호출 전 Task epoch를 재조회해 관측된 교체를 막음 | 부분 완료. 조회→Guard 제출 사이 경쟁은 B의 잠금 내부 expected_epoch 검증 필요. 실제 제공자 핸들·캐시 분리와 C Task 생성/통합 T21 인수 대기 |
| A23 | [B Guard/Catalog 리뷰](a-b-review.md)는 `0c02b61`의 당시 A 연결 관점 finding 0건·Catalog 단위 71건 통과, Guard 대상 테스트는 환경 시간초과로 미확인. 후속 B `f3c2d2b`의 승인 Task 결속 diff를 확인·반영했다. 별도 보안 스캔에서 B 스키마 정규식 가용성 발견 1건을 추가 기록했다 | A 관점 소스 검토 완료. B의 정규식 지원 계약 결정, 최신 B 전체 테스트·C Store/fixture 인수와 담당 리뷰는 남음 |
| A24 | [PySide6/asyncio bridge 커널](../../src/guard_app/services/bridge.py) 구현. Qt/백엔드 스레드, 준비/실패/종료 고정 코드, 불변 신호, 비동기 명령 접수·offscreen 시험. 시작·종료 제한시간의 NaN/무한대를 거절해 bounded 대기를 유지한다 | 커널 부분 완료. 실제 앱 시작·Runner/Store/Registry 조립과 C 수명·UI 조회 계약 필요 |
| A25 | bridge에 bounded 실행·일반 사람 제어·긴급 제어 큐, B `HumanControl` capability, 느린 승인 worker가 모두 바쁠 때 긴급 차단 전용 worker, bounded 완료 ID 캐시/포화 거절·미확정 결과 표시 구현 | 큐 기반 부분 완료. B/C 확정 상태 조회·이벤트·내구적 명령 ID/actor 계약과 C T12/T20 검증 필요 |
| A26 | 확정 이벤트·snapshot 재동기화 없음 | A24·A25 및 합의된 이벤트/리비전 계약 필요 |
| A27 | 앱 시작·종료 경로 없음 | A10·A21·A24–A26 및 C DB 수명 계약 필요 |
| A28 | [Qt 공통 UI](../../src/guard_app/ui/window.py), PlainText 상세, 큰 글꼴/등급 배지·빈/오류 상태 구현. 320×360 최소 창에서 폭 또는 높이가 부족하면 메뉴 탐색으로 전환하고 AGT·모델 상태를 별도 줄에 둔다 | 시각 골격 부분 완료. 실제 bridge와 실제 OS DPI·다중 모니터 종합 검증은 A24/A37 대기 |
| A29 | Qt 현황은 핵심 카드 → 연결 서버 → 미해결 확인 항목 → 최근 호출 → 호출 흐름 순서로 구현. 연결 서버는 현재 화면 폭에서 한 줄에 들어가는 카드만 미리 보여주고 목록 이동을 제공한다. 긴 공개 ID가 화면 폭을 늘리지 않게 줄바꿈하며 값 미제공/조회 실패를 0으로 바꾸지 않음 | 읽기 전용 표시 부분 완료. 집계/조회 계약·확정 이벤트·실데이터 연결은 C/B 대기 |
| A30 | Qt 서버 목록·필터·공개 DTO 상세 구현. 작은 폭에서 ID·등록 상태를 남기고 다른 필드는 상세에 보존한다. Registry ACTIVE, 세션 연결, tier, max_label을 분리하고 endpoint 원문은 표시하지 않음 | 읽기 전용 표시 부분 완료. 실제 C Registry·Scanner/지문 조회 계약·연결 대기 |
| A31 | [Qt 로컬 등록 초안 폼](../../src/guard_app/ui/server_add.py)에 방식별 입력·전환 보존·구문 오류·비밀값을 숨긴 미리보기·중복 로컬 확인 방지를 구현했다. 좁은 창에서 필드를 한 열로 배치하고 세로 스크롤로 확인 버튼에 도달한다. 신뢰 경계/최대 수신 등급을 명시적으로 선택해야 로컬 초안을 준비하며 Registry를 호출하거나 PENDING을 만들지 않는다 | 부분 완료. 실제 payload·권한·등록/위험 입력 검증 API 및 C Registry 확정 이벤트 필요 |
| A32 | [Qt 검사 상태 카드](../../src/guard_app/ui/scan_status.py)를 서버 상세 상단에 연결했다. required/completed/skipped/failed 분석기, 지문 미수집·불일치, 검사·Registry·세션 상태를 분리한다. 상세를 열 때 서버 ID와 UI 조회 토큰을 내보내고 같은 창의 응답만 적용한다. 검사 완료를 통과/ACTIVE로 바꾸거나 활성화 버튼을 만들지 않는다 | 읽기 전용 표시 부분 완료. C Scanner/Registry 조회·관리자 제어·확정 이벤트 계약과 실제 검사 검증 필요 |
| A33 | [Qt 승인 대기/이력 목록](../../src/guard_app/ui/approvals.py)에 서버 조회용 6건 페이지 요청, 검색·서버 필터·정렬·총건수·늦은 응답 배제·페이지 축소·선택 소멸을 구현했다. 좁은 창에서는 ID·서버·상태를 바로 보여주고 공개 선택 요약의 제어·bidi 문자를 이스케이프해 상태 줄 사칭을 막는다. 승인/거절 버튼과 승인 원문 캐시는 없다 | 읽기 전용 조회 부분 완료. B 승인 조회·인가·snapshot/이벤트·사본/제어 계약과 C 실제 소비 계수 필요 |
| A34 | [승인 상세 일반 텍스트 뷰](../../src/guard_app/ui/approval_detail.py)에 전체 수신처·정규 UTF-8 인자 표시, 비가시/bidi/혼동 문자 코드포인트 표시, 닫을 때 Qt 텍스트 참조 정리를 구현했다. 상세 창은 작은 부모 창과 가용 화면에 맞춰 열고 긴 내용은 내부 스크롤로 열람한다. 악성 마크업/링크/원격 이미지 문자열의 Qt 표시와 URL handler 미호출을 합성 시험했다 | 표시 컴포넌트 부분 완료. B의 같은 승인 메모리 사본을 인증 조회하는 API와 C 외부 요청 계수·T18 통합 인수 없이는 실제 승인 화면/T18 전체 통과가 아님 |
| A35 | [Qt 호출 로그](../../src/guard_app/ui/call_logs.py)에 공개 DTO 목록·검색, 호출 ID와 창 토큰이 맞는 큰 일반 텍스트 상세, 전체 날짜·단계 미실행/미제공 구분, 공개 필드만 쓰는 수식 방어 CSV 복사를 구현했다. 좁은 창에서는 발생 날짜·시·분·초·KST, 호출 ID·현재 상태를 먼저 보여주고 상세/CSV 버튼을 세로로 둔다 | 읽기 전용 표시 부분 완료. C 감사 조회/상세/페이지/CSV 공개 필드 계약, 실제 로그 연결과 I9/T36 검증 필요 |
| A36 | B `TaskStatus` 6종을 [읽기 전용 상태 카드](../../src/guard_app/ui/task_status.py)로 구분해 앱의 에이전트 실행 화면에 연결했다. 미확인 상태와 `UNKNOWN_OUTCOME`은 실행 가능/성공으로 표시하지 않으며 제어 버튼은 없다 | 부분 완료. B/C 확정 Task 조회·리비전·제어 접수/적용 이벤트, A26 연결 후 실제 행동 UI 필요 |
| A37 | 이전 1260×820·780×580 Windows Qt 렌더링과 키보드, Qt 125%·150% 배율 시뮬레이션에 이어 UI를 320×360부터 1100×750까지 합성 자료로 확인했다. 폭·높이에 따라 메뉴/사이드바, 필터·버튼·표 열을 재배치했다. 후속 A29 정합 수정에서는 문서 순서대로 연결 서버 다음에 미해결 항목을 배치하고 화면 폭에 맞는 서버 카드 한 줄만 먼저 표시한다. 긴 공개 ID의 가로 팽창, 표의 Tab 순환, 낮은 넓은 창의 사이드바 겹침, 좁은 로그 시각의 초 생략을 수정했다. 페이지·표 가로 스크롤 0과 하단 버튼·상세 접근을 확인했다 | 현재 구현 화면의 네이티브 Qt 합성 검증과 후속 A29 순서·폭별 카드 수 offscreen 검증 완료. A31–A36 실제 backend 제품 연결, 실제 OS DPI·다중 모니터 검증 필요 |
| A38 | 정상/승인/차단 통합 인수 미실행 | A16·A18·A22·A27·A29–A36, B/C 구현·C 통합 테스트 필요 |
| A39 | 장애/재전송/비공개 검증 미실행 | A38, C 공격·결함 주입 fixture/테스트 필요 |
| A40 | 모델 격리·replay 실험 미실행 | A22·A38, C 실험/통합 테스트와 승인 모델 환경 필요 |
| A41 | [A 보안 경계 검토](a-security-review.md)와 44개 Python 소스의 Codex Security Standard Scan 완료: medium 3건 중 A 소유 모델/MCP 경계 2건 보강, B 소유 정규식 1건 미해결. 반응형 UI 변경의 공개 승인 요약 상태 사칭을 별도 수동 검토·수정했다. ARTEX 실행 조건 기록. lock/재설치/CI·실제 침투 인수는 미실행 | A07·A08·A17·A27, B 정규식 정책, C stream 수신 상한·격리 fixture와 합의된 lock/CI 담당 필요 |
| A42 | 이 작업별 진행/재개 근거, [호환성 실행 준비](a-compatibility-runbook.md), [B/C 미전송 인계 초안](a-prompts/07-handoffs.md)을 갱신했다. 실제 담당 리뷰·제품 시작/시연 증거는 없다 | 부분 인계 자료 완료. A23·A37–A41의 실제 통합 결과와 B/C 리뷰 필요 |

## 이번 구현 검증과 다음 인수

- 사용자의 B 브랜치 활용 결정 이후 B 소스 사본과 A 소유 모듈을 함께 두고 `.venv`의 Python 3.12.15에서 `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`으로 `pytest -q -p no:_hypothesis_pytestplugin tests/unit`: 당시 **165 passed**. 변경한 UI·bridge 파일의 `ruff check`·`ruff format --check`와 관련 UI 소스의 mypy가 통과했다. Windows Qt에서 새 화면 780×580 합성 렌더링·좁은/넓은 표 배치, 추가 키보드 시험 58건, Qt 125%·150% 시뮬레이션 관련 시험 각각 85건을 확인했다. 실제 전송이나 B/C 계약 인수를 대체하지 않는다.
- 보안 경계 보강 뒤 같은 Python 3.12.15 환경에서 `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `python -m pytest -q -p no:cacheprovider tests/unit`: **188 passed**. MCP 결과 단위 21건과 모델·Runner 단위 49건을 별도로 다시 확인했다. [스캔](a-security-review.md)은 `75a52c6` 기준 소스 검토이며 수정 후의 실서버 침투 시험이 아니다. B 정규식, C 수신 예산·Store·launcher·fixture는 여전히 미검증이다.
- 반응형 화면 수정 뒤 같은 환경에서 Hypothesis 플러그인만 제외한 전체 `tests/unit` **201 passed**. 변경 UI의 Ruff check·format check, 8개 UI 소스의 mypy가 통과했다. Windows Qt 100%에서 320×360/400·540×580·900×700·1100×750, Qt 125%·150% 시뮬레이션에서 320×400·540×580을 합성 자료로 확인했다. 실제 OS DPI·다중 모니터·B/C 제품 연결은 아직 확인하지 않았다.
- A07/A08/A11/A12/A14–A16/A38–A41의 실제 전송·격리·계수는 C의 launcher/endpoint/Registry/Store/fixture와 공통 설정·lock/CI가 있어야 재개할 수 있다. A24/A25 커널은 구현했지만 A26/A27/A31–A36의 제품 연결에는 B/C 불변 UI 명령·이벤트, 승인 조회/사본, 등록·감사·Scanner 조회 계약이 필요하다. A31 로컬 초안·A32/A33/A35 읽기 화면·A34 표시·A36 읽기 화면은 그 계약 없이 가능한 범위다. A17 실제 AGT는 배포판/버전이 미정이고 A20 실제 모델 제공자/승인 환경도 미정이다.
- B·C가 [포트 검토](a-port-review.md)·[플랫폼 요구](a-platform-review.md)·[UI 계약 제안](a-ui-contract-proposal.md)·[환경 결정표](a-environment-decisions.md)를 검토하고 필요한 산출물을 제공하면, A는 현재 모듈을 실제 서비스에 연결하고 C의 동일 계약·계수 테스트로 인수한다. 다른 담당에게 보낼 문안은 [전달용 초안](a-prompts/07-handoffs.md)에 있으며 실제 전송은 별도 요청이 필요하다.

`approval_consumed`, `host_dispatch`, `wire_tools_call`, `receiver_effect`는 아직 제품 실행으로 측정되지 않았다. 이 문서의 단위 테스트와 offscreen UI 결과는 전체 I1–I10/T/S 검증 통과를 뜻하지 않는다.
