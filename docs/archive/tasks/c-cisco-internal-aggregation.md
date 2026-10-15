# C · Cisco LLM·YARA 내부 집계 구현

2026-10-13 사용자 "내부 집계까지 구현해" 지시로 `src/guard_app/adapters/cisco_aggregation.py`를 구현했다. **같은 전체 정의 사본의 병행 검사, 독립 실패 처리, 출처·심각도 보존, 도구별·분석기별 집계와 JSON 출력**까지 C 내부에서 동작한다. B ScanReport·domain·ports·digest·storage와 A transport·UI는 변경하지 않았다. 공통 API 합의·B 리뷰는 남아 있다.

실제 생성한 [JSON 예시](../examples/c-scanner-internal-aggregation.json)와 [검증 근거](../evidence/cisco-internal-aggregation-2026-10-13.json)를 함께 남겼다. 이전 [제안 형식 예시](../examples/c-scanner-aggregation-examples.json)는 구현 전 제안이며 현재 결과 필드는 새 예시를 따른다.

## 구현 동작

1. `scan(server_id, definitions)`에서 첫 `await` 전에 모든 정의를 한 번씩 사본으로 만든다. `to_dict()`는 원본별 한 번만 호출하며 이후 원본 변경은 검사 입력을 바꾸지 않는다. 중첩 schema·annotations·추가 필드를 보존한다.
2. 도구 하나의 LLM·YARA는 병행하고 다음 도구는 두 분석기가 끝난 뒤 검사한다. 기존 Luna low·60초·4,096토큰·재시도 0 설정을 유지한다. YARA는 별도 thread에서 실행해 모델의 async loop를 막지 않는다.
3. 기본 상한은 전체 Tool JSON UTF-8 기준 65,536바이트이며 `max_definition_bytes` 인자로 변경한다. 초과·잘못된 정의는 자르지 않고 두 분석기 모두 미실행으로 기록한다. 다른 정의 검사는 계속한다.
4. 예상한 SDK/API/native 오류는 고정 코드로 기록한다. 한쪽 준비·검사 실패가 다른 분석기 실행을 생략하지 않는다. 알려지지 않은 오류 메시지를 코드로 복사하지 않는다. 예상하지 못한 프로그래밍 오류는 다른 분석기를 끝낸 후 호출 경계에 전달한다. 넓은 예외 처리를 추가하지 않았다.
5. SDK finding에서는 분석기 출처·원래 severity·category·선택적 `details.threat_type`만 복사한다. evidence·raw_response·모델 응답·전체 정의·키는 결과에 넣지 않는다. 두 분석기가 같은 위험을 찾으면 원래 항목을 각각 보존하며 합치거나 severity를 낮추지 않는다.
6. 결과는 frozen dataclass와 tuple로 고정한다. 기본 `repr`에서 도구 이름·서버 ID·분류/코드를 숨긴다. `to_dict()`·`to_json()`은 명시적 산출물이며 자동 로그·UI 공개·저장·차단 판정을 하지 않는다.

| 상태 | 의미 | findings |
| --- | --- | --- |
| 분석기 `completed` | 호출과 결과 검증 완료 | 빈 목록 또는 발견 목록 |
| 분석기 `failed` | 준비·호출·결과 검증 실패 | 결과 없으면 `null`; 이미 검증한 항목은 보존 |
| 분석기 `not_run` | 입력 준비 실패로 실행하지 않음 | `null` |
| 도구 `completed` | 두 분석기 모두 완료 | 위험이 있어도 완료 상태 유지 |
| 도구 `partial` | 한 분석기만 완료 | 완료 결과와 실패 정보 함께 보존 |
| 도구 `failed` | 두 분석기 모두 완료하지 못했고 검사 실패가 있음 | 고정 오류·알려진 발견 항목 보존 |
| 도구 `not_scanned` | 입력 준비 실패 | `input_error_code`와 두 분석기 미실행 |

`summary`에는 전체·완료·부분 완료·실패·미검사 도구 수, 알려진 finding 수, finding 경고가 있는 도구 수, 최대 관찰 심각도가 나온다. 실패/미검사 경고의 수는 각각의 상태 계수로 확인한다. `analyzer_summary`에는 LLM/YARA별 완료·실패·미실행·알려진 finding 수와 준비 오류가 나온다. `tools_with_warnings`는 finding 경고 도구 수이며 진단 실패 도구 수를 합치지 않는다. `UNKNOWN` severity는 보존하되 결과 검증 실패로 기록한다. `maximum_observed_severity: null`은 안전 판정이 아니다.

범위는 항상 `provided_definitions`다. 빈 전달 집합의 완료도 MCP 전체 목록 수집 완료나 등록 허용을 뜻하지 않는다. 분석기 준비가 실패했다면 빈 집합에서도 준비 오류를 보존하고 `not_scanned`로 기록한다. Scanner 위험·진단 실패는 사용자 결정대로 경고 자료로만 제공한다. Guard fail-closed·I10 정의 변경·승인 무효화·관리자 활성화 의미는 별도다.

## 사용 예

```python
from pathlib import Path
from guard_app.adapters.cisco_aggregation import connect_metadata_scanner

# 신뢰한 호출자가 server_id와 수집한 전체 Tool 객체 목록을 제공한다.
async with connect_metadata_scanner(Path(".env")) as scanner:
    result = await scanner.scan(server_id, definitions)
    payload = result.to_dict()
```

`connect_metadata_scanner`는 기본 64KiB, 기존 OpenAI 설정과 native YARA를 함께 연결한다. 두 연결은 개별 준비 실패를 보존한다. 테스트에서는 `environ`과 HTTP `transport`를 주입할 수 있다. 앱 설정 로딩과 MCP 수신 중 제한을 구현한 것은 아니다.

## 실행한 검증

| 검증 | 실제 결과 |
| --- | --- |
| 전체 단위·SDK 계약 회귀 | **96 passed, 20 subtests passed**, 1.75초 |
| 신규 내부 집계 단위 | **24개**: 사본 1회·같은 입력·병행 실행·독립 실패·불변 결과·형식/크기 경계·미실행·취소·예상 밖 오류 |
| 신규 실제 SDK/native YARA 계약 | **10개**: metadata 위치 3개·HTTP 429·키 누락·native 오류/timeout·준비/버전 실패·sync matcher의 loop 비차단 |
| Ruff lint·format | 기존 B 설정 사용, 제품/테스트 **14개 파일** 확인 |
| strict mypy | 제품 소스 **5개**, namespace package 옵션 사용 |
| 이전 실제 수집 정의를 새 집계로 검사 | 변형 **144개**, LLM SDK HTTP 대역·native YARA 모두 완료, GitHub YARA HIGH **1건** 보존 |
| 산출물 예시 | 정상 무탐지·양쪽 경고·LLM 실패/YARA 경고·입력 상한 초과를 실제 코드로 생성 |
| 네트워크 | HTTP 대역 총 **147건**: 실제 정의 144개 + 합성 유효 입력 3개. 외부 socket 접속 시도 **0**, 실제 모델 API **0** |

이번 LLM 응답은 `httpx.MockTransport`로 합성했다. 실제 SDK의 프롬프트·HTTP 요청·응답 파싱·finding 생성·집계는 실행했지만 모델 탐지 정확도·성능 재평가는 아니다. 실제 모델 144건을 호출한 이전 [운영 검증](c-scanner-operating-validation.md)과 구분한다. 공개 MCP 서버를 이번에 재접속하지 않았고 앞서 수집한 전체 정의를 임시 파일에서 읽었다. 새 테스트에서는 외부 socket 접속을 막았다.

관련 I6·I9와 T17·T19의 입력/완료/오류/비노출 부분을 검증했다. T10·T17의 Registry 전이, S04 전체 정적 강제 규칙, Guard dispatch·wire·수신 부작용은 이번 내부 집계만으로 검증할 수 없다.

## 재현

```bash
PYTHONPATH=src LITELLM_LOCAL_MODEL_COST_MAP=True PYTHON_DOTENV_DISABLED=1 \
  /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python -m pytest tests -q

/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/ruff check \
  --config /tmp/mcp-guard-b-review-f3c2d2b/pyproject.toml src tests
/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/ruff format --check \
  --config /tmp/mcp-guard-b-review-f3c2d2b/pyproject.toml src tests
MYPYPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/mypy \
  --strict --no-incremental --namespace-packages --explicit-package-bases src

# 새 출력 폴더 필요. metadata-dir 생략 시 합성 예시만 생성한다.
PYTHONPATH=src LITELLM_LOCAL_MODEL_COST_MAP=True PYTHON_DOTENV_DISABLED=1 \
  /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python \
  tests/experiments/scanner_aggregation_probe.py \
  --output /tmp/mcp-guard-internal-aggregation-rerun \
  --metadata-dir /tmp/mcp-guard-real-metadata-5K33Xa
```

임시 경로는 이번 환경의 기록이다. 다른 환경에서는 동일 버전과 기존 B 검사 설정·공개 정의 수집 파일을 준비해야 한다. 원문 정의는 저장소에 추가하지 않았다.

## 남은 작업

- 전역 동시성·대기열·전체 deadline·취소 시 worker 종료와 YARA native timeout. 현재 `to_thread` 취소가 실행 중인 native matcher를 강제로 멈추지는 않는다. **5초 YARA 제한·앱 전역 LLM 4개/YARA 1개를 보장하지 않는다.**
- A 목록 페이지 1MiB·조회 누적 8MiB 수신 제한, 앱 설정·경고/진행 UI, C Registry·B Store/digest·정의 변경 연결.
- B 리뷰와 공통 Scanner 계약. B ScanReport 제외 지시는 유지한다. 관리 도구의 실제 dispatch·wire·수신 부작용 인수는 A/B 실행 경로 연결 후 진행한다.
