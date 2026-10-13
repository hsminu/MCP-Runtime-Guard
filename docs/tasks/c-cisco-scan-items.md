# C · Cisco MCP Scanner 검사 항목

2026-10-13 정리. 기준은 앞서 설치·검증한 **cisco-ai-mcp-scanner 4.8.6**의 wheel 코드이며 공식 v4.8.6 commit은 `5d147dc7e3635606e41104112fa297b45444b05c`다. 최신 버전 전체의 기능을 단정하는 문서가 아니다. 실제 실행한 범위는 [스파이크 보고](c-cisco-scanner-spike.md)를 따른다.

## 1. 분석기별 대상과 목적

| 분석기 | 검사 대상 | 확인하는 항목 | 실행 조건 |
| --- | --- | --- | --- |
| YARA | 설명·정의 등의 텍스트 | 미리 정한 위험 문자열·정규식과 조건의 일치 | 로컬, API 키 불필요 |
| LLM | 도구 이름·설명·인자 정의 등의 텍스트 | 지시문 조작·숨은 행동·정보 유출 등 위험의 모델 분석 | 모델 엔드포인트 필요. 로컬 모델 경로도 존재 |
| Cisco API | 도구 설명 등의 텍스트 | Cisco AI Defense의 보안·콘텐츠 분류 | Cisco API 키·외부 호출 |
| Readiness | 도구 정의·설정 | 운영 준비에 관한 20개 휴리스틱 | 기본 검사는 로컬. OPA·LLM judge는 선택 |
| Prompt Defense | 도구 설명 | 방어 문구의 포함 여부·충분성 12개 항목 | 로컬 정규식, 키 불필요 |
| Behavioral | MCP 도구 소스 코드 | 데이터 흐름과 문서화된 동작의 불일치, 숨은 전송 등 | 코드 정적 분석·모델 분석 필요 |
| Vulnerable Package | requirements·프로젝트·설치 환경 | 알려진 Python 의존성 취약점 | pip-audit·취약점 DB 조회, LLM 불필요 |
| VirusTotal | 파일·디렉터리 | 파일 해시의 알려진 악성 여부. 선택 업로드 | VirusTotal 키·외부 호출 |
| Meta | 다른 분석기의 findings | 모델이 오탐으로 판단한 finding의 제거 | 모델 분석 필요. 새 위협 탐지나 위험도 상향 기능이 아님 |

출처는 [공식 배포 안내](https://pypi.org/project/cisco-ai-mcp-scanner/4.8.6/), [분석기 코드](https://github.com/cisco-ai-defense/mcp-scanner/tree/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers)다. 위 항목은 검사 목적이며 탐지·차단 성공 보장이 아니다. 이번 프로젝트의 등록 검사 입력은 도구 정의 metadata로 제한한다.

## 2. YARA의 10개 위험 패턴

| 규칙 | 검사하는 패턴 |
| --- | --- |
| prompt_injection | 기존 지시 무시·역할/권한 변경·숨은 지시 등 |
| tool_poisoning | 공개된 도구 목적과 다른 숨은 부가 동작 지시 |
| coercive_injection | 강압·긴급성·권위 등을 이용해 행동을 유도하는 지시 |
| data_exfiltration | 데이터를 외부로 유출하도록 유도하는 표현 |
| credential_harvesting | 인증정보·토큰·비밀값 수집 지시 |
| command_injection | 셸·명령 주입 패턴 |
| code_execution | 코드의 동적 실행 패턴 |
| sql_injection | SQL 주입 패턴 |
| script_injection | 스크립트 주입 패턴 |
| system_manipulation | 시스템·설정·권한 조작 패턴 |

이 검사는 입력 텍스트가 규칙에 맞는지를 확인한다. 실제 서버가 그 행동을 수행했는지는 별도 검증이 필요하다. 규칙 조건에 맞춘 대조군은 탐지됐지만 별도 공격 문구는 탐지되지 않은 실험을 보고서에 남겼다. 근거는 [동봉 YARA 규칙](https://github.com/cisco-ai-defense/mcp-scanner/tree/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/data/yara_rules)이다.

## 3. Readiness의 20개 운영 준비 항목

| ID | 검사 항목 |
| --- | --- |
| HEUR-001 | timeout 설정 누락 |
| HEUR-002 | 지나치게 긴 timeout |
| HEUR-003 | 재시도 횟수 제한 누락 |
| HEUR-004 | 무제한 또는 과도한 재시도 |
| HEUR-005 | 재시도 backoff 전략 누락 |
| HEUR-006 | 오류 응답 스키마 누락 |
| HEUR-007 | 오류 스키마의 code 필드 누락 |
| HEUR-008 | 출력·응답 스키마 누락 |
| HEUR-009 | 설명 누락·짧거나 모호한 설명 |
| HEUR-010 | 너무 많은 기능을 한 도구에 포함하는 표현 |
| HEUR-011 | 입력 스키마의 필수 필드 선언 누락 |
| HEUR-012 | 입력값 제약·검증 표시 부족 |
| HEUR-013 | 요청 빈도 제한 설정 누락 |
| HEUR-014 | 버전 정보 누락 |
| HEUR-015 | 관측·모니터링 설정 누락 |
| HEUR-016 | 자원 해제 동작 설명 부족 |
| HEUR-017 | 상태 변경 도구의 멱등성 설명 부족 |
| HEUR-018 | 위험한 연산을 암시하는 키워드 |
| HEUR-019 | 외부 자원 접근 도구의 인증 설정 부족 |
| HEUR-020 | 자기 참조·순환 의존 위험의 표현 |

도구 정의와 설정의 표시를 보는 휴리스틱이다. 실제 timeout·인증·재시도·멱등성이 실행 경로에서 강제되는지 증명하지 않는다. 특히 SEND 재시도 설정의 존재를 장려하는 일반 운영 점검은 우리 I8의 결과 불명 SEND 재전송 금지와 별도로 해석한다. 실제 정상형 합성 정의에서도 HIGH가 나왔으므로 등록 차단에 모든 결과를 일괄 적용하는 정책은 미확정이다. 근거는 [Readiness 구현](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers/readiness/readiness_analyzer.py)이다.

## 4. Prompt Defense의 12개 방어 문구 항목

| 항목 | 설명에서 찾는 방어 내용 |
| --- | --- |
| INSTRUCTION_OVERRIDE | 기존 지시를 덮어쓰는 요청에 대한 방어 |
| DATA_LEAKAGE | 비밀·인증정보 등의 노출 방지 |
| ROLE_ESCAPE | 역할·권한 이탈 방지 |
| INDIRECT_INJECTION | 외부 내용의 지시를 따르지 않는 방어 |
| OUTPUT_WEAPONIZATION | 악성 목적의 결과 생성 방지 |
| OUTPUT_MANIPULATION | 결과 조작에 대한 방어 |
| MULTILANG_BYPASS | 다국어 표현을 이용한 우회 방어 |
| UNICODE_ATTACK | Unicode·보이지 않는 문자 등의 공격 방어 |
| CONTEXT_OVERFLOW | 긴 입력·context 초과 방어 |
| SOCIAL_ENGINEERING | 사회공학·권위·긴급성 유도에 대한 방어 |
| INPUT_VALIDATION | 입력 검증·허용 목록 등에 관한 문구 |
| ABUSE_PREVENTION | 악용·남용 방지 문구 |

방어 문구가 부족하다는 finding은 악성 문구를 발견했다는 뜻과 다르다. 방어 문구가 있다고 실제 방어가 집행됐다는 증거가 되는 것도 아니다. 근거는 [Prompt Defense 구현](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers/prompt_defense_analyzer.py)이다.

## 5. Cisco API의 기본 요청 분류

API 분석기 코드의 enabled_rules에는 프롬프트 주입, 괴롭힘, 혐오 표현, 욕설, 성적 콘텐츠·착취, 사회적 분열·양극화, 폭력·공공 안전 위협, 코드 탐지 8개가 들어 있다. 보안 위협과 콘텐츠 정책 분류를 함께 포함한다. 계정에 사전 설정된 규칙이 있으면 명시 규칙 없이 재요청하는 코드도 있어 실제 서비스 정책은 별도 확인 대상이다. 이번 스파이크에서 Cisco API 호출은 미실행이다. 근거는 [API 분석기 코드](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers/api_analyzer.py)다.

## 6. 서버·소스·패키지 검사 경로

- remote·stdio·config·known-configs는 MCP 정의를 수집하는 경로다. 선택한 분석기가 어떤 내용을 검사하는지는 별도로 확인한다.
- prompts·resources·instructions는 도구 정의 외의 프롬프트·자원 내용·서버 지시를 검사하는 경로다. 우리 등록 어댑터의 metadata-only 입력 범위에 포함하지 않는다.
- 설치된 CLI의 behavioral·pypi-scan·npm-scan은 소스나 패키지를 대상으로 한다. PyPI·npm 다운로드/검사는 별도 모드이며 도구 정의 입력만으로 실행되는 검사가 아니다.
- ProtocolAnalyzer 코드에는 HTTP 암호화, 인증 없는 요청 수용, 서명 표시, 재전송 방어, 도구 지문 표시, 신원 위조, 잘못된 요청에서 fail-open 동작, 요청 빈도 제한의 8개 점검이 있다. 서버 응답·표시를 이용한 점검이며 모든 보안 통제를 증명하지 않는다. 4.8.6 AnalyzerEnum·일반 CLI 선택에 Protocol 항목이 없고 cli.py에도 해당 분석기 연결을 찾지 못했다. 개별 SDK 사용·실제 동작 검증은 미실행이다.

근거는 [프로토콜 분석기](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/core/analyzers/protocol_analyzer.py), [CLI](https://github.com/cisco-ai-defense/mcp-scanner/blob/5d147dc7e3635606e41104112fa297b45444b05c/mcpscanner/cli.py)다.

## 7. 우리 프로젝트에서 사용할 범위

현재 권장은 도구 정의 metadata에 대한 YARA를 필수 후보로, Readiness·Prompt Defense를 선택 후보로 두는 것이다. 모델·외부 서비스·소스·패키지·실제 서버 검사는 따로 선택하고 검증한다. 상세 필수 목록과 Registry 활성화 계약은 미확정이다.

Task 등급 누적, 서버·목적지 경계 판정, 사람 승인과 한 번 소비, READ 커밋 후 공개, SEND 결과 불명 재전송 금지와 실제 wire/부작용 계수는 우리 Guard·저장소·Transport와 C 독립 검증의 책임이다. Scanner의 무탐지·완료·safe 표시로 이 인수를 대신하지 않는다.

YARA·Readiness·Prompt Defense·정적 CLI는 앞선 스파이크에서 실행했다. 이 문서 작성 시 새 서버·모델·외부 API·패키지 스캔을 실행하거나 미실행 분석기를 통과로 표시하지 않았다.
