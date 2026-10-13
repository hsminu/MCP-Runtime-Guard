# C · Cisco Scanner 남은 작업

2026-10-13 사용자가 Scanner LLM을 **Luna low**로 선택했다. `.env`·공유 템플릿과 설정 문서에 반영했고, 임시 요청 보정 코드의 실제 API 두 건은 성공했다. [현재 모델 설정](c-openai-scanner-setup.md), [none 확장 평가](c-luna-scanner-evaluation.md)를 따른다. 모델 선택은 제품 Scanner·Registry 구현 완료나 API 계약 합의를 뜻하지 않는다.

같은 날 사용자의 후속 구현 지시로 [전체 도구 정의 입력·분석기 호출 부분](c-cisco-metadata-implementation.md)을 제품 소스에 반영하고 23개 테스트를 통과했다. 당시 모델 요청 보정·ScanReport 집계·Registry는 남아 있었다.

후속 "연결해 b scanreport는 연결하지마" 지시로 [Luna low 제품 연결](c-cisco-luna-connection.md)을 구현했다. 현재 62개 오프라인 테스트·정적 검사와 low 실제 API 28건을 확인했다. 공격 16/16 탐지·정상 오탐 1/12다. **현재 작업에서 B ScanReport 연결은 제외하며**, Registry·Guard·실제 MCP 연결도 남아 있다.

## 권장 순서

| 순서 | 작업 | 완료 조건·의존성 |
| --- | --- | --- |
| 1 | C1.2 · Scanner·Registry 계약 초안 | 전체 metadata의 범위·크기 제한, YARA 필수/외부 LLM 선택 규칙, 완료·실패·생략·UNKNOWN의 의미, 지문 집합·관리자 활성화를 구체화. B의 ScanReport·저장소 초안과 함께 A·B·C가 검토 |
| 2 | C4 · 실제 Cisco 어댑터 | 전체 정의 입력·Luna low 설정/요청 연결 완료. 도구별·분석기별 집계는 남아 있으며 현재 사용자 지시로 B ScanReport 번역·연결은 제외. StaticAnalyzer/CLI의 safe·종료 코드에만 의존하지 않음 |
| 3 | C5/C4 · 실제 MCP metadata 왕복 | 정상/악성 합성 MCP를 stdio·HTTP로 띄우고 실제 `tools/list` 정의를 Scanner에 전달. 현재 수작업 JSON 시험과 구분하며 A의 transport·공통 SDK 및 fixture 리뷰와 맞춤 |
| 4 | T17·T19·S04 · 회귀·결함 주입 | low 28개 실제 회귀·연결 오류 HTTP 대역 확인 완료. 전체/부분 분석 실패·skip과 실제 Registry/로그 경로 인수는 남음. 실패를 findings 0개나 완료로 오인하지 않음 |
| 5 | C4 · Registry·저장소 연결 | 신규 PENDING, 필수 검사 완료 후 관리자 확인, 정의 지문 변경 시 PENDING·승인 무효화. B의 저장소·digest와 A의 변경 알림에 연결 |
| 6 | C6 · Guard 통합 인수 | 실제 도구 호출은 Guard를 거치고 승인·dispatch·wire·수신 부작용 계수로 검증. A transport와 B Guard·SQLite 연결 후 착수 |

1·2는 다음 권장 작업이다. 3의 metadata 왕복은 도구 실행 검증과 구분한다. 현재 관리 대상 `tools/call`을 Guard 밖에서 실행해서 검증을 대신하지 않는다. 필수 분석기·실패 시 등록 정책·새 API의 미확정 항목을 모델 선택 결정으로 확정하지 않는다.

## 어댑터에 반영할 확인된 사항

- `MCP_SCANNER_LLM_REASONING_EFFORT`는 프로젝트 변수다. Scanner 4.8.6 CLI의 기본 설정 로더는 읽지 않는다. C 제품 연결은 설정을 읽고 직접 low를 전달한다.
- C 제품 Luna 요청은 `max_completion_tokens`를 사용하고 temperature를 생략한다. 기존 이름만 교체하는 upstream 경로는 실제 HTTP 400을 재현했다.
- C-CISCO-01: 분석 실패를 완료·safe로 표시하는 정적 검사 경로를 피하고 C가 실행 결과를 직접 집계한다.
- C-CISCO-02: 원문 예외·Finding의 raw_response/evidence·인증정보를 기본 로그/화면으로 전달하지 않는다.
- C-CISCO-03: 루트 schema·annotations·중첩 인자·추가 정의 필드를 보존하는 입력 어댑터를 제품 소스에 반영했다. 설치 SDK의 프롬프트·실제 YARA 규칙을 회귀 검증했다. B 리뷰·지문 계약·실제 MCP/Registry 연결은 남아 있다.
- JSON 객체가 파싱된 것과 필수 결과 스키마가 유효한 것을 구분한다. 필수 필드 누락·잘못된 severity·도구 지문 불일치는 완료로 처리하지 않는다.

관련 불변식은 I6·I7·I9·I10, 등록·검사 인수는 T17·T10이며 원문 비노출은 T19·S04와 연결한다. 실제 tools/call 인수는 I1–I3·I8 및 C6 계수 검증으로 연결한다. 이 목록은 아직 테스트 전체 통과 기록이 아니다.

## 추가 분석기의 순서

현재 등록 보안 검사 후보는 오프라인 YARA와 선택 LLM이다. 정확한 필수 목록·외부 실패 처리·Registry 판정은 1에서 확정한다.

Readiness·Prompt Defense는 운영 준비·방어 문구를 보는 보조 검사로 후순위에 둔다. 정상 정의에도 HIGH를 반환한 사례가 있어 보안 finding과 같은 활성화 기준을 무조건 적용하지 않는다. Behavioral은 MCP 소스가 있을 때, Vulnerable Package는 의존성 검사가 필요할 때 검토한다. Cisco API·VirusTotal은 별도 키와 외부 호출이 필요하다. Meta는 finding을 제거하는 기능이므로 필수 검사 결과를 완화하는 용도로 기본 활성화하지 않는다. 항목별 범위는 [검사 항목](c-cisco-scan-items.md)을 따른다.

모델 추가 비교는 현재 필수 선행 작업이 아니다. 남은 핵심은 실제 어댑터·MCP 정의 수집·실패 의미·등록과 집행 연결이며, 정확도 우위는 별도 비교 결과가 있을 때만 보고한다.
