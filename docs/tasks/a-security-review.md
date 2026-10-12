# A 경계 보안 검토와 ARTEX 실행 조건

기준: 2026-10-12, `feat/a-application`. 이 문서는 A 구현의 보안 경계와 미완료 통합 조건을 추적한다. 기존 A01–A42의 완료 판정은 [A 작업 현황](a-progress.md)을 따른다.

## 소스 보안 검토

Codex Security Standard Scan(`ecbf3668-f263-4cdb-850e-a4637305eb12`)은 `75a52c6`의 `src/guard_app` Python 파일 44개를 오프라인으로 검토했다. 전체 보고서는 로컬 스캔 산출물로 보관하며 저장소에 복사하지 않는다. 보고서의 coverage는 MCP 결과 문제의 중복 후보 1건이 보류 항목으로 남아 `partial`이다. 실제 C launcher, Store, Registry, fixture와 모델 제공자가 없어 실행 경로의 안전성까지 검증한 결과도 아니다.

| 발견 | 근거 | 소유·조치 |
| --- | --- | --- |
| 모델 응답의 무제한 텍스트·호출·인자 처리(CWE-400, medium) | `adapters/model_client.py`에서 provider 응답을 크기 검사 전에 파싱·동기 검증 | A: `54a82ff`에서 응답 예산·분리 사본과 단위 시험 적용. 실제 provider의 수신 상한은 A20 결정 후 확인 |
| MCP 결과의 크기 검사 전 전체 변환(CWE-400, medium) | `adapters/mcp_transport.py`의 `model_dump`·불변 사본이 `ResponseGate`보다 앞섬 | A: `c0e07b9`에서 변환 전 요소·바이트 예산 적용. C: SDK 역직렬화 전 wire 상한 구현·검증 필요 |
| 등록 스키마 정규식의 매칭 시간 상한 없음(CWE-1333, medium) | B `services/catalog_schema.py`는 `re.compile`로 문법만 검사하고 `jsonschema`가 동기 매칭 | B: B3의 `patternProperties` 지원 계약을 포함해 안전한 정규식 정책 또는 격리된 검증기를 결정. A: 모델 인자 수신 예산을 함께 적용하되 B 문제 해결로 표시하지 않음 |

세 발견은 이 소스의 가용성 경로에 대한 것이다. 입력이 실제 제품에서 이 지점에 도달하려면 ACTIVE 등록, 실제 provider 또는 MCP stream 구성이 필요하다. 보고서의 조건부 위험을 통합 검증 성공이나 실제 침투 성공으로 읽지 않는다.

두 A 패치 이후 `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`의 Python 3.12.15에서 단위 테스트 **188건 통과**를 확인했다. 스캔은 패치 전 리비전에 고정돼 있으므로, 보고서의 발견 수와 현재 코드의 보강 상태를 구분한다. B의 정규식 위험은 A의 인자 길이 상한만으로 해결되지 않는다.

반응형 Qt 변경분은 별도로 수동 검토했다. 새 승인 선택 요약에 공개 문자열의 줄바꿈·제어·bidi 문자를 그대로 붙이면 가짜 `상태:` 줄을 만들 수 있어, 동적 값을 한 줄 코드포인트 표기로 바꾸고 합성 회귀 시험을 추가했다(`e427da1`). 표와 상세는 계속 일반 텍스트만 표시하고, 기존 공개 CSV 필드·승인 상세 닫기 시 텍스트 정리 경계를 유지한다. 이는 변경분 검토이며 위 고정 리비전의 정식 스캔 재실행이나 B/C 통합 보안 인수가 아니다.

## 2026-10-12 · A29/A10/A21/A25 변경분 보안 스캔

Codex Security Diff Scan(`91103c2f-93eb-41c7-9e9b-46c561b2c4f0`)은 변경 불가능한 `97e3c96..40786f5` 범위의 소스 5개(`adapters/model_client.py`, `adapters/mcp_transport.py`, `services/runner.py`, `services/bridge.py`, `ui/overview.py`)를 오프라인으로 검토했다. 변경 파일 5개 모두 coverage가 완료됐고 새 보안 취약점 발견은 **0건**이다. 이 결과는 해당 diff의 소스 검토에 한정되며, 기존 Standard Scan의 발견이나 C 통합·실제 전송의 안전성을 해소했다는 뜻은 아니다. 전체 보고서는 scan ID로 찾을 수 있는 로컬 스캔 산출물에 보관하고 저장소에 복사하지 않는다.

스캔 기준 뒤 `f5dc095`에서 bridge ingress drain의 넓은 `except Exception`을 제거해 `AGENTS.md`의 예외 경계 규칙에 맞췄다. 따라서 이 후속 수정은 위 고정 diff 스캔 범위 밖이다. 후속 코드의 관련 bridge 단위 테스트 31건, 전체 단위 테스트 **218건**, Windows 네이티브 Qt의 A29 반응형 표적 테스트 1건이 통과했다. 이 시험은 C의 계수나 ARTEX 침투 검증을 대신하지 않는다.

후속 반응형 UI 커밋(`c6daa72..60958b5`)은 별도로 읽기 전용 소스 검토했다. 승인 상세의 일반 텍스트 표시, 좁은 승인 상태·시각, 서버 보안 요약, 현황 탐색에서 새 보안 취약점 후보는 확인하지 못했다. 운영상 결함은 1건 재현했다. 확인 항목의 3페이지를 보던 중 새 항목이 앞에 들어와도 상단 바로가기가 3페이지에 머물러 새 항목을 가렸다. `60958b5`에서 신규 항목·첫 페이지 변경을 감지해 첫 페이지를 열고, 바로가기도 항상 첫 페이지로 이동하게 수정했다. 이 검토는 위 정식 Diff Scan의 범위를 넓히거나 C 제품 경로의 안전성을 증명하지 않는다.

## 2026-10-12 · A09/A20/A34 후속 변경분 스캔

Codex Security Diff Scan `33627382-7343-4e49-b8c0-2047002356b4`는 고정 범위 `e7af7fa..186a318`의 변경 제품 소스 `adapters/mcp_transport.py`, `adapters/model_client.py` 두 파일을 모두 검토했다. A34 변경은 테스트·문서만이며 제품 소스 변경은 없다. 두 파일의 source review coverage가 완료됐고 새 취약점 후보·확정 발견은 **0건**이다. 보고서는 로컬 Codex Security 산출물 `report.md`에 보관한다.

검토는 최대 4개 MCP worker 시작의 실패·취소 정리와 모델 deadline 뒤 늦은 응답이 Runner/Guard에 닿지 않는지를 추적했다. 전체 Qt offscreen 단위 시험 **246건**, A34 Windows 네이티브 Qt **6건**은 별도 실행 근거다. 취소를 무시하는 실제 제공자 내부 I/O 수명, C 제품 경로의 wire/effect 계수와 ARTEX 시험은 검증하지 않았다. 앞선 B 등록 스키마 정규식 발견도 이 diff의 새 발견 0건으로 닫히지 않는다.

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
