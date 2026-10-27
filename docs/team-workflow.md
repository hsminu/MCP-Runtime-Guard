# 3인 협업 규칙

설계 기준은 [기술 설계 및 개발 문서](technical-design.md)이며, 현행 역할·리뷰는 이 문서의 소유권 표를 따른다. 확정 결정은 [공통 계약](contracts.md#사용자-결정)에 날짜와 함께 기록하고, 같은 변경에서 개발문서의 해당 절도 고친다(2026-10-17 결정).

## 브랜치와 작업 문서

| 담당 | 브랜치 | 먼저 읽을 문서 |
| --- | --- | --- |
| A | `feat/a-application` | `docs/a-status.md` |
| B | `feat/b-security-core` | `docs/b-status.md`, `docs/contracts.md` |
| C | `feat/c-security-platform` | `docs/c-status.md`, `docs/weakness_register.md` |

`main`의 공통 문서 커밋을 세 브랜치가 공유한다. 각 담당은 자기 현황 문서와 자기 완료 기록만 고친다. 그래야 세 브랜치를 합칠 때 서로 덮어쓰지 않는다.

## 문서 작성

| 문서 | 작성 |
| --- | --- |
| `docs/a-status.md`, `docs/b-status.md`, `docs/c-status.md` | 각 담당. 자기 브랜치에서 작업과 함께 고친다 |
| 완료 기록 `docs/archive/a/`, `docs/archive/b/`, `docs/archive/c/` | 각 담당. 항목을 끝낼 때 한 번 쓴다 |
| 그 밖의 모든 문서(`README.md`, `AGENTS.md`, `docs/technical-design.md`, `docs/contracts.md`, `docs/team-workflow.md`, `docs/weakness_register.md`) | 사용자만 작성·수정한다 |

팀원이 공통 문서를 바꾸고 싶으면 `main` 대상 PR로 제안한다. 사용자가 검토하고 필요하면 문구를 고쳐 병합한다. 개인 연락은 논의에만 쓰고 결론은 PR에 남긴다. 공통 문서는 역할 브랜치에서 고치지 않는다.

## 현황 문서와 완료 기록

현황 문서는 세 담당이 같은 틀을 쓴다. 남은 일과 막힌 곳을 보는 체크리스트이고, 세부는 완료 기록에 둔다.

```markdown
# C 현황

갱신: 2026-10-15 · main `<커밋>` · 이전 상세: [보관본](archive/status/c-status-2026-10-15.md)

## 막힌 것

| 기다리는 것 | 누구 | 막힌 항목 |
| --- | --- | --- |
| ScanReport 번역 계약 | B | C4.9 |

## C4 · Registry와 Scanner

- [x] C4.5 LLM·YARA 내부 집계 · [기록](archive/c/C4.5-aggregation.md)
- [ ] C4.9 ScanReport 번역 — 막힘: B 계약
```

- **한 항목은 한 줄**이다. 체크박스, ID, 짧은 이름을 쓰고 막혔으면 `— 막힘: 무엇`, 끝났으면 `· [기록](...)`을 붙인다.
- **ID**는 기존 번호를 유지한다. 새 항목은 같은 묶음 안에서 다음 번호를 붙인다(예: C4.17). `B6-01`처럼 하이픈이 붙은 ID는 `contracts.md`의 계약 요청이다.
- **`[x]`는 담당 구현이 끝난 항목**이다. 다른 담당의 인수나 합의가 남았으면 그 부분을 별도 `[ ]` 항목으로 둔다.
- **넣지 않는 것:** 커밋 해시·테스트 수·날짜별 경과·역할 설명·읽을 문서 목록. 역할은 이 문서, 읽을 순서는 `AGENTS.md`에 있다.
- **막힌 것 표**는 다른 사람이나 결정을 기다리는 항목만 모은다.

완료 기록 규칙:

1. 체크리스트 항목 하나를 끝내면 기록 하나를 쓴다. 경로는 `docs/archive/<담당>/<ID>-<짧은 이름>.md`다. 날짜별 일지는 만들지 않는다.
2. 기록에는 한 일, 실제 검증, 남은 한계, PR 번호를 적는다. 쓴 뒤에는 고치지 않는다. 다시 작업하면 새 항목과 새 기록을 만든다.
3. 실험 근거 JSON은 `docs/archive/evidence/`에 두고 기록에서 링크한다.
4. 요청·회신·논의는 PR·이슈로 한다. 파일로 만들지 않는다.
5. 2026-10-15 이전에 끝난 항목은 기존 archive 문서나 PR 번호를 근거로 링크한다. 그때의 전체 현황은 `docs/archive/status/`에 보관했다.

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
| `adapters/mcp_transport/` | A | B |
| `core` 접근 규칙 함수, 기존 AGT 코드 제거 | B | C. AGT 제거 중 A 소유 파일을 고친 부분은 A |
| `AccessChecker` 연결 | A | B |
| `services/runner.py` (Codex 세션 관리자) | A | B·C |
| Codex L1 설정·모델 정보 파일·설치 스크립트·L2 시작 시험 | C | A |
| 로컬 파일 MCP 서버 | C | B |
| `services/bridge.py`, `ui/*`, `__main__.py` | A | C |
| `fixtures/*`, `attack_fixtures/*` | C | A |
| `tests/contract`, `integration`, `attacks`, `faults`, `experiments` | C | 관련 모듈 담당, C 소유 모듈의 테스트는 B |
| CI, Semgrep, import-linter | C | B |
| 모듈별 단위·성질 테스트 | 해당 모듈 담당 | 해당 모듈 리뷰 담당 |

`pyproject.toml`·`uv.lock` 같은 공통 설정·lock은 `main` 대상 PR로 바꾼다. 편집 담당 합의는 [ENV-01](contracts.md#미결-요청)로 남아 있다. 원문에 없는 파일 소유권을 임의로 확정하지 않는다.

저장소 단위 테스트·S03의 바인딩 SQL 단위 부분은 B가 작성하고 C가 리뷰한다. 저장소 계약·통합·공격·결함 주입·실험 테스트는 C가 작성하고 B가 리뷰한다. C는 Registry·Scanner 서비스의 의미를 구현하고, 영속화에는 B 저장소의 검토된 인터페이스를 사용한다. 단일 writer를 공유하는 계약을 정하며 `services/registry.py`에 별도 SQL 실행 경로를 만들지 않는다. SQL 제한 Semgrep·import-linter 규칙은 C가 관리하고 B의 저장소 구현과 함께 검토한다.

## 개발 순서와 인수 조건

| 단계 | A | B | C | 완료 조건 |
| --- | --- | --- | --- | --- |
| 0 · 공통 계약 | Transport·AccessChecker·Codex 세션 관리자·UI 이벤트 요구 제공 | domain·ports·결과·상태 계약 초안 | Store·Scanner·Registry·계수 요구 제공 | 세 사람이 계약 및 미확정 항목 확인 |
| 1 · 독립 구현 | SDK stdio·HTTP 호환 확인, 어댑터·Codex 세션 관리자 구현 | 순수 정책·접근 규칙·등급 병합·Catalog·SQLite·단위 테스트 구현, AGT 코드 제거 | 안전 연산·fixture·수신 계수, Codex L1·L2, 로컬 파일 서버 구현, 저장소 계약 검증 준비 | 동일 SDK 조합과 계약 테스트 기반 확보 |
| 2 · 실행 경로 | worker·Runner를 Guard에 연결 | Guard·승인·응답 게이트와 원자적 저장소 연결 | Registry·Scanner 연결, 저장소 리뷰·통합·결함 주입 | ALLOW/ASK/DENY와 커밋 후 공개를 계수로 증명 |
| 3 · 화면과 공격 | bridge·GUI·승인 렌더링, T18 | 공격·경쟁 실패 수정, 계약 유지 | T/S 검증·replay·CI·약점 관리대장 | 담당 리뷰, 불변식 검증, 제한과 과잉 제한 보고 |

상세 태스크와 진행 상태는 담당 현황 문서에 있다. 다른 담당의 구현이 아직 없으면 필요한 계약 초안·문서·담당 모듈의 독립 작업을 진행한다. 임시로 보안 경로를 우회해 연결하지 않는다.

## Git 사용

세 사람은 별도 clone 또는 별도 worktree를 사용한다.

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

## 담당 변경

1. 담당을 바꾸면 이 문서의 소유권 표와 `docs/contracts.md`의 사용자 결정 표에 기록한다. 사용자 결정은 결정 근거로 적고, A·B·C의 확인을 대신 적지 않는다.
2. 변경은 문서 PR로 `main`에 반영하고 각 담당은 `main`을 자기 브랜치에 병합한다. 다른 담당의 브랜치를 통째로 병합해 담당 변경을 전달하지 않는다.

## 리뷰와 완료 보고

- 구현 변경마다 PR 설명과 완료 기록에 담당 범위, 관련 I/T/S ID, 검증 결과, 계약 변경 여부를 적는다.
- `domain/*`·`ports.py` 변경은 A·B·C의 합의 근거를 기록한다. 다른 변경은 소유권 표의 리뷰 담당이 확인한다.
- 판정·승인 경로는 B가 구현하고 C가 실제 수신 계수로 검증한다. 실패 수정은 관련 모듈 담당이 한다.
- Scanner 검사 완료·경고와 사용자 활성화를 구분해서 기록한다.
- CI 규칙 예외는 개발문서 13.5절에 따라 사유와 리뷰어 1인 승인을 `docs/weakness_register.md`에 남긴다. 이미 승인된 예외는 반복 승인하지 않는다.
- 날짜·성능값·스캔 결과는 실제 측정값만 기록한다. 미실행 항목은 미실행으로 둔다.
