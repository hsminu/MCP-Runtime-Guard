# B 구현 인수 안내

2026-10-13 문서 갱신. 기준 설계는 [최종 기술설계서](technical-design.md)다.
현행 소유권은 [협업 규칙](team-workflow.md)을 따르며, 2026-10-12 사용자 결정에 따라
SQLite 구현·영속 순번·감사 저장·시작 복구는 B6, 저장소 리뷰·독립 검증은 C가 맡는다.
이 자료는 A·C가 B 코드와 연결할 위치, 확인할 계약, 검증 순서를 모은 인수 초안이다.
A·C의 동의·리뷰·실제 MCP·SQLite 실행을 완료했다는 기록은 아니다.

## 전달 범위와 현재 상태

| B 단계 | 전달 코드 | 구현 상태 / 남은 연결 |
| --- | --- | --- |
| B1 | `domain/*`, `ports.py` | 불변 모델·결과·포트 초안 구현. 공통 계약 합의 필요 |
| B2 | `core/matrix.py`, `policy.py`, `taint.py` | 순수 정책·등급 누적 구현. C 담당 리뷰 필요 |
| B3 | `services/catalog*.py` | 요청·출처 해석 구현. C Registry·안전 경로 연결 필요 |
| 해시·예외 | `core/snapshot.py`, `security/digest.py`, `failclosed.py` | 사본·양시계 만료·예약 전후 예외 처리 구현 |
| B4 | `services/guard.py`, `response_gate.py` | Task 직렬화·검사·커밋 후 공개 구현. 실제 A·C 연결 필요 |
| B5 | `services/approvals.py` | 사람 제어·재평가·소비 연계 구현. 실제 DB·제어 큐 연결 필요 |
| B6 | `storage/*` 예정 | SQLite 담당 인수·API 검토 단계. 구현·저장소 단위 검증·C 독립 인수 미완료 |

담당 단위·성질 테스트는 `tests/unit`에 있다. 테스트 대역은 이 디렉터리에만 있으며
운영용 가짜 Transport·Store·Registry나 보안 경로 우회 구현을 추가하지 않았다.
B1–B5 전달물에는 MCP SDK·AGT·Scanner·GUI·SQLite 구현·C의 CI 규칙이 없다.
A 브랜치 `5598009`에는 transport·AGT mock·모델·Runner·bridge·Qt UI 구현이 있으나
실제 B 저장소·C 서비스와 제품 연결은 미완료다. 모의 AGT와 실제 연동을 구분한다.

상세 근거는 [B1](b1-contract-draft.md), [B2](b2-policy-and-taint.md),
[B3](b3-catalog.md), [B4·B5](b4-b5-execution.md), [B6](tasks/b-sqlite-store.md),
[공통 계약](contracts.md), [C Store 요구](tasks/c-store-requirements.md),
[C 처리 제안 v1](tasks/c-store-proposal.md)에 있다.

## A가 연결할 위치

| A 구성요소 | B 입구 / 포트 | 연결 조건 |
| --- | --- | --- |
| MCP Transport·Worker | `Transport.call(req) -> RawResult` | Guard가 전달한 요청만 실행. 결과 불명 SEND 재전송 금지 |
| AGT 어댑터 | `AccessChecker.check_access(req, ctx) -> AccessResult` | 접근 검사만 수행. 오류·형식 불명은 error=True. 사람 승인 대체 금지 |
| ModelClient·Runner | `GuardService.submit(task_id, ProposedCall) -> Outcome` | Task ID는 신뢰 실행 맥락에서 전달. 모델은 도구·인자만 제안 |
| 모델용 도구 목록 | `Catalog.tool_views(server_ids)` | ACTIVE·현재 지문 일치·지원 종류의 도구만 노출 |
| 인증된 사람 제어 큐 | `guard.human_control(operator_id)` | 반환 HumanControl을 모델 도구·실행 큐에 노출하지 않음 |
| 화면·대화 반영 | Outcome의 kind별 처리 | Released.result만 본문으로 공개. 예외 문자열·RawResult 공개 금지 |

Guard의 request_id·call_seq와 모델의 model_call_id는 서로 다른 식별자다.
같은 모델 ID나 같은 인자를 새로 제안했다고 이전 요청을 재사용하지 않는다.
Runner는 자기 호출 맥락과 Guard 결과를 연결하되 보안 식별자를 모델에서 받지 않는다.
새 Task의 대화·제공자 캐시·클라이언트 상태 격리는 A가 구현해야 한다.

RawResult.payload는 SDK 결과의 JSON 사본이다. content 배열, optional structuredContent
객체, optional isError bool을 사용한다. DTO 검증에서 배열은 불변 tuple로 변환된다.
게이트는 TextContent만 지원하며 텍스트와 structured JSON의 UTF-8 합계 크기를 검사한다.
자료가 포함된 isError는 failed=False인 수신 결과다. failed=True는 payload=None인
알려진 무자료 전송 실패에만 사용한다. 예상 밖 수신·변환 실패는 DispatchError로
전달하며 원문 대신 예외 타입만 담는다. stderr·progress·늦은 결과는 A에서 격리한다.

## B6 저장소와 C가 연결할 위치

| 담당 / 구성요소 | B에서 사용하는 입구 | 필요한 보장 |
| --- | --- | --- |
| C · Registry | registration / status / fingerprint | 저장된 신뢰 등록·현재 지문. 변경 시 PENDING과 승인 무효화. 영속화는 B 저장소와 단일 writer 계약 |
| C · 안전 경로 | `ResourceResolver(ToolSpec, str) -> str` | safe_path로 확인한 신뢰 루트 상대 POSIX ID. 원문 경로 fallback 금지 |
| B6 · Task·요청 저장 | Store의 load / record_decision / reserve_dispatch / set_task_status | ID 연결·상태 전이·revision 충돌·감사 내구성 |
| B6 · READ 커밋 | `commit_observation(req, label) -> TaskState` | 등급·미분류·버전·근거·요청·감사 원자 커밋 후 반환 |
| B6 · SEND 커밋 | `commit_send_outcome(req)` | 요청 성공·감사만 저장. READ merge 호출 금지 |
| B6 · 실패 저장 | `commit_request_failure(...) -> TaskState` | 요청·Task·감사를 실패 종류에 맞춰 원자 기록 |
| B6 · 승인 저장·소비 | create / get / transition / consume_and_reserve | 상태·해시·만료·Task·요청 연결 검사와 1회 소비 |
| C · 안전 ID / B6 · 영속 순번 | request_id_factory / approval_id_factory / next_call_seq | 충돌 없는 신뢰 ID, 기존 Task·재시작에서도 순번 재사용 금지 |
| B6 · 오류 감사 | `failure_audit(FailureEvent)` | Task·요청 ID, 단계, reason, 예외 타입만 내구성 기록 |

표의 메서드는 기존 B1 초안이며, 실제 저장소의 API·DTO·상태·오류와 승인 원자 묶음은
B가 변경안을 제공하고 A·C와 검토해야 한다. B6 구현·단위 테스트 후 C가 실제 Store/Fake
계약·통합·결함 주입 검증을 맡는다. C의 Registry·Scanner·안전 경로·안전 ID 소유권은 유지한다.

미연결 ResourceResolver는 자원형 READ를 거부한다. Catalog의 문자열 세그먼트 검사는
파일 존재·심볼릭 링크·Windows drive·루트 탈출·TOCTOU 검증을 대신하지 않는다.

## 구성 예시

아래 함수는 실제 A·C 구현을 인자로 받아 B 서비스를 구성하는 예시다.
함수 정의 자체로 서버·DB를 실행하지 않으며 실행 앱·대역 구현도 아니다.
검증된 Profile·목적지·출처 규칙과 사전 등록 운영자만 전달해야 한다.

```python
import time
from collections.abc import Awaitable, Callable

from guard_app.domain.models import Boundary
from guard_app.ports import AccessChecker, Registry, Store, Transport
from guard_app.services.approvals import HumanControl
from guard_app.services.catalog import Catalog, ResourceResolver
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import FailureEvent, GuardService


def compose_guard(
    *,
    store: Store,
    registry: Registry,
    transport: Transport,
    access_checker: AccessChecker,
    profile_id: str,
    policy_version: str,
    operator_id: str,
    destinations: tuple[Boundary, ...],
    source_rules: tuple[SourceRule, ...],
    resource_resolver: ResourceResolver | None,
    request_id_factory: Callable[[], str],
    approval_id_factory: Callable[[], str],
    next_call_seq: Callable[[str], Awaitable[int]],
    failure_audit: Callable[[FailureEvent], Awaitable[None]],
) -> tuple[GuardService, HumanControl]:
    catalog = Catalog(
        registry,
        profile_id=profile_id,
        destinations=destinations,
        source_rules=source_rules,
        resource_resolver=resource_resolver,
    )
    guard = GuardService(
        store,
        registry,
        catalog,
        transport,
        access_checker,
        profile_id=profile_id,
        policy_version=policy_version,
        operator_id=operator_id,
        request_id_factory=request_id_factory,
        approval_id_factory=approval_id_factory,
        next_call_seq=next_call_seq,
        failure_audit=failure_audit,
        monotonic=time.monotonic,
        approval_ttl_seconds=300,
        max_result_bytes=262144,
        access_timeout_seconds=30.0,
        taint_mode="accumulate",
    )
    return guard, guard.human_control(operator_id)
```

실행 시 Guard·Store·제어 명령은 백엔드의 같은 asyncio loop에서 사용한다.
사람의 승인 응답을 기다리는 동안 Task lock을 유지하지 않는다.
모델 실행 경로에는 submit 입구를 연결하고, 위 함수가 반환한 HumanControl은
A의 인증된 사람 제어 경로에서만 보관한다.

## 합의가 필요한 항목

- [ ] domain·ports DTO, SDK 결과의 JSON 변환, UI 명령·이벤트와 reason 표시.
- [ ] ID·순번·FailureEvent 감사 callback의 정식 계약과 배치 위치.
- [ ] Task WAITING 전이와 승인 생성, 승인 종결과 Task 복귀를 원자적으로 묶는 방법.
- [ ] WAITING↔RUNNING에서 security_version을 증가시킬지 결정.
  현재는 WAITING 반환 상태로 snapshot을 만들고 소비 후 버전 유지·1 증가를 허용한다.
  다른 보안 맥락은 같아야 한다.
- [ ] consume_and_reserve의 정확한 bool, 내구성 commit ACK·취소의 의미,
  실제 트랜잭션 시점의 UTC 만료 검사. Guard도 전송 직전에 양시계 만료를 재검사한다.
- [ ] policy·Registry 변경 시 승인 일괄 무효화와 재시작 시 잔존 승인 정리.
- [ ] A의 인증된 제어 큐, Task 생성·시작과 새 Task 격리 책임.
- [ ] 일반 submit에 trusted `expected_epoch`를 전달하고 Guard의 Task lock 내부에서 비교하는 계약.
- [ ] 인증된 UI가 실행에 사용할 동일 메모리 승인 사본을 조회하는 API와 상태·수명·누락 처리.
- [ ] 권위 있는 승인·Task 조회, revision·종결 여부, 제어 명령의 접수/적용 결과 또는 확정 이벤트.

이 목록은 [공통 계약](contracts.md)의 미합의 항목을 구체화한 것이다.
기존 domain·ports 시그니처는 이 자료만으로 확정하거나 변경하지 않는다.
A의 추가 요청 근거는
[A 계약 검토 7절](https://github.com/hsminu/MCP-Runtime-Guard/blob/55980091fc49871903d72e8ba1e657ab7682035b/docs/tasks/a-port-review.md#7-b-head-f3c2d2b-연동-중-드러난-호출제어-계약-질문)이다.
SQLite 담당 변경은 확정됐지만 이들 API 세부 사항과 C Store 처리 제안은 미합의다.

## 환경 재현과 최종 자체 점검

의존성 lock은 Windows x64·Python 3.12.15에서 검증한 B 단독 구성이다.
MCP SDK·AGT·Scanner·GUI를 포함한 전체 호환 구성은 A·C와 별도로 확인한다.
아래 python 명령에는 Python 3.12 실행 파일을 사용한다. 기존 .venv가 있으면 생성을 생략한다.

```powershell
python --version
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
& '.\.venv\Scripts\python.exe' -m pip install --no-deps --no-build-isolation -e .
& '.\.venv\Scripts\python.exe' -m pytest tests/unit -q
& '.\.venv\Scripts\python.exe' -m ruff check .
& '.\.venv\Scripts\python.exe' -m ruff format --check .
& '.\.venv\Scripts\python.exe' -m mypy
& '.\.venv\Scripts\python.exe' -m pip check
```

2026-10-12 자체 점검에서는 긴급 차단 시 Store가 다른 Task의 DTO를 반환하는 경우의
ID 검증을 보강했다. RUNNING·HALTED·CLOSED 세 경우를 수정 전에 재현하고,
상태 변경·차단 완료 판단 전에 거부하여 global halt하는 단위 테스트를 추가했다.
실제 SQLite 결함 주입 검증은 C 인수 단계에 남아 있다.

| 검증 | 2026-10-12 최초 인수 준비 실행 결과 · 0c02b61 |
| --- | --- |
| B 단위·성질 테스트 | 840 passed |
| Ruff 검사·포맷 | 통과, Python 파일 41개 포맷 일치 |
| mypy strict | 소스 파일 24개 통과 |
| pip check·lock 대조 | 의존성 충돌 없음, 설치된 33개 패키지의 버전 일치 |
| 구성 예시 | 구문·import·Catalog/GuardService 생성자 시그니처 확인 |
| 문서·소스 점검 | 문서 링크 확인, 넓은 예외는 fail_closed·after_dispatch에만 존재 |

위 결과는 로컬 가상환경에서 실행한 B 검증이다. 실제 MCP·AGT·Scanner·SQLite,
wire·receiver_effect 계수, C의 CI·공격·결함 주입·replay 통과를 의미하지 않는다.

### 승인 식별자 후속 수정 · f3c2d2b

2026-10-12 `f3c2d2bc225e0406c50731c9b63779a3d890cd70`에서 승인 Task 조회·정지 직전의
Task ID·snapshot epoch를 검사하도록 보강했다. 다른 Task·교체된 epoch의 행은 변경하지
않으며 승인 사본 폐기·승인 종결·global halt로 후속 실행을 차단한다.
승인·거절·만료·재평가 거부와 정지 경로의 24개 회귀 케이스를 수정 전 실패로 재현하고 보완했다.

| 검증 주체 / 환경 | 2026-10-12 후속 수정 실행 결과 |
| --- | --- |
| B · Windows x64·Python 3.12.15 | 단위·성질 테스트 864개 통과. Ruff·mypy·pip check 통과 |
| C · Ubuntu 24.04.4 x86_64·Python 3.12.3 | 같은 커밋에서 단위·성질 테스트 864개·Ruff·mypy 재현 통과 |

근거는 [수정 커밋](https://github.com/hsminu/MCP-Runtime-Guard/commit/f3c2d2bc225e0406c50731c9b63779a3d890cd70)과
[C의 후속 검토](tasks/c-b-review.md#후속-수정-f3c2d2b-확인)이다. 840개 최초 결과와
864개 후속 결과는 서로 다른 커밋의 기록이다. 2026-10-13 문서 갱신 자체로 새 코드 시험을
통과했다고 기록하지 않는다. 실제 SQLite·wire·수신 부작용 검증은 여전히 미완료다.

## 연결 후 인수 순서

1. B가 현재 Store와 C 요구·제안 및 A 조회·제어 요구를 대조해 API·DTO·상태/오류 변경안을 제공한다.
2. A·C가 계약 영향을 리뷰하고 실제 합의 근거를 contracts.md에 기록한다.
3. B가 합의된 계약으로 B6 SQLite와 저장소 단위 테스트를 구현하고 대상 커밋·설정·검증 결과를 전달한다.
4. C가 실제 Store와 Fake에 동일 계약 테스트를 적용하고 저장소 리뷰·결함 주입을 수행한다.
5. A 연결 후 ALLOW·DENY·ASK 승인 전·승인 후의 소비·dispatch·wire·부작용 계수를 확인한다.
6. T07·T09–T12·T19–T20·T29·T33·T35–T37의 커밋 실패·경합·누출 방지와 C의 CI·공격·replay를 검증하고 실측 결과·한계를 기록한다.
