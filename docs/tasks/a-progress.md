# A 진행 현황

2026-10-13 리뷰 반영. 출발점 A `5598009`, B 동기화 기준 `f22968c`.
변경 이력은 현재 브랜치의 git log에서 확인한다. 과거 세부 증거와 미정 항목은
[이전 기록](a-progress-history.md), 현재 재현법과 한계는 [리뷰 반영 기록](a-review-fixes.md)을 따른다.

| 항목 | 상태 | 막힌 이유 | 다음 행동 |
| --- | --- | --- | --- |
| A01·A03 계약 | B ref·테스트 동기화, Runner epoch 전달 | domain/ports 합의 표는 미확정 | B ref 변경마다 전체 단위 검증 |
| A02·A06 환경 | pyproject·uv.lock 추가, Python 3.12 설치 재현 | AGT/Scanner 실제 패키지 미선정 | 별도 환경 호환 증거 추가 |
| A04 C 연결 | 요구사항 전달 | C launcher·Registry 미연결 | C 인수 환경에서 대조 |
| A05 UI 계약 | 앱 내부 Task projection·사람 review 연결 | 공통 조회 DTO 합의 미완료 | B·C 정식 계약 검토 |
| A07·A11 stdio | 실제 SDK·Guard 왕복 스파이크 실행 | 정식 C 수신 fixture 미사용 | C 인수 증거로 확장 |
| A08·A12 HTTP | 미실행 | HTTP 왕복 코드·인수 미완료 | A 독립 loopback 스파이크부터 수행 |
| A09·A10 worker | 구현·회귀 검증 | 수신 직전 wire-size 상한은 C 의존 | C stream ingress 연결 |
| A13 종료 | 앱 종료·스레드 종료 smoke 검증 | 실제 DB 없음 | B6 close 순서 검증 |
| A14 원시 출력 | 필수 errlog·SDK worker 로그 원문 격리 | 정식 C 공개 handler 인수 없음 | C 로그 정책·A39 함께 검증 |
| A15·A16 등록 | 요구·단위 구현 | 지문/Registry/launcher 미연결 | 실제 등록 계약 연결 |
| A17 AGT 선정 | 질문·경계·담당 제안 기록 | 실제 배포판·결정권자 미확정 | 제안 승인 후 후보 조사 |
| A18 AGT mock | 명시적 로컬 mock만 bootstrap | 실제 AGT·영속 mode 감사 미구현 | B6 mode 저장 후 실제 후보 검증 |
| A19·A20 모델 | 중립 adapter·한계 단위 검증 | 실제 제공자 미선정 | 승인 환경 확정 |
| A21·A22 Runner | expected_epoch·승인 resume 연결 | 실제 모델 세션 격리 미검증 | 제공자 선정 후 T21 |
| A23 B 리뷰 | AGT 지연 중 긴급 차단 수정·회귀 검증 | C 독립 wire 검증 미실행 | B·C 소유자 리뷰 |
| A24–A27 bridge·앱 | __main__ + 실제 stdio 합성 앱 | Store·Registry는 B 단위 대역 | B6·C 구성으로 교체 |
| A28–A32 화면 | 기존 읽기 화면 유지 | 제품 Registry/Scanner 조회 없음 | 합의된 조회 연결 |
| A33·A34 승인 | 실행 탭에서 사본 확인·승인·거절 가능 | 제품 조회 목록과 실행 탭 통합 미완료 | 동일 사람 capability로 목록 연결 |
| A35 로그 | enum 값 매핑 수정 | 영속 감사 조회 미연결 | B6 조회 연결 |
| A36·A37 제어·표시 | 확정 상태/요청됨 분리, 한글 표시 수정 | C 전체 GUI 인수 미실행 | 실제 운영 구성 검증 |
| A38–A40 통합 | A 합성 smoke 6경로 | SQLite·HTTP·C launcher 없음 | C 정식 T/S 계수 검증 |
| A41 패키징 | lock·설치·단위 검증 | 전체 보안 스캔/CI는 미실행 | C CI 연결 |
| A42 인수 | 현재 문서 간소화 | 제품 인수 의존성 위 표 참조 | 리뷰 결과와 검증을 함께 인계 |

검증: 전체 단위·앱 smoke **1153 passed**, lock 새 환경의 stdio 6경로 재현.

현재 실행: 저장소 루트에서 `uv sync --locked`, `uv run python -m guard_app --demo`.
합성 자료 전용이며 실제 모델·외부 송신·SQLite 제품 실행으로 표시하지 않는다.
