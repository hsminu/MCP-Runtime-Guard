# 작업 브랜치 지침

공통 규칙은 [AGENTS.md](AGENTS.md), 상세 설계는 [개발문서](docs/technical-design.md), 계약 기준은 [공통 계약 체크리스트](docs/contracts.md)를 따른다.

## 담당 선택

먼저 `git branch --show-current`를 확인하고 아래 문서를 읽는다. 이 파일은 세 작업 브랜치에서 같은 내용으로 유지한다. 역할별 지침은 담당 문서에 기록한다.

| 현재 브랜치 | 담당 | 필수 작업 문서 |
| --- | --- | --- |
| `feat/a-application` | A · 애플리케이션 | `docs/tasks/a-application.md`, `docs/integration-notes.md` |
| `feat/b-security-core` | B · 보안 코어 | `docs/tasks/b-security-core.md`, `docs/contracts.md` |
| `feat/c-security-platform` | C · 보안 기반·검증 | `docs/tasks/c-security-platform.md`, `docs/weakness_register.md` |
| `main` | 공통 문서·통합 | `README.md`, `docs/team-workflow.md` |

사용자가 별도의 담당이나 하위 작업 브랜치를 명시하면 그 역할 문서를 따른다. 이름만으로 역할을 알 수 없으면 기존 작업 지시를 확인한다. 필요한 문서가 없으면 다른 역할 지침으로 대체하지 않는다.

## 착수와 인수

1. 사용자 요청이 문서 준비인지 구현인지 확인한다. 최초 준비 작업의 소스 뼈대 제외 조건은 당시 범위의 기록이며, 후속 구현은 승인된 역할 범위에서 진행한다.
2. 작업 트리와 계약 상태를 확인하고 담당 문서의 우선순위대로 진행한다.
3. 자기 모듈과 단위 테스트를 맡는다. 통합·공격·결함 주입·실험은 C 담당이다.
4. 계약 변경이 필요하면 B의 초안과 A·C의 합의 기록에 반영한다.
5. 변경 결과와 실제 검증, 관련 I/T/S ID, 다음 담당에게 전달할 정보를 기록한다.

실제 실행 여부는 Guard·승인 상태와 수신 계수로 판단한다. 모의 외부 연동을 실제 연동처럼 보고하지 않는다. 실행하지 않은 테스트나 스캔을 완료 표시하지 않는다.
