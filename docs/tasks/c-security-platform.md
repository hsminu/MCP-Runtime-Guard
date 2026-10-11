# C · 보안 기반·검증 작업 지침과 체크리스트

작업 브랜치는 `feat/c-security-platform`이다. **안전한 위험 연산과 원자적 저장소를 만들고, 보안 판정이 실제 전송 경로에서 집행됐음을 측정으로 증명하는 역할**이다. 현재는 문서 준비 단계이며, 구현 지시 전에는 소스·fixture·테스트·CI 뼈대를 만들지 않는다.

## 담당 범위

제품 경로는 향후 `src/guard_app/` 기준이다.

| 파일 / 영역 | 구현 책임 | 리뷰 |
| --- | --- | --- |
| `storage/*` | 스키마, SQLite 단일 writer, 바인딩 SQL, 원자적 연산 | B |
| `security/paths.py`, `endpoints.py`, `ids.py`, `secrets.py`, `redact.py`, `launcher.py` | 경로·URL·ID·비밀값·로그·프로세스 안전 연산 | B |
| `services/registry.py`, `adapters/cisco.py` | 등록 상태·도구 지문·Scanner 결과와 활성화 | B |
| `fixtures/*`, `attack_fixtures/*` | 정상/악성 MCP, 합성 자료, wire·수신 부작용 계수 | A |
| 계약·통합·공격·결함 주입·실험 테스트 | 독립 집행 검증, replay·지연 측정 | 관련 모듈 담당, C 소유 모듈 테스트는 B |
| CI·Semgrep·import-linter | 위험 API·계층·예외·취약점 검사를 강제 | B |
| `docs/weakness_register.md` | 실제 진단 결과·조치·재발 방지·규칙 예외 기록 | B |

`security/failclosed.py`·`security/digest.py`는 B 소유다. 같은 패키지에 있다는 이유로 가져오지 않는다. 정책·승인·응답 게이트 구현은 B, transport·AGT 연결·GUI는 A가 맡는다.

## 먼저 읽을 설계

개발문서 0–2장, 6–8장(커밋·승인·직렬화), 9.4절(계수), 10.1–10.3절(등록·검사·AGT), 11장(Store), 13–15장, 부록 A·B를 읽는다.

## 작업 순서

### C1 · 계약과 관측 기반

- [ ] B의 Store·Scanner·Registry 조회·상태·감사 계약을 검토한다. 메서드·오류·트랜잭션 조건을 `docs/contracts.md`에 합의한다.
- [ ] A와 fixture 실행 방법·MCP 버전·host_dispatch 관측·서버 지문 변경 알림을 맞춘다.
- [ ] 단위 테스트는 모듈 담당이, 계약·통합·공격·결함 주입·실험은 C가 작성하는 경계를 유지한다.
- [ ] 제품 소스와 악성 fixture를 구분한다. 정상 fixture는 제출 소스와 함께, 공격 fixture는 설계에 따라 제출 범위 밖에서 관리한다.

### C2 · 위험 연산과 정적 강제

- [ ] safe_path로 빈 값·NUL·역슬래시·절대경로·상위 경로·심볼릭 링크·루트 밖 접근을 거부한다. Windows 드라이브·UNC·junction 등 플랫폼 우회도 검토한다.
- [ ] 검사와 열기 사이 TOCTOU 한계를 기록하고 fixture 루트 쓰기 권한·지원 플랫폼의 no-follow 옵션을 확인한다.
- [ ] URL의 scheme·userinfo·IDNA·끝 점·기본 포트·인코딩 경로를 검증한다. IP·DNS 결과로 내부 신뢰를 추정하지 않는다.
- [ ] 등록된 절대 실행 파일·화이트리스트·인자 리스트·최소 환경으로만 프로세스를 실행한다. shell=True·동적 실행을 금지한다.
- [ ] ID는 암호학적 난수로 만들고 비밀값 누락은 전용 예외나 기능 비활성으로 처리한다. 기본 비밀값을 두지 않는다.
- [ ] 구조화 감사에서 원문·전체 인자·인증정보를 제외하고 마스킹 필터를 2차 방어로 적용한다.
- [ ] 위 연산의 단일 구현 지점을 Semgrep·import-linter로 강제한다. 예외 판정·해시 모듈은 B와 규칙을 맞춘다.

### C3 · SQLite 저장소

- [ ] 백엔드 단일 writer가 연결을 소유한다. WAL·foreign_keys·synchronous FULL·명시적 BEGIN IMMEDIATE를 사용한다.
- [ ] SQL은 sqlite_store 안에서 바인딩 파라미터로만 실행한다. requests의 Task/순번 UNIQUE와 approvals의 request UNIQUE 등 제약을 적용한다.
- [ ] commit_observation에서 등급·security_version·승격 근거·감사·요청 상태를 한 트랜잭션으로 커밋한다.
- [ ] commit_send_outcome에서 요청·감사만 갱신한다. SEND 성공이 Unclassified를 만들거나 등급을 변경하지 않게 한다.
- [ ] consume_and_reserve에서 승인·요청·Task 상태를 함께 전이한다. hash·UTC 만료·기대 상태·security_version·변경 행 수를 검사하고 실패는 모두 rollback한다.
- [ ] row_revision 기반 충돌과 security_version 기반 보안 변경을 구분한다.
- [ ] 문서·인자·결과·인증정보·승인 사본 원문을 DB에 저장하지 않는다. 재시작에서 미소비 승인을 실행 권한으로 복원하지 않는다.
- [ ] 부분 실패·DB 잠금·감사 커밋 실패·승인 경쟁을 주입하고 원자성·내구성을 검증한다.

### C4 · Registry와 Scanner

- [ ] 신규 서버는 PENDING, 완전한 필수 검사와 관리자 확인 후 ACTIVE로 전이한다. 검사 통과만으로 자동 활성화하지 않는다.
- [ ] name·description·inputSchema·annotations를 B의 정규화·digest 계약으로 지문화한다. 변경은 즉시 PENDING과 기존 승인 무효로 연결한다.
- [ ] Scanner에는 도구 정의 metadata만 보낸다. 업무 본문·인증정보를 보내지 않는다.
- [ ] 필수 분석기 skip·failed, 형식 불명, HIGH 이상, 지문 집합 불일치는 PENDING 또는 검사 무효로 처리한다.
- [ ] 고정 버전에서 실제 오프라인 분석기를 확인하고 필수 목록을 정의한다. 외부 API·LLM 분석기는 선택 항목으로 둔다.
- [ ] 라이브러리 API/CLI 호출을 검증한 뒤 선택한다. CLI는 launcher를 사용하고 의존성 충돌이면 별도 환경에 둔다.

### C5 · fixture와 계수

- [ ] Local Document stdio 서버에 public/company/secret 합성 문서와 TEST_SECRET 표지를 둔다. 실제 기밀 자료는 사용하지 않는다.
- [ ] Remote Send HTTP 서버는 127.0.0.1에 바인딩한다. tools/call과 프로토콜 메시지를 구분해서 계수한다.
- [ ] approval_consumed·host_dispatch·wire tools/call·receiver_effect를 요청 ID·시나리오와 연결한다.
- [ ] fixture의 wire_tools_call과 설계의 wire_post 명칭을 명시적으로 대응시킨다. 카운터는 테스트 지정 임시 파일에 기록하고 HTTP 조회 기능으로 노출하지 않는다.
- [ ] 응답 유실·307/308 redirect·재연결·도구 정의 변조·stderr/progress 유출 공격 fixture를 준비한다.
- [ ] 관찰 불가능한 stdio 서버 내부 외부 호출 등을 검증 불가로 기록한다. 화면 메시지를 계수 증거로 쓰지 않는다.

### C6 · 검증·실험·CI

- [ ] Fake와 실제 Transport·Store·AccessChecker·Scanner에 동일 의미의 계약 테스트를 적용한다.
- [ ] 아래 C 담당 T/S 목록을 구현하고 원문의 각 기대 결과·불변식과 연결한다.
- [ ] AGT 거절·오류·시간초과·형식 불명에서 host_dispatch 0을 확인한다.
- [ ] 정책·승인·응답 게이트 공격을 구현하고 실패 수정은 A/B의 해당 담당에게 전달한다.
- [ ] accumulate·last_read·none replay를 동일 호출 시퀀스·시계·설정 조건으로 수행한다. 모드 외 설정 해시를 기록한다.
- [ ] 사전 정답과 기대 판정을 결과를 보기 전에 고정한다. 모드별 시나리오를 최소 10회 반복한다.
- [ ] 정책 일치율·제한 성공률·과잉 제한률·정상 작업 완료율·정책 지연 p50/p95를 보고한다. MCP·모델·사람 대기는 별도 측정한다.
- [ ] ruff → strict mypy → import-linter → 단위/계약 → 통합 → Bandit → Semgrep → pip-audit 순의 CI를 구축한다. 공격·결함 주입·실험 검증도 별도로 실행한다.
- [ ] 공격 fixture는 제품 보안 스캔에서 제외한다. 제품 예외는 사유와 리뷰어 승인 근거를 약점 관리대장에 남긴다.

## C가 작성할 검증 목록

| 계층 / 묶음 | 개발문서 ID |
| --- | --- |
| 통합·정적 집행 | T01–T04, T08–T12, T20–T22, T26, T29–T30, T33, T35, T37 |
| 공격·전송·유출 | T14–T16, T19 |
| 등록·검사 계약 | T17 |
| 결함 주입 | T07, T36, S05 |
| replay·과잉 제한·지연 | T23, T24, T28, T34 |
| C 모듈 단위·안전 연산 | S01–S04 및 storage·Registry·Scanner 등 담당 모듈 단위 테스트 |

T04·T26·T30의 단위 부분은 B, 통합 부분은 C다. T18은 A의 UI 검증이다. B의 단위·성질 테스트를 독립 통합 검증 대신 쓰지 않는다.

## 인수 기준

- [ ] DENY와 승인 전/거절/만료 ASK는 dispatch·wire·부작용 0이다.
- [ ] 승인 1회 및 더블클릭은 consumed·dispatch·wire·부작용이 각각 1이다.
- [ ] 쓰기 응답 유실 후 UNKNOWN_OUTCOME으로 잠기며 새 호출 ID·SDK 재연결로 추가 전송이 없다.
- [ ] READ 커밋 실패·미분류·원시 로그 공격에서 결과·기밀 표지가 모델·UI·로그로 공개되지 않는다.
- [ ] B의 저장소·위험 연산·등록·Scanner·CI 리뷰, A의 fixture 리뷰를 받는다. C 소유 테스트는 B가 리뷰한다.
- [ ] 측정 원자료·미실행/검증 불가 항목·약점 조치와 재현 방법을 남긴다.

이 역할은 I1–I10의 실제 집행 증거를 책임진다. 약점이 0이라고 보고하려면 해당 범위와 도구로 실제 검사한 근거가 있어야 한다.
