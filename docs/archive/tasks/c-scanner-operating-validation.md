# C · Scanner 운영 추천안 검증

2026-10-13 사용자 "검증 ㄱㄱ" 요청으로 기존 회귀, 실제 공개 MCP 정의의 Luna low/YARA 검사, stdio·HTTP 경계 fixture, YARA timeout 결함 주입을 실행했다. **현재 C 입력·LLM 연결은 정상 동작했다. 동시 LLM 4개·YARA worker 1개의 실험 배치는 완료됐지만, A 수신 제한과 제품 병행 실행·UI는 아직 구현되지 않았다.**

[집계·측정 근거](../evidence/scanner-operating-validation-2026-10-13.json)에 도구별 해시·크기·완료 상태·고정 분류와 심각도, 실제 API 상태, fixture 송수신 계수와 시간을 기록했다. 원문 Tool 정의·API 키·전체 모델 요청/응답·SDK evidence는 저장소에 추가하지 않았다. 제품 소스·`.env`·B domain/ports/ScanReport는 변경하지 않았다. 새 추천값을 사용자 승인으로 기록하지 않았다.

## 기존 제품 회귀

| 검증 | 결과 |
| --- | --- |
| 오프라인 단위·설치 SDK·HTTP 대역 | **62 passed, 20 subtests passed**, 1.63초 |
| Ruff lint | 기존 B 설정으로 `src`, `tests`, 신규 공격 fixture 통과 |
| Ruff format | 같은 설정으로 **11개 파일** 확인 |
| strict mypy | namespace package 옵션으로 **제품 소스 4개** 통과 |
| 전체 정의 바이트 경계 | 65,535·65,536바이트 허용, 65,537바이트는 `SCAN_METADATA_TOO_LARGE` |
| 초과 입력의 분석기 호출 | **0회**. 큰 입력을 잘라 검사하지 않음 |

처음에 기본 Ruff 설정과 namespace 옵션 없는 mypy 명령을 사용해 검사 설정 차이로 실패했다. 기존 [재현 문서](c-cisco-metadata-implementation.md)의 B Ruff 설정과 [Luna 연결 문서](c-cisco-luna-connection.md)의 namespace 옵션으로 다시 실행해 통과했다. 제품 코드를 수정해 검사 결과를 맞추지 않았다. 신규 실험 파일의 lint·format은 별도로 확인했다.

## 실제 MCP 정의와 실제 모델 API

공개 서버 Filesystem·Memory·Sequential Thinking·Playwright·GitHub 기본/all·DeepWiki를 다시 연결했다. GitHub는 실제 자격증명 대신 placeholder를 사용했고 DeepWiki는 공개 원격 HTTP에 접속했다. 로컬 5종은 stdio, DeepWiki는 Streamable HTTP다. 실제 `initialize`·`tools/list` 왕복은 설정 7개 모두 완료했으며 SDK는 MCP 1.30.0, 협상 프로토콜은 2025-11-25였다.

총 189개 항목에서 GitHub 설정 간 중복을 제거하되 정의 해시가 다르면 따로 검사했다. 서버 종류와 도구 이름의 조합은 143개이고 `get_label`의 아이콘 차이까지 구분한 정의 변형은 **144개**였다. 실제 수집한 전체 Tool 객체를 현재 C 어댑터에 전달했다.

| 항목 | 관찰 결과 |
| --- | --- |
| 모델 | `openai/gpt-6-luna`, low, 요청 timeout 60초·출력 상한 4,096토큰 |
| 실제 OpenAI HTTP 요청 | **144건, 모두 HTTP 200**, 정의 변형마다 1건 |
| LLM 완료 / 실패 | **144 / 0** |
| 실제 Cisco YARA 규칙 검사 완료 | **144 / 144** |
| 실험 배치의 peak 분석 작업 | **LLM 4개, YARA 1개** |
| 배치 분석 구간 | **69.764초** |
| LLM 작업 시간 | p50 **1.811초**, p95 nearest-rank **3.220초**, 최대 **4.689초** |
| Finding이 있는 정의 변형 | LLM **5개**, YARA **1개**, 합집합 **6개** |
| MCP `tools/call` | 공개 서버 수집·경계 fixture 모두 **0회** |

실험 스케줄러가 Semaphore로 LLM 4개·YARA 1개를 제한했다. 제품 병행 runner를 구현한 것이 아니다. YARA는 `asyncio.to_thread`에서 실행했고 제품의 YARA 5초 제한은 적용하지 않았다. 600초 전체 deadline도 실험 배치에 강제 적용하지 않았다. 측정한 배치 구간은 metadata 수집·Tool 사본 준비·YARA 초기화를 제외하고 Luna 연결과 두 분석기의 실행을 포함한다. LLM 작업 시간은 Semaphore 대기 시간을 제외한 값이다.

따라서 이 표본에서 동시 작업 4개가 정상 완료된 근거는 생겼지만, 256개 도구의 완료 시간·모든 계정의 rate limit·제품 전역 동시성·600초 내 전체 등록 완료를 보장하지 않는다. 과거 합성 공격의 순차 측정과 입력·시점이 달라 속도 우위나 탐지율 비교로 해석하지 않는다.

| 분석기 | HIGH Finding이 나온 도구 | 분류 |
| --- | --- | --- |
| LLM | `sequentialthinking`, `list_pull_requests`, `assign_copilot_to_issue_with_intent`, `get_notification_details`, `list_notifications` | PROMPT INJECTION |
| YARA | `create_or_update_file` | PROMPT INJECTION |

공식 공개 서버의 정의에도 경고가 나왔다. 서버가 악성이라는 판정이나 오탐률은 산출하지 않았다. 독립 정답 리뷰와 실제 서버 행동 검증이 없으므로 여기서는 **검사기의 경고 관찰**로 기록한다. 두 분석기의 일치를 요구하지 않고 출처·원래 HIGH를 보존하는 기존 사용자 정책을 유지한다.

## 실제 stdio·HTTP 경계 관찰 22개

신규 [공격 fixture](../../../attack_fixtures/scanner_limits/server.py)는 synthetic metadata만 내보내며 관리 도구를 구현하지 않는다. 동일한 11개 시나리오를 실제 SDK stdio와 로컬 HTTP에서 각각 실행했다. Fixture가 송신한 JSON-RPC 메시지의 UTF-8 바이트를 별도 계수 파일에 기록했다. stdio newline·HTTP 프레이밍은 제외하며 응답 envelope·공백을 포함한다. 모든 `tools/call` 계수는 0이었다.

| 시나리오 | stdio·HTTP 공통 관찰 | 판단 |
| --- | --- | --- |
| 페이지 정확히 1MiB | 응답 1,048,576바이트 수신 | 경계 표본 확보 |
| 페이지 1MiB + 1바이트 | **1,048,577바이트도 SDK가 수신·파싱** | A의 파싱 전 수신 제한 필요 |
| 도구 257개 | SDK가 257개 반환, C 입력 준비도 257개 허용 | 도구 수 상한 미집행 |
| 페이지 33개 | SDK를 통해 33개 수집 | 페이지 수 상한 미집행 |
| 누적 8MiB 초과 | 9페이지·**9,432,685바이트** 수집 | 누적 수신 제한 미집행 |
| 같은 도구 이름 2개 | 두 항목 반환, 개별 입력 준비 허용 | 전체 목록 중복 검증 필요 |
| Tool 깊이 64 / 65 | 두 깊이 모두 SDK·C 입력 준비 허용 | 깊이 64 상한 미집행 |
| 반복 cursor | 두 응답 수신 후 **실험 코드가** 반복 감지·중단 | 제품 수집 경로의 보호 동작으로 보고하지 않음 |
| 페이지 응답 10초 초과 | 약 10.10초 뒤 응답 정상 반환 | 페이지 deadline 10초 미집행 |
| 초기화 10초 초과 | 약 10.10–10.14초 뒤 초기화 완료 | 초기화 deadline 10초 미집행 |

누적 초과 표본은 도구 144개이며 각 Tool 정의가 65,500바이트여서 **개별 64KiB 검사는 모두 통과**했다. 각 페이지도 1MiB 이하였지만 합계는 8MiB를 넘었다. 따라서 단일 Tool 제한이나 페이지 제한을 누적 제한 대신 사용할 수 없다는 실제 계수 근거다.

이 관찰은 현재 SDK와 C 개별 입력 경로의 동작이며 SDK 결함 판정이 아니다. A 제품 transport가 없는 상태에서 경계 fixture를 받은 결과다. 수신 중 중단·미완료 경고·부분 목록의 Registry 교체 방지는 아직 제품 경로에서 검증할 수 없다. HTTP SSE·압축 응답·메모리의 파싱 전 제한도 이번에 검증하지 않았다.

Fixture 첫 시도는 관찰 stream의 async context manager 누락으로 중단됐다. 관찰 코드를 수정하고 **모델 API 재호출 없이** fixture 22개를 다시 실행해 완료했다. 최초 실행에서 완료·저장한 실제 API 144건은 그대로 사용했다.

## YARA 시간 제한

| 실험 | 결과 | 의미 |
| --- | --- | --- |
| 실제 Cisco analyzer의 matcher에 80ms 블로킹 주입, 바깥 async timeout 10ms | **80.15ms 뒤 빈 Finding 정상 반환**, timeout 예외 없음 | sync matcher가 event loop를 막으면 async timeout만으로 제한할 수 없음 |
| 별도 native YARA의 고비용 synthetic 규칙에 `timeout=5` | **5.00005초**에 `yara.TimeoutError` | 설치된 YARA 4.5.4의 native timeout 기능 확인 |

Native timeout 실험은 [별도 프로세스 probe](../../../tests/experiments/yara_timeout_probe.py)로 실행했고 외부 프로세스 deadline 15초·kill 유예 2초를 두었다. Cisco 제품 matcher에 native timeout을 연결한 검증은 아니다. 실제 YARA 입력은 1,024바이트의 합성 문자열이며 도구 호출·파일 읽기·실제 인증정보 사용은 없었다.

현재 5초 제한은 제품에서 보장되지 않는다. 별도 worker에서 native timeout을 전달하고 timeout·SDK 오류를 미완료로 집계하는 구현이 필요하다. 주어진 시간 뒤에 생성된 빈 결과를 완료·안전으로 처리하지 않도록 검증해야 한다.

## 실행 방법

실험 조합은 Python 3.12.3·MCP 1.30.0·Cisco 4.8.6·OpenAI 2.54.0·yara-python 4.5.4다. 개발 도구는 앞선 스파이크 환경을 사용했다. 기존 B Ruff 설정 파일의 SHA-256은 근거 JSON에 기록했다.

```bash
PYTHONPATH=src LITELLM_LOCAL_MODEL_COST_MAP=True PYTHON_DOTENV_DISABLED=1 \
  /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python -m pytest tests -q

/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/ruff check \
  --config /tmp/mcp-guard-b-review-f3c2d2b/pyproject.toml \
  src tests attack_fixtures/scanner_limits/server.py

/tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/ruff format --check \
  --config /tmp/mcp-guard-b-review-f3c2d2b/pyproject.toml \
  src tests attack_fixtures/scanner_limits/server.py

MYPYPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/mypy \
  --strict --no-incremental --namespace-packages --explicit-package-bases src

# 공개 서버 재수집: 앞선 조사 환경의 검증된 배포물 사용, tools/call 없음.
PYTHONPATH=src .venv-scanner/bin/python \
  /tmp/mcp-guard-real-metadata-5K33Xa/measure.py

# 신규 출력 폴더 필요. --live는 사용자 .env로 실제 모델 요청을 실행한다.
PYTHONPATH=src:. .venv-scanner/bin/python \
  tests/experiments/scanner_operating_probe.py \
  --output /tmp/mcp-guard-operating-verify-xc8NCX/run \
  --metadata-dir /tmp/mcp-guard-real-metadata-5K33Xa --live

# API를 재호출하지 않는 stdio·HTTP 경계 관찰.
PYTHONPATH=src:. .venv-scanner/bin/python \
  tests/experiments/scanner_operating_probe.py \
  --output /tmp/mcp-guard-operating-verify-xc8NCX/fixtures

timeout --kill-after=2s 15s .venv-scanner/bin/python \
  tests/experiments/yara_timeout_probe.py \
  --output /tmp/mcp-guard-operating-verify-xc8NCX/native-yara-timeout.json
```

임시 경로는 이번 실행 기록이며 재실행하려면 동일 배포물·환경을 준비하고 새 출력 폴더를 지정한다. CLI는 기존 출력 폴더에 덮어쓰지 않는다. Raw 공개 정의는 임시 파일에서만 읽으며 저장소에는 집계·해시만 보관한다.

## 남은 인수

1. A: 페이지·누적량·도구 수·페이지 수·초기화/수집 deadline을 실제 수신 경로에 구현한다. C가 같은 fixture로 파싱 전 중단·계수·부분 목록의 비공개/미완료를 확인한다.
2. C: 제품 LLM/YARA 병행 runner, global 동시성·대기열·취소·600초 deadline·YARA native timeout·부분 실패 집계를 구현한다. B ScanReport 제외를 유지한다.
3. A·C: 위험·미완료 경고 UI·중복 표시·진행 표시를 연결한다. 캐시·주기적 재검사·수동 재검사·정의 변경 결과 무효화는 별도 검증한다.
4. B 계약 검토와 A 제품 연결 후 Registry·Store·Guard·관리 도구 실제 dispatch/wire/수신 부작용을 인수한다.

관련 I1의 metadata-only 범위 준수, I9·I10 및 T17·T19·S04의 입력·SDK 관찰 부분이다. T10의 Registry/승인 무효화와 Guard 전체 불변식·Bandit/Semgrep/pip-audit는 이번에 검증하지 않았다. C 작성 fixture·실험 코드는 B 리뷰가 남아 있다. 새 추천값은 이 실험 결과로 자동 확정하지 않는다.
