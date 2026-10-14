# C · Cisco Scanner Luna low 제품 연결

2026-10-13 사용자가 "연결해 b scanreport는 연결하지마"라고 지시했다. C 소유 `adapters/cisco_llm.py`에 환경 설정·키 로딩·Luna low 요청·오류 처리를 연결했다. B의 `ScanReport`·domain·ports·digest는 변경하거나 연결하지 않았다. 관련 I6·I9, T17·T19·S04의 Scanner 연결 부분이다.

## 구현과 사용

`connect_luna_scanner`는 선택한 모델과 HTTP 클라이언트를 async context manager로 연다. 기존 전체 정의 입력 어댑터를 반환하고, 종료·오류·취소 때 클라이언트를 닫는다. 결과는 내부 Cisco SDK Finding 목록이며 공통 ScanReport나 Registry 활성화 판정이 아니다.

```python
from pathlib import Path
from guard_app.adapters.cisco_llm import connect_luna_scanner

async def inspect_definition(tool_definition, *, max_definition_bytes):
    async with connect_luna_scanner(
        Path(".env"), max_definition_bytes=max_definition_bytes
    ) as scanner:
        return await scanner.analyze_tool_definition(tool_definition)
```

`max_definition_bytes`는 호출자가 정한다. 실험에서 사용한 100,000은 실험 상한이며 제품 기본값·전체 프롬프트 토큰 제한으로 확정한 값이 아니다. 후속 사용자 결정으로 [도구당 기본 64KiB·설정 가능 정책](../contracts.md#scanner-입력-크기-사용자-결정)을 문서에 확정했으며 제품 기본값·설정·경고 연결은 미반영이다. 도구 정의만 입력하고 실제 업무 문서·실행 인자·결과·연결 설정은 입력에 합치지 않는다.

환경 파일을 명시적으로 읽고 프로세스 환경 설정을 우선한다. `${OPENAI_API_KEY}` 별칭은 병합한 값으로 확장하며, 다른 비밀 환경변수 참조는 거부한다. 키는 프로세스 환경으로 복사하거나 repr·로그에 공개하지 않는다. Python-dotenv 자동 파일 탐색도 사용하지 않는다.

| 항목 | 제품 적용 |
| --- | --- |
| 모델·추론 | `.env`의 `openai/gpt-6-luna`·`low` 프로필. 다른 값은 지원하지 않는 프로필 오류 |
| 실제 API | OpenAI SDK 2.54.0의 Chat Completions. HTTP 모델 이름은 `gpt-6-luna` |
| 출력 상한 | `.env`의 `MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS`를 `max_completion_tokens`로 전달 |
| sampling | temperature·top_p·logprobs 등을 요청에 넣지 않음 |
| 시간 제한 | `.env`의 시간값을 HTTP 요청과 비동기 검사 전체에 적용. CPU 작업을 강제 중단하는 프로세스 제한은 아님 |
| 재시도 | OpenAI·Cisco의 재시도 0. 오류를 자동 재호출하지 않음 |
| API 주소 | 현재 선택한 `https://api.openai.com/v1`만 허용. HTTP redirect·환경 proxy 비활성 |
| SDK import | 외부 가격표 조회를 import 전에 차단. import 중 암묵적 load_dotenv를 비활성화하고 기존 플래그 복원. tokenizer는 해시를 검증한 사용자 캐시 사본으로 고정(2026-10-14, [C-CISCO-05](../weakness_register.md#발견-항목과-조치)) |
| 이벤트 루프 | SDK import·TLS 인증서 로딩·OpenAI 리소스 지연 import·YARA 준비를 작업 스레드에서 실행. import 환경은 lock으로 직렬화(2026-10-14, [C-CISCO-06](../weakness_register.md#발견-항목과-조치)) |
| SDK 로그 | 현재 Scanner 호출의 OpenAI 요청·응답 원문 디버그 로그를 ContextVar 범위에서 제한. 다른 호출의 로그 설정을 비활성화하지 않음 |

OpenAI Docs에서 Luna의 low 지원과 sampling 제한, Chat Completions의 `max_completion_tokens` 형식을 확인했다. 검사는 텍스트 분류이며 API tool calling을 사용하지 않는다. [Luna 사양](https://developers.openai.com/api/docs/models/gpt-6-luna), [요청 제한](https://developers.openai.com/api/docs/guides/deployment-checklist), [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

## Cisco 호환 지점과 실패 처리

Cisco 4.8.6의 공개 `LLMAnalyzer.analyze`와 기본 프롬프트·Finding 생성은 그대로 사용한다. 이 버전에는 completion 주입 설정이 없어서 **해당 인스턴스의 `_make_llm_request`만 C의 OpenAI 요청 함수로 연결**한다. 전역 `acompletion`과 설치 패키지 파일은 수정하지 않는다. private 연결 지점이므로 버전은 4.8.6만 허용하고 업그레이드 시 재검증한다. 서로 다른 검사와 동시 호출의 상태는 분리한다.

SDK가 API·파싱 오류를 INFO infrastructure finding으로 바꾸는 경우에도 C가 오류 상태를 확인해 고정 오류를 반환한다. 다음 경우를 findings 0개나 정상 완료로 바꾸지 않는다.

- 키 누락·지원하지 않는 프로필/주소·잘못된 제한값·SDK 버전 불일치.
- 인증 실패·rate limit·API 오류·시간 초과.
- 응답 절단·거절·tool call·빈 응답·유효하지 않은 JSON.
- 필수 threat 구조/타입 누락·모순된 악성 여부와 분류 목록·알 수 없는 분류·중복 JSON 키·NaN.

오류에는 원문 예외·응답·키·파일 경로를 넣지 않는다. 실패 코드는 `SCAN_LLM_*`이며 B의 reason enum이나 ScanReport를 확장하지 않았다. 이 오류를 Registry 상태에 연결하는 정책은 미구현이다.

## 실제 검증

오프라인 단위·설치 SDK·HTTP 대역은 **62 passed, 20 subtests passed, 1.97초**였다. 기존 입력 테스트도 포함한다. 요청 본문의 low·토큰 상한·temperature 생략, 필드 전체 전달, 401·429·500·timeout·형식 오류·응답 절단·동시 호출 격리·클라이언트 종료·취소·DEBUG 로그의 원문 비노출을 확인했다. 이 테스트들의 socket 연결 시도는 0이다.

Ruff lint·format과 4개 제품 소스의 strict mypy가 통과했다. 별도 새 프로세스에서 사용자 `.env`를 명시적으로 읽어 SDK를 열고 닫았고, 키 환경변수와 기존 dotenv 플래그가 그대로임을 확인했다. 이 확인은 모델 요청을 보내지 않았다.

같은 수작업 정답의 정상 12개·공격 16개를 **제품 코드와 사용자 키로 각 1회**, 실제 OpenAI API에 보냈다. [실행 근거](../evidence/luna-low-product-2026-10-13.json)

| 항목 | 실제 결과 |
| --- | --- |
| API 요청·완료 | 28건, 모두 HTTP 200·정상 종료·필수 응답 구조 유효 |
| 전체 정의 전달 | 28/28 실제 HTTP 메시지에서 전체 JSON 확인 |
| 공격 | 16/16 탐지, 누락 0 |
| 정상 | 11/12 무경고, 오탐 1 |
| 오탐 | B06 교육용 공격 문구 인용 사례가 HIGH로 분류됨 |
| 완료 시간 | p50 4.991초, p95 nearest-rank 7.212초. Guard 정책 지연 아님 |

이 결과는 입력 전달·제품 연결과 작은 합성 세트의 결과다. 반복성·독립 정답 리뷰·실제 서버 metadata·다른 모델 정확도 우위를 증명하지 않는다. 앞선 none 평가의 정상 오탐 0개와 다르지만, 이번에는 low와 제품 요청 경로·JSON 표현도 바뀌었으므로 차이를 추론 설정 하나의 영향으로 단정하지 않는다. 오탐을 숨기기 위해 프롬프트·모델을 바꾸거나 결과를 재시도하지 않았다.

실제 API 평가 이후 SDK import의 자동 dotenv 로딩을 막는 처리를 추가했다. 모델 요청·프롬프트·결과 검증은 동일하며, 이 보완은 오프라인 회귀와 새 프로세스 import로 검증했다. API 28개를 다시 실행하지 않았다.

재현은 Scanner·개발 도구가 설치된 Python으로 루트에서 실행한다. 실행 조합은 Python 3.12.3, Cisco 4.8.6, OpenAI 2.54.0, MCP 1.30.0, LiteLLM 1.93.2이며 기존 스파이크 환경의 pytest 9.1.1·Ruff 0.17.0·mypy 2.4.0을 사용했다.

```bash
PYTHONPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python \
  -m pytest tests/unit tests/contract -q -p no:cacheprovider

MYPYPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/mypy \
  --strict --no-incremental --namespace-packages --explicit-package-bases src
```

현재 C 브랜치에는 B의 최상위 package 파일이 없으므로 mypy에는 namespace와 `src` 경로를 명시한다. B의 파일을 복제하거나 packaging·공통 lock을 새로 정의하지 않았다. `.venv-scanner`의 개발 도구 설치 완료를 뜻하지 않는다.

실제 API 평가 스크립트는 `/tmp/mcp-guard-luna-product-quBKcJ/probe.py`다. 환경 파일과 키·전체 요청/응답은 저장하지 않고 고정 메타데이터·사용량·case별 finding 수만 근거에 남겼다.

## 범위와 남은 작업

사용자 지시대로 B ScanReport 연결은 제외했다. Registry·저장소·Guard와 실제 MCP 연결도 없다. 관리 대상 tools/call·dispatch·wire·부작용을 실행하거나 검증하지 않았다.

다음 검토는 입력 크기 정책·필수/선택 검사 실패 정책·오탐 처리이며, C 구현·테스트의 B 리뷰도 남아 있다. C2 위험 연산 전체·T17/T19/S04 전체·I10의 지문 변경 인수·전체 보안 스캔 완료를 주장하지 않는다.
