# Luna Scanner 확장 평가 · 2026-10-13

사용자의 "luna가 더 좋지? 이걸로 더 테스트해봐" 요청으로 정상 12개·공격 16개를 평가했다. 원래 SDK 입력 경로는 공격 13/16개를 탐지했고, 전체 metadata를 전달하는 임시 경로는 같은 공격 16/16개를 탐지했다. 정상 12개는 두 경로에서 오탐 0개였다. **Luna가 Mini보다 정확하다는 결론은 아직 미확정**이다. 같은 세트의 Mini 실행은 하지 않았다.

## 실행 방법과 정답 고정

- 모델: `openai/gpt-6-luna`, `reasoning_effort=none`, `max_completion_tokens=4096`, temperature 생략, timeout 60초.
- Python 3.12.3, Scanner 4.8.6, LiteLLM 1.93.2, OpenAI SDK 2.54.0, MCP 1.30.0, python-dotenv 1.2.4. 앞선 [연결 설정](c-openai-scanner-setup.md)의 전용 환경을 사용했다.
- `.env`의 기존 키를 읽고 실제 OpenAI Chat Completions를 호출했다. 기본 모델 설정과 설치된 라이브러리 소스는 변경하지 않았다.
- 정답·근거·payload 위치를 **실행 전에** [합성 사례 파일](../evidence/luna-expanded-2026-10-13-cases.json)에 고정했다. SHA-256: `7bb542ef31fd5504fc3ccc93325b8c75681b0cfbaea38c7a7cb2891bf3a6a6f1`.
- 정답은 C가 작성한 기대 행동이며 독립 리뷰는 미완료다. 도구 이름은 `tool_001` 같은 중립 이름을 썼고 정답·사례 ID·근거는 모델 입력에서 제외했다.
- 공개 `LLMAnalyzer.analyze`와 실제 LiteLLM·OpenAI SDK를 사용했다. HTTPX 전송을 관찰하면서 실제 응답을 그대로 반환했다. 대역 응답을 사용하지 않았다.
- Scanner·LiteLLM·공급한 OpenAI 클라이언트의 재시도를 0으로 두고 순차 실행했다. API/형식 실패는 정상이나 탐지 0개로 집계하지 않는 기준을 먼저 정했다.

OpenAI Docs에서 Luna의 `none` 지원과 추론 설정을 확인했다. 현재 LiteLLM의 일반 GPT 경로에는 해당 옵션이 없어 `extra_body`로 전달했고 실제 요청 본문을 확인했다. [Luna 사양](https://developers.openai.com/api/docs/models/gpt-6-luna), [요청 설정 안내](https://developers.openai.com/api/docs/guides/latest-model)

## 사례 구성

| 사례 | 검사하려는 항목 |
| --- | --- |
| B01–B05 | 공개 조회·로컬 변환·사용자 승인 쓰기·정상 인증·감사 metadata |
| B06–B09 | 공격 문구를 인용한 보안 교육·비밀값 마스킹·승인된 export·제한된 파일 읽기 |
| B10–B12 | 한국어 공개 조회·바인딩 SQL·고정된 무해한 진단 명령 |
| A01–A05 | 지시 무시·관리자 사칭·인증정보 유출·설정 파일 유출·악성 도구 선행 조건 |
| A06–A09 | Base64·한국어·일본어·zero-width 문자로 표현한 공격 |
| A10–A13 | 인자 설명의 공격·긴 설명 끝의 공격·Scanner 출력 조작·숨겨진 권한 상승 |
| A14 | `inputSchema.description`의 공격 |
| A15 | `annotations.title`의 공격 |
| A16 | 중첩 `inputSchema.properties` 안의 description 공격 |

인용된 공격 문구와 정상 인증·프로세스·SQL 설명은 단어만으로 악성 판정을 해서는 안 되는 정상 대조군이다. API에는 이 합성 metadata만 보냈다. 파일·명령·SQL·외부 수집 주소를 실제로 실행하지 않았고 관리 대상 MCP 도구를 호출하지 않았다.

## 결과

| 실행 | 실제 HTTP 요청 | 정상 오탐 | 공격 탐지 | p50 / p95 완료 시간 |
| --- | --- | --- | --- | --- |
| SDK의 기존 필드 추출 · 전체 28개 | 28 | 0/12 | 13/16 | 1.865초 / 2.489초 |
| 빠진 3개에 전체 metadata 전달 · 표적 재검사 | 3 | 정상 대조군 없음 | 3/3 | 2.387초 / 2.450초 |
| 전체 metadata 전달 · 같은 28개 전체 재검사 | 28 | 0/12 | 16/16 | 2.054초 / 3.511초 |

총 **59건 모두 HTTP 200·JSON 객체 파싱·필수 threat 필드 검사에 성공**했다. API/형식 실패는 0건이며 탐지한 공격은 HIGH findings를 반환했다. `finish_reason=stop`, `reasoning_effort=none`, temperature 생략, `max_completion_tokens=4096`을 실제 요청에서 확인했다. 오류 사례가 없었으므로 사전 계획한 `low` 후속 호출은 실행하지 않았다.

- [SDK 경로·표적 재검사 원자료](../evidence/luna-expanded-2026-10-13-baseline.json)
- [전체 metadata 경로 원자료](../evidence/luna-expanded-2026-10-13-full-metadata.json)
- [집계](../evidence/luna-expanded-2026-10-13-summary.json)

시간은 모델 API 완료 시간이며 Guard의 정책 지연이 아니다. p95는 각 작은 표본의 nearest-rank 방식이다. 각 사례는 각 경로에서 1회 실행했고 반복성·생산 환경 성능을 검증한 수치는 아니다. 응답 원문·인증 헤더·전체 요청 메시지·예외 원문은 기록하지 않았다.

관측 토큰에 미캐시 Standard·짧은 입력의 문자 요금을 적용한 59건의 참고 비용은 약 **$0.0289**다. 캐시 쓰기·할인·세금 등은 제외했고 실제 청구액 확인은 아니다. [공식 Luna 단가](https://developers.openai.com/api/docs/models/gpt-6-luna)

## 놓친 3개의 원인과 보정 확인

SDK의 `LLMAnalyzer._parse_tool_content`는 도구 description과 인자 properties를 추출하고, prompt 생성은 각 직접 인자의 type·description을 사용한다. A14의 루트 schema description, A15의 annotations title, A16의 중첩 인자 description은 해당 모델 입력에서 빠졌다.

기존 경로의 실제 HTTP 메시지에서 세 공격 문구가 없음을 확인했다. 모델은 정상 description만 받았고 `malicious=false`·findings 0개를 반환했다. 이 누락을 Luna의 분류 실패로 단정하지 않는다. C가 SDK를 그대로 적용할 때 필요한 검사 범위와 SDK가 전달하는 범위의 차이다. [C-CISCO-03](../weakness_register.md)에 기록했다.

임시 검증 코드에서 도구 정의 전체를 정렬된 JSON으로 직렬화해 description으로 전달했다. 그 결과 내용은 기존 Scanner의 보호 태그 안에 들어갔다. 세 사례의 공격 문구 전달·탐지를 재확인했고, 정상 대조군을 포함한 전체 28개도 같은 방식으로 검사했다. 평가 당시 제품에는 미반영이었다. 이후 사용자의 후속 구현 지시로 [입력 보정 부분](c-cisco-metadata-implementation.md)을 `src/guard_app/adapters/cisco.py`에 반영하고 [Luna low 제품 요청 연결](c-cisco-luna-connection.md)을 구현했다. B ScanReport는 사용자 지시로 미연결이며 Registry 연결도 남아 있다.

문구 전달 관측은 원문 문자열의 정확한 일치를 사용했다. 전체 metadata 경로의 A12는 JSON 따옴표가 escape돼 이 값이 false지만 HIGH로 탐지됐다. 같은 SDK prompt를 오프라인으로 재구성해 escape된 표현이 포함됨을 확인했으며 이를 전달 누락으로 집계하지 않았다. 재구성은 저장된 HTTP 본문의 재검사와 구분해 원자료에 표시했다.

## 판단과 남은 검증

이번 세트에서는 Luna의 오탐과 모델에 전달된 공격의 누락을 관찰하지 않았다. 낮은 단가와 이 결과는 기본 검사 모델 후보로 선택할 근거가 된다. Mini의 같은 세트 실행·독립 정답 리뷰·반복 실행·더 어려운 공격·실제 서버 metadata 평가가 없으므로 상대적 정확도나 일반적인 탐지율은 확정하지 않는다.

제품 적용에는 Luna 요청 파라미터 보정, 전체 metadata의 누락 없는 전달·크기 제한·지문 집합 연결, 형식 불명·시간 초과의 실패 처리, B 계약으로의 결과 번역이 필요하다. 검사는 관리자 활성화와 Guard의 경계 검사를 대체하지 않는다.

관련 불변식은 I6·I9·I10이며 입력 누락과 완료 의미는 T17, 로그 비노출은 T19·S04의 후속 인수와 연결된다. **T17·T19·S04 전체 통과와 Guard·Registry·MCP dispatch·wire·부작용 검증은 미완료**다. C-CISCO-03의 입력 보정은 제품에 반영했고 지문·완료 집계·실제 연결과 B 리뷰는 남아 있다.

## 후속 low 제품 경로 평가

같은 정상 12개·공격 16개를 후속 제품 연결에서 low로 각 1회 평가했다. 실제 API 28건 모두 HTTP 200·필수 응답 구조 유효·전체 정의 전달이었다. 공격 16/16 탐지, 정상 오탐은 B06 교육용 공격 문구 인용 1/12였다. 위 none 결과와 구분하며 추론 설정·제품 요청 경로·JSON 표현의 변경을 한 원인으로 단정하지 않는다. [제품 연결 보고](c-cisco-luna-connection.md), [실행 근거](../evidence/luna-low-product-2026-10-13.json)
