# B3 Catalog 구현과 연동 경계

기준은 기술설계서 4장, 10.4·13.2·14.3절과 B 작업 문서의 B3 체크리스트다.
기존 `feat/b-security-core`에서 Catalog의 독립 구현·단위 검증을 진행했다.
`domain/*`·`ports.py`의 B1 공통 계약은 변경하지 않았다.

## 구현한 API

| 위치·API | 동작 |
| --- | --- |
| `services.catalog.Catalog` | 한 Profile의 목적지·출처 설정 사본과 현재 Registry 조회 결합 |
| `resolve_tool(tool_ref)` | 네임스페이스, ACTIVE 상태, 지원 종류, 현재 지문 일치 확인 |
| `resolve(state, call, *, request_id, call_seq)` | 고정 인자 스키마·서버 경계·목적지·기대 자원 ID를 해석해 ResolvedRequest 생성 |
| `classify_source(server_id, tool_name, resource_id)` | 출처 규칙의 정확한 서버·도구·세그먼트 접두어 매칭 |
| `classify_read(req, actual_resource_id=None)` | 자원형 결과의 기대 ID 대조 또는 도구 단위형의 빈 경로 분류 |
| `tool_views(server_ids)` | ACTIVE·지원 종류·지문 일치 도구의 닫힌 불변 스키마 사본 노출 |
| `services.catalog_sources.SourceRule` | strict·frozen인 서버·도구·접두어·등급 설정 |
| `services.catalog_schema.ArgumentSchema` | 고정 Draft, 원본 검사, 닫힌 사본과 원본 제약으로 인자 검증 |

Task·epoch는 신뢰된 TaskState에서, request_id·call_seq는 Guard의 호출 인자에서 가져온다.
Catalog는 ID를 생성하거나 모델의 model_call_id를 신뢰 식별자로 쓰지 않는다.
Task의 profile_id가 Catalog의 Profile과 다르면 POLICY_CHANGED로 거부한다.
등록 상태와 지문은 요청을 해석할 때마다 다시 확인한다.

## 네임스페이스와 등록

도구 참조는 `server_id__tool_name`이다. 첫 `__`로 나눠 도구 이름의 나머지를 보존한다.
서로 다른 서버의 같은 도구 이름은 각각의 등록·경계를 사용한다.

서버 ID에는 `__`와 끝 `_`를 허용하지 않는다. 끝 `_`도 막아야
`a_ / b`와 `a / _b`가 같은 `a___b`가 되는 충돌을 예방할 수 있다.
도구 이름 내부 `__`와 앞 `_`는 원래 이름 그대로 사용할 수 있다.
서버 ID·출처 세그먼트는 제어 문자·surrogate를 거부하고 문자열 정규화를 하지 않는다.

미등록 서버는 UNKNOWN_SERVER, 미등록·OTHER·잘못된 도구 참조는 TOOL_UNSUPPORTED,
비활성 등록은 REG_NOT_ACTIVE, 지문 불일치는 TOOL_FINGERPRINT_CHANGED다.
Registry가 조회 ID와 다른 서버 레코드를 돌려주면 구성 오류로 실패한다.
원격 annotation에서 ToolKind를 추론하는 경로는 없다.

## 고정 인자 스키마

런타임 의존성은 jsonschema 4.26.0·referencing 0.37.0을 추가했다.
strict mypy용 types-jsonschema 4.26.0.20261006과 설치 조합은 pyproject·lock에 기록했다.
기존 Python 3.12.15 가상환경을 그대로 사용한다.

1. 등록 원본의 루트 type=object와 JSON Schema 자체 유효성을 검사한다.
2. 독립 사본의 **schema 위치**만 순회해 지원 여부를 확인한다.
3. 루트와 명시적 객체의 값 검증 위치에 additionalProperties=false를 적용한다.
4. 원본 전체를 allOf로 함께 적용해 사본 변형이 등록본의 거부를 완화하지 못하게 한다.
5. 동일한 최종 스키마를 인자 검증과 ToolView에 사용한다.

등록 ToolSpec.input_schema와 fingerprint는 변경하지 않는다.
const/default/examples 안의 일반 JSON은 스키마처럼 해석하거나 바꾸지 않는다.
if의 판별 조건과 not의 서브트리는 닫기로 바꾸지 않아 조건의 의미를 유지한다.
oneOf는 분기 강화가 전체 조건을 완화할 수 있으므로 원본 제약도 함께 검사한다.

| 항목 | 이번 MVP 지원 범위 |
| --- | --- |
| Draft | 2020-12 고정, `$schema` 생략 시 같은 Draft. 다른 Draft는 구성 오류 |
| 객체 | root object, 명시적 중첩 object·객체 타입 union·properties·patternProperties의 값 검증 위치를 닫음 |
| 스키마 없이 열어 둔 값 | `{}`·boolean true는 JSON Schema의 일반 값 의미 유지. 모든 일반 JSON 내부 객체를 닫는 기능은 아님 |
| 조건·조합 | scalar allOf, anyOf·oneOf·if/then/else·not 등. 원본 제약을 유지한 추가 폐쇄 적용 |
| 객체 allOf | 같은 instance에 객체 속성을 분산 선언하는 원본 allOf는 미지원 구성 오류. 중간 조합 아래 선언도 검사 |
| 참조 | `$ref`·`$dynamicRef`·`$recursiveRef`는 내부·외부·파일 참조 모두 미지원 구성 오류 |
| format | date·email·ipv4·ipv6·regex·uuid를 명시적으로 검사. 다른 format은 구성 오류 |
| 숫자 | B1 Arguments의 float 금지 유지. integer·number에 bool을 묵시 변환하지 않음 |

참조 검색은 외부 조회 함수가 없는 referencing.Registry를 사용한다.
미지원·잘못된 스키마는 고정 메시지의 CatalogConfigError, 인자 위반은 원문 없는
ResolutionError(INVALID_ARGUMENTS)다. 이 서비스의 예상 밖 오류는 이후 Guard의
예약 전 fail_closed 경계에서 처리한다.

추가 속성과 객체 조합의 의미는 [JSON Schema 객체 공식 문서](https://json-schema.org/understanding-json-schema/reference/object),
Draft·format 검증은 [jsonschema 검증 공식 문서](https://python-jsonschema.readthedocs.io/en/stable/validate/),
참조 검색은 [jsonschema 참조 공식 문서](https://python-jsonschema.readthedocs.io/en/stable/referencing/)와
설치된 고정 버전 코드에서 확인했다.

## 목적지와 READ 출처

SEND의 destination_arg는 등록 목적지 ID와 정확히 일치해야 한다. 이름의 대소문자·
공백을 바꾸거나 IP·DNS·stdio·이름의 internal 문구로 신뢰를 추정하지 않는다.
목적지 설정·인자가 없거나 string이 아니거나 미등록이면 UNKNOWN_DESTINATION이다.
등록 스키마에서 먼저 실패한 값은 INVALID_ARGUMENTS다.
URL 정규화·URL 목적지 확장은 이번 구현 범위에 포함하지 않는다.

출처는 서버·도구 정확 일치와 경로 세그먼트 접두어로 비교한다.
company는 companyX와 매칭되지 않는다. 중첩·중복 규칙은 모두 비교해서 최고 등급을
선택하고, 미매칭은 None(Unclassified)이다. 자원 ID를 임의로 축약하거나 유니코드
정규화하지 않는다. 자원형 결과의 실제 ID가 기대 ID와 다르거나 없으면 None이다.

도구 단위 READ는 빈 경로를 사용해서 prefix=() 규칙만 적용한다. 원격 결과가 주장하는
경로와 label은 이 결정을 바꾸지 않는다. 규칙이 없으면 None이다.
SEND 응답의 출처 분류는 등급 누적에 사용하지 않는다.
None 이후 HALTED·미공개 집행은 B4 응답 게이트와 C Store에서 연결해야 한다.

## 확인한 팀 의존성과 C 경로 연결

2026-10-11 원격 A·C 브랜치를 읽어 확인한 파일 목록에는 문서만 있었다.

| 브랜치 | 확인한 commit | 후속 연결 대상 |
| --- | --- | --- |
| feat/a-application | e13dddc4aa4ea088a483a12d02ae99e8d7c9b23e | Transport·AccessChecker·Runner의 Catalog 사용 |
| feat/c-security-platform | 0f30f253789d8d0aa0bd9693a22488911076a3c8 | Registry·security/paths.py·Store |

자원형 READ용 ResourceResolver는 서비스 내부 Callable[[ToolSpec, str], str]이다.
C의 safe_path(root, user_path)에서 검증한 경로를 해당 서버의 신뢰 루트에 대한
상대 POSIX ID로 반환해야 한다. 알려진 경로 거부는 ResolutionError로 번역하고,
예상 밖 오류는 예약 전 fail_closed에 전달한다. 상세 C 어댑터는 담당 리뷰 대상이다.

실제 C 함수가 없는 현재는 resolver를 연결하지 않은 자원형 READ가 INTERNAL_ERROR로
거부된다. 원래 경로 문자열을 기대 ID로 대신 사용하지 않는다.
Catalog의 canonical_resource_parts는 **문자열 세그먼트 구조 검사**다.
경로 존재·심볼릭 링크·Windows drive·루트 탈출·TOCTOU 안전성을 증명하지 않는다.
테스트의 resolver는 단위 대역이며 실제 파일 경로 안전성 검증이 아니다.

## 검증과 다음 단계

단위 검증은 T04·T06·T13·T32·T38의 해석·분류 부분과 I6·I7·I9의 해당 반환값 부분을
다룬다. 출처 성질은 Hypothesis로 세그먼트 충돌·규칙 순서·최고 등급·불변성을 확인한다.
스키마 성질은 사본 검증이 원본의 거부를 허용으로 바꾸지 않는지 확인한다.
실제 Registry·안전 경로·MCP 전송·SQLite 원자성·결과 공개·HALTED는 후속 연결 대상이다.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/unit -q
& '.\.venv\Scripts\python.exe' -m ruff check .
& '.\.venv\Scripts\python.exe' -m ruff format --check .
& '.\.venv\Scripts\python.exe' -m mypy
& '.\.venv\Scripts\python.exe' -m pip check
```

2026-10-11 검증 결과:

- 전체 단위 테스트 521개 통과: 기존 B1·B2 340개와 B3 추가 181개.
- B3 추가 테스트는 Catalog 71개, 출처 분류 75개, 인자 스키마 35개다.
- Ruff 검사 통과, 포맷 검사 28개 파일 통과, mypy의 소스 17개 파일 검사 통과.
- pip check 통과. Python 3.12.15 가상환경의 33개 패키지가 requirements.lock과
  일치하며, 잠금 파일 밖 패키지는 editable 프로젝트를 제외하면 없다.

다음 B 작업은 digest·snapshot·failclosed 준비다. 이후 B4 Guard·응답 게이트,
B5 승인 재평가·원자적 소비를 구현하고 A·C의 실제 연동 검증을 진행한다.
B3의 실제 C 안전 경로 연결과 담당 A 리뷰, B1 공통 계약 합의는 계속 남아 있다.

후속 해시·스냅샷·예외·B4·B5 서비스 구현은 [B4·B5 문서](b4-b5-execution.md)에 기록했다.
