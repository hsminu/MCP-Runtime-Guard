# A 경계 보안 검토와 ARTEX 실행 조건

기준: 2026-10-12, `feat/a-application`. 이 문서는 A 구현의 보안 경계와 미완료 통합 조건을 추적한다. 기존 A01–A42의 완료 판정은 [A 작업 현황](a-progress.md)을 따른다.

## 소스 보안 검토

Codex Security Standard Scan(`ecbf3668-f263-4cdb-850e-a4637305eb12`)은 `75a52c6`의 `src/guard_app` Python 파일 44개를 오프라인으로 검토했다. 전체 보고서는 로컬 스캔 산출물로 보관하며 저장소에 복사하지 않는다. 실제 C launcher, Store, Registry, fixture와 모델 제공자가 없어 실행 경로의 안전성까지 검증한 결과는 아니다.

| 발견 | 근거 | 소유·조치 |
| --- | --- | --- |
| 모델 응답의 무제한 텍스트·호출·인자 처리(CWE-400, medium) | `adapters/model_client.py`에서 provider 응답을 크기 검사 전에 파싱·동기 검증 | A: 응답 경계 예산과 단위 시험. 실제 provider의 수신 상한은 A20 결정 후 확인 |
| MCP 결과의 크기 검사 전 전체 변환(CWE-400, medium) | `adapters/mcp_transport.py`의 `model_dump`·불변 사본이 `ResponseGate`보다 앞섬 | A: 변환 전 요소·바이트 예산. C: SDK 역직렬화 전 wire 상한이 필요한지 확인 |
| 등록 스키마 정규식의 매칭 시간 상한 없음(CWE-1333, medium) | B `services/catalog_schema.py`는 `re.compile`로 문법만 검사하고 `jsonschema`가 동기 매칭 | B: B3의 `patternProperties` 지원 계약을 포함해 안전한 정규식 정책 또는 격리된 검증기를 결정. A: 모델 인자 수신 예산을 함께 적용하되 B 문제 해결로 표시하지 않음 |

세 발견은 이 소스의 가용성 경로에 대한 것이다. 입력이 실제 제품에서 이 지점에 도달하려면 ACTIVE 등록, 실제 provider 또는 MCP stream 구성이 필요하다. 보고서의 조건부 위험을 통합 검증 성공이나 실제 침투 성공으로 읽지 않는다.

## 아키텍처 완료 조건

1. **단일 실행 입구:** C의 구성 루트는 관리 대상 `tools/call`을 B `GuardService.submit()`을 통해서만 호출하고 A의 transport를 UI·Runner·AGT에 직접 노출하지 않는다(I1, T08).
2. **자원 예산:** 모델 응답과 MCP 응답은 파싱·복사·정규식 검증 전에 바이트·요소·호출 수 상한을 적용한다. A의 객체 단계 제한만으로 SDK의 wire 단계 수신 상한을 증명하지 않는다.
3. **승인과 상태:** B의 `expected_epoch` 비교, 승인 claim, 정책·지문 재평가, Task lock, C Store의 원자적 상태 변경을 같은 실제 경로에서 확인한다(I3, I4, I10; T09–T12, T29, T37).
4. **격리된 결과:** READ 원문은 분류·등급/감사 커밋 전 모델·UI·로그에 공개하지 않는다. SEND 결과 불명은 `OutcomeUnknown`으로 유지하고 자동 재전송하지 않는다(I5, I8; T07, T14, T36).
5. **비밀과 전송:** stdio 최소 환경, HTTP endpoint 검증·redirect 차단, MCP SDK stderr/root logger 격리, 공개 로그 allowlist를 C의 launcher·fixture와 함께 검증한다(I9; T16, T19, S04).

이 조건은 A 모듈 단위 시험만으로 닫히지 않는다. B·C 실제 계약과 C의 `approval_consumed / host_dispatch / wire_tools_call / receiver_effect` 계수가 필요하다.

## ARTEX 실행 조건

로컬 `artex-ko`는 실행 중인 목표를 대상으로 LLM 에이전트가 실제 도구를 호출하는 침투 테스트 시스템이다. 부분 구현된 Qt/Python 소스를 정적 분석하는 명령이 아니며, 작업 에이전트에 파일·명령 실행 권한이 있다. 따라서 다음 조건이 갖춰진 시점에만 A38–A41의 별도 공격 검증으로 사용한다.

1. C launcher·Registry·Store·격리 fixture로 앱의 요청→Guard→MCP→결과 경로가 실제로 구동되고 계수가 관측된다.
2. 폐기 가능한 별도 실행 환경과 합성 자료를 사용한다. ARTEX의 작업 디렉터리와 DB는 개발 작업 트리·실제 자격 증명과 분리한다.
3. 대상은 소유한 로컬 fixture 주소와 명시한 포트로 제한하고 외부 통신은 환경 차원에서 차단한다. 프롬프트 제약만을 네트워크 격리로 취급하지 않는다.
4. 범위·시간·동시 worker·요청량 상한을 정하고, READ 비공개, SEND 재전송, 승인 1회, 로그 누출, 정의 변조와 결과 초과를 C 계수 및 감사 자료로 판정한다.
5. ARTEX 실행 결과와 C의 독립적 재현 증거를 구분한다. 통합 대상이 생기기 전에는 ARTEX 통과 또는 실패를 주장하지 않는다.

현재 ARTEX 저장소만 확인했고 실행하지 않았다. PostgreSQL, LLM 설정, 격리된 실행 대상이 준비되지 않았다.
