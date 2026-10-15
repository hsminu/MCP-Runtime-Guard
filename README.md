# MCP Runtime Guard

MCP 에이전트가 읽은 자료의 기밀성 등급을 작업 단위로 누적하고, 다음 도구 호출의 **MCP 서버 경계와 최종 목적지 경계**를 검사하는 런타임 보안 게이트다. 각 호출을 `ALLOW`, `ASK`, `DENY`로 판정하고, 사람 승인과 실제 전송을 연결한다.

`main`에는 보안 코어(B), transport·Runner·Qt 앱(A), Cisco Scanner 어댑터(C)가 들어 있고 **합성 자료로 처음부터 끝까지 실행**할 수 있다.
실제 stdio·로컬 HTTP MCP 서버와 로컬 AGT 판정을 연결했고, AGT는 명시적으로 real/mock을 선택한다.
Store는 기본 메모리 대역 또는 SQLiteStore를 선택하고, Registry는 단위 대역이며 모델은 mock이다. Scanner는 아직 앱에 연결하지 않았다.
SQLite는 새 Task 실행만 연결했다. 시작 복구·launcher/endpoint 검증·실제 모델·독립 인수는 남아 있다. 담당별 남은 일은 현황 문서에 있다.

## 설치와 실행

Python 3.12와 [uv](https://docs.astral.sh/uv/)를 사용한다. winget의 특정 패치 버전을 요구하지 않는다.
uv가 `.python-version`의 3.12를 설치한다.

```powershell
uv sync --locked
uv run python -m guard_app --demo --agt-mode real
uv run python -m guard_app --demo --agt-mode real --mcp-transport http
uv run python -m guard_app --demo --agt-mode real --scenario deny
uv run python -m guard_app --demo --agt-mode real --agt-policy deny-send
uv run python -m guard_app --demo --agt-mode mock
uv run python -m guard_app --demo --agt-mode mock --store sqlite --db-path <파일>
uv run python -m examples.verify_stdio_spike --agt-mode real
uv run python -m examples.verify_http_spike --agt-mode real
uv run pytest -q
```

SQLite의 부모 디렉터리는 미리 있어야 한다. 같은 DB에 새 Task만 생성하며 이전 실행·승인·결과 불명 SEND 복구와 영속 call_seq는 미지원이다.

저장소 루트에서 실행한다. `--demo` 없는 제품 시작은 아직 제공하지 않는다.
실행 탭에서 실행 → 승인 사본 확인 → 승인 → 다음 모델 단계를 누른다.
합성 INTERNAL 읽기 후 ASK, 승인 뒤 SEND 한 번이 기본 경로다.
`--scenario allow`는 PUBLIC, `--scenario deny`는 CONFIDENTIAL 읽기를 사용한다.
실제 외부 목적지로 보내지 않고 합성 서버 내부 계수만 올린다.

`--mcp-transport http`는 앱이 띄운 `127.0.0.1` 합성 서버와 실제 TCP로 통신한다.
기본값은 `stdio`다. HTTP는 JSON·SSE 응답을 지원하고 redirect·자동 인증·POST 재시도를
사용하지 않는다. 응답 유실·timeout 뒤 SEND는 결과 불명으로 처리한다.
현재 CLI는 임의 외부 서버 URL·인증정보를 받지 않는다. 제품 endpoint 검증·등록 연결은
C 후속이며, 지원 범위와 계수는 [HTTP 연결 기록](docs/archive/tasks/a-http-integration.md)을 따른다.

`--agt-mode`는 필수다. real은 `agent-governance-toolkit-core==5.0.0`의 로컬
`PolicyEngine`으로 판정하며, 시작 실패·판정 오류·시간초과 시 차단한다.
자동 mock 전환은 없다. `--agt-policy deny-send`는 SEND만, `deny-all`은 READ부터 막는다.
실행 탭과 창 제목에 확인된 AGT 모드를 표시한다. AGT 허용은 Guard의 ASK·DENY를 완화하지 않는다.
[배포판 선정](docs/archive/tasks/a-agt-selection.md)과 [실제 연결·검증 범위](docs/archive/tasks/a-agt-integration.md)를 참고한다.

환경은 `uv.lock` 하나로 관리하고 `requirements.lock`은 거기서 생성한다.
CI(Windows)는 locked 설치 → `requirements.lock` 일치 → Ruff → 포맷 → mypy → 단위 테스트 순서로 돈다.
Ruff·포맷·mypy의 A 기준선과 명령은 `pyproject.toml` 주석과 [A+B 통합 기록](docs/archive/integration-a-b.md)에 있다.

## 개발 문서

관리하는 문서는 이 README와 아래 7개다. 결정은 계약 문서, 진행 상태는 담당 현황 문서에 기록한다.

| 문서 | 용도 |
| --- | --- |
| [기술 설계 및 개발 문서](docs/technical-design.md) | 제공받은 개발문서 원본 사본. 상세 설계, 테스트 T01–T38·S01–S05, ADR 기준 |
| [3인 협업 규칙](docs/team-workflow.md) | 모듈 소유권, 리뷰 담당, 개발 순서, 브랜치 사용법 |
| [공통 계약](docs/contracts.md) | 사용자 결정, 합의할 계약과 체크리스트, 미결 요청 |
| [보안약점 관리대장](docs/weakness_register.md) | 검사 실행 기록, 발견 항목과 조치, 규칙 예외 |
| [A 현황](docs/a-status.md) | A 구현·검증·남은 조건 |
| [B 현황](docs/b-status.md) | B 보안 코어·SQLite 체크리스트 |
| [C 현황](docs/c-status.md) | C 보안 기반·검증 체크리스트 |

[공통 에이전트 지침](AGENTS.md)은 에이전트가 읽는 작업 규칙이다. 완료 기록과 이전 작업 기록·실험 근거는 [docs/archive](docs/archive/)에 있다. 기록은 한 번 쓰고 고치지 않는다.

## 3인 작업 분담

| 담당 | 작업 브랜치 | 주요 책임 | 현황 문서 |
| --- | --- | --- | --- |
| A · 애플리케이션 | `feat/a-application` | MCP transport, AGT 연결, 모델·Runner, Qt↔asyncio bridge, GUI | [A 현황](docs/a-status.md) |
| B · 보안 코어·SQLite | `feat/b-security-core` | domain·ports, 정책·등급 병합, Catalog, GuardService, 승인·응답 게이트, SQLite·스키마·복구·저장소 단위 테스트 | [B 현황](docs/b-status.md) |
| C · 보안 기반·검증 | `feat/c-security-platform` | 위험 연산 모듈, 서버 등록·Scanner, 저장소 리뷰·독립 검증, fixture·계수, 통합·공격·실험, CI | [C 현황](docs/c-status.md) |

`main`은 공통 문서와 리뷰를 마친 변경을 모으는 브랜치다. 소유권과 리뷰는 [협업 규칙](docs/team-workflow.md), 개발문서와 다른 담당 배정(예: SQLite는 B)은 [공통 계약](docs/contracts.md#사용자-결정)에 있다.

## 해결할 문제

공개 문서를 읽고 외부 이슈를 작성하는 호출과, 내부 기획서를 읽은 뒤 같은 이슈를 작성하는 호출은 도구와 인자가 비슷해도 위험이 다르다. Guard는 작업의 누적 등급을 다음 호출에 적용한다. 최종 목적지가 내부여도 중간의 외부 MCP 서버가 인자를 받는다면 서버 경계에서 검사한다.

- 자료 등급: `PUBLIC < INTERNAL < CONFIDENTIAL`.
- 기본 추적 방식: Task별 high-watermark를 유지하는 `accumulate`.
- `Unclassified`는 별도 플래그다. 출처를 확정하지 못하면 작업을 정지하며, 기존 작업에서 해제하지 않는다.
- `stdio`·Streamable HTTP는 접속 방식이다. 내부·외부 신뢰는 관리자 등록 정보로 결정한다.
- AGT는 거부권만 가진다. AGT 허용이 Guard의 `ASK`·`DENY`를 완화하거나 사람 승인을 대체하지 않는다.

## 호출 흐름

```mermaid
flowchart LR
    Runner[Model / Runner] --> Guard[GuardService.submit]
    Guard --> Catalog[Catalog / 등록 정보]
    Catalog --> Policy[정책 / AGT 합성]
    Policy --> Deny[DENY: 실행 없음]
    Policy --> Ask[ASK: 사람 승인]
    Policy --> Allow[ALLOW: 실행 예약]
    Ask --> Recheck[재평가 / 승인 원자적 소비]
    Recheck --> Transport[MCP Transport]
    Allow --> Transport
    Transport --> Gate[응답 격리 / 분류]
    Gate --> Commit[상태와 감사 커밋]
    Commit --> Release[결과 공개]
```

관리 대상 도구 실행의 유일한 입구는 `GuardService.submit()`이다. 승인 후 실행도 같은 Guard의 재평가·예약·dispatch 경로를 따른다. 결과는 **수신 → 격리 → 분류 → 상태 커밋 → 공개** 순서로 처리한다.

## 기본 정책

먼저 `effective_label > boundary.max_label`이면 `DENY`다. 이를 통과한 경계에는 아래 기본 행렬을 적용한다.

| 작업의 유효 등급 | internal | approved_external | general_external |
| --- | --- | --- | --- |
| PUBLIC | ALLOW | ALLOW | ALLOW |
| INTERNAL | ALLOW | ASK | ASK |
| CONFIDENTIAL | ALLOW | DENY | DENY |

모든 호출은 서버 경계를 검사하고, `SEND`는 목적지 경계도 검사한다. 서버·목적지·AGT 결과 중 가장 엄격한 판정이 최종 판정이다. 외부 경계의 `max_label`은 `INTERNAL` 이하만 허용하므로 기밀 자료의 외부 전송은 승인으로 우회할 수 없다. 기밀의 내부 전송도 해당 경계의 수신 권한이 충분해야 한다.

`ASK`는 요청 사본의 해시가 일치하고, 현재 상태로 재평가를 통과하며, 승인 1건을 원자적으로 소비했을 때 최대 1회 실행한다. 쓰기 호출의 결과가 불명이면 `UNKNOWN_OUTCOME`으로 작업을 잠그고 자동 재전송하지 않는다.

## 구성과 담당

| 구성 | 책임 | 담당 |
| --- | --- | --- |
| `domain`, `ports` | 신뢰 출처가 분리된 불변 모델과 모듈 간 계약 | B, A·C 공동 리뷰 |
| `core` | 외부 I/O 없는 정책 판정·등급 병합·결정표 | B |
| `services` | Guard·승인·응답 게이트(B), Runner·bridge(A), registry(C) | 역할별 분담 |
| `adapters` | MCP·AGT·모델(A), Cisco Scanner(C) | 역할별 분담 |
| `security` | 위험 연산(C), 예외 판정 변환·승인 해시(B) | 역할별 분담 |
| `storage` | SQLite·스키마·복구·원자적 트랜잭션·단위 테스트 | B 구현, C 리뷰·독립 검증 |
| `ui` | 실행·승인·서버 검사·감사 화면 | A |
| `fixtures`, 테스트, CI | 실제 수신 계수로 집행을 검증 | C 주도 |

표는 전체 제품의 소유권이다. 구현 여부는 담당별 현황 문서에서 확인한다. 전체 예정 구조는 개발문서 2.1절에 있다.

## 보안 불변식

| ID | 반드시 지킬 조건 |
| --- | --- |
| I1 | 관리 대상 도구 실행은 Guard를 거친다. transport를 외부에 노출하지 않는다. |
| I2 | DENY는 승인 경로를 포함해 dispatch되지 않는다. |
| I3 | ASK는 일치하는 승인의 원자적 소비 후 최대 1회 실행한다. |
| I4 | 누적 등급은 내려가지 않고 Unclassified는 해제되지 않는다. |
| I5 | 결과는 보안 상태 커밋 후에만 모델·UI·로그로 공개한다. |
| I6 | 판단 불가와 예외는 DENY 또는 작업 정지로 처리한다. |
| I7 | 등급·경계·승인자·Task 식별자는 신뢰 설정과 실행 상태에서 도출한다. |
| I8 | 결과 불명의 쓰기는 자동 재전송하지 않는다. |
| I9 | 원문·인증정보·전체 인자를 기본 로그에 기록하지 않는다. |
| I10 | 도구 정의 변경 시 서버를 PENDING으로 돌리고 기존 승인을 무효화한다. |

`DISPATCHING` 예약 전의 예외는 `Denied`로 처리한다. 예약 후의 예상하지 못한 예외는 READ의 결과 미공개·Task 정지, SEND의 결과 불명으로 처리한다. 실제로 나갔을 수 있는 요청을 차단된 것으로 보고하지 않는다.

## 개발 환경과 진행 순서

설계 기준은 Python 3.12, MCP Python SDK 1.x, Pydantic, PySide6, SQLite, pytest다. 앱·보안 코어·AGT·Scanner 의존성은 `pyproject.toml`·`uv.lock`에 고정했다. 공통 조합의 합의 기록은 ENV-01로 남아 있다.

1. 세 사람이 공통 문서와 각자의 현황 문서를 읽는다.
2. B가 모델·포트·명령·이벤트 계약을 제안하고 A·C와 확정한다.
3. A는 연결 호환성을, C는 fixture·수신 계수를 준비하고 B는 순수 정책을 구현한다.
4. Guard·저장소·transport·승인·응답 게이트를 연결해 보안 불변식을 검증한다.
5. GUI를 붙이고 공격·결함 주입·replay·CI를 완료한다.

다음 명령은 각 담당자의 **별도 clone 또는 작업 디렉터리**에서 사용한다. 같은 디렉터리에서 세 사람이 브랜치를 번갈아 바꾸지 않는다.

```bash
# A
git switch feat/a-application

# B
git switch feat/b-security-core

# C
git switch feat/c-security-platform
```

새 clone이면 `git switch --track origin/<브랜치>`를 쓴다. 상세 동기화·리뷰 절차는 [협업 규칙](docs/team-workflow.md)에 있다.

## 검증 목표와 보호 범위

차단 여부는 화면 문구 대신 `approval_consumed`, `host_dispatch`, 실제 HTTP `tools/call` 수, `receiver_effect`로 확인한다. DENY·승인 전 ASK의 전송 계수는 0, 승인된 ASK의 소비·dispatch·수신 부작용은 각각 최대 1이어야 한다.

replay는 `accumulate`를 주 대조군 `last_read` 및 참고군 `none`과 비교한다. 제한 성공률, 과잉 제한률, 정상 작업 완료율, 정책 지연 p50·p95를 보고한다. 테스트 목록과 기대 결과는 개발문서 14장·부록 B를 따른다.

MVP는 기밀성 추적과 관리 대상 `tools/call` 경로를 보호한다. 모델 API로 보내는 프롬프트 자체, 원격 서버 수신 후 재전송, 호스트 OS·로컬 악성 프로세스, 사용자의 직접 민감정보 입력, `resources/read`·`prompts/get`, 무결성 추적은 설계 범위 밖이다. 시연에는 합성 자료와 승인된 모델 환경을 사용한다.
