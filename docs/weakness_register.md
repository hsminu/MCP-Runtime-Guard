# 보안약점 관리대장

담당 C, 리뷰 B. [개발문서](technical-design.md) 13.5절 기준이다. C의 Scanner 입력·Luna 연결·LLM/YARA 내부 집계·키 로딩·SDK 로그 제한 부분은 구현·검증했고, 제품 대상 Bandit·Semgrep 등의 보안 스캔은 **미실행**이다. B 코어 검토·대역 회귀와 Cisco Scanner의 합성 metadata 검사·결함 주입·실제 모델 API 검증은 아래에 기록하며 실제 SQLite·MCP 집행 검증과 구분한다. 진단 항목이 없다는 사실은 약점이 없다는 뜻이 아니다.

## 검사 실행 기록

| 실행일 | 검사 도구·버전 | 대상 커밋 / 범위 | 결과·산출물 | 담당 / 리뷰 |
| --- | --- | --- | --- | --- |
| 2026-10-13 | C 내부 집계·Cisco 4.8.6·OpenAI 2.54.0 HTTP 대역·native YARA 4.5.4·pytest/Ruff/mypy | C 미커밋 작업 트리, 입력 사본·독립 실패·부분 finding·JSON·이전 실제 수집 정의 144개 | 회귀 96개·20 subtest·Ruff/strict mypy 통과. HTTP 대역 147건·외부 socket 시도 0, 실제 모델 API 0. 실제 YARA HIGH 1건 보존. [구현/검증](tasks/c-cisco-internal-aggregation.md)·[근거](evidence/cisco-internal-aggregation-2026-10-13.json) | C / B 리뷰·전역 예산·native timeout·Registry/Guard 인수 미완료 |
| 2026-10-13 | MCP SDK 1.30.0 실제 stdio/HTTP·Cisco 4.8.6·Luna low·YARA 4.5.4, 회귀·경계·timeout 실험 | C 현재 제품 소스·공개 MCP 정의 144변형·신규 synthetic 경계 fixture | 회귀 62개·20 subtest·Ruff/strict mypy 통과. 실제 API 144건 HTTP 200·YARA 144개 완료. stdio/HTTP 관찰 22개로 수신 상한·개수·깊이 미집행 확인, async timeout 무효·native 5초 확인. [검증 보고](tasks/c-scanner-operating-validation.md) | C / 실험이며 제품 병행 runner·A 수신 제한·Registry·Guard·B 리뷰 미완료 |
| 2026-10-12 | 코드 검토·기존 테스트 대역을 사용한 결함 재현 / Python 3.12.3 | B `0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc`, 승인 Task 조회·상태 변경 경로 | C-B-01 재현. [실행·관측 근거](tasks/c-b-review.md). 자동 스캔·실제 SQLite·wire 검증 미실행 | C / B 리뷰 미완료 |
| 2026-10-12 | 수정 코드 검토·pytest/Ruff/mypy 재실행 / Python 3.12.3 | B `f3c2d2bc225e0406c50731c9b63779a3d890cd70`, 승인 식별자 수정과 대역 회귀 | 864개 테스트·Ruff·포맷·mypy 통과. C-B-01 대역 회귀 확인. [후속 근거](tasks/c-b-review.md#후속-수정-f3c2d2b-확인). 자동 스캔·실제 SQLite·wire 검증 미실행 | C / 실제 SQLite 인수 미완료 |
| 2026-10-12 | Cisco MCP Scanner 4.8.6 설치 코드 조사·SDK/CLI 오프라인 실행·matcher 결함 주입 / Python 3.12.3 | 공식 commit `5d147dc7e3635606e41104112fa297b45444b05c`와 동일한 wheel, 합성 도구 metadata | C-CISCO-01·02 재현. SDK 1.30.0·B lock 33개 일치, pip check·B 864개 테스트 통과. [보고·관측 자료](tasks/c-cisco-scanner-spike.md) | C / B 리뷰·제품 어댑터·Registry 인수 미완료 |
| 2026-10-13 | Cisco Scanner 4.8.6·LiteLLM 1.93.2·OpenAI SDK 2.54.0 실제 LLM 호출 / Python 3.12.3 | `gpt-5.4-mini-2026-03-17`, 정상·악성 합성 metadata 각 1건 | 실제 HTTP 200 두 건·JSON 파싱 성공, 정상 0건·악성 HIGH 2건. 키 참조 수정, 원문 로그 억제. [보고](tasks/c-openai-scanner-setup.md)·[관측 자료](evidence/openai-scanner-live-2026-10-13.json). I6·I9·I10, 후속 T17·T19·S04 전체 통과는 아님 | C / 통계 평가·Guard·Registry 인수 미완료. Luna 소규모 비교는 아래 후속 기록 |
| 2026-10-13 | 같은 Scanner·LiteLLM·OpenAI SDK의 Luna 원형·요청 보정 비교 | `gpt-6-luna`, 같은 정상·악성 합성 metadata | 원형은 `max_tokens` HTTP 400·분석 미완료. 임시 보정 후 HTTP 200 두 건·정상 0건·악성 HIGH 2건. [보고](tasks/c-openai-scanner-setup.md)·[관측 자료](evidence/openai-scanner-luna-live-2026-10-13.json). I6·I9·I10, 후속 T17·T19·S04 전체 통과는 아님 | C / 제품 요청 보정·통계 평가·Guard·Registry 인수 미완료 |
| 2026-10-13 | Scanner 4.8.6·LiteLLM 1.93.2·Luna none 확장 평가 / 실제 API 59건 | 정상 12개·공격 16개, 기존 추출·표적 보정·전체 metadata 경로 | C-CISCO-03: root schema·annotations·중첩 schema의 공격 3개가 모델 입력에서 빠짐. 전체 metadata 임시 경로는 같은 28개에서 공격 16/16·정상 오탐 0/12. [보고](tasks/c-luna-scanner-evaluation.md)·[집계](evidence/luna-expanded-2026-10-13-summary.json) | C / 정답 독립 리뷰·제품 보정·Guard·Registry 인수 미완료 |
| 2026-10-13 | 사용자 선택 Luna low 설정 확인 / 실제 API 두 건 | 변경된 `.env`의 low·전체 metadata·요청 보정 임시 코드 | HTTP 200 두 건, 정상 0건·악성 HIGH 2건. 추론 토큰 22·225. [보고](tasks/c-openai-scanner-setup.md)·[근거](evidence/openai-scanner-luna-low-2026-10-13.json). none 확장 결과와 구분 | C / low 전체 회귀·제품 어댑터·실제 MCP 인수 미완료 |
| 2026-10-13 | 제품 Cisco metadata 입력 구현 / 단위·설치 SDK 회귀·Ruff·strict mypy | 전체 정의·추가 필드·UTF-8 경계·실제 SDK 프롬프트·실제 YARA·matcher 결함 | 23개 테스트·20개 subtest 통과. C-CISCO-03 입력 부분 제품 반영, B 실제 FrozenJsonObject 호환. SDK LLM completion 대역·socket 연결 시도 0. [구현·검증 기록](tasks/c-cisco-metadata-implementation.md) | C / B 리뷰·지문·ScanReport·Registry·실제 MCP 인수 미완료 |
| 2026-10-13 | 제품 Luna low 연결 / HTTP 대역·실제 API 28건·import 검증·Ruff·strict mypy | 기존 정상 12개·공격 16개, C 전체 정의 입력과 인스턴스 요청 연결 | 실제 API 모두 HTTP 200·전체 정의 전달, 공격 16/16·정상 오탐 B06 1/12. 오프라인 62개·20개 subtest 통과. SDK 자동 dotenv 로딩 차단·키 환경 보존 확인. [보고](tasks/c-cisco-luna-connection.md)·[근거](evidence/luna-low-product-2026-10-13.json) | C / 사용자 지시로 B ScanReport 미연결. Registry·Guard·실제 MCP·B 리뷰 미완료 |

정상 fixture와 제품 소스를 포함한 범위, 의도적 악성 `attack_fixtures/`의 제외 범위를 기록한다. 실행하지 않은 도구의 결과를 채우지 않는다.

## 발견 항목과 조치

| ID | 발견일 / 도구 / 규칙 | KISA 분류 | 위치 | 심각도 | 조치 내용 | 재발 방지 모듈·규칙·테스트 | 재검사 근거 | 상태 / 리뷰 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C-B-01 | 2026-10-12 / 코드 검토·대역 오반환 재현 / Task ID 검증 | 입력데이터 검증 및 표현·시간 및 상태(C 분류) | 최초 B `approvals.py:283`, `0c02b61`; 수정 `f3c2d2b` | 중간(C 판정: 다른 Task 정지, 전송 우회는 관찰하지 않음) | B가 snapshot Task ID·epoch의 선행 검증과 정지 경로의 기대 epoch 검사를 추가. 불일치 시 global halt | 관련 I6·I7 및 T09–T12·T20. B가 회귀 24개 추가, C가 재실행 | 수정 커밋 전체 864개 테스트·Ruff·포맷·mypy 통과. 대역 Task 변경·소비·transport 호출 0 확인 | 대역 재검사 확인 / 실제 SQLite 결함 검증·인수 미완료 |
| C-CISCO-01 | 2026-10-12 / SDK·CLI 실행·matcher 실패 주입 / 필수 분석 완료 확인 | 오류 처리·시간 및 상태(C 분류) | 외부 4.8.6 StaticAnalyzer._analyze_content·scan_tools_file, CLI static | 높음(C 판정: 실패를 완료로 오인 가능, 실제 Guard 연결 미실행) | matcher 전체/부분 실패에도 completed·safe처럼 표시되는 upstream 경로 대신 개별 공개 분석기와 C 내부 완료/실패 집계를 구현 | I6·I10, T17의 전체/부분 실패·미실행·준비 오류, 실제 Registry 의미는 사용자 경고 정책에 따라 별도 인수 | [최초 재현](tasks/c-cisco-scanner-spike.md#5-실제-실행-결과와-실패-의미), [내부 집계 회귀](tasks/c-cisco-internal-aggregation.md): native 오류/timeout·HTTP 오류·미실행을 완료와 구분 | 내부 대응·회귀 확인 / B 리뷰·native 시간 제한·Registry 인수 미완료 |
| C-CISCO-02 | 2026-10-12 / 예외 표지 주입·stdout 관측 / 예외 원문 출력 | 오류 처리·정보 노출(C 분류) | 외부 4.8.6 StaticAnalyzer._analyze_content의 경고 print, CLI raw stdout | 중간(C 판정: 합성 예외 표지 노출, 실제 인증정보는 사용하지 않음) | CLI/Static 경로를 사용하지 않고 C 요청/집계의 고정 오류·허용 scalar finding·repr 제한을 구현 | I9, T19 입력/SDK 로그 회귀. S04 전체 규칙·실제 UI는 별도 인수 | [내부 집계 회귀](tasks/c-cisco-internal-aggregation.md)에서 오류·키·정의 표지의 로그/JSON 비노출, SDK evidence/raw_response 미복사 확인 | 내부 대응·회귀 확인 / B 리뷰·UI/Guard 경로 인수 미완료 |
| C-CISCO-03 | 2026-10-13 / Luna 합성 28개·표적 3개·전체 경로 28개 / metadata 필드 전달 | 입력데이터 검증 및 표현·보안 기능(C 분류) | 외부 4.8.6 LLMAnalyzer._parse_tool_content·_create_threat_analysis_prompt를 C에 적용할 때의 검사 범위 | 높음(C 판정: 일부 정의를 검사하지 않고 findings 0개를 얻을 수 있음. 실제 Guard 우회는 관찰하지 않음) | A14 root schema description·A15 annotations title·A16 중첩 description의 누락을 전체 정의 description 봉투로 보정. C 입력·내부 집계에서 같은 사본 전달 | I6·I10 및 T17, 입력 범위·크기·완료 조건 회귀. 지문·Registry는 별도 계약. 원문 로그 제한은 I9·T19·S04 | [기존 API 평가](tasks/c-luna-scanner-evaluation.md)는 공격 16/16·정상 오탐 0/12. [내부 집계](tasks/c-cisco-internal-aggregation.md)는 회귀 96개·20 subtest, 실제 SDK의 전체 입력과 native YARA·144개 공개 정의 처리 확인 | 입력·집계 제품 반영·회귀 확인 / B 리뷰·지문·Registry/Guard 연결·인수 미완료 |
| C-CISCO-04 | 2026-10-13 / 제품 Luna low 실제 API / 정상 정의 오탐 | 보안 기능(C 분류: 정상 사용 과잉 제한 가능성) | B06 교육용 공격 문구 인용 정의 | 낮음(C 판정: 정상 1/12 HIGH. Registry 자동 제한은 미연결) | 교육용으로 인용한 공격 문구가 HIGH로 탐지됨. 결과를 변경·재시도하지 않고 기록 | 후속 T17의 정상 대조군·반복 평가·오탐 처리 정책 검토 | [제품 low 28개 평가](tasks/c-cisco-luna-connection.md)·[실행 근거](evidence/luna-low-product-2026-10-13.json). 각 사례 1회 실행이며 none 대비 차이의 원인은 미확정 | 관찰 / 원인 분석·반복 평가·등록 정책·B 리뷰 미완료 |

상태는 발견·조치 중·재검사 완료 등 실제 진행을 표시한다. 코드 위치와 커밋, 관련 I/T/S ID, 재현 가능한 검사 근거를 남긴다. 민감한 본문·토큰·전체 인자·예외 메시지는 기록하지 않는다.

## 규칙 예외 기록

| ID | 규칙 / 위치 / 커밋 | 예외 사유와 범위 | 대체 통제 / 검증 | 리뷰어 승인 근거 | 재검토 조건 |
| --- | --- | --- | --- | --- | --- |

제품 코드의 인라인 `# nosemgrep: <rule-id>` 예외는 사유와 리뷰어 1인 승인을 기록한다. 설계에서 허용한 launcher·storage·failclosed·transport의 경계와 임의 규칙 해제를 구분한다. 이미 있는 합의·승인 근거가 적용되면 반복해서 확인하지 않는다.
