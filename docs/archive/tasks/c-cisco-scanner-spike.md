# C · Cisco MCP Scanner 4.8.6 확인 보고

확인일은 2026-10-12 UTC다. 범위는 C1.2·C1.4의 외부 Scanner 조사·설치·API/CLI 실행과 합성 metadata 결함 주입이다. 관련 I6·I7·I9·I10, T17 및 후속 T10·T19다. 제품 어댑터·Registry·SQLite·MCP 서버 연결·실제 dispatch/wire/부작용 검증은 미실행이다.

## 1. 결론과 권장 경로

**metadata의 오프라인 검사는 가능하지만 StaticAnalyzer/CLI의 완료·안전 표시를 등록 허용으로 직접 사용하면 안 된다.** 분석기 전체 실패·부분 실패가 완료·안전으로 표시되는 동작을 실제 재현했다. 권장은 공식 SDK의 개별 분석기를 호출하고 C 어댑터가 필수 목록·도구별 실행 완료·실패·생략을 직접 집계하는 방식이다.

필수 오프라인 검사 후보는 YARA다. Readiness·Prompt Defense는 운영 준비·방어 문구의 존재를 검사하며 정상형 합성 metadata에도 HIGH를 반환했다. 필수 등록 조건에 합칠지는 별도 검토한다. YARA가 탐지한 양성 대조군과 놓친 공격 문구를 모두 기록하며, 탐지 0을 전체 공격 차단의 증거로 사용하지 않는다.

분석기별 대상과 YARA 10개·Readiness 20개·Prompt Defense 12개의 세부 분류는 [검사 항목 정리](c-cisco-scan-items.md)를 참고한다. 이 목록은 코드로 확인한 기능과 실제 실행한 범위를 구분한다.

Store·Scanner 공통 API의 실제 합의와 제품 구현은 미완료다. 이 보고는 조사 결과와 C 권장안을 기록한다.

## 2. 버전과 환경 검증

| 항목 | 실제 확인 |
| --- | --- |
| 공식 배포 | [cisco-ai-mcp-scanner 4.8.6](https://pypi.org/project/cisco-ai-mcp-scanner/4.8.6/), PyPI wheel 업로드 2026-10-07 |
| 공식 소스 | v4.8.6의 commit `5d147dc7e3635606e41104112fa297b45444b05c`. 검사한 소스 9개가 wheel과 바이트 단위 일치 |
| wheel SHA-256 | `1cddf00dc4c7ee59cb567f0268e3fd131691506ae3557b9031bafd9d60d152b3` |
| Python | 배포 요구 `>=3.11.4`, 실제 Ubuntu 24.04.4 x86_64·Python 3.12.3 |
| MCP SDK | 배포 요구 `mcp[cli]>=1.25.0`. 상한 없이 설치하면 2.3.0 선택. 프로젝트 1.x 기준으로 1.30.0을 별도 고정해 새 환경 검증 |
| 함께 확인한 주요 버전 | Pydantic 2.14.0, yara-python 4.5.4, LiteLLM 1.93.2 |
| B 호환 범위 | B f3c2d2b의 lock 33개 버전 일치, pip check 통과, 같은 환경에서 B 단위·성질 864개 통과(2.63s) |
| 설치 환경 | 임시 `venv-1x`에 총 118개 패키지. C 작업 디렉터리의 `.venv`는 pip만 설치된 상태 유지 |

같은 환경에서 설치·B 단위 테스트가 가능하다는 확인이다. A의 AGT·실제 stdio/HTTP 왕복·GUI 조합은 검증하지 않았으므로 프로젝트 공통 lock은 확정하지 않는다. 별도 프로세스 적용과 입력 크기·실행 시간·취소 제한도 후속 검증 대상이다.

실험용 [전체 freeze](../evidence/cisco-scanner-4.8.6-spike-freeze.txt)와 [요약 관측 JSON](../evidence/cisco-scanner-4.8.6-spike.json)을 남겼다. freeze는 실험 재현 자료이며 제품 의존성 lock이 아니다.

## 3. 실제 API와 CLI

설치된 코드의 signature를 inspect로 확인하고 실행했다.

| 입구 | 입력·반환 | 확인과 적용 |
| --- | --- | --- |
| `YaraAnalyzer.analyze` | content 문자열·선택 context → await 후 SecurityFinding 목록 | metadata 전체를 JSON 문자열로 전달하는 오프라인 SDK 경로 실행 |
| `ReadinessAnalyzer.analyze` | metadata JSON·tool_definition context → Finding 목록 | OPA·LLM judge를 명시적으로 꺼서 실행 |
| `PromptDefenseAnalyzer.analyze` | 설명 문자열·선택 context → Finding 목록 | 오프라인 실행. 방어 문구 부족을 찾는 분석기 |
| `StaticAnalyzer.scan_tools_file` | 파일 경로 → 도구별 dict 목록 | 공개 API지만 분석 실패 완료 오판이 있어 운영 판정 근거로 직접 사용하지 않음 |
| CLI `static --tools` | tools/list 형태의 JSON 파일 | YARA-only 실행·raw stdout 확인. 라이브러리 StaticAnalyzer의 문제를 공유 |

도구 파일 형식은 최상위 tools 배열에 name·description·inputSchema·annotations metadata를 넣는 형태다. 업무 본문·도구 실행 인자·인증정보는 입력하지 않았다. 원격 서버·stdio·resources/read·프로토콜 probe·패키지 다운로드 스캔 모드는 사용하지 않았다.

README의 `Scanner.analyze` 예시는 4.8.6 실제 클래스에 해당 메서드가 없다. private `_analyze_tool`을 제품 인터페이스로 채택하지 않는다. CLI console entry point는 `mcpscanner.cli:cli_entry_point`다. `--format raw`는 stdout JSON을 반환했고 이번 static 경로의 `--output` 파일은 생성되지 않았다.

근거는 고정 커밋의 [StaticAnalyzer](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers/static_analyzer.py), [CLI](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/cli.py), [Scanner](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/scanner.py)다.

## 4. 오프라인 분석기와 네트워크 범위

| 분석기 | 확인한 동작 | C 권장 |
| --- | --- | --- |
| YARA | 동봉 규칙 10개 파일을 로컬 컴파일·실행, 키 불필요 | 필수 후보. 버전·규칙 묶음과 완료 여부를 기록 |
| Readiness | 기본 휴리스틱은 로컬. 선택 OPA·LLM judge 경로 존재 | 우선 선택 항목. 실행하면 OPA·LLM 옵션을 명시 |
| Prompt Defense | 로컬 정규식으로 방어 문구 점검 | 우선 선택 항목. YARA의 악성 패턴 탐지와 결과 목적을 구분 |
| API·LLM·Behavioral·Meta·VirusTotal | 외부 서비스/모델 경로 또는 별도 스캔 대상이 필요함 | 이번 필수 오프라인 metadata 목록에서 제외. 실제 외부 분석은 미실행 |

YARA 규칙 묶음의 실험 지문은 `d61cad81a61246cbe13aefb9163e52b4de734d7b5b8156771da0fdd0118fb6bf`다. 정렬한 파일 이름·NUL·원문 바이트·NUL 순서로 SHA-256을 계산했다. 도구 지문과 규칙 묶음 지문은 별개다.

기본 mcpscanner import는 LiteLLM의 외부 모델 가격표 조회를 1회 시도했다. 이 실험에서는 Python socket 연결을 차단했으므로 해당 조회는 실패하고 로컬 자료로 대체됐다. `LITELLM_LOCAL_MODEL_COST_MAP=True` 적용 후 import·SDK·CLI 실험의 연결 시도는 각각 0이었다. `.env` 자동 로딩도 비활성화하고 CLI 자식 프로세스에는 최소 환경만 전달했다.

관측은 Python socket의 connect/connect_ex/create_connection 차단·계수다. 패킷 캡처나 네이티브/다른 프로세스의 모든 네트워크 경로를 증명한 것은 아니다. API 키를 사용한 외부 서비스는 호출하지 않았다.

## 5. 실제 실행 결과와 실패 의미

여기서 matcher 실패 주입은 실제 YARA 네이티브 엔진의 고장·자원 고갈을 발생시킨 실험이 아니다. 실제 YaraAnalyzer 인스턴스의 `_rules`를 테스트용 BrokenRules로 교체해 `match` 호출이 RuntimeError를 내도록 했다. 전체 실패는 모든 호출이 예외를 내고, 부분 실패는 첫 호출이 빈 매칭 목록을 반환한 다음 두 번째 호출이 예외를 낸다. CLI도 임시 자식 프로세스에서 같은 매칭 대역을 사용했다. 설치된 패키지 파일은 수정하지 않았으며 실제 오류 발생률·네이티브 메모리 부족·timeout·프로세스 중단은 검증하지 않았다. 이 실험은 매칭 호출이 예외를 낼 때 실제 YaraAnalyzer·StaticAnalyzer·CLI의 예외 처리와 반환 상태를 확인한다.

| 실행 케이스 | 관측 결과 | 적용할 해석 |
| --- | --- | --- |
| 정상형 합성 metadata, YARA | 0건, 정적 SDK/CLI completed·safe | 해당 규칙 실행 결과이며 자동 ACTIVE 근거가 아님 |
| YARA 규칙에 맞춘 양성 대조군 | SDK·CLI HIGH 1건. CLI 종료 코드 0 | 종료 코드 0을 안전으로 해석하면 안 됨 |
| 별도 합성 명령 무시·비밀 전송 문구 | YARA 탐지 0, CLI safe | 이번 문구의 탐지 누락. 규칙 검사를 전체 공격 방어로 보고하지 않음 |
| 실제 YARA matcher가 항상 RuntimeError 발생 | 정적 SDK completed·safe·YARA 완료 표시, errors 필드 없음 | C-CISCO-01. 필수 실패를 어댑터가 직접 집계해야 함 |
| 설명 매칭은 빈 목록 반환으로 성공을 모의하고 parameters 매칭은 실패 주입 | 동일하게 completed·safe | 부분 성공만으로 필수 분석기 전체 완료를 기록하면 안 됨 |
| StaticAnalyzer 분석기 0개 | completed·safe·analyzers 빈 목록 | 구성/필수 목록 확인 필요 |
| CLI yara,llm, LLM 키 없음 | 경고 후 종료 0·completed·safe, YARA만 결과에 존재 | 요청 목록은 실행 완료 목록이 아님 |
| CLI yara,readiness | 종료 0·completed·safe, YARA만 결과에 존재 | static CLI는 Readiness를 실행하지 않음. Readiness만 선택하면 종료 1 |
| 분석기 예외 메시지에 합성 표지 삽입 | 표지가 SDK/CLI stdout 경고에 그대로 나타남 | C-CISCO-02. 예외 원문을 로그·UI에 전달하지 않음 |
| CLI YARA 실패 | 경고 뒤 완료·safe JSON, 종료 0. stdout 전체는 유효 JSON이 아님 | 엄격 파싱은 실패로 처리. 경고를 제거하고 안전 결과로 수용하지 않음 |
| 잘못된 JSON·tools 누락·tools 타입 오류 | JSONDecodeError 또는 ValueError | 고정 실패 결과로 변환, PENDING 유지 |
| name 누락 | unknown으로 보충하고 완료·safe 반환 | 호출 전 metadata 구조를 엄격 검증하고 기본 이름으로 통과시키지 않음 |
| 정상형 metadata, Readiness/Prompt Defense | 각각 9건/12건, HIGH 포함 | 운영 준비·방어 문구의 부족 판단. 필수 등록 HIGH 조건에 합칠지 검토 |
| SecurityFinding에 CRITICAL 입력 | UNKNOWN으로 정규화 | UNKNOWN·미지원 severity를 안전값으로 내리지 않고 불완전/형식 오류 처리 |

StaticAnalyzer는 분석 예외를 경고 출력으로 처리하면서 도구 결과를 completed로 고정하고 구성된 분석기를 완료처럼 기록한다. CLI raw 결과는 analyzer별 findings 객체로 구성돼 B의 ScanReport·Finding과 직접 호환되지 않는다. 실패 시에는 예외 원문 출력 때문에 raw JSON 파싱도 깨졌다. 위 두 조치는 [약점 관리대장](../../weakness_register.md)에 기록한다.

## 6. B 계약과 C 어댑터에 필요한 후속 작업

- B의 입력 `tuple[FrozenJsonObject, ...]`에서 도구 정의만 엄격 검증·직렬화한다. name·description·inputSchema·annotations와 입력 크기·타입 허용 범위를 정한다.
- C가 정한 필수 목록과 실제 실행 목록을 도구별로 대조한다. 모든 검사 대상과 필요한 내용의 분석이 완료된 경우에만 completed로 집계한다. 생략·예외·시간초과·형식 불명을 실패로 구분한다.
- SDK의 mutable Finding·raw severity·analyzer 이름을 B의 불변 ScanReport·Finding으로 번역한다. 허용된 code·등급·지문·UTC 시각·버전만 기록하고 원문 description·evidence·예외 메시지를 기본 로그에 내보내지 않는다.
- 도구 지문·패키지/규칙 버전·필수 분석기 설정을 신뢰 등록 맥락에 연결한다. B digest의 schema 숫자 처리와 Registry의 지문 변경·승인 무효화/영속화 계약을 검토한다.
- Registry는 필수 검사 실패·생략·형식 불명에서 PENDING을 유지한다. HIGH 이상은 관리자 검토 대상으로 두고, 완전한 검사 뒤에도 관리자 승인 없이 ACTIVE로 만들지 않는다.
- T17의 필수 분석기 실패·부분 완료·생략·빈 구성·잘못된 metadata/결과 케이스를 제품 어댑터와 Registry에 적용한다. T10의 지문 변경·승인 무효화와 T19의 원문 노출은 실제 연결 후 검증한다.

라이브러리의 자체 결과만으로 실패 목록을 복원하기 어려우므로 개별 공개 분석기 API와 C의 실행 집계를 권장한다. 실제 래퍼 구현·테스트는 후속 작업이다.

## 7. 재현 자료와 실행 범위

임시 작업 경로는 `/tmp/mcp-guard-cisco-4.8.6-96st8lp7`다. `wheel_source/`, `venv-1x/`, metadata 파일·결함 주입 probe.py·cli_probe.py·설치 report·요약 결과를 두었다. 제품 소스·테스트/fixture·CI 파일은 추가하지 않았다.

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True PYTHON_DOTENV_DISABLED=1 \
  /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python \
  /tmp/mcp-guard-cisco-4.8.6-96st8lp7/probe.py

PYTHONPATH=src /tmp/mcp-guard-cisco-4.8.6-96st8lp7/venv-1x/bin/python \
  -m pytest tests/unit -q
```

두 번째 명령의 cwd는 B f3c2d2b 검토 worktree다. 최초 설치는 B requirements.lock과 공식 wheel을 함께 사용했고, MCP 1.30.0을 명시한 새 환경에서 최종 검증했다. `/tmp` 자료가 없어지면 문서의 고정 버전·공식 wheel hash와 실험 freeze를 기준으로 재구성한다.

이 보고는 실제 실행한 스파이크 기록이다. Scanner 전체 공식 테스트·제품 계약/통합·성능·지원 플랫폼 전반·AGT·실제 MCP 왕복·보안 스캔 전체 통과는 보고하지 않는다.
