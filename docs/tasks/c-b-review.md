# C · B 구현 초안 확인과 인수 검토

최초 검토 시각은 2026-10-12 06:12 UTC다. `git fetch origin` 후 당시 B 원격 tip인 `0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc`를 확인했다. C 작업 브랜치와 기존 변경은 유지하고 `/tmp/mcp-guard-b-review-0c02b61`의 detached worktree에서 읽기·재현을 수행했다. 같은 날짜에 후속 수정 `f3c2d2b`도 별도 worktree에서 검토·재검증했다.

현재 상태는 **B1–B5 구현 초안 확인, C-B-01 발견 후 B 수정·대역 회귀 확인**이다. 공통 계약 합의·전체 C 인수·실제 MCP/SQLite 집행 검증을 완료한 것은 아니다. B 제품 코드를 수정하거나 C 브랜치에 병합하지 않았다. 아래 최초 검토와 결함 재현은 `0c02b61` 기준이며 후속 수정 결과는 별도 절에 기록한다.

2026-10-12 사용자 결정으로 SQLite 구현은 [B6](b-sqlite-store.md)로 이관했다. 최초 검토 당시의 C Store 구현 배정은 이 결정으로 대체하며 C는 저장소 리뷰·독립 검증을 맡는다.

## 확인한 구현 범위

| B 단계 | 코드·문서에서 확인한 내용 | 남은 연결 |
| --- | --- | --- |
| B1 | domain DTO·enum·고정 오류, 중첩 JSON 불변성, 전체 ports 초안 | A·C 공통 계약 합의 |
| B2 | 서버·목적지·AGT 판정 합성, effective_label, READ taint | C 리뷰·실제 상태 저장 |
| B3 | Catalog namespace·고정 스키마·출처·목적지 해석 | C Registry·safe_path resolver |
| B4 | Guard Task lock, 예약 전후 실패, READ/SEND 응답 게이트 | A transport·AGT·Runner와 C Store·감사 |
| B5 | 사람 제어 capability, 메모리 사본, 재평가·양시계 만료·승인 소비 연계 | 실제 승인 DB·A 인증된 제어 큐, 아래 결함 수정 |

읽은 범위는 README·B 작업 문서·B1/B2/B3/B4·B5/인수 문서·계약 문서와 ports·domain·core·Catalog·Guard·승인·응답·해시·예외의 핵심 코드다. 기존 테스트의 대역·관련 케이스도 확인했다. 전 파일의 정식 보안 리뷰나 자동 보안 스캔 완료를 뜻하지 않는다.

근거는 [B 인수 안내](https://github.com/hsminu/MCP-Runtime-Guard/blob/0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc/docs/b-handoff.md), [Store 포트](https://github.com/hsminu/MCP-Runtime-Guard/blob/0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc/src/guard_app/ports.py), [실행·승인 인수 문서](https://github.com/hsminu/MCP-Runtime-Guard/blob/0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc/docs/b4-b5-execution.md)다. 아래 시그니처도 합의 전 B 제안이다.

## 실제로 재현한 검증

B의 `requirements.lock`으로 별도 `.venv`에 33개 패키지를 설치하고, 프로젝트를 `--no-deps --no-build-isolation -e .`로 설치했다. 환경은 Ubuntu 24.04.4 x86_64·Python 3.12.3이다. C 작업 디렉터리의 `.venv`는 변경하지 않았다.

| 실행 명령 | 실제 결과 |
| --- | --- |
| `.venv/bin/python -m pytest tests/unit -q` | 840 passed, 3.20s |
| `.venv/bin/python -m ruff check .` | 통과 |
| `.venv/bin/python -m ruff format --check .` | 41개 파일 포맷 일치 |
| `.venv/bin/python -m mypy` | 24개 소스 파일 문제 없음 |
| `.venv/bin/python -m pip check` | 의존성 충돌 없음 |

이 결과는 B의 단위·성질·정적 검사 재현이다. MCP·AGT·Scanner·실제 SQLite·wire·receiver_effect·GUI·C CI·통합·replay는 실행하지 않았다. 기존 840개 통과와 아래 추가 결함 재현은 별개다.

## C-B-01 · 승인 재평가에서 다른 Task 상태를 사용해 정지시킴

상태는 **최초 대역 결함 재현, 후속 B 수정·대역 회귀 확인, 실제 SQLite 검증 미완료**다. 관련 I6·I7, T09–T12의 승인 맥락 검증에 추가할 저장소 오반환 케이스다. 아래 재현 결과는 수정 전 `0c02b61`이며 실제 SQLite나 원격 공격으로 재현한 결과는 아니다.

위치는 [approvals.py의 Task 조회·불일치 처리](https://github.com/hsminu/MCP-Runtime-Guard/blob/0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc/src/guard_app/services/approvals.py#L283)다. `_prepare_approval`은 `load_task(snapshot.task_id)`의 반환 Task ID·epoch를 검증하기 전에 정책을 검사하고, 맥락 불일치 처리에서 반환된 `state.task_id`로 `halt_task`를 호출한다. `_finish`의 재개 처리에도 같은 조회 후 식별자 검증을 검토할 지점이 있다. 후자는 별도 실행 재현하지 않았다.

재현에는 B의 기존 `tests/unit/test_guard.py` 대역과 실제 Guard·ApprovalService를 사용했다.

1. INTERNAL Task에서 외부 SEND를 submit해 정상 Pending 승인을 만든다.
2. 같은 정책·epoch·등급의 별도 WAITING_APPROVAL Task를 추가한다.
3. Store의 원래 Task 조회가 별도 Task DTO를 반환하도록 오반환을 주입한다.
4. 사람 제어 채널에서 원래 승인을 approve한다.

| 관측 | 실제 결과 |
| --- | --- |
| 반환 | Denied(APPROVAL_STALE) |
| 원래 Task | WAITING_APPROVAL 유지 |
| 별도 Task | HALTED로 변경 |
| 원래 승인 | CANCELLED |
| Guard global halt | false |
| 대역 승인 소비 | 0 |
| 대역 transport 호출 | 0 |
| wire·수신 부작용 | 대역이므로 미관측 |

전송 우회는 관찰하지 않았지만 잘못된 Task 조회가 다른 Task의 상태 변경으로 이어진다. 예상 Task의 lock 안에서 별도 Task를 변경한다는 점도 검토해야 한다. 코드의 `_task_matches`가 불일치를 발견한 뒤에도 잘못된 ID를 상태 변경 대상으로 사용한다.

B 수정 요구는 조회 직후 DTO·요청한 Task ID·epoch를 검증하고, 오반환의 ID를 상태 변경에 사용하지 않으며 정해진 fail-closed 경계로 전달하는 것이다. 승인·거절·만료의 조회 경로도 함께 검토한다. 수정 후 같은 재현에서 별도 Task 변경 0·소비/dispatch 0과 정지 정책을 확인해야 한다. B의 모듈 단위 테스트로 회귀 검증하고 실제 Store 연결 후 C가 결함 주입 검증을 수행한다.

## 후속 수정 f3c2d2b 확인

2026-10-12 원격 갱신 후 `f3c2d2bc225e0406c50731c9b63779a3d890cd70`을 `/tmp/mcp-guard-b-review-f3c2d2b` detached worktree로 검토했다. 변경은 승인·Guard 식별자 처리와 단위 테스트 4개 파일이며 domain·ports·lock 변경과 SQLite 구현은 없다.

승인 Task 조회가 Task ID·epoch를 먼저 검증하고, 정지 경로에도 snapshot의 기대 epoch를 전달한다. 오반환·다른 epoch이면 잘못된 Task 행을 수정하지 않고 global halt한다. 추가 테스트는 승인·거절·만료·재평가 거부 등의 20개 조합과 정지 경로의 epoch 교체 4개다. 대역 상태 변경 0·소비 0·transport 호출 0·후속 실행 거부를 검사한다.

기존 검토 venv의 의존성을 재사용하고 새 worktree에서 `PYTHONPATH=src`로 최신 코드를 읽도록 했다. 실행 전 import 경로가 새 worktree의 approvals.py인지 확인했다.

| 새 worktree에서 실행한 명령 | 실제 결과 |
| --- | --- |
| `PYTHONPATH=src /tmp/mcp-guard-b-review-0c02b61/.venv/bin/python -m pytest tests/unit -q` | 864 passed, 2.86s |
| `/tmp/mcp-guard-b-review-0c02b61/.venv/bin/python -m ruff check .` | 통과 |
| `/tmp/mcp-guard-b-review-0c02b61/.venv/bin/python -m ruff format --check .` | 41개 파일 포맷 일치 |
| `PYTHONPATH=src /tmp/mcp-guard-b-review-0c02b61/.venv/bin/python -m mypy` | 24개 소스 파일 통과 |

최신 코드로 대역 회귀를 확인한 결과다. 실제 SQLite·wire·receiver effect 검증은 미실행이고, 기존 840개 결과와 새 864개 결과를 구분한다. 근거는 [승인 식별자 검사](https://github.com/hsminu/MCP-Runtime-Guard/blob/f3c2d2bc225e0406c50731c9b63779a3d890cd70/src/guard_app/services/approvals.py#L559), [회귀 테스트](https://github.com/hsminu/MCP-Runtime-Guard/blob/f3c2d2bc225e0406c50731c9b63779a3d890cd70/tests/unit/test_guard.py#L660)다.

## C Store 요구 초안과의 대조

| C 초안 항목 | B에서 확인한 제안 | 남은 결정·검증 |
| --- | --- | --- |
| U1 · 반환·오류 | READ 커밋은 TaskState, SEND 성공 커밋은 None, 소비는 정확한 bool. 조건 충돌 False·저장 실패 StoreError | 실제 포트 합의, 커밋/ACK 확정 불가·취소 의미 |
| U2 · 보안 버전 | Task를 WAITING으로 저장한 반환 상태로 사본 생성. 소비 후 버전 유지·1 증가 모두 허용 | 최종 상태별 security_version 규칙 하나로 확정 |
| U3 · 관계·revision | 소비 포트에 승인→요청→Task 관계·각 영향 행 수 1 명시 | 실제 SQL·epoch·revision·중복 처리 검증 |
| U4 · 소비 감사 | 소비·예약·Task RUNNING·감사를 같은 트랜잭션으로 명시 | 실제 감사 사건·revision·실패 rollback |
| U5 · SEND 실패 | 성공만 commit_send_outcome. SEND isError·형식 실패·StoreError는 UNKNOWN_OUTCOME 경로 | 알려진 실패도 결과 불명으로 처리하는 보수적 선택의 합의, 실패 저장 연결 |
| U6 · 시간·DB 대기 | UTC-aware datetime, Guard가 소비 ACK 후에도 양시계 만료 재검사 | 실제 DB 트랜잭션 시점의 최신 시각·잠금 상한·취소·ACK 내구성 |
| U7 · 재시작 | 메모리 사본이 없으면 승인 권한 복원 없이 만료 처리 | 전체 잔존 승인·DISPATCHING 요청 정리, global halt·복구 계약 |
| U8 · 제어·응답 | Task WAITING과 create_approval, 승인 종결과 Task 복귀가 각각 별도 호출. C-B-01은 f3c2d2b 수정·대역 회귀 확인 | 생성·종결의 원자 묶음, 중간 실패·제어 경쟁, 실제 SQLite 오반환 검증 |

Store 초안의 세 연산 외에도 B가 사용하는 계약이 있다. Task create/load/status, decision 기록·ALLOW 예약, 승인 create/get/transition, `commit_request_failure`를 함께 검토해야 한다. 실패 커밋은 요청·Task·감사를 원자 기록하고 반환 TaskState를 검증받는다.

신뢰 ID factory·재시작에도 중복 없는 call_seq·FailureEvent 내구성 감사 callback도 필요하다. SQLite 이관 후 영속 순번·감사 저장은 B6, 안전 ID 모듈은 C가 맡고 정식 배치는 공통 계약으로 검토한다. Registry의 동기 조회, 안전 경로의 상대 POSIX ID 반환, Scanner의 불변 metadata 입력과 B digest의 스키마 숫자 처리도 C1.2–C1.4에서 검토한다.

## 다음 인수 작업

- [x] 후속 [C Store 계약 처리 제안 v1](c-store-proposal.md) 작성. 상태 버전·원자 승인·커밋 ACK/취소·실제 시각·복구의 권장 선택을 제시했다. 합의·구현·새 검증은 미완료다.
- [x] B가 C-B-01과 관련 승인 조회 경로를 f3c2d2b에서 수정했고 C가 최신 코드의 대역 회귀 검증을 재현했다. 실제 SQLite 인수는 별도다.
- [ ] C가 실제 B ports 초안 기준으로 Store 전체 메서드와 U1–U8 제안을 검토한다.
- [ ] 승인 생성·종결의 원자 묶음, 상태 버전·실패·시간·ACK 의미를 A·B·C가 확정하고 합의 기록을 남긴다.
- [ ] 합의된 계약을 B6 SQLite 구현과 C 독립 인수 기준에 반영하고 실제 SQLite·계수 검증으로 진행한다.

현재 확인으로 B 계약 초안을 기다리던 C 작업을 구체화할 수 있다. B 코어의 완성·공통 계약 동결·C 인수 통과로 표시하지 않는다.
