# 3인 협업 규칙

기준 문서는 [기술 설계 및 개발 문서](technical-design.md)다. 아래 역할·리뷰는 원문 0.5절을 따른다. 이 문서는 작업 배정과 인수 조건을 정한다. 현재 A 구현·검증·미완료 조건은 [A 작업 현황](tasks/a-progress.md)을 기준으로 확인한다.

## 브랜치와 작업 문서

| 담당 | 브랜치 | 브랜치 안에서 먼저 읽을 문서 |
| --- | --- | --- |
| A | `feat/a-application` | `AGENT.md`, `docs/tasks/a-application.md`, `docs/integration-notes.md` |
| B | `feat/b-security-core` | `AGENT.md`, `docs/tasks/b-security-core.md`, `docs/contracts.md` |
| C | `feat/c-security-platform` | `AGENT.md`, `docs/tasks/c-security-platform.md`, `docs/weakness_register.md` |

`main`의 공통 문서 커밋을 세 브랜치가 공유한다. 역할 브랜치의 루트 `AGENT.md`는 같은 내용으로 유지하며, 현재 브랜치에 따라 고유한 작업 문서를 읽는다. 역할별 규칙을 바꿀 때는 `docs/tasks/`의 자기 문서를 수정한다. 이렇게 해야 세 브랜치를 합칠 때 역할 지침 파일이 서로 덮어써지지 않는다.

## 파일 소유권과 리뷰

표의 제품 경로는 `src/guard_app/` 기준이다. 담당 경로가 표에 있어도 실제 구현 여부는 별도로 확인한다.

| 경로 / 작업 | 주 담당 | 리뷰 |
| --- | --- | --- |
| `domain/*`, `ports.py` | B | A·C, 변경은 3인 합의 |
| `core/*` | B | C |
| `services/guard.py`, `services/catalog.py` | B | A |
| `services/approvals.py`, `services/response_gate.py` | B | C |
| `security/failclosed.py`, `security/digest.py` | B | C |
| `security/paths.py`, `endpoints.py`, `ids.py`, `secrets.py`, `redact.py`, `launcher.py` | C | B |
| `storage/*`, `services/registry.py`, `adapters/cisco.py` | C | B |
| `adapters/mcp_transport.py` | A | B |
| `adapters/agt.py` 연결·번역 | A | B, 합성 규칙은 B 담당 |
| `adapters/model_client.py`, `services/runner.py` | A | B |
| `services/bridge.py`, `ui/*`, `__main__.py` | A | C |
| `fixtures/*`, `attack_fixtures/*` | C | A |
| `tests/contract`, `integration`, `attacks`, `faults`, `experiments` | C | 관련 모듈 담당, C 소유 모듈의 테스트는 B |
| CI, Semgrep, import-linter | C | B |
| 모듈별 단위·성질 테스트 | 해당 모듈 담당 | 해당 모듈 리뷰 담당 |

`config/`·패키징·의존성 lock의 세부 편집 담당은 착수 때 [계약 체크리스트](contracts.md)에 합의해서 기록한다. 원문에 없는 파일 소유권을 임의로 확정하지 않는다.

## 개발 순서와 인수 조건

| 단계 | A | B | C | 완료 조건 |
| --- | --- | --- | --- | --- |
| 0 · 공통 계약 | Transport·AccessChecker·ModelClient·UI 이벤트 요구 제공 | domain·ports·결과·상태 계약 초안 | Store·Scanner·Registry·계수 요구 제공 | 세 사람이 계약 및 미확정 항목 확인 |
| 1 · 독립 구현 | SDK stdio·HTTP·AGT 호환 확인, 어댑터 구현 | 순수 정책·등급 병합·Catalog 구현 | 안전 연산·SQLite·fixture·수신 계수 구현 | 동일 SDK 조합과 계약 테스트 기반 확보 |
| 2 · 실행 경로 | worker·Runner를 Guard에 연결 | Guard·승인·응답 게이트 구현 | 원자적 저장소·등록을 연결하고 통합·결함 주입 | ALLOW/ASK/DENY와 커밋 후 공개를 계수로 증명 |
| 3 · 화면과 공격 | bridge·GUI·승인 렌더링, T18 | 공격·경쟁 실패 수정, 계약 유지 | T/S 검증·replay·CI·약점 관리대장 | 담당 리뷰, 불변식 검증, 제한과 과잉 제한 보고 |

상세 태스크는 각 브랜치의 `docs/tasks/`에 있다. 다른 담당의 구현이 아직 없으면 필요한 계약 초안·문서·담당 모듈의 독립 작업을 진행한다. 임시로 보안 경로를 우회해 연결하지 않는다.

## Git 사용

세 사람은 별도 clone 또는 별도 worktree를 사용한다. 초기 준비 작업의 로컬 브랜치 생성 범위와 이후 구현·원격 게시 기록은 각 역할의 진행 문서에서 구분한다.

원격 브랜치가 있는 새 clone에서는 다음처럼 해당 역할 브랜치를 가져온다. 아래 명령은 A 예시이며 B·C는 이름을 바꾼다.

```bash
git fetch origin
git switch --track origin/feat/a-application
```

이미 로컬 브랜치가 있다면 `git switch feat/a-application`을 쓴다. 공통 기준이 바뀌면 작업을 커밋하고 `main`의 변경을 역할 브랜치에 병합한다. 원격을 사용하는 경우 A의 예시는 다음과 같다.

```bash
git fetch origin
git switch feat/a-application
git merge origin/main
```

작업은 역할 브랜치에서 커밋하고 `main` 대상으로 리뷰한다. 승인된 변경은 단계별로 합친다. B의 공통 계약을 먼저 반영하고 A·C가 그 변경을 가져오면 병렬 구현의 충돌을 줄일 수 있다. 공용 브랜치의 force push나 다른 사람의 커밋 삭제를 하지 않는다.

## 리뷰와 완료 보고

- 구현 변경마다 담당 범위, 관련 I/T/S ID, 검증 결과, 계약 변경 여부를 적는다.
- `domain/*`·`ports.py` 변경은 A·B·C의 합의 근거를 기록한다. 다른 변경은 소유권 표의 리뷰 담당이 확인한다.
- 판정·승인 경로는 B가 구현하고 C가 실제 수신 계수로 검증한다. 실패 수정은 관련 모듈 담당이 한다.
- Scanner 완료·관리자 활성화, AGT 모의·실제 연동을 구분해서 기록한다.
- CI 규칙 예외는 원문 13.5절에 따라 사유와 리뷰어 1인 승인을 `docs/weakness_register.md`에 남긴다. 이미 승인된 예외는 반복 승인하지 않는다.
- 날짜·성능값·스캔 결과는 실제 측정값만 기록한다. 미실행 항목은 미실행으로 둔다.
