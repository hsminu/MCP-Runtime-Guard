# A 진행 현황

2026-10-13 최신 B 병합. A `281cbae`에 B `3bf414c`(코드 보완 `29025e0`)를 반영했다.
환경 분리·현재 AGT 차이·검증은 [최신 B 동기화 기록](a-b-sync-2026-10-13.md)을 따른다.
동기화 후 전체 **1,371 passed**, 별도 B 환경 **971 passed**, AGT real/mock × stdio/HTTP **32경로 통과**.
변경 이력은 현재 브랜치의 git log에서 확인한다. 과거 세부 증거와 미정 항목은
[이전 기록 및 로컬 QA 수정 근거](a-progress-history.md), 현재 재현법과 한계는 [리뷰 반영 기록](a-review-fixes.md)을 따른다.

| 항목 | 상태 | 막힌 이유 | 다음 행동 |
| --- | --- | --- | --- |
| A01·A03 계약 | B ref·테스트 동기화, Runner epoch 전달 | domain/ports 합의 표는 미확정 | B ref 변경마다 전체 단위 검증 |
| A02·A06 환경 | AGT core 5.0.0 포함 lock, Python 3.12 새 환경 설치 재현 | Scanner 선정·공통 제품 조합 미완료 | C Scanner 조합 인수 |
| A04 C 연결 | 요구사항 전달 | C launcher·Registry 미연결 | C 인수 환경에서 대조 |
| A05 UI 계약 | 앱 내부 Task projection·사람 review 연결 | 공통 조회 DTO 합의 미완료 | B·C 정식 계약 검토 |
| A07·A11 stdio | 실제 SDK·Guard 왕복 스파이크 실행 | 정식 C 수신 fixture 미사용 | C 인수 증거로 확장 |
| A08·A12 HTTP | [Streamable HTTP adapter·loopback 왕복·JSON/SSE·재전송 차단](a-http-integration.md) | C endpoint 검증·독립 fixture 인수·인증 확장 미완료 | C 등록/endpoint 연결과 독립 계수 인수 |
| A09·A10 worker | 구현·회귀 검증 | 수신 직전 wire-size 상한은 C 의존 | C stream ingress 연결 |
| A13 종료 | 앱 종료·스레드 종료 smoke 검증 | 실제 DB 없음 | B6 close 순서 검증 |
| A14 원시 출력 | 필수 errlog·stdio/HTTP SDK와 합성 HTTP 서버 로그 격리 | 정식 C 공개 handler 인수 없음 | C 로그 정책·A39 함께 검증 |
| A15·A16 등록 | 요구·단위 구현 | 지문/Registry/launcher 미연결 | 실제 등록 계약 연결 |
| A17 AGT 선정 | [core 5.0.0 선정](a-agt-selection.md), Windows 판정 스파이크 8경로 통과 | Scanner와 함께 쓸 공통 제품 조합 미검증 | C 조합 인수 |
| A18 AGT 연결 | [실제 로컬 판정·명시적 real/mock·timeout/취소 검증](a-agt-integration.md), 10-13 QA 반영(동시 16·멈춘 worker 교체·종료 확인·한국어 결과 문구) | [AGT 역할·규칙 관리 결정](a-agt-role-decision.md) 미확정, 영속 mode 감사·C 독립 인수 미완료, B Guard 4개 상한 연결 단위 검증 완료(독립 인수 별도) | 역할 결정, B 규칙/감사 검토, C dispatch 0 인수 |
| A19·A20 모델 | 중립 adapter·한계 단위 검증 | 실제 제공자 미선정 | 승인 환경 확정 |
| A21·A22 Runner | expected_epoch·승인 resume 연결 | 실제 모델 세션 격리 미검증 | 제공자 선정 후 T21 |
| A23 B 리뷰 | AGT 지연 중 긴급 차단 수정·회귀 검증 | C 독립 wire 검증 미실행 | B·C 소유자 리뷰 |
| A24–A27 bridge·앱 | __main__ + 실제 stdio/HTTP 합성 앱 | Store·Registry는 B 단위 대역 | B6·C 구성으로 교체 |
| A28–A32 화면 | 기존 읽기 화면 유지 | 제품 Registry/Scanner 조회 없음 | 합의된 조회 연결 |
| A33·A34 승인 | 실행 탭에서 사본 확인·승인·거절 가능 | 제품 조회 목록과 실행 탭 통합 미완료 | 동일 사람 capability로 목록 연결 |
| A35 로그 | enum 값 매핑 수정 | 영속 감사 조회 미연결 | B6 조회 연결 |
| A36·A37 제어·표시 | 확정 상태/요청됨 분리, 한글 표시 수정 | C 전체 GUI 인수 미실행 | 실제 운영 구성 검증 |
| A38–A40 통합 | AGT real/mock × stdio/HTTP 합성 경로, A HTTP 어댑터 수신 계수 검증 | SQLite·C launcher/endpoint·독립 인수 없음 | C 정식 T/S 계수 검증 |
| A41 패키징 | lock·설치·단위 검증 | 전체 보안 스캔/CI는 미실행 | C CI 연결 |
| A42 인수 | 현재 문서 간소화 | 제품 인수 의존성 위 표 참조 | 리뷰 결과와 검증을 함께 인계 |

원격 리뷰 당시 검증: 전체 단위·앱 smoke **1153 passed**, lock 새 환경의 stdio 6경로 재현.

현재 실행: 저장소 루트에서 `uv sync --locked`, `uv run python -m guard_app --demo --agt-mode real`.
합성 자료 전용이며 실제 모델·외부 송신·SQLite 제품 실행으로 표시하지 않는다.

## 2026-10-13 동기화

로컬 QA 수정 `0882890`–`dc8d77b`와 원격 A `62546cf`를 병합했다.
세션 종료 확인·늦은 bootstrap 자원 정리·승인 경합 뒤 Task 상태 재확인과
상세창 재사용·공개 텍스트 표시·stdio 입력 상한을 원격 앱 실행 경로와 함께 유지한다.
미추적 QA 보고서·재현 자료는 사용자 작업 트리에 보존하며 병합 커밋에 포함하지 않는다.

공통 기준 `origin/main`의 `4179f98`도 병합해 SQLite B6 소유권과 C 독립 검증 문서를 반영했다.
병합 후 전체 단위·앱 테스트 **1179 passed** (29.74초, Qt offscreen), 충돌한 Python 파일과 Runner 회귀 테스트의 Ruff 검사를 통과했다.
Runner 테스트 대역도 `expected_epoch`를 받아 검증하도록 원격 계약에 맞췄다.

실제 stdio 합성 서버의 allow·deny·승인·거절·취소·긴급 차단 6경로도 통과했다.
bridge·앱 runtime 집중 재검증은 **41 passed** (3.98초)였다. 정식 C wire·SQLite 인수와는 구분한다.

## 2026-10-13 실제 AGT 연결

`agent-governance-toolkit-core==5.0.0`의 로컬 PolicyEngine을 기존 AccessChecker에 연결했다.
CLI의 `--agt-mode real|mock`은 필수이고, real 초기화 실패 시 mock으로 전환하지 않는다.
규칙은 신뢰 도구의 ID·종류를 해시한 정확 일치 목록이며 SDK에 전체 인자·문서를 주지 않는다.
timeout·취소·늦은 결과 폐기·종료 미확인과 Guard의 거부권 합성을 검증했다.

새 lock 환경 설치 66개 배포판의 의존성 검사 성공, 전체 테스트 **1,243 passed**
(40.11초, Qt offscreen, SDK deprecation 경고 4건), real/mock stdio 각각 8경로 통과.
상세 계수·재현법·B 규칙/감사와 C 독립 인수 의존성은 [A18 연결 기록](a-agt-integration.md)을 따른다.

## 2026-10-13 HTTP MCP 연결

Streamable HTTP factory·로컬 TCP 서버를 연결했고 CLI에서 `--mcp-transport http`로 실행한다.
JSON/SSE × stateful/stateless, 307/308·인증/세션 오류·응답 유실·timeout·취소의
SEND 추가 전송 0을 검증했다. SDK/client·합성 서버 로그도 같은 context에서 격리한다.

새 lock 환경 설치·66개 배포판 의존성 검사 성공, 전체 **1,282 passed**
(22.55초, Qt offscreen, SDK 경고 5건), HTTP 앱의 AGT real/mock 각각 8경로 통과.
HTTP 단위 probe의 wire 계수와 제품 endpoint/인증·C 독립 인수 한계는
[A08/A12 연결 기록](a-http-integration.md)을 따른다.
