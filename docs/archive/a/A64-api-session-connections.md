# A64 · 새 Task와 모델 API의 A 연결부

## 범위와 담당 확인

- 2026-10-18 사용자는 새 세션의 API·CLI 연결과 Windows 자격증명 관리자 보관을 요청했다. 담당 경계를 확인한 뒤 **A 연결부만 구현하고 C 구현을 기다린다**고 선택했다.
- 원격 main `1d34276`, B `3307f5d`, C `d315d71`과 이슈 #20·#21을 확인했다. B의 Task/Store 계약은 현재 브랜치와 같고, C에도 Windows 자격증명 저장 API·launcher·L1/L2 구현이 없었다. 열린 PR #18은 문서 제안이었다.
- B의 domain/ports·Guard·SQLite와 C의 security 파일을 변경하지 않았다. 공통 문서·의존성·lock도 변경하지 않았다. `CredentialOperations`는 A 내부의 주입 경계이며 C의 공개 API 합의를 대신하지 않는다.

## 구현

- 기존 `Store.create_task/load_task`를 사용해 SQLite에 새 Task를 만들고 Task마다 Runner와 모델 client를 분리했다. 기존 Task나 승인 권한을 복원하지 않는다.
- `SessionRuntime`은 현재 도구 없는 대화 구성이다. Guard와 Catalog가 없는 경우 서버 목록은 비어 있어야 하고, 모델의 도구 호출 제안은 거부한다. 합성 서버나 테스트 대역을 제품 구성에 등록하지 않는다.
- 새 세션에서 API/CLI와 모델 ID를 선택하고 Task를 생성한다. 세션 전환 시 입력·커서·대화를 보존하고, 선택하지 않은 Task의 응답이 현재 상태·모델 배지·제목을 덮어쓰지 않게 했다. 세션 이름은 앱 수명 동안 중복되지 않는 순번을 쓴다.
- 모델 키 입력은 가림, 저장 요청 직후 및 화면 숨김 시 입력 폐기, 백엔드 확인 뒤 상태 표시를 적용했다. 저장 실패를 성공이나 계속 확인 중으로 표시하지 않는다. 키 변경은 새 세션부터 적용하고, 사용 중인 API client가 있으면 키 삭제를 거부한다.
- 실제 Windows 저장 기능은 주입하지 않았다. 환경변수·`.env`·설정 파일·자체 저장 구현으로 대체하지 않는다. 기본 구성에서는 키 저장·모델 전송을 차단하고 이유를 표시한다. Scanner 키 입력과는 별도다.
- `OpenAIModelClient`는 기존 `ModelClient` 포트에 맞춰 Responses API 요청과 응답 정규화를 구현했다. endpoint 고정, redirect·재시도·환경 proxy 사용 차단, 요청/응답/메모리 replay 상한, `store=False`를 적용했다. 키는 구성에서 명시적으로 받아 메모리에서만 사용한다.
- Guard ToolView만 안정적인 function 별칭으로 변환하며 도구를 직접 실행하지 않는다. 설치된 OpenAI 2.54.0 응답 형식과 대조해 reasoning·phase를 보존하고, 알 수 없는 도구·호출 주체·namespace·중복 JSON 키·비정상 응답은 거부한다. 종료·timeout·취소 뒤 늦은 응답이 캐시를 복원하지 못하게 했다.
- 제품 진입점은 `python -m guard_app --db-path <기존 부모 폴더 안의 SQLite 파일>`이다. `examples`는 명시적 `--demo` 분기에서만 import한다. 기존 합성 MCP·Guard 승인 경로는 유지했다.

## 실제 검증

- Python 3.12.15, PySide6 6.12.0, OpenAI 2.54.0, httpx 0.28.1. 모든 GUI 검증은 `QT_QPA_PLATFORM=offscreen`으로 수행했다.
- 전체 `pytest -q`: **2,499개와 subtest 20개 통과**, SDK 경고 5개, 126.18초.
- `ruff check src tests examples`, `ruff format --check src tests examples` 통과. 포맷 검사 136개 파일, 전체 mypy 65개 소스 통과.
- API 어댑터 신규 42개, 기존 모델 경계 포함 98개 회귀 통과. 정상/거부/함수 결과, SDK 필드, 크기 상한, 429/500 재시도 없음, redirect·timeout·취소·늦은 응답·close를 대역 HTTP로 확인했다.
- 독립 GUI QA 및 재실행: 48개 관측·10장 캡처, 실제 SQLite 2 Task와 대역 HTTP 3회. 입력·응답·종료 격리, 다른 Task를 정지한 뒤에도 남은 Task 사용, DB의 대화 원문·대역 키 비보존, 선택 Task 표시 유지, 기본 C 미연결 차단을 확인했다.
- 기존 합성 Guard에서 승인 전 SEND 부작용 0, 정확한 승인 뒤 READ+SEND dispatch 2/수신 부작용 1을 확인했다.
- 실제 `__main__.main()` 제품 진입점을 임시 SQLite 경로로 실행해 대화 페이지 진입과 정상 종료(exit 0)를 확인했다. 사용자 데스크톱에 창을 띄우지 않았다.
- 캡처·대역 실행 스크립트·검사 로그는 Git 제외 경로 `.test-artifacts/`에 있다. 실제 API 키를 조회하거나 저장하지 않았고 외부 모델·CLI를 실행하지 않았다.

## 남은 연결과 리뷰

- **API 실제 사용:** C의 Windows 자격증명 저장·읽기·삭제 기능과 연결 검토가 필요하다. 현재 제품은 이 기능이 없어 전송하지 않는다.
- **CLI 실제 사용:** 선택과 미연결 안내만 제공한다. C의 고정 실행 파일·launcher·L1/L2·정리 검증, B의 승인 최종 알림·시스템 정지 계약이 없어 Codex 프로세스를 실행하지 않는다.
- **MCP 작업:** 제품 Registry·설정·안전 실행 기반 연결이 남아 있다. 새 제품 세션의 도구 없는 대화를 MCP 작업 실행 완료로 간주하지 않는다.
- 사용자 선택에 따라 직접 API 어댑터를 추가했지만 기존 Codex 하네스 후속과 공통 계약을 임의로 변경하지 않았다. 실제 모델 계정 접근 가능성·응답 품질·C 저장 동작은 검증하지 않았다.
- 관련 I1·I3·I7·I8·I9·I11, T18 및 T21의 A/대역 범위다. C의 T22 및 실제 모델 세션 인수 완료를 뜻하지 않는다. A 구현, B·C 리뷰 대상. PR 미생성, `feat/a-application`에 커밋·푸시한다.
- API 응답/도구 형식은 [OpenAI function calling 문서](https://developers.openai.com/api/docs/guides/function-calling)를 확인했다. CLI의 `dynamicTools`/`item/tool/call`은 [공식 app-server 문서](https://learn.chatgpt.com/docs/app-server)에 실험적 기능으로 명시돼 있으며, 실제 연결은 저장소의 버전 고정·검증 절차를 기다린다.
