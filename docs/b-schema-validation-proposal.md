# B3 · 제한된 JSON Schema 검증 연결안

기준일: **2026-10-13**. 상태는 **B 처리 방향 선택·A/C 연결 계약 검토 대기**다.
사용자는 최종 기술설계서와 GitHub 문서를 근거로 B가 판단하도록 요청했다.
B는 기존 `pattern`·`patternProperties` 기능을 유지하고, JSON Schema 작업 전체를
종료 가능한 별도 프로세스에서 실행하는 방향을 선택했다. 이 문서는 연결 제안이며,
공통 API 합의·launcher 구현·정규식 문제 해결 완료를 뜻하지 않는다.

현재 A·C에게 회신을 요청할 항목과 답변 형식은
[A·C 공동 계약 검토 요청](ac-contract-review-request.md)의 **SCHEMA-01**에 모았다.
회신·동의는 아직 기록되지 않았다. AGT·HTTP 연결 추가로 동기 JSON Schema 검증 문제가
해결됐다고 간주하지 않으며, 이번 문서 작업은 제품 검증 코드를 변경하지 않는다.

## 선택 근거

- [최종 기술설계](technical-design.md) 4.1절은 등록본 `input_schema`의 jsonschema 검증과
  추가 속성 폐쇄를 요구한다. I6은 시간초과를 DENY 또는 Task 정지로 처리한다.
- [B3 지원표](b3-catalog.md)는 `patternProperties`를 지원한다. 등록 원본·지문 보존,
  원본 전체와의 AND, 같은 최종 스키마의 인자 검증·ToolView 사용도 유지해야 한다.
- A `5598009`의 [B 검토](https://github.com/hsminu/MCP-Runtime-Guard/blob/55980091fc49871903d72e8ba1e657ab7682035b/docs/tasks/a-b-review.md)와
  [보안 검토](https://github.com/hsminu/MCP-Runtime-Guard/blob/55980091fc49871903d72e8ba1e657ab7682035b/docs/tasks/a-security-review.md)는
  정규식 키워드 일괄 거부가 B3 기능 계약을 바꾸며, A의 길이 제한만으로 해결되지 않는다고 기록했다.
- [소유권](team-workflow.md)에 따라 검증 의미는 B, 모델 입력 호출은 A,
  프로세스 실행·종료는 C `security/launcher.py`가 맡는다. B 모듈에 subprocess를 추가하지 않는다.

최종 기술설계가 별도 검증 프로세스를 직접 지정한 것은 아니다. 위 요구와 기존 기능을
함께 지키기 위한 B의 구현 방향이다. 프로세스·비동기 호출 계약은 공동 검토해야 한다.

## 격리할 범위와 호출 위치

정규식 매칭 두 함수만 감싸지 않고 다음 작업 전체를 worker 안에서 수행하도록 제안한다.

| 작업 | worker가 수행할 내용 | 부모가 확인할 내용 |
| --- | --- | --- |
| PREPARE | 등록 스키마 자체 검사·format 컴파일·지원 범위 검사·객체 폐쇄·원본 AND·최종 스키마 생성 | 등록 ID/지문에 연결된 결과, 응답 상한·형식, 원본 불변 |
| VALIDATE | 준비된 최종 스키마와 분리된 인자 사본의 전체 검증 | 요청과 결과 대응, 성공/위반/오류 구분, 현재 등록·정책 맥락 |

이는 작업 의미의 이름이며 새로운 Protocol·DTO·CLI 명령을 확정한 표가 아니다.
`check_schema`도 부모의 동기 코드에 남기지 않는다. `format: regex`의 컴파일,
`patternProperties`를 참조하는 추가 속성/helper, `propertyNames`, 조합·배열·조건,
원본 AND의 검증까지 같은 제한 안에 둔다. const/default/examples는 데이터로 유지한다.
기존 참조 금지와 외부 조회가 없는 Registry 의미도 유지한다.

| 연결 위치 | 필요한 변경·검토 |
| --- | --- |
| B Catalog 도구 목록 | PREPARE 완료한 최종 스키마만 ToolView로 공개. 공개 전에 부모에서 정규식 검사하지 않음 |
| B Guard 제출·전송 직전 재확인 | 현재 동기 resolve/validation을 제한된 실행 경계에 연결. 최초 검증은 예약 전, 마지막 재확인은 예약 후이므로 실패 처리를 구분 |
| B 승인 재평가 | 동일 snapshot 인자와 현재 등록·지문을 재확인. Task lock과 승인 만료 의미 유지 |
| A 모델 인자 변환 | 현재 `model_client.py`의 직접 동기 Draft 검증도 같은 경계를 사용. provider 대기만 제한해 완료로 보지 않음 |
| A 앱 시작 | C launcher와 B 검증 adapter를 주입. 경계가 없으면 실행을 개방하지 않음 |
| C Registry | PREPARE 실패·시간초과 도구의 ACTIVE 허용 여부와 도구 목록 실패 범위를 검토 |

A가 받은 ToolView의 스키마는 이미 폐쇄·원본 AND가 적용된 결과다.
이를 등록 원본용 `ArgumentSchema`에 다시 넣어 준비하지 않는다. 준비된 스키마의 검증과
등록본 준비를 구분하고, 직접 생성·변조된 ToolView에도 같은 입구와 제한을 적용한다.
캐시를 사용하면 등록본 지문·검증 정책·검증기 버전에 묶고 변경 시 무효화한다.
worker의 성공 반환은 ALLOW 판정이나 승인 소비 증명이 아니다. 정책·등록 재확인과
Guard의 기존 실행 예약·승인·응답 게이트는 이어서 수행해야 한다.

## launcher와 실패 의미 제안

- C가 등록한 절대 실행 파일·고정 module/인자 리스트·최소 환경을 사용한다.
  schema나 도구 metadata가 실행 파일·명령·환경·작업 디렉터리를 선택하지 않는다.
- 스키마·전체 인자는 제한된 IPC로만 전달한다. 명령행·환경·로그·DB·임시 파일에 넣지 않는다.
  worker의 stderr와 예외 본문을 사용자 오류나 기본 로그로 전달하지 않는다.
- 부모 입력 복사·직렬화·출력 파싱에도 바이트·깊이·요소 상한을 적용한다.
  worker 실행에는 monotonic deadline, 동시 worker 수, 대기열·출력 상한을 둔다.
  정확한 수치·메모리/CPU 제한의 플랫폼별 수단은 A/C와 검토·측정 후 고정한다.
- timeout·취소·출력 초과·worker 실패 시 C가 종료하고 회수한다. 늦은 결과는 폐기하고,
  종료를 확인하지 못하면 worker 재사용·새 실행 개방을 금지하는 실패 경계를 검토한다.
  부모 coroutine 취소만으로 동기 regex 작업이 종료됐다고 간주하지 않는다.
- 검증 도중 Task lock을 유지하되 부모 이벤트 루프를 막지 않는다. Task 취소·긴급 차단이
  검증 작업을 중단하는 경로와 lock 해제 순서를 C/A와 확인한다.

| 결과 | 호출부 처리 제안 |
| --- | --- |
| 스키마 유효·인자 적합 | 해당 검증만 성공. Guard는 기존 재평가를 계속함 |
| 스키마 유효·인자 위반 | 기존 INVALID_ARGUMENTS 의미 유지 |
| 스키마 구성 오류 | 고정 CatalogConfigError 의미 유지. invalid 인자와 혼동하지 않음 |
| 시간초과·자원 제한·비정상 종료·응답 불명 | I6에 따른 고정 실패 reason과 단계별 차단. 성공으로 취급하거나 전송하지 않음 |

실패 경계는 검증 호출의 단계에 따라 달라진다. 최초 제출 검증 실패는 예약 전 DENY이고,
승인 소비 전 재평가 실패는 새 소비·예약 없이 승인 프로토콜에 따라 종결한다.
현재 Guard는 예약 뒤 `_dispatch_reserved`의 after_dispatch 경계 안에서
`_dispatch_and_commit`이 Catalog를 다시 검증한다. 이 지점의 실패를
일반 Denied로 되돌리지 않는다. READ는 Withheld/HALTED, SEND는 OutcomeUnknown으로
격리하는 기존 경계를 유지한다. 승인 소비 후라면 소비 1·실제 전송 0이 가능하며,
자동 재시도나 이미 소비한 승인의 복원은 하지 않는다(I3·I8).

새 reason·오류·비동기 포트, schema 결과 DTO, timeout/취소 후 Task 상태는
`domain/*`·`ports.py` 변경 여부를 포함해 [공통 계약](contracts.md)에서 A/B/C가 검토한다.
정상처럼 보이는 fallback 동기 검증이나 worker 재시도로 차단 경계를 우회하지 않는다.

## 담당별 순서와 리뷰 질문

SCHEMA-01 회신에는 A의 사용할 비동기 API/주입 위치와 ToolView 준비/검증 분리,
C의 제한된 IPC·deadline/자원 상한·취소/종료 확인·지원 OS/Registry 실패 상태를 구체적으로
남겨 줘. 입력/출력 DTO·안전한 오류·예약 전후 Task 상태와 검증 계수에 동의하는지 또는 수정안을
기록하고, 구현·검증하지 않은 수치나 미응답을 합의 근거로 채우지 않는다.

1. **B 준비 완료:** 기능 유지 방향, 전체 작업 범위, 실패 의미와 아래 인수 기준을 제공한다.
2. **A 검토 대기:** 모델 변환과 ToolView 준비/검증 분리, 비동기 호출, 앱 주입 위치,
   입력 예산·deadline·취소 계약의 수용 여부와 필요한 변경을 남긴다.
3. **C 검토 대기:** launcher의 제한된 IPC·출력·동시성·종료/회수 API, 지원 OS별
   자원 제한, Registry 실패 상태와 독립 검증 계획을 남긴다.
4. **공동 계약 후 B 구현:** 기존 검증 의미를 worker 내부 연산으로 분리하고 Catalog·Guard·
   승인 호출과 단위 테스트를 연결한다. C가 프로세스 실행·종료를, A가 모델 경로를 구현한다.
5. **C 독립 인수 후 상태 갱신:** 실제 ACTIVE 등록·모델→Guard 경로·host/wire/receiver
   계수와 제어 응답성을 확인한 결과로만 해결 상태를 기록한다.

인수에서는 지연 pattern·patternProperties·propertyNames와 원본 AND/helper 경로의
강제 종료, 등록 검사/format 컴파일 제한, 과다 출력·취소·비정상 종료·늦은 결과,
정상 정규식·비정규식·기존 추가 속성 폐쇄 의미를 함께 확인한다.
최초 검증 timeout은 예약·소비·dispatch·wire·부작용 0, 승인 소비 전 timeout은 새 소비·
예약 0, 예약/소비 후 마지막 검증 timeout은 실제 dispatch·wire·부작용 0으로 확인한다.
마지막 경우의 예약/소비 이력은 보존하고 READ/SEND 격리 상태를 대조한다.
다른 정상 Task와 취소·긴급 차단 처리가 응답해야 한다. 정지한 Task를 정상 재개해
후속 요청 성공을 만들지 않는다(I1·I2·I3·I6·I8·I9·I10, 관련 T04·T08·T17·T20·T21).
새 반복 횟수나 시간 기준을 실측 완료처럼 기재하지 않는다.

현재 정규식 관련 제품 소스·의존성·지원 기능은 변경하지 않았다.
B의 기존 886개 단위 테스트 통과는 이 연결안 구현·종료 보장·C 독립 인수를 증명하지 않는다.
