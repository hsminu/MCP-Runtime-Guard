# 2026-10-13 A 리뷰 반영

> 이 문서는 앞선 리뷰의 실행 기록이다. 이후 [AGT 선정](a-agt-selection.md)과
> [A18 실제 연결](a-agt-integration.md)을 반영했다. 아래 mock 전용·AGT 미포함 설명은
> 당시 상태이며, 현재 앱·lock·검증은 A18 기록과 [진행 현황](a-progress.md)을 따른다.
> HTTP의 이후 구현·재현은 [A08/A12 연결 기록](a-http-integration.md)을 따른다.

출발점 A `5598009`, B `f22968c`. 사용자의 이 리뷰에 대한 “고쳐” 요청을 근거로
A 구현·패키징과 지적된 B Guard/승인 경계를 수정했다. B·C의 별도 리뷰나 3인 합의가
완료됐다는 뜻은 아니다. domain·ports·SQLite 스키마는 변경하지 않았다.

## 구현과 재현

| 리뷰 | 변경 | 확인 범위 |
| --- | --- | --- |
| 실행 골격 | `__main__`, RuntimePanel, bridge Task projection·사람 review·Runner resume | 실제 stdio 합성 서버, Qt 버튼 실행·사본 확인·승인·종료 |
| 독립 스파이크 | `examples/synthetic_app.py`, `verify_stdio_spike.py` | C fixture 없이 A가 실행하는 SDK 조사. C 인수와 구분 |
| B 동기화 | f22968c Guard의 expected_epoch, B 단위 테스트 가져옴 | Runner가 trusted epoch를 전달. B 승인 Host 대역은 새 내부 stop 인터페이스 반영 |
| 테스트 충돌 | A 목록 테스트 → `test_ui_approvals.py` | B `test_approvals.py`와 공존 |
| 호출 종류 | enum `.value`에서 READ/SEND 매핑 생성 | 시험 입력도 실제 enum 값 사용 |
| stderr·SDK 로그 | 필수 격리 errlog, worker context의 LogRecord 원문 제거 | malformed JSON·notification·자식 stderr 표식이 공개 로그에 없음 |
| 한글 상세 | 단일 문자 체계 일반 텍스트 유지, 비가시·bidi·혼합 표식 | `분기 보고서` 유지, 키릴 유사 도메인·literal escape 구분 |
| 환경 | pyproject·uv.lock, Python 3.12 범위 | CPython 3.12.15, MCP 1.29.0, PySide6 6.12.0 |
| 진행 문서 | 현재 표와 역사 증거 분리, 중복 커밋 표 삭제 | 미정·기존 결정은 보존 |
| 시안 | 연결 0과 등록 ACTIVE 구분, 승인 요청 후 미확인 유지 | 보라는 조작 강조, 기밀은 적색 계열, 결과 불명은 황토색 |

저장소 루트에서 실행한다.

```powershell
uv sync --locked
uv run python -m guard_app --demo --agt-mode mock
uv run python -m examples.verify_stdio_spike --agt-mode mock
uv run pytest -q
```

uv가 PATH에 없다면 별도 로컬 환경에 `uv==0.13.0`을 설치해 사용할 수 있다.
이번 실행은 `.devtools/bootstrap/Scripts/uv.exe`와 `.venv/Scripts/python.exe`를 썼다.
`.python-version`은 3.12 계열이며 winget에서 3.12.15를 찾을 필요가 없다.
lock은 A 앱·B 코어·단위 검증 범위다. 실제 AGT/Scanner 패키지는 포함하지 않는다.
jsonschema는 A SDK 조합의 4.25.1을 고정했고 B 테스트로 호환을 확인한다.

| 합성 시나리오 | host_dispatch (READ 포함) | receiver_effect (SEND) |
| --- | ---: | ---: |
| PUBLIC → SEND | 2 | 1 |
| CONFIDENTIAL → 외부 SEND | 1 | 0 |
| INTERNAL → 승인, 같은 승인 재요청 | 2 | 1 |
| INTERNAL → 거절 | 1 | 0 |
| INTERNAL → 취소 | 1 | 0 |
| INTERNAL → 긴급 차단 | 1 | 0 |

host_dispatch는 A worker의 실제 `ClientSession.call_tool` 직전 callback에서 계수한다.
receiver_effect는 별도 stdio 프로세스에서 실행한 합성 부작용 계수다.
wire tools/call은 별도 관측하지 않았으므로 host_dispatch와 같다고 주장하지 않는다.
SDK 기본 stdio launcher·B 메모리 Store/Registry 대역을 사용하며 SQLite·C launcher·HTTP는 없다.
외부 SEND는 실제 네트워크 전송이 아닌 합성 서버 내부 계수 증가다.

## 긴급 차단 경계

인증된 cancel/emergency_halt는 Task lock 전에 task별 stop event를 올린다.
Guard는 AGT와 stop을 함께 기다리고, 늦은 AGT 결과를 폐기한다.
판정 감사 후 예약 전, 승인 소비 전, 실제 transport 호출 직전에도 stop을 검사한다.
다른 Task를 막기 위해 global halt를 쓰지 않는다.

재평가부터 응답 커밋까지 Task lock은 그대로 유지한다. 예약 ACK 대기 중에는 Store 작업을
강제 취소하지 않는다. 이미 예약된 요청의 차단은 기존 after_dispatch 경계로 보내
READ 미공개/HALTED, SEND UNKNOWN_OUTCOME을 보존한다. 이미 시작된 전송을 취소가
되돌렸다고 표시하지 않는다. 실제 상태 확인 전에는 화면에 요청됨/적용 미확인만 표시한다.

관련 I2·I3·I5·I6·I7·I8, T20·T37. `test_control_interruption.py`는 submit/승인 ×
cancel/긴급 차단 × 취소를 무시하는 AGT, 잘못된 운영자, 감사 대기, 예약 ACK 경합을 검사한다.
3초 AGT 대기 중 제어 반환은 0.5초 제한 안에 완료하며 승인 소비·dispatch 0을 확인한다.
예약 뒤 SEND 결과 불명 보존과 정상 승인 소비·dispatch 각 1도 확인한다.

## AGT 경계와 남은 결정

현재 앱의 명시적 개발 모드는 **동일 프로세스 로컬 mock**이다. 임의 AGT 배포판을 설치하지
않고 원격 AGT를 구성할 CLI/설정 경로도 제공하지 않는다. 실제 실패를 mock으로 바꾸지 않는다.
mock은 이미 신뢰 설정에 등록된 server/tool ID에 대한 allow/deny 규칙만 읽으며 인자를
직렬화·로그·네트워크 전송하지 않는다. Guard 합성은 거부권만 허용하는 ADR-3을 유지한다.

실제 AGT 후보를 연결하기 전 다음 제안을 B 계약으로 확정해야 한다. 아래는 합의 기록이 아니다.

| 항목 | 제안과 다음 행동 | 담당 제안 |
| --- | --- | --- |
| 목적 | 고정 경계 정책이 놓치는 도구 사용 위험의 추가 거부. 승인·경계 완화 기능은 제외 | B 규칙 의미, A 연결, C 검증 |
| 선정 기준 | pre-call 판정만 가능, 자체 실행 없음, 로컬 동작 또는 승인된 경계, 입력 최소화, 취소·timeout, 로그 무원문, 버전 고정 | A 근거 조사, B 판정 검토, C 계수 |
| 결정권·기한 | 제품 선택은 사용자/팀 책임자의 명시 결정 필요. 다음 실제 AGT 구현 착수 전에 확정; 달력 날짜·담당 실명은 미확정 | A가 후보/근거 제출, 사용자 결정 |
| 입력 | 기본 server/tool ID·종류·효과 분류만. 본문·전체 인자·Task/운영자 ID는 기본 미전송 | B가 최소 DTO 제안, A 구현 |
| 외부 경계 | 로컬 ASK라도 인자를 원격 AGT로 먼저 보내면 안 됨. AGT를 독립 수신 경계로 등록하고 max_label·승인·최소화 검증 전 사용 금지 | B 정책·계약, A 전송 경로 |
| 규칙 | 규칙 의미·버전·정책은 B, 번역은 A, 위험 파일 경로/로딩은 C, 공격·효과 검증은 C | B·C 배정 확인 필요 |
| 이중 승인 | AGT 사람 승인 기능은 사용하지 않고 Guard 승인만 사용. 스파이크 질문 3은 A·B 공동 | A·B |
| 감사 | bootstrap의 고정 `agt_mode`를 B6 Store에 전달, audit_events에 NOT NULL mode와 규칙 버전 저장. 모델/도구 출력에서 mode 추론 금지 | B Store, A bootstrap, C 독립 인수 |
| timeout | Guard `access_timeout_seconds`가 전체 상한. 어댑터의 I/O timeout은 그보다 짧게 파생, Guard는 취소·늦은 결과 폐기, adapter는 소유 자원 정리 | B 상한, A cleanup |

`agt_mode`는 현재 bootstrap·UI에 mock으로 표시된다. **B6 SQLite가 없는 현재 트리에서 영속
감사 필드가 구현됐다고 보고하지 않는다.** mode 저장·migration은 B6의 남은 구현 의존성이다.
mock 규칙 파일 로더도 미구현이며 앱은 고정된 합성 규칙만 사용한다.

## 인수 한계

- 정식 사람 인증, 외부 모델, 실제 AGT/Scanner, SQLite, HTTP, C launcher는 미연결이다.
- HumanControl.review는 인증된 capability로 유효한 메모리 사본만 반환한다. 모델 도구에 노출하지 않는다.
- demo의 운영자는 소스에 고정된 로컬 합성 운영자다. 임의 문서·서버·명령 입력을 받지 않는다.
- SDK LogRecord factory는 설치 후 유지하고 worker context의 원문만 제거한다. 이후 호스트가 factory를 교체하면 격리 보장이 깨지므로 구성 완료 후 교체를 허용하지 않아야 한다.
- 새 Task UI·전체 목록 조회 연결·C의 독립 T/S 인수·CI/전체 보안 스캔은 미완료다.

## 최종 검증 기록

2026-10-13 로컬 실행 결과:

- `.venv/Scripts/python.exe -m pytest -q`: **1153 passed**, 9.53초.
- 변경 Python 파일·스파이크·신규 회귀 테스트의 `ruff check`: 통과.
- `node --check ui-preview/app.js`, `git diff --check`: 통과.
- `uv pip check --python .venv/Scripts/python.exe`: 47개 패키지 충돌 없음.
- 새 `.cache/repro-venv`에 `uv sync --locked` 설치 후 stdio 스파이크 6경로 다시 통과.
- Windows native Qt로 실제 서버 연결·승인 사본 화면을 렌더링하고 확인했다.
  offscreen 플랫폼의 기본 폰트에서는 문자가 사각형으로 보여 native Windows 렌더링으로 따로 확인했다.
- 독립 읽기 전용 리뷰에서 즉시 거절 신호 유실·초기 조회 cleanup 누락·SDK 로그 누출·동형 도메인을 지적받아 수정했고 회귀 테스트를 추가했다.

완료 판정은 위 범위에 한정한다. 제품용 Store/실제 AGT/HTTP와 C 독립 인수는 미완료다.
