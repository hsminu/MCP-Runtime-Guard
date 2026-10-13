# A18 · 실제 AGT 로컬 판정 연결

2026-10-13, `feat/a-application`. [A17 선정](a-agt-selection.md) 이후 사용자의
진행 요청으로 `agent-governance-toolkit-core==5.0.0`을 앱에 연결했다.
합성 앱에서 `--agt-mode real`을 선택하면 실제 SDK가 판정하고, `mock`은 기존 규칙 대역을 사용한다.
실제 모드의 시작 실패·오류를 mock으로 바꾸지 않는다.

## 변경 범위와 계약

- A 소유: `adapters/agt_real.py`, mock 문서, 합성 bootstrap·검증 스크립트,
  CLI·모드 표시, A 단위 테스트, 패키지·lock과 문서.
- B 소유 `domain/*`, `ports.py`, Guard·판정 합성·SQLite는 변경하지 않았다.
  기존 `AccessChecker.check_access(req, ctx) -> AccessResult`를 구현한다.
- 관련 설계 10.3·12장, I1·I2·I3·I6·I7·I9, T08·T20·T21·T26·T27.
  아래는 A의 어댑터 단위 검증과 합성 실행이며 C의 독립 인수 완료가 아니다.

## 실제 SDK 경계

`agentmesh.governance.policy.PolicyEngine`을 전용 daemon 스레드 하나가 소유한다.
설치된 배포판 버전을 시작 시 확인하고, 정책 생성·동기 평가도 그 스레드에서 한다.
고정 agent 대상, `deny_overrides`, `default_action="deny"`, `stage="pre_tool"`을 사용한다.
SDK 실행·사람 승인·원격 backend·파일 로더·동적 evaluator는 연결하지 않는다.

신뢰 설정의 `(server_id, tool_name, ToolKind) -> bool` 규칙만 받는다.
규칙은 최대 256개, 각 식별자는 1–256자, 버전은 제한된 ASCII 1–64자다.
복사한 규칙은 변경할 수 없고 SDK 표현식에는 길이 구분 후 SHA-256을 계산한 hex 키만 쓴다.
요청의 신뢰 Task/epoch 일치는 어댑터 안에서 확인한다. SDK에는 Task·운영자 ID,
전체 인자·문서·인증정보·경계 권한을 넘기지 않는다. 해시는 익명화 보장이 아니라
원문 노출과 표현식 삽입을 줄이는 고정 키다.

`PolicyDecision`의 정확한 타입·bool·action을 확인한다. `allowed is True`와
`action == "allow"`가 일치할 때만 허용 후보로 반환한다. 승인 요구는 거절이고,
형식 불명·모순·예외는 `error=True`다. reason은 고정 `AGT_*` 코드로 바꾸며
SDK 원시 reason·metadata·예외 문자열을 UI/감사에 전달하지 않는다.
Guard가 최종 합성과 실행을 담당하므로 AGT 허용은 ASK·DENY를 완화하지 않는다.

이 규칙은 **등록된 도구의 사용 허용 목록**이다. 본문을 검사하는 프롬프트 주입 탐지나
의미 기반 유출 판정 기능을 구현한 것으로 간주하지 않는다.

## 제한시간과 종료

합성 bootstrap은 Guard 상한 2초에서 adapter 판정 상한 1초를 파생한다.
시작 상한 5초, 종료 확인 상한 1초이며, 설정은 유한한 양수·최대 30초만 받는다.
동시에 미완료 판정은 1개만 허용한다. 취소 뒤 기존 작업이 끝나기 전 추가 요청은
`AGT_BUSY`, 시간초과 뒤 해당 인스턴스는 닫힌 상태가 된다. 자동 재시작·재시도는 없다.
실제 모드는 SDK 준비를 확인한 뒤에만 MCP 서버를 시작하고 실행 버튼·모드를 공개한다.

스레드는 강제로 종료할 수 없다. SDK가 멈추면 호출자를 제한시간 안에 차단하고
종료 확인 실패를 `AgtShutdownError`로 전달한다. bridge는 이를 정리 미확인으로 다룬다.
daemon은 프로세스 종료를 막지 않지만, 이 동작을 작업 스레드 종료 성공으로 보고하지 않는다.
이후 늦은 허용 결과는 폐기한다. SDK 예외는 기존 `security.failclosed.fail_closed`
경계에서 처리하며 새로운 넓은 예외 처리 경계를 추가하지 않았다.

## 의존성과 재현

Windows x64, CPython 3.12.15, AGT core 5.0.0, MCP 1.29.0, Pydantic 2.14.0,
jsonschema 4.25.1, HTTPX 0.28.1, PySide6 6.12.0.
core의 `cryptography>=46.0.7,<50` 제약 때문에 lock의 cryptography가 50.0.2에서
49.0.0으로 바뀌었다. 주 MCP·Pydantic·Qt 버전은 유지했다. core가 구 import 이름의
DeprecationWarning 4건을 내지만 실제 배포판은 core 5.0.0이다.

```powershell
uv sync --locked
uv run python -m guard_app --demo --agt-mode real
uv run python -m guard_app --demo --agt-mode real --agt-policy deny-send
uv run python -m guard_app --demo --agt-mode mock
uv run pytest -q
uv run python -m examples.verify_stdio_spike --agt-mode real
uv run python -m examples.verify_stdio_spike --agt-mode mock
uv run python examples/verify_agt_candidate.py
```

## 실행한 검증

새 `.cache/agt-lock-check`에 `UV_PROJECT_ENVIRONMENT`를 지정해 `uv sync --locked`로
설치했고, `uv pip check`에서 설치 배포판 66개의 선언된 의존성 충돌이 없었다.

| 검증 | 실제 결과 |
| --- | --- |
| 새 lock 환경 전체 pytest, Qt offscreen | **1,243 passed**, 40.11초, SDK deprecation 경고 4건 |
| 실제 adapter·Qt runtime 집중 검증 | **69 passed**, 4.77초 |
| 실제 SDK 선택 probe 재실행 | 8경로 통과, audit hook의 network/process 시도 0건 |
| real/mock stdio smoke | 각 모드 8경로 통과, 아래 계수 일치 |
| 변경 Python 9개 Ruff check·format check | 통과 |
| `git diff --check` | 통과 |

새 adapter 단위 검증은 입력 최소화·정확한 규칙/종류 결속·기본 거절·버전 오류,
원시 예외 비공개·잘못된 결과·timeout·취소·늦은 시작/결과·busy·종료 미확인을 포함한다.
Guard 단위 대역에서는 오류/거절/timeout의 transport 호출 0, AGT 허용 후 ASK/DENY 유지,
사람 취소/긴급 차단 중 늦은 허용의 호출 0, 승인 소비/전송 1을 확인했다.
Qt runtime 검증은 real/mock 표시, 사본 조회·승인·중복 클릭·stdio 전송·backend 종료를 포함한다.
테스트 대기 루프는 `time.sleep`으로 GIL을 반환한다. 이전 `QTest.qWait` 반복은
최초 SDK import 진행을 지연시켜 adapter 시작 제한시간을 넘겼다.

| real/mock 공통 합성 시나리오 | host_dispatch (READ 포함) | receiver_effect (SEND) |
| --- | ---: | ---: |
| PUBLIC → SEND | 2 | 1 |
| CONFIDENTIAL → 외부 SEND | 1 | 0 |
| INTERNAL → 승인·같은 승인 재요청 | 2 | 1 |
| INTERNAL → 거절/취소/긴급 차단 (각각) | 1 | 0 |
| AGT deny-send | 1 | 0 |
| AGT deny-all | 0 | 0 |

host_dispatch는 실제 `ClientSession.call_tool` 직전 callback,
receiver_effect는 별도 stdio 합성 프로세스의 계수다. 독립 wire 계수는 관측하지 않았다.

## 남은 인수

- B: 제품 규칙의 의미·버전 변경/승인 결속과 `agt_mode`·규칙 버전의 영속 감사.
  현재 bootstrap의 규칙은 고정 합성이며 임의 규칙 파일 로더는 없다.
- C: 정식 fixture에서 거절·오류·시간초과·형식 불명의 dispatch/wire/수신 계수,
  전체 T/S 인수와 Scanner 포함 설치 조합.
- 실제 모델·SQLite·C Registry/launcher·HTTP는 아직 제품 구성에 연결하지 않았다.
  이 어댑터를 붙인 것으로 전체 제품 인수를 완료 표시하지 않는다.
