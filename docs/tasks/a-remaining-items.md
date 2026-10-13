# A 리뷰 반영 후 남은 항목

작성일: 2026-10-13  
기준: `feat/a-application` HEAD `631e116` + 이번 W1–W4 작업 트리 변경(커밋 없음)

합성 서버로 실행 → 승인 → 전송하는 경로는 연결했다. 아래는 제품 구성 연결과 AGT 계약·구현에서 남은 항목이다. 담당 배분과 계약 제안은 합의 완료를 의미하지 않는다.

| 항목 | 현재 반영 상태 | 남은 작업 |
| --- | --- | --- |
| 1. 제품 전체 실행 경로 | SQLite 선택·새 Task 시작, 같은 프로세스 미종결/정리 미확인 재시작 차단(W1), 옛 Task 실행·제어 거부(W2), 합성 stdio·HTTP 승인·SEND 연결 | B 시작 복구·영속 call_seq·전체 목록·감사, C launcher·프로세스 간 잠금. 실제 모델은 Codex CLI 연동으로 결정(사용자, 2026-10-13), 아직 미연결 |
| 실제 모델 연결 | Codex CLI 연동으로 결정(사용자, 2026-10-13), 아직 연결하지 않음 | Codex CLI adapter 연결·승인 환경 확정(T22)·제한 검증, 연결 방식·SDK 시그니처 미확정 |
| HTTP MCP | JSON/SSE adapter·loopback 합성 앱·재전송 금지·결과 불명 검증 | C endpoint/Registry 연결·독립 wire 인수, 인증 지원 시 별도 replay 검증 |
| 3. B 동기화 | B `6115fa6` 병합 `4388298` 기준 SQLiteStore 공개 API 연결·기본 메모리 경로 보존 | [B-SQL-01–06](a-b-sqlite-requests-2026-10-13.md): 영속 call_seq(B 보류)·시작 복구·목록/감사 API·HumanControl 실행 회차 범위 검토 |
| AGT 데이터 경계·최소화 | 로컬 실제 adapter에 server/tool/종류의 해시만 전달 | B 제품 규칙 의미·버전 계약 검토, 원격 AGT는 미지원 |
| AGT 규칙 담당·로딩 | 역할·규칙 관리 경로는 2026-10-14 회의에서 결정(보류, 사용자(A), 2026-10-13) | 규칙 로더는 결정 후 구현 |
| `agt_mode` 감사 | 명시적 real/mock 선택·실제 초기화 확인 후 UI 표시 | DB 감사 필드·마이그레이션 구현, 영속 감사 기록 연결 |
| AGT 선정·timeout | core 5.0.0 고정, 실제 adapter의 timeout·취소·늦은 결과 폐기·정리 단위 검증 | C 독립 dispatch 0·wire 인수, Scanner 포함 공통 환경 검증 |

상세 변경과 검증 근거는 [리뷰 반영 기록](a-review-fixes.md), 전체 진행 상태는 [A 진행 현황](a-progress.md)을 따른다.
