# A65 · Codex 전용 연결부와 Task별 동시 실행

2026-10-18. `origin/main`의 #22(`818b118`)를 A 브랜치에 병합하고 10월 18일 사용자 결정을 반영했다. 사용자의 기존 범위인 **A 연결부만 구현하고 C 구현을 기다림**을 유지했다.

## 한 일

- 제품의 `adapters/openai_model.py`와 직접 모델 API·키 저장 콜백, 새 세션의 API/CLI 선택·모델 ID 입력을 제거했다. 모델은 Codex `gpt-6.1-sol`로 고정하고 로그인은 Codex 자체 ChatGPT 기기 코드/API 키 방식 및 Windows 자격증명 관리자 저장이라는 안내로 바꿨다. 별도 Scanner API 키 기능은 유지했다.
- `services/runner.py`는 A 로컬 `TaskExecution`·`CodexSession` 인터페이스 제안과 Task별 입력·늦은 응답 차단 연결부로 바꿨다. 기존 일반 모델 어댑터·모델 루프는 `examples/legacy_model_client.py`, `examples/legacy_runner.py`로 옮겨 합성 Guard 검증에만 쓴다. 제품 시작은 이 예제를 import하지 않는다.
- `SessionRuntime`은 주입형 L2 검사와 Task별 세션 생성 입구를 가진다. L2가 없거나 실패하면 Store Task 생성 전에 거부하며 건너뛰기는 없다. 검사 결과는 앱 수명에 공유하고, 각 세션 생성 구현은 고정 실행 파일·config·모델 정보 해시 확인과 독립 프로세스 소유를 제공해야 한다. 누락된 C 기능을 로컬 코드로 대체하지 않았다.
- bridge 실행 큐는 전체 대기 상한을 유지하면서 서로 다른 Task를 동시에 처리하고 같은 Task는 직렬화한다. 동일 Task의 대기 요청 여러 개가 다른 Task를 막지 않게 했다. 종료 시 ACCEPTED·PROCESSING 요청 모두 종결 상태를 받는다.
- 화면의 처리 중 상태·제어 알림을 Task/epoch별로 분리했다. 한 Task가 기다리는 동안 다른 Task의 전송·새 세션 생성이 가능하다. 선택한 세션, 초안, 대화와 기존 관리 화면을 유지한다.
- 인증 실패·비정상 세션 실패·취소는 해당 Task에만 적용하고 늦은 응답을 공개하지 않는다. interrupt·close에는 상한을 두며 앱 종료는 세션들을 병렬 정리한다. 정리 실패는 불확실성으로 남겨 다른 Task의 동작과 별개로 최종 종료·재시작 게이트에 전달한다.

## 실제 검증

- 전체 `pytest -q`: **2,466 passed, 20 subtests passed**, 기존 의존성 경고 5개, 117.36초. 전체 실행 중 마지막 정리 불확실성 latch가 추가되어 해당 최종 변경은 별도 `test_session_runtime.py`·`test_bridge.py` **72 passed**로 검증했다.
- 최종 코드 `ruff check src tests examples` 통과, `ruff format --check src tests examples` 136파일 통과, `mypy` 63소스 통과. 기존 합성 모델·Runner·SQLite 회귀도 집중 검사와 전체 검사에서 통과했다.
- 독립 offscreen GUI 검증: 23개 관찰, 1280×800·320×680 화면 6장, 발견 문제 없음. 실제 bridge·SQLite와 합성 주입 세션으로 두 전송이 모두 완료 전 진입한 것을 확인했다. A 취소 뒤 B가 계속 실행·완료하고 A 원문과 늦은 응답이 제거됨을 확인했다. C 미연결 제품에서는 시작 거부와 미생성 상태를 확인했다. 실제 Codex 프로세스 실행 수는 0이다.
- 실제 제품 `__main__.main()`을 offscreen으로 시작·종료해 반환 코드 0, 대화 초기 화면, 시연 모듈 미로드를 확인했다. 최종 제품 import 검사에서도 직접 API 어댑터·시연 모듈이 로드되지 않았다.
- 관련 기준: I1·I6·I7·I8·I9·I11, T18·T21·T22·T46의 A 연결부에 해당한다. 합성 관찰을 C의 실제 프로세스 T46 인수로 간주하지 않는다.

## 남은 의존성과 담당 경계

- 실제 Codex 0.162.1 실행, 앱 시작 config 생성·Task 시작 해시 확인, Task별 `sqlite_home`, `account/read`·로그인·keyring, L1/L2와 T43/T46 검증은 C 구현을 기다린다. 제품 기본 구성에서는 실제 모델을 호출하거나 실행 중 Task를 만들 수 없다.
- A 로컬 인터페이스는 공통 계약 확정이 아니다. B의 미사용 `ports.ModelClient` 선언 제거와 세션 관리자 계약 합의, Pending 최종 결과 알림·Task 정지·고정 문구표·Profile 로딩이 남는다. 실제 JSON-RPC·Guard 도구 중계와 L3 감시는 이 의존성을 연결한 뒤 검증해야 한다.
- `domain/*`, `ports.py`, B 서비스·SQLite, C 보안·Scanner 코드, 공통 설정·lock은 수정하지 않았다. 공통 문서는 원격 병합 내용만 반영했고 A 현황과 이 완료 기록만 작성했다. 기존 사용자 미커밋 변경은 별도 보존했다.
- 리뷰: 별도 에이전트가 Task별 직렬화·동시 실행·종료 경쟁·정리 실패·API 잔재를 검토했고 발견한 문제를 수정 확인했다. B·C 담당자의 인수를 대신하지 않는다. PR은 생성하지 않았으며 사용자 지시에 따라 A 브랜치에 커밋·푸시한다.
