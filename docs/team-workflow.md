# 3인 협업 규칙

설계 기준은 [기술 설계 및 개발 문서](technical-design.md)이며, 현행 역할·리뷰는 이 문서의 소유권 표를 따른다. 2026-10-12 사용자가 **"sqlite b로 넘기자"**고 결정해 SQLite 구현을 B로 이관했다. 기술 설계 원문 0.5·2.1절 등의 `storage` C 표기와 저장소 단위 테스트 담당 표기는 이 결정으로 대체한다. 원문은 보존한다.

B1–B5 구현 초안은 B 브랜치에 있다. SQLite 이관은 담당 배정이며, Store API의 세부 합의·SQLite 구현·독립 인수 완료를 뜻하지 않는다. 기록은 [담당 변경](contracts.md#사용자-결정), 인수 작업은 [B6 · SQLite](b-status.md)를 따른다.

현재 A 구현·검증·미완료 조건은 [A 작업 현황](a-status.md), 임시 앱 계약은 [리뷰 반영 기록](archive/tasks/a-review-fixes.md)을 따른다.

## 브랜치와 작업 문서

| 담당 | 브랜치 | 먼저 읽을 문서 |
| --- | --- | --- |
| A | `feat/a-application` | `docs/a-status.md` |
| B | `feat/b-security-core` | `docs/b-status.md`, `docs/contracts.md` |
| C | `feat/c-security-platform` | `docs/c-status.md`, `docs/weakness_register.md` |

`main`의 공통 문서 커밋을 세 브랜치가 공유한다. 각 담당은 자기 현황 문서만 고친다. 그래야 세 브랜치를 합칠 때 서로 덮어쓰지 않는다. 새 작업 일지·요청·회신 파일은 만들지 않고 PR·이슈 댓글로 남긴다. 이전 기록은 `docs/archive/`에 있으며 갱신하지 않는다.

## 문서 작성

| 문서 | 작성 |
| --- | --- |
| `docs/a-status.md`, `docs/b-status.md`, `docs/c-status.md` | 각 담당. 자기 브랜치에서 작업과 함께 고친다 |
| 그 밖의 모든 문서(`README.md`, `AGENTS.md`, `docs/technical-design.md`, `docs/contracts.md`, `docs/team-workflow.md`, `docs/weakness_register.md`) | 사용자만 작성·수정한다 |

팀원이 공통 문서를 바꾸고 싶으면 `main` 대상 PR로 제안한다. 사용자가 검토하고 필요하면 문구를 고쳐 병합한다. 개인 연락은 논의에만 쓰고 결론은 PR에 남긴다. 공통 문서는 역할 브랜치에서 고치지 않는다.

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
| `storage/*` · SQLite·스키마·마이그레이션·감사 저장·영속 순번·시작 복구 | B | C |
| `services/registry.py`, `adapters/cisco.py` | C | B |
| `adapters/mcp_transport.py` | A | B |
| `adapters/agt.py` 연결·번역 | A | B, 합성 규칙은 B 담당 |
| `adapters/model_client.py`, `services/runner.py` | A | B |
| `services/bridge.py`, `ui/*`, `__main__.py` | A | C |
| `fixtures/*`, `attack_fixtures/*` | C | A |
| `tests/contract`, `integration`, `attacks`, `faults`, `experiments` | C | 관련 모듈 담당, C 소유 모듈의 테스트는 B |
| CI, Semgrep, import-linter | C | B |
| 모듈별 단위·성질 테스트 | 해당 모듈 담당 | 해당 모듈 리뷰 담당 |

`config/`·패키징·의존성 lock의 세부 편집 담당은 착수 때 [계약 체크리스트](contracts.md)에 합의해서 기록한다. 원문에 없는 파일 소유권을 임의로 확정하지 않는다.

저장소 단위 테스트·S03의 바인딩 SQL 단위 부분은 B가 작성하고 C가 리뷰한다. 저장소 계약·통합·공격·결함 주입·실험 테스트는 C가 작성하고 B가 리뷰한다. C는 Registry·Scanner 서비스의 의미를 구현하고, 영속화에는 B 저장소의 검토된 인터페이스를 사용한다. 단일 writer를 공유하는 계약을 정하며 `services/registry.py`에 별도 SQL 실행 경로를 만들지 않는다. SQL 제한 Semgrep·import-linter 규칙은 C가 관리하고 B의 저장소 구현과 함께 검토한다.

## 개발 순서와 인수 조건

| 단계 | A | B | C | 완료 조건 |
| --- | --- | --- | --- | --- |
| 0 · 공통 계약 | Transport·AccessChecker·ModelClient·UI 이벤트 요구 제공 | domain·ports·결과·상태 계약 초안 | Store·Scanner·Registry·계수 요구 제공 | 세 사람이 계약 및 미확정 항목 확인 |
| 1 · 독립 구현 | SDK stdio·HTTP·AGT 호환 확인, 어댑터 구현 | 순수 정책·등급 병합·Catalog·SQLite·단위 테스트 구현 | 안전 연산·fixture·수신 계수 구현, 저장소 계약 검증 준비 | 동일 SDK 조합과 계약 테스트 기반 확보 |
| 2 · 실행 경로 | worker·Runner를 Guard에 연결 | Guard·승인·응답 게이트와 원자적 저장소 연결 | Registry·Scanner 연결, 저장소 리뷰·통합·결함 주입 | ALLOW/ASK/DENY와 커밋 후 공개를 계수로 증명 |
| 3 · 화면과 공격 | bridge·GUI·승인 렌더링, T18 | 공격·경쟁 실패 수정, 계약 유지 | T/S 검증·replay·CI·약점 관리대장 | 담당 리뷰, 불변식 검증, 제한과 과잉 제한 보고 |

상세 태스크와 진행 상태는 담당 현황 문서에 있다. 다른 담당의 구현이 아직 없으면 필요한 계약 초안·문서·담당 모듈의 독립 작업을 진행한다. 임시로 보안 경로를 우회해 연결하지 않는다.

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

## 담당 변경과 작업 전달

1. 담당 변경은 이 문서의 소유권 표와 `docs/contracts.md`에 근거·범위·리뷰 책임을 기록한다. 사용자 결정은 결정 근거로 기록하며 실제 A·B·C 확인을 대신 작성하지 않는다.
2. 공통 지침·담당표·B6 작업 문서·C 요구 문서 변경을 문서 PR로 묶어 `main`에 반영한다. C 브랜치의 제품 구현 전체를 B에 병합하는 방식으로 담당 변경을 전달하지 않는다.
3. B와 A는 각자의 작업을 저장한 뒤 공통 문서 커밋을 가져온다. B는 자기 역할 문서·인수 안내의 이전 SQLite 제외 문구를 갱신한다. 담당 변경의 재승인을 기다리지 않고 배정을 적용하되, 미확정 API는 기존 계약 절차로 검토한다.
4. 개인 메시지는 결정·문서 PR 또는 커밋·다음 태스크 링크를 알리는 데 사용한다. 전달 여부·착수·구현·검증 상태는 문서와 PR에 구분해서 남긴다. 로컬 문서 수정만으로 다른 clone에 전달됐다고 보고하지 않는다.
5. B는 API·DTO·상태/오류 규칙을 먼저 검토 가능한 변경으로 제공한다. C는 독립 인수 기준을 대조하고 A는 시작 게이트·제어 큐·호출 결과 영향을 확인한다. SQLite 구현·단위 검증 후 C 계약·결함 주입, A 연결 후 실제 계수 검증 순으로 진행한다.

## 리뷰와 완료 보고

- 구현 변경마다 담당 범위, 관련 I/T/S ID, 검증 결과, 계약 변경 여부를 적는다.
- `domain/*`·`ports.py` 변경은 A·B·C의 합의 근거를 기록한다. 다른 변경은 소유권 표의 리뷰 담당이 확인한다.
- 판정·승인 경로는 B가 구현하고 C가 실제 수신 계수로 검증한다. 실패 수정은 관련 모듈 담당이 한다.
- Scanner 완료·관리자 활성화, AGT 모의·실제 연동을 구분해서 기록한다.
- CI 규칙 예외는 원문 13.5절에 따라 사유와 리뷰어 1인 승인을 `docs/weakness_register.md`에 남긴다. 이미 승인된 예외는 반복 승인하지 않는다.
- 날짜·성능값·스캔 결과는 실제 측정값만 기록한다. 미실행 항목은 미실행으로 둔다.
