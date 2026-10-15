# C · Cisco 도구 정의 누락 방지 구현

2026-10-13 사용자가 전체 도구 정의 검사 제안에 대해 "반영해보"라고 지시했다. C 소유 `src/guard_app/adapters/cisco.py`에 입력 준비·개별 분석기 호출 경로를 구현했다. 관련 I6·I9, T17·T19·S04의 입력 부분이다. Scanner 전체·Registry·T/S 전체 인수 완료는 아니다.

## 반영 범위

- `name`, `description`, `inputSchema`, `annotations`와 추가 정의 필드를 모두 보존한다. `$defs`, 중첩 객체·배열, 예시·기본값, `outputSchema`, `_meta`도 JSON의 구조·값을 유지한다. 문자열을 요약·정규화·절단하지 않는다.
- `prepare_tool_definition`은 도구 객체 하나를 정렬된 UTF-8 JSON 사본으로 만든다. 실행 인자·결과·연결 설정을 입력에 합치지 않는다. 호출자는 A가 수집한 도구 정의만 제공해야 한다.
- `CiscoMetadataAdapter`는 주입받은 Cisco 개별 분석기의 공개 `analyze`를 호출한다. YARA에는 전체 정의 JSON을, LLM에는 전체 JSON을 `description`에 넣은 전용 봉투를 전달한다. SDK가 직접 인자만 추출해 내용을 빠뜨리는 경로를 피하며 SDK의 무작위 untrusted 구간 안에 전체 내용이 들어간다. 이 구간은 공격 탐지를 보장한다는 뜻은 아니다.
- B의 `FrozenJsonObject.to_dict()`를 구조적 인터페이스로 사용한다. B의 domain·ports·digest를 복제하거나 수정하지 않았다.
- 이름·입력 스키마의 기본 구조, 유한 JSON 숫자·문자열 키·UTF-8, 순환과 직렬화 가능 여부를 확인한다. 실패는 원문 없는 `SCAN_METADATA_INVALID`다. 전체 JSON Schema의 의미 검증은 이 범위가 아니다.
- 크기 상한은 생성자의 `max_definition_bytes`로 호출자가 명시한다. 상한 초과는 `SCAN_METADATA_TOO_LARGE`로 실패하고 분석기를 호출하지 않는다. 구현 당시 값·기본값은 미확정이었다. 후속 사용자 결정으로 [도구당 기본 64KiB·설정 가능 정책](../../contracts.md#scanner-입력-크기-사용자-결정)을 문서에 확정했으며 제품 기본값·경고 연결은 미반영이다. 테스트의 10,000은 제품 기본값이 아니다. 측정은 전체 정의 JSON의 UTF-8 바이트 수이며 LLM 봉투·프롬프트의 크기나 메모리 상한을 의미하지 않는다. 토큰 예산은 별도다.
- JSON 사본은 메모리에 보관하고 기본 repr에서 숨긴다. 원래 이름은 JSON 안에 보존하고 SDK의 로그용 context에는 고정 이름을 넣는다. SDK가 던지는 예외·취소를 findings 0개로 바꾸지 않는다.

생성자는 분석기 종류와 제한값을 명시적으로 받는다. 공통 Scanner 포트나 `ScanReport`를 대신하는 API가 아니라 그 포트의 구현에서 사용할 C 내부 구성요소다. SDK findings는 내부 원시 결과로만 반환하며 UI·로그 공개 또는 등록 허용을 뜻하지 않는다.

## 실제 검증

설치 코드 기준 Cisco Scanner 4.8.6, LiteLLM 1.93.2, MCP 1.30.0, OpenAI SDK 2.54.0, Pydantic 2.14.0을 사용했다. 도구는 pytest 9.1.1·Ruff 0.17.0·mypy 2.4.0이다.

| 검증 | 실제 결과 |
| --- | --- |
| 단위 테스트 15개 | 전체 필드 왕복·사본 고정·선택 필드·UTF-8 크기 경계·잘못된 JSON·원문 없는 오류·repr·분석기 호출 0·예외·취소 확인 |
| 설치 SDK 회귀 8개 | 실제 LLM SDK 프롬프트에 루트 schema·annotations·중첩 schema·추가 필드 전체가 무작위 untrusted 구간 안에 들어감. 실제 YARA 규칙은 이전 누락 위치 3곳의 양성 대조군에서 HIGH 탐지. matcher 예외 전파 확인 |
| 전체 테스트 | **23 passed, 20 subtests passed, 1.38초**. 네트워크 connect/connect_ex 시도 0. LLM completion만 대역이며 실제 모델 정확도 평가는 아님 |
| B 실제 입력 | B `f3c2d2b`의 `FrozenJsonObject`에 float·추가 필드가 포함된 정의를 넣어 전체 JSON 왕복·repr 비노출 확인. B 파일 변경 없음 |
| 정적 검사 | B의 기존 Ruff 설정으로 lint·format 확인, 제품 소스 strict mypy 통과. 새 공통 설정·lock은 만들지 않음 |

재현은 루트에서 해당 Scanner·개발 도구가 설치된 Python으로 실행한다. 이번 실행은 기존 스파이크의 `venv-1x`를 사용했다. `.venv-scanner`에는 pytest·Ruff·mypy가 설치되지 않았으므로 그 환경의 도구 설치 완료를 주장하지 않는다.

```bash
PYTHONPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python \
  -m pytest tests/unit/adapters/test_cisco_metadata.py \
  tests/contract/test_cisco_metadata_sdk.py -q -p no:cacheprovider

/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/ruff check \
  --config /tmp/mcp-guard-b-review-f3c2d2b/pyproject.toml \
  src/guard_app/adapters/cisco.py tests/unit/adapters/test_cisco_metadata.py \
  tests/contract/test_cisco_metadata_sdk.py

/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/mypy --strict --no-incremental \
  src/guard_app/adapters/cisco.py
```

초기 SDK 회귀 4개는 테스트가 보호 규칙 설명에 등장하는 태그를 실제 데이터 구간으로 잘못 선택해 실패했다. 데이터 구간의 줄 경계를 확인하도록 관측을 수정한 뒤 위 결과를 얻었다. 제품 구현을 바꿔 결과를 맞춘 것은 아니다.

## 남은 연결

1. 이후 사용자 지시로 [Luna low 제품 연결](c-cisco-luna-connection.md)을 구현했다. 환경 설정·요청 보정과 low 28개 실제 API 평가를 완료했으며 기본 upstream CLI는 변경하지 않았다.
2. 제품 Luna 연결은 INFO infrastructure finding·잘못된 LLM 응답·응답 절단·시간 초과를 고정 오류로 처리한다. 분석기별·도구별 전체 집계는 남아 있으며, 사용자 지시로 B `ScanReport`는 연결하지 않는다. 현재 SDK Finding 목록을 Registry 활성화 증거로 사용할 수 없다.
3. 추가 정의 필드와 float를 포함한 검사 범위에 맞는 지문 계약은 B와 검토해야 한다. 기존 네 필드 지문 범위를 임의로 확대하거나 별도 해시를 만들지 않았다. I10·T10 검증은 남아 있다.
4. A의 실제 `tools/list` 수집·추가 필드 보존·pagination과 Registry·B 저장소·Guard 연결이 남아 있다. 실제 MCP 연결, dispatch·wire·부작용 검증은 하지 않았다.
5. C 제품·테스트에 대한 B 리뷰와 공통 계약 검토가 필요하다. 이 입력 구현의 초기 검증에서는 새 유료 모델 API 호출을 하지 않았고, 후속 연결에서 low 28개 실제 회귀를 실행했다. 전체 보안 스캔은 미실행이다.

[현재 다음 작업](c-cisco-next-steps.md), [기존 API 평가](c-luna-scanner-evaluation.md)를 함께 따른다.
