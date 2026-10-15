# A 브랜치 문서 길잡이

`feat/a-application`에서 작성한 문서의 용도별 색인이다. 현재 구현·검증·남은 조건은 [A 작업 현황](../../a-status.md)을 기준으로 확인한다. 날짜가 적힌 검토와 실행 기록은 해당 시점의 근거이며, 프롬프트는 작업 지시이지 완료 표시가 아니다.

## 먼저 읽을 문서

| 문서 | 용도 |
| --- | --- |
| [A 작업 현황](../../a-status.md) | A01–A42 상태, 커밋, 검증, 재개 조건의 현재 기준 |
| [A 역할과 인수 체크리스트](a-application.md) | A 소유 범위와 보안·검증 기준 |
| [A01–A42 실행 프롬프트](a-prompts/README.md) | 작업별 선행 조건·수정 범위·완료 기준 |

## 계약 검토와 결정 요청

| 문서 | 성격 |
| --- | --- |
| [A03 · B 포트 검토](a-port-review.md) | B 도메인·포트·Guard 초안과 A 연결 요구 |
| [A04 · C 플랫폼 요구](a-platform-review.md) | Registry·launcher·fixture·계수 인수 조건 |
| [A05 · UI 계약 제안](a-ui-contract-proposal.md) | 명령·이벤트·조회 형태의 협의 초안 |
| [A06 · 환경 결정표](a-environment-decisions.md) | 설정·패키징·모델 환경의 미확정 결정 |
| [A17 · AGT 배포판 선정](a-agt-selection.md) | core 5.0.0 선정, Windows 설치·판정 스파이크와 A18 연결 조건 |
| [A18 · 실제 AGT 연결](a-agt-integration.md) | 로컬 판정 adapter, 명시적 real/mock, 제한시간·취소·Guard 경계 검증 |
| [A23 · B 코드 리뷰](a-b-review.md) | 특정 B 커밋의 Guard·Catalog 검토와 후속 질문 |

## 호환성과 보안 기록

| 문서 | 성격 |
| --- | --- |
| [연동·호환성 기록](../integration-notes.md) | 날짜별 로컬 환경·SDK·Qt 검증 기록 |
| [A08/A12 · HTTP 연결](a-http-integration.md) | HTTP client 설정·실제 loopback 왕복·재전송 금지·지원 한계 |
| [A07·A08 · 호환성 실행 준비](a-compatibility-runbook.md) | C fixture가 준비되면 실행할 stdio·HTTP 왕복 절차 |
| [A 보안 검토](a-security-review.md) | 고정 리비전의 스캔 근거와 ARTEX 실행 조건 |

공통 설계·협업·계약 문서는 이 색인의 관리 대상이 아니다. `docs/` 루트의 B 계약 초안과 인수 문서는 B 브랜치에서 가져온 자료이며, A가 합의를 확정한 기록으로 해석하지 않는다.
