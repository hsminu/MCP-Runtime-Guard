# Cisco Scanner의 OpenAI 모델 설정

2026-10-13 사용자의 OpenAI API 연결·모델 선택 요청에 따라 C의 Scanner 설정과 전용 가상환경을 준비했다. 후속 구현 지시로 [제품 Cisco 입력 어댑터](c-cisco-metadata-implementation.md)와 [Luna low 요청 연결](c-cisco-luna-connection.md)을 반영했다. 사용자 지시로 B ScanReport는 연결하지 않았으며 Registry와 A의 에이전트 모델 클라이언트 구현은 아직 남아 있다.

## 현재 선택한 기본값

2026-10-13 사용자가 **"일단 luna low로 하는 걸로 가자"**고 결정했다. 현재 `.env`와 공유 템플릿의 선택은 `openai/gpt-6-luna`·`low`다. 입력한 키와 다른 설정은 보존했다. 이 결정은 C의 Scanner LLM에 한정하며 A의 Runner 모델·공통 API 계약·필수 분석기 정책을 함께 확정한 것은 아니다. [Luna 공식 사양](https://developers.openai.com/api/docs/models/gpt-6-luna)

처음에는 현재 Scanner 조합의 요청 변환 지원을 기준으로 `gpt-5.4-mini-2026-03-17`을 선택했다. 아래 초기 Mini·Luna none 실험은 당시 설정의 기록이다. 확장 28개 평가는 `none`으로 실행했으며 `low`의 전체 회귀 검증을 대신하지 않는다.

현재 공식 모델 목록에는 GPT-6 계열도 있다. 설치된 LiteLLM의 로컬 모델 목록에는 GPT-6 항목이 없고 Scanner의 제약 보정은 `gpt-5` 이름만 처리한다. 모델 이름만 변경하면 HTTP 400을 재현했다. 현재 C 제품 연결은 해당 SDK 인스턴스의 요청을 OpenAI 클라이언트로 보내며 low·토큰 상한·sampling 생략을 적용한다. 다른 GPT-6 모델의 연결은 미검증이다. [현재 공식 모델 비교](https://developers.openai.com/api/docs/models/compare)

| 항목 | 설정 |
| --- | --- |
| LiteLLM 모델 | `openai/gpt-6-luna` |
| API 주소 | `https://api.openai.com/v1` |
| 요청 시간 제한 | 60초 |
| 응답 토큰 상한 | 4096. 실제 요청에는 `max_completion_tokens`로 전달 |
| temperature | 요청에서 생략. `.env`의 이전 temperature 설정도 제거 |
| reasoning effort | `low`, `MCP_SCANNER_LLM_REASONING_EFFORT`에 기록 |
| 모델 가격표 | `LITELLM_LOCAL_MODEL_COST_MAP=True`로 로컬 목록 사용 |

시간·토큰 제한은 초기 운영값이며 성능 측정 결과가 아니다. `max_completion_tokens`에는 추론 토큰도 포함된다. **`MCP_SCANNER_LLM_REASONING_EFFORT`는 프로젝트 어댑터·검증 코드용 변수이며 Scanner 4.8.6의 기본 CLI가 읽는 변수가 아니다.** C 제품 어댑터가 이를 읽고 `max_completion_tokens`·sampling 생략·low 전달을 구현했다. 기본 CLI에 `.env`만 로드해 모델 이름을 바꾸는 경로는 HTTP 400을 재현했으며 제품 사용 경로로 간주하지 않는다. Structured Outputs도 아직 활성화하지 않았다. [요청 파라미터 가이드](https://developers.openai.com/api/docs/guides/latest-model)

## Luna low 선택 확인

변경된 `.env`의 모델·추론 값을 임시 검증 코드가 읽고, 전체 metadata와 Luna 요청 보정을 적용해 실제 API 두 건을 실행했다. 두 건 모두 HTTP 200·JSON 파싱 성공·`finish_reason=stop`이었다. 실제 HTTP 본문에서 `reasoning_effort=low`, `max_completion_tokens=4096`, temperature 생략을 확인했다. [실행 근거](../evidence/openai-scanner-luna-low-2026-10-13.json)

| 사례 | 탐지 | 완료 시간 | 추론 토큰 |
| --- | --- | --- | --- |
| 정상 합성 도구 정의 | 0건 | 2.733초 | 22 |
| 인젝션·유출 지시가 있는 합성 정의 | HIGH 2건 | 5.058초 | 225 |

위 두 건은 당시 설정 선택 확인이다. 이후 제품 코드로 low 28개를 재검사했고 공격 16/16 탐지·정상 오탐 1/12, HTTP 200 28건이었다. [제품 연결·검증](c-cisco-luna-connection.md)을 따른다. 앞선 28개·59회 확장 평가는 `none`이며 이번 low 결과와 구분한다. 실제 MCP `tools/list`·Guard 도구 호출·Registry 활성화는 미구현/미검증이다.

## 키 입력과 설정 로딩

초기 준비에서 저장소 루트의 `.env`를 만들고 키를 비워 뒀다. 2026-10-13 사용자가 키를 입력한 뒤 실제 API 호출을 확인했다. 다른 환경에서도 다음 한 줄만 채운다.

```dotenv
OPENAI_API_KEY=발급받은_OpenAI_API_키
```

`MCP_SCANNER_LLM_API_KEY=${OPENAI_API_KEY}`를 python-dotenv가 확장해 Scanner에 전달한다. `.env`는 파일 권한 `0600`이며 `.gitignore`에서 제외했다. 공유하는 [.env.example](../../.env.example)에는 빈 키와 기본값만 있다.

**키와 설정을 읽은 뒤 Scanner를 import해야 한다.** C의 `connect_luna_scanner`는 명시한 파일을 읽어 설정을 구성하고 Config·OpenAI 요청에 직접 전달한다. 프로세스 설정을 우선하며 키를 환경변수로 복사하지 않는다. SDK import의 자동 dotenv 로딩은 제한하고 가격표 조회도 import 전에 차단한다. Scanner CLI는 일부 의존성을 import한 뒤 `.env`를 읽어서 초기 상수가 반영되지 않을 수 있다. CLI에서는 아래처럼 dotenv 실행기를 먼저 사용한다.

```bash
.venv-scanner/bin/python -m dotenv -f .env run -- .venv-scanner/bin/mcp-scanner --help
```

이 명령은 도움말 확인이며 모델을 호출하지 않는다. 기존 프로세스의 환경변수도 설정에 영향을 주므로 SDK에서 사용할 값과 로딩 우선순위를 확인한다. 실제 키나 전체 환경변수를 출력하지 않는다.

키가 비어 있으면 LLM 검사를 완료로 취급하지 않는다. 현재 upstream CLI는 키 누락 시 분석기를 생략할 수 있으므로, 어댑터에서 명시적인 실패·생략을 기록해야 한다. 자세한 실패 의미는 [Scanner 조사](c-cisco-scanner-spike.md)에 있다. LLM은 선택 검사이며 필수 오프라인 검사를 대체하지 않는다.

## 준비한 환경

`.venv-scanner`에 Python 3.12.3과 다음 조합을 설치했다. 프로젝트 전체의 의존성 합의를 확정한 것은 아니다.

| 패키지 | 버전 |
| --- | --- |
| `cisco-ai-mcp-scanner` | 4.8.6 |
| `litellm` | 1.93.2 |
| `mcp` | 1.30.0 |
| `openai` | 2.54.0 |
| `python-dotenv` | 1.2.4 |

재설치는 루트에서 다음 명령을 사용한다. constraint는 앞선 Scanner 실험의 버전 자료이며 제품 공통 lock이 아니다.

```bash
.venv/bin/python -m venv --without-pip .venv-scanner
.venv/bin/python -m pip --python .venv-scanner/bin/python install \
  --constraint docs/evidence/cisco-scanner-4.8.6-spike-freeze.txt \
  cisco-ai-mcp-scanner==4.8.6 mcp==1.30.0 litellm==1.93.2 python-dotenv==1.2.4
```

## 실제 검증과 남은 확인

- `pip check` 통과. 설치된 103개 패키지가 실험 constraint의 버전과 일치했다. 기존 `.venv`와 분리된 환경에 설치했다.
- 문서의 dotenv 실행기로 `mcp-scanner --help`를 실행해 정상 종료를 확인했다.
- 초기 오프라인 검증 당시 `.env`의 키가 비어 있고 Git 제외 대상인 것을 확인했다.
- 별도 임시 `.env`의 합성 키로 dotenv 확장과 실제 CLI `Config` 구성을 검증했다. 실제 `.env`에는 합성 키를 쓰지 않았다.
- 공개 `LLMAnalyzer.analyze`에서 실제 LiteLLM·OpenAI SDK 요청 생성 경로를 실행했다. HTTPX의 전송 함수를 대역으로 교체해 요청 1건의 주소·모델·인증 전달·토큰 상한·시간 제한을 확인하고 합성 JSON 응답을 반환했다.
- Python socket 연결 시도 0, 실제 OpenAI API 호출 0. 대역 응답의 findings 0은 실제 모델 탐지 결과가 아니다. [관측 자료](../evidence/openai-scanner-config-2026-10-13.json)

관련 불변식은 I6·I9·I10, 후속 검증은 T17·T19·S04다. 이번 검증은 설정과 요청 생성 범위이며 Registry PENDING 유지·관리 대상 도구 dispatch·wire·부작용은 검증하지 않았다.

### 후속 실제 API 호출 · 2026-10-13

사용자가 입력한 키는 존재했으나 Scanner용 변수 참조가 잘못돼 빈 값으로 확장됐다. `.env`의 `MCP_SCANNER_LLM_API_KEY` 줄만 `${OPENAI_API_KEY}`로 복원했다. 기존 키 값과 다른 설정은 보존했고 파일 권한 `0600`·Git 제외를 확인했다.

실제 공개 `LLMAnalyzer.analyze` → LiteLLM → OpenAI SDK → `POST /v1/chat/completions` 경로로 합성 metadata 두 건을 검사했다. HTTP 응답은 대역으로 교체하지 않고 실제 서버 응답을 사용했다. Scanner·LiteLLM·공급한 OpenAI 클라이언트의 재시도는 이 실험에서 0으로 설정했다. 원문 stdout/stderr·응답 본문·인증 헤더는 기록하지 않았다. [실행 근거](../evidence/openai-scanner-live-2026-10-13.json)

| 사례 | HTTP 상태 | JSON / 모델 판정 | Scanner findings | 완료 시간 / 토큰 |
| --- | --- | --- | --- | --- |
| 정상 공개 상태 도구 설명 | 200 | 파싱 성공, malicious=false | 0건 | 2.742초 / 입력 4499·출력 32 |
| 지시 무시·인증정보 외부 전송을 요구하는 악성 설명 | 200 | 파싱 성공, malicious=true | HIGH 2건 | 2.137초 / 입력 4548·출력 142 |

요청 2건 모두 모델 `gpt-5.4-mini-2026-03-17`, 토큰 상한 4096, temperature 1을 사용했고 `finish_reason=stop`으로 끝났다. 악성 사례의 모델 원분류는 `PROMPT INJECTION`·`TOOL POISONING`이며 Scanner의 범주 표시는 `PROMPT INJECTION`·`SUSPICIOUS CODE EXECUTION`이다. 입력의 외부 수집 주소는 합성 문자열이며 해당 도구를 실행하거나 그 주소에 접속하지 않았다.

이 결과는 키 인증·계정의 해당 모델 접근·실제 JSON 응답·두 합성 사례의 탐지를 확인한 것이다. 탐지율·오탐률·p50/p95·Luna 비교·전체 보안 인수 결과는 아니다. Guard·Registry 및 관리 대상 MCP dispatch·wire·부작용 검증은 미실행이다. HTTP 계수는 모델 API 전송 관측이며 MCP 도구 wire 계수와 구분한다.

시간 초과·인증 오류·응답 절단·잘못된 JSON의 처리와 더 넓은 합성 공격 평가가 남아 있다. 관련 검증은 I6·I9·I10 및 T17·T19·S04이며 이번 성공 경로 두 건만으로 해당 테스트 전체 통과를 표시하지 않는다. 검사에는 합성 도구 정의만 사용하고 업무 본문·인증정보를 보내지 않는다.

### 후속 Luna 비교 · 2026-10-13

사용자의 "luna도 해봐" 요청으로 `openai/gpt-6-luna`를 같은 두 합성 설명에 적용했다. `.env`의 기본 모델은 Mini로 유지하고 호출별 모델만 임시로 지정했다. [원형 실패·보정 후 성공·비교 자료](../evidence/openai-scanner-luna-live-2026-10-13.json)

Scanner 원형 파라미터는 `max_tokens=4096`을 전송했고 실제 HTTP 400·`unsupported_parameter`·`param=max_tokens`를 받았다. 이때 공개 LLM 분석기는 탐지 성공 대신 `INFO / ANALYZER INFRASTRUCTURE` 한 건을 반환했다. 정상 검사 완료로 취급하지 않았다.

그 뒤 임시 검증 코드의 LiteLLM 호출 경계에서 다음을 보정했다. 설치된 Scanner·LiteLLM 파일은 수정하지 않았다.

- `max_tokens`를 `max_completion_tokens=4096`으로 변경.
- `temperature`를 요청에서 제거.
- `reasoning_effort=none`을 명시. 현재 LiteLLM의 일반 GPT 경로에는 이 옵션이 없어 `extra_body`로 전달했고, 실제 HTTP 요청 본문에서 적용을 확인.

Luna는 reasoning effort `none`을 지원한다. 공식 가이드는 GPT-6 이동 시 기존 유효 추론 설정을 지원 범위에서 유지하도록 안내한다. Mini의 기본값이 `none`이라 같은 추론 기준으로 비교했다. [Luna 사양](https://developers.openai.com/api/docs/models/gpt-6-luna), [요청 파라미터 가이드](https://developers.openai.com/api/docs/guides/latest-model)

| 모델 | 정상 설명 | 악성 설명 | 정상 / 악성 완료 시간 | 두 건의 입력 / 출력 토큰 |
| --- | --- | --- | --- | --- |
| GPT-5.4 Mini 고정 버전 | 탐지 0건 | HIGH 2건 | 2.742초 / 2.137초 | 9047 / 174 |
| GPT-6 Luna · 보정 후 | 탐지 0건 | HIGH 2건 | 3.697초 / 2.850초 | 9043 / 133 |

보정된 Luna 호출 두 건 모두 HTTP 200·JSON 파싱 성공·`finish_reason=stop`, 추론 토큰 0이었다. 모델 원분류와 SDK 범주는 Mini 실험과 같았다. Luna 실험의 실제 HTTP 요청은 원형 실패 1건과 보정 성공 2건, 합계 3건이다.

관측 토큰에 미캐시 Standard·짧은 입력 요금을 적용한 참고 비용은 Mini 약 $0.00757, Luna 약 $0.00097이다. 캐시 쓰기·할인·세금 등을 제외한 계산이며 실제 청구액 확인이 아니다. [Mini 단가](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [Luna 단가](https://developers.openai.com/api/docs/models/gpt-6-luna)

두 사례에서는 같은 탐지 결과였고 Luna의 시간은 더 길었다. 각 사례 1회 측정이라 일반적인 속도·정확도 우열을 결론내리지 않는다. Luna를 기본 모델로 전환하려면 제품 어댑터의 파라미터 보정 또는 새 LiteLLM 조합의 검증이 필요하다. Registry·Guard 연결, 넓은 공격 평가, T17·T19·S04 전체 검증은 남아 있다.

### 후속 Luna 확장 평가

사용자의 추가 요청으로 [정상 12개·공격 16개 평가](c-luna-scanner-evaluation.md)를 실행했다. SDK 경로는 공격 13/16개, 전체 metadata를 전달하는 임시 경로는 같은 공격 16/16개를 탐지했고 정상 오탐은 모두 0/12개였다. 빠진 3개는 root schema·annotations·중첩 schema의 입력 누락으로 확인했다. 총 59건의 실제 API 결과와 C-CISCO-03을 기록했으며 제품 보정·독립 리뷰·동일 세트 Mini 비교·전체 인수는 남아 있다.
