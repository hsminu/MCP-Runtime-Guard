# A17 · AGT 배포판 선정

2026-10-13, A 브랜치. 사용자의 배포판 선정 요청에 따라 현재 Windows MVP의
연결 대상으로 **Microsoft AGT `agent-governance-toolkit-core==5.0.0`**을 선정한다.
사용 범위는 `agentmesh.governance.policy.PolicyEngine`의 로컬 판정이다.
선정 이후 [A18 실제 어댑터](a-agt-integration.md)를 연결했다. 공통 규칙 계약·C 독립 인수는 남아 있다.

## 선택과 대안

| 후보 | 확인한 배포 상태 | 결정 |
| --- | --- | --- |
| `agent-governance-toolkit-core==5.0.0` | Python >=3.11, 범용 Python wheel, 정책 판정 API 포함. Windows Python 3.12.15 설치·실행 확인 | 현재 MVP의 로컬 판정 배포판으로 선정 |
| `agent-governance-toolkit[full]==4.1.0` | 메타패키지가 core `<5.0`을 요구하고 CLI·통합·프로토콜 패키지도 설치 | 현재 선정한 core와 맞지 않고 필요한 범위보다 넓어 제외 |
| `agent-control-specification==0.3.1b1` | 공개 wheel은 Linux x86-64. 공식 문서가 PyPI 배포판과 최신 소스 API의 차이를 명시 | Windows MVP의 즉시 설치 대상으로 제외 |
| `agent-control-spec==0.4.0a4` | upstream ACS, Python >=3.11. 조회 시 공개 파일은 Linux x86-64 wheel과 소스 배포 | Windows 빌드 검증이 없어 이번 선택에서 제외. 후속 ACS 전환 시 재검토 |

현재 AGT의 주 정책 런타임은 ACS지만, 공식 이전 문서는 **AgentMesh Wire Protocol
정책 엔진이 별도로 유지되는 제품**이라고 구분한다. 선정한 API는 이 AgentMesh
규칙 엔진이다. `AgentControl`·`HostSession` 또는 제거된 Agent OS 정책 API와 섞지 않는다.
Windows wheel이 없다는 사실은 Windows 소스 빌드가 불가능하다는 뜻이 아니다.

선정 근거:

- `evaluate(agent_did, context, stage="pre_tool")`로 판정만 반환받을 수 있다.
- MCP 호출·사람 승인·Task 등급과 상태는 기존 Guard가 계속 소유할 수 있다.
- 외부 계정이나 원격 판정 서비스 없이 로컬 합성 메타데이터로 실행했다.
- 기존 MCP·Pydantic·JSON Schema·HTTPX·Qt 버전을 유지한 별도 환경에서 설치됐다.

## 실제 검증

제품 `.venv`와 분리한 `.cache/agt-selection/venv`에서 확인했다.

| 항목 | 결과 |
| --- | --- |
| 환경 | Windows x64, CPython 3.12.15 |
| 함께 설치한 주요 버전 | MCP 1.29.0, Pydantic 2.14.0, jsonschema 4.25.1, HTTPX 0.28.1, PySide6 6.12.0 |
| 의존성 검사 | `uv pip check`: 56개 설치 배포판의 선언된 의존성 충돌 없음 |
| 판정 스파이크 | 정책 없음, 허용, 명시적 거절, 미등록 동작, 다른 agent, 필드 누락, 거절 우선, 승인 요구의 8경로 통과 |
| 외부 I/O 관측 | 스파이크 import·판정 중 Python audit hook의 socket connect/DNS·프로세스 실행 이벤트 0건 |
| 정적 검사 | 스파이크 Ruff check 통과 |
| wheel SHA-256 | `6f8494c5ada3d5824f90e56d5e450f8ca6fba9759c107f077bdc0c87b7c496d2`, PyPI 메타데이터와 일치 |

재현 스크립트는 [verify_agt_candidate.py](../../examples/verify_agt_candidate.py)다.
새 Python 3.12 가상환경에 다음 버전을 설치하고 저장소 루트에서 실행한다.

```powershell
uv pip install --python <검증환경-python> agent-governance-toolkit-core==5.0.0 mcp==1.29.0 pydantic==2.14.0 jsonschema==4.25.1 httpx==0.28.1 PySide6==6.12.0
<검증환경-python> examples/verify_agt_candidate.py
uv pip check --python <검증환경-python>
```

판정은 `PolicyEngine(conflict_strategy="deny_overrides")`, 명시적인 agent 대상,
`default_action="deny"`를 사용했다. agent 대상을 생략하면 규칙이 적용되지 않고 거절된다.
core 설치 후에도 `agentmesh` import가 구 배포판 이름의 DeprecationWarning을 출력하는 것을
관찰했다. 패키지 버전은 import 이름과 별개로 `importlib.metadata`에서 검증한다.

이 결과는 SDK 선택 근거다. Qt 전체 앱, Guard의 실제 dispatch·wire·수신 부작용,
SDK 전체의 네트워크 격리, 공격 저항성 또는 제품 lock 재현을 증명하지 않는다.

## A18 연결 기준과 남은 검증

- 기존 `AccessChecker` 포트에 번역한다. `allowed is True`이면서 `action == "allow"`인
  결과만 허용 후보로 쓰고 Guard의 ASK·DENY를 완화하지 않는다(I1·I2·I3).
- SDK의 `require_approval`은 허용으로 바꾸지 않는다. AGT의 별도 승인·도구 실행 경로를
  연결하지 않고 기존 Guard의 사람 제어 계약을 유지한다.
- 입력은 신뢰 등록의 server/tool ID·종류 등 최소 메타데이터로 시작한다.
  문서 본문·전체 인자·인증정보·Task/운영자 원문 식별자는 제공하지 않는다(I7·I9).
- 규칙의 의미·버전은 B 검토 대상이다. 초기에는 검증한 제한된 규칙 형식만 만들고
  임의 표현식·외부 policy URL·동적 evaluator·SDK 자체 파일 로더를 노출하지 않는다.
  공개 SDK의 일반 표현식 처리 전체가 검증된 것으로 간주하지 않는다.
- 동기 판정을 Qt/asyncio 루프에서 직접 실행하지 않는다. 규칙·입력 크기 상한,
  Guard 제한시간·취소·늦은 결과 폐기, SDK 오류·잘못된 결과의 `AGT_ERROR` 변환을
  어댑터 검증에 포함한다(I6, T26·T27). 스파이크는 이 어댑터를 구현하지 않는다.
- 실제 모드 실패를 mock으로 전환하지 않는다. UI 실제 모드 표시까지 A18에 연결했고,
  B6의 `agt_mode`·규칙 버전 영속 감사, C의 거절·오류 시 dispatch 0 인수는 남아 있다.

선정 스파이크 시점에는 앱·lock을 변경하지 않았다. 이후 사용자의 진행 요청에 따라
A18에서 `pyproject.toml`·`uv.lock`에 버전을 고정하고 명시적 real/mock 실행을 연결했다.

## 공식 근거

- [AGT 패키지 구분](https://microsoft.github.io/agent-governance-toolkit/packages/)
- [core 5.0.0 배포](https://pypi.org/project/agent-governance-toolkit-core/5.0.0/)
- [메타패키지 4.1.0 배포 메타데이터](https://pypi.org/pypi/agent-governance-toolkit/4.1.0/json)
- [ACS 호스트 SDK와 PyPI 배포 상태](https://microsoft.github.io/agent-governance-toolkit/packages/agent-control-specification/)
- [upstream ACS 0.4.0a4 배포 메타데이터](https://pypi.org/pypi/agent-control-spec/0.4.0a4/json)
- [v4 제거 및 AgentMesh 정책 엔진의 별도 유지](https://microsoft.github.io/agent-governance-toolkit/v4-removal/)
