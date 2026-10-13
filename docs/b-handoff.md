# B 구현 인수 안내

2026-10-13 재검토 기준: **A `cd75bf9`, B `3bf414c`, C/main `4179f98`**.
서비스 회귀 기준은 B `29025e0`이며 과거 커밋/시험은 당시 이력으로 구분한다.
기준 설계는 [최종 기술설계서](technical-design.md)다.
현행 소유권은 [협업 규칙](team-workflow.md)을 따르며, 2026-10-12 사용자 결정에 따라
SQLite 구현·영속 순번·감사 저장·시작 복구는 B6, 저장소 리뷰·독립 검증은 C가 맡는다.
이 자료는 A·C가 B 코드와 연결할 위치, 확인할 계약, 검증 순서를 모은 인수 초안이다.
A·C의 동의·리뷰·MCP/SQLite 제품 통합 실행을 완료했다는 기록은 아니다.

현재 회신 입구는 [A·C 계약 검토 요청 초안](ac-contract-review-request.md)이다.
최신 브랜치 기준, 담당별 질문, 합의와 구현의 경계를 모았으며 실제 전달·동의 기록은 아니다.

## 전달 범위와 현재 상태

| B 단계 | 전달 코드 | 구현 상태 / 남은 연결 |
| --- | --- | --- |
| B1 | `domain/*`, `ports.py` | 불변 모델·결과·포트 초안 구현. 공통 계약 합의 필요 |
| B2 | `core/matrix.py`, `policy.py`, `taint.py` | 순수 정책·등급 누적 구현. C 담당 리뷰 필요 |
| B3 | `services/catalog*.py` | 요청·출처 해석 구현. C Registry·안전 경로 연결 필요 |
| 해시·예외 | `core/snapshot.py`, `security/digest.py`, `failclosed.py` | 사본·양시계 만료·예약 전후 예외 처리 구현 |
| B4 | `services/guard.py`, `response_gate.py` | Task 직렬화·검사·커밋 후 공개 구현. 실제 A·C 연결 필요 |
| B5 | `services/approvals.py` | 사람 제어·상세 조회·재평가·소비 연계 구현. 긴급 차단 후 소비·전송 방지 보강. 실제 DB·제어 큐 연결 필요 |
| B6 진행 | `storage/sqlite_store.py` | 연결·v1 초기 스키마/마이그레이션과 현행 Store 12/12개 메서드·B 단위 검증 완료. 분리 승인 생성/종결 간격·제품 연결·복구·새 계약·C 독립 인수는 남음 |

담당 단위·성질 테스트는 `tests/unit`에 있다. 테스트 대역은 이 디렉터리에만 있으며
운영용 가짜 Transport·Store·Registry나 보안 경로 우회 구현을 추가하지 않았다.
B1–B5 전달물에는 MCP SDK·AGT·Scanner·GUI·SQLite 구현·C의 CI 규칙이 없다.
후속 B6 첫 단계는 [SQLite 구현/검증 기록](b6-sqlite-foundation.md)을 따른다.
후속 [상태·판정·ALLOW 예약](b6-sqlite-decisions.md) 3개, [승인·응답 저장](b6-sqlite-lifecycle.md)
7개를 추가해 현재 **현행 12/12개 메서드·전체 1,395개 테스트 통과**다. DB 하나와 7개 테이블이다.
감사 쓰기에는 신뢰 audit_id_factory가 필요하고 승인 만료 검사는 신뢰 utc_now도 사용한다.
재개방한 옛 ALLOW/승인/예약에서 실행·응답 커밋 권한을 복원하지 않는다.
시작 복구·새 open/finish 합의·A 구성 루트 연결·C 독립 인수가 남아 제품에 주입하지 않았다.
이번 SQLite 변경은 로컬 add까지 검증한 뒤 후속 사용자 지시로 commit·push를 진행한다.
A `5598009`의 transport·AGT mock·모델·Runner·bridge·Qt UI 기록과 `655aef5`의
긴급 차단·승인 상세는 당시 상태의 근거다. `655aef5`의 B 소유 변경은 사용자 지시로
선별 반영했고, 세 후속 보완을 포함한 B `29025e038f7e4beb6cdcce15305f8cafc9550897`을 공개했다.

현재 코드·문서 대조 기준 A는 `cd75bf9fa2a96a76f84fc1ce17a746705265ae53`이다.
[실제 로컬 AGT](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-agt-integration.md)와
[loopback HTTP MCP](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-http-integration.md)가 추가됐고,
Runner는 신뢰 실행 맥락의 `expected_epoch=self.epoch`를 Guard에 전달한다.
추가로 `6452b13`은 AGT 대기/실행 상한 16개·퇴역 worker 예산 2개·미평가 대기열 이관과
전체 스레드 종료 확인을, `91b966b`는 한국어 실행/제어 문구와 RUNNING 입력 대기 안내를 반영했다.
새 [AGT 역할·규칙 관리 요청](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-agt-role-decision.md)은
운영자 차단 목록/조건 규칙 역할과 서명 정책 관리의 선택을 요청하며 아직 미확정이다.
이는 소스·문서 확인이며 실제 모델·B6 SQLite·C Registry/endpoint/Scanner의 제품 연결은 미완료다.
A `cd75bf9`는 B `3bf414c`를 병합해 승인 반환 ID 검사·상세 조회 마지막 만료/현재성 확인·
늦은 AGT 작업 추적 보완을 반영했다. 현재 domain·ports·Guard·승인 소스는 B와 동일하다.
[A 동기화 기록](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-b-sync-2026-10-13.md)의
real/mock AGT × stdio/HTTP 검증 보고도 확인했다. 공동 계약 합의와 C 독립 인수는 계속 필요하다.

상세 근거는 [B1](b1-contract-draft.md), [B2](b2-policy-and-taint.md),
[B3](b3-catalog.md), [B4·B5](b4-b5-execution.md), [B6](tasks/b-sqlite-store.md),
[공통 계약](contracts.md), [C Store 요구](tasks/c-store-requirements.md),
[C 처리 제안 v1](tasks/c-store-proposal.md), [B6 계약 변경안](b6-contract-proposal.md)에 있다.

## A가 연결할 위치

| A 구성요소 | B 입구 / 포트 | 연결 조건 |
| --- | --- | --- |
| MCP Transport·Worker | `Transport.call(req) -> RawResult` | Guard가 전달한 요청만 실행. 결과 불명 SEND 재전송 금지 |
| AGT 어댑터 | `AccessChecker.check_access(req, ctx) -> AccessResult` | 접근 검사만 수행. 오류·형식 불명은 error=True. 사람 승인 대체 금지 |
| ModelClient·Runner | `GuardService.submit(task_id, ProposedCall, expected_epoch=...) -> Outcome` | Task ID·기대 epoch는 신뢰 실행 맥락에서 전달. 현재 A Runner 전달 구현은 있으며 교체 경합의 C 독립 인수는 남음 |
| 모델용 도구 목록 | `Catalog.tool_views(server_ids)` | ACTIVE·현재 지문 일치·지원 종류의 도구만 노출 |
| 인증된 사람 제어 큐 | `guard.human_control(operator_id)` | 반환 HumanControl을 모델 도구·실행 큐에 노출하지 않음 |
| 사람 승인 상세 | `HumanControl.review(approval_id)` | 같은 메모리 사본 또는 `None` 반환. A는 사람 UI에 표시하고 만료·종결 시 상세 수명을 종료 |
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
- [ ] 구현된 `HumanControl.review`의 사람 UI 표시·상태·수명·누락 처리 인수와 B6의 revision 지정 조회 제안 검토.
- [ ] 권위 있는 승인·Task 조회, revision·종결 여부, 제어 명령의 접수/적용 결과 또는 확정 이벤트.
- [ ] 실제 AGT 제품 규칙과 `rule_version`·`policy_version`의 관계, 변경 시 승인 무효화,
  `agt_mode`·규칙 버전의 B6 영속 감사와 허용 메타데이터. 현재 A의 규칙은 고정 합성 설정이다.
- [ ] A18-D의 운영자 차단 목록/조건 규칙 및 서명 정책 관리 선택, 기본 판정·신뢰 입력·
  로더/안전 경로/키 관리 책임을 AGT-01로 회신한다. 제안 제시와 실제 동의를 구분한다.
- [ ] B 검사 상한 4개·정리 대기 0.05초와 A 기본 대기/실행 16개·퇴역 worker 예산 2개·
  대기 포함 deadline·실패 요청 폐기/미평가 요청 이관·예산 소진·실제 종료 확인을 AGT-03로 검토한다.

이 목록은 [공통 계약](contracts.md)의 미합의 항목을 구체화한 것이다.
기존 domain·ports 시그니처는 이 자료만으로 확정하거나 변경하지 않는다.
초기 UI/조회 질문의 역사 근거는 최신 A에도 보존된
[A 포트 검토 7절](https://github.com/hsminu/MCP-Runtime-Guard/blob/cd75bf9fa2a96a76f84fc1ce17a746705265ae53/docs/tasks/a-port-review.md#7-b-head-f3c2d2b-연동-중-드러난-호출제어-계약-질문)이다.
현재 동작은 최신 source와 QA 기록으로 대조했으며 위 역사 문서의 당시 미구현 설명을 현재 상태로 쓰지 않는다.
SQLite 담당 변경은 확정됐지만 이들 API 세부 사항과 C Store 처리 제안은 미합의다.

## 환경 재현과 최종 자체 점검

의존성 lock은 Windows x64·Python 3.12.15에서 검증한 B 단독 구성이다.
MCP SDK·AGT·Scanner·GUI를 포함한 전체 호환 구성은 A·C와 별도로 확인한다.
A `cd75bf9`의 `pyproject.toml`·`uv.lock`은 jsonschema 4.25.1, B `29025e0`의
`pyproject.toml`·`requirements.lock`은 4.26.0이다. Pydantic 2.14.0·referencing 0.37.0은 같다.
A는 MCP 1.29.0·AGT core 5.0.0·HTTPX 0.28.1·PySide6 6.12.0과 cryptography 49.0.0을
기록했다. A `cd75bf9`는 실행 pyproject·uv.lock을 유지하고 B requirements.lock을 별도 검사
환경에서 검증했다. B Ruff·strict mypy 설정도 config/b-core/pyproject.toml에 보존했다.
A 실행 환경에 B lock을 덮어 설치하지 않는다. 공통 제품 버전·설치 방식과 Scanner 포함
조합은 ENV-01 검토 대기다.
이번 문서 작업에서는 설치·테스트를 실행하거나 lock을 변경하지 않았다.

A 최신 동기화 문서는 **A lock 전체 1,371 passed(48.87초)**, **별도 B lock의 B 소유 테스트
971 passed(18.83초)**와 **AGT real/mock × stdio/HTTP 4조합·각 8경로·총 32경로**를 기록했다.
새 Guard 4슬롯 결합 단위 시험은 실제 SDK adapter와 대역 Store/transport를 사용하며
4개 허용·5번째 AGT_ERROR/transport 0을 확인한다. 승인 소비·실제 wire 인수는 아니다.
이전 A `281cbae`의 1,298 passed·87개 × 30회 실패0·stdio 각 모드 8경로와 HTTP
1,282 passed(22.55초)는 당시 기록이다. 모두 A 자체 보고이며 이번 B 재현이나 C 독립 인수로 보지 않는다.
아래 B 자체 검증과 후속 **971개** 기록은 기존 대상 커밋의 결과로 유지한다.
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
통과했다고 기록하지 않는다. SQLite 승인/응답 원자 연산·wire·수신 부작용 검증은 미완료다.
후속 연결·초기 스키마·Task 생성/조회 단위 검증은 [첫 단계 기록](b6-sqlite-foundation.md)으로 구분한다.

## 연결 후 인수 순서

2026-10-13에는 일반 submit의 선택적 trusted `expected_epoch` 검사를 B 서비스에 추가했다.
Task lock 획득 후 조회한 상태의 ID·epoch를 정책 변경 처리 전에 확인한다.
불일치는 새 epoch Task 변경·요청/순번 발급·AGT·예약·전송 없이
`Denied(TASK_NOT_RUNNING)`으로 반환한다. bool·음수·float·문자열은 `STATE_MISSING`으로 거부한다.
현재 epoch의 ALLOW·ASK와 기존 생략 호출은 유지한다. 현재 A `cd75bf9` Runner는 기대 epoch를
전달한다. 이 소스 연결과 C의 실제 Task 교체 경합·추가 전송 0 검증은 구분한다.
22개 회귀를 포함한 B 전체 **886개 통과**, Ruff·포맷·mypy·pip check 통과를 확인했다.
이는 B 대역 단위 검증이며 A 제품 연결·C 실제 경합 검증·SQLite 인수를 뜻하지 않는다.

### A 긴급 차단·승인 상세 선별 반영 · B 브랜치 공개 승인

B `f22968c`를 기준으로 A `655aef5`의 B 소유 서비스 변경을 반영하고 세 지적을 보완했다.
인증 후 Task lock 전에 정지 의도를 기록하고 AGT 대기·소비·예약·전송 직전에 확인한다.
이미 예약된 요청의 실패는 기존 READ 미공개·SEND 결과 불명 경계를 유지한다.

`HumanControl.review(approval_id)`는 strict 승인 모델·요청 승인 ID·PENDING·운영자·hash·
Task ID/epoch·WAITING 맥락·현재 ACTIVE 등록과 지문을 확인한다. 마지막 await 뒤
양시계 만료와 메모리 객체 현재성을 다시 검사하며 같은 불변 사본만 반환한다.
상태 쓰기·승인 소비·DB 원문 재구성은 없다. 만료·정지·조회 취소는 `None`, 깨진 Store·
모델·Task 관계는 `None`과 global halt다. B6의 `view_approval(expected_approval_revision)`은
현재 API와 별개인 미구현 공동 제안이다.

승인 ID 대조는 상세 조회뿐 아니라 승인·거절·만료·Task 무효화·소비 실패 재조회·오류 정리와
승인 전이 반환에도 적용했다. Store가 다른 ID의 행을 반환하면 추가 행 변경·소비·전송 없이
global halt하고 해당 Task의 메모리 사본을 폐기한다. 관계없는 Task의 승인·사본은 유지한다.
이미 정상 전이가 커밋된 뒤 반환 DTO가 깨진 경우에는 그 전이를 되돌리지 않고 실행을 차단한다.

AGT 작업은 실제 완료 전까지 추적한다. 취소 후 미종료 작업이 있으면 새 검사는 접근 오류로
거부하고, 작업 완료 후 다시 허용한다. 기본 `max_access_checks=4`와
`access_cleanup_timeout_seconds=0.05`는 보수적 설정이며 실측 최적값이 아니다.
동기적으로 이벤트 루프를 막는 어댑터의 강제 종료는 이 asyncio 경계가 보장하지 않는다.
정지 latch는 Task ID에 유지하므로 새 Task ID로 재시작한다. 같은 ID/epoch 복구는 미합의다.

관련 테스트 **250개(1.36초)**, 전체 **971개(4.21초, 기존 886개+85개)**가 통과했다.
조회 중 UTC·monotonic 만료, 다른 승인 ID, 취소를 무시하는 AGT 반복 생성 반례를 회귀에 포함했다.
독립 검토에서 발견한 동일 ID 오류의 상태 변경 경로를 보완하고 관련 회귀 11개도 포함했다.
Ruff·45개 Python 파일 포맷·24개 소스 strict mypy·pip check·diff 검사도 통과했다.
최종 설계·domain·ports·lock·B6 공유 API는 변경하지 않았다.
사용자는 당시 B 서비스 변경과 기존 계약 요청 문서(`3bf414c`)의 B 브랜치 commit·push를 승인했다.
팀원의 검토 회신·계약 동의와 실제 인수 결과는 별도로 기록한다.
위 B 단독 시험 당시의 한계와 현재 A 합성 앱 연결은 구분한다. 실제 SQLite·C 서비스·
모델의 제품 연결과 C 독립 인수는 미완료다.

1. B가 [검토 요청 초안](ac-contract-review-request.md)과 B6·정규식 제안에 현재 Store·A 요구·C 인수 기준의 질문을 제공한다. 기존 Store 포트를 바꾸지 않는 SQLite 독립 구현·단위 검증 준비는 진행할 수 있다.
2. A·C가 B6·정규식·환경·AGT 계약 영향을 회신하고 B가 실제 합의 근거를 contracts.md에 기록한다. 새 공유 API·DTO·상태/버전·오류 규칙은 합의 후 변경한다.
3. B가 기존 포트의 독립 구현과 합의된 변경을 B6 SQLite·호출부·저장소 단위 검증에 반영하고 대상 커밋·설정·실제 결과를 전달한다.
4. C가 실제 Store와 Fake에 동일 계약 테스트를 적용하고 저장소 리뷰·결함 주입을 수행한다.
5. A `cd75bf9`의 최신 B 서비스 병합과 real/mock AGT × stdio/HTTP 32경로 자체 보고는 확인했다. 후속 SQLite·공유 계약 변경을 연결하면 A 앱 검증을 갱신한다. C는 timeout 실패 요청과 미평가 이관 요청을 구분하고 ALLOW·DENY·ASK의 소비·dispatch·wire·부작용을 독립 인수한다.
6. T07·T09–T12·T19–T20·T29·T33·T35–T37의 커밋 실패·경합·누출 방지와 C의 CI·공격·replay를 검증하고 실측 결과·한계를 기록한다.

## 2026-10-13 · 검토 자료와 선택한 방향

사용자는 B6 계약을 **A·C 검토를 받은 뒤 확정**하도록 결정했다.
[B6 변경안 10절](b6-contract-proposal.md)에 A의 앱·조회·제어 영향과 C의 U1–U8·
원자성·버전·오류·복구 회신 항목을 제공했다. 새 공유 계약과 그에 의존하는 구현은 검토 후 진행한다.
기존 Store 포트 안의 SQLite 독립 구현·단위 검증 준비까지 회신 대기로 막는 의미는 아니다.
새 공유 API·DTO·상태/버전·오류 규칙과 그에 의존하는 변경은 회신·공통 계약 기록 후 반영한다.

정규식은 최종 기술설계와 GitHub 문서를 근거로 기존 기능을 유지하고 전체 검증을
종료 가능한 프로세스로 옮기는 방향을 선택했다. [B3 검증 연결안](b-schema-validation-proposal.md)을
A가 모델 입력·ToolView·비동기 호출 관점에서, C가 launcher·제한·종료/회수·Registry·
독립 검증 관점에서 검토해야 한다. 예약 전 실패와 예약/승인 소비 후 실패를 구분한다.
처리 방향 선택과 실제 수정은 별개이며, 현재 정규식 위험은 해결 전이다.

B6·정규식 검토 자료는 B `f22968c`로 원격에 공유했다. A/C 전달·리뷰·동의는 미완료이며,
긴급 차단·상세 보완은 B `29025e0`으로 공개됐다. 이번 [A·C 계약 검토 요청](ac-contract-review-request.md)은
문서 요청 초안이며 실제 전달·공동 합의·SQLite/정규식 구현·이번 B의 새 테스트 실행 완료를 기록하지 않는다.
A의 B 병합과 A 자체 시험 보고는 위 최신 상태에 별도로 기록했다.
