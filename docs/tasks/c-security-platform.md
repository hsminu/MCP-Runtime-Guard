# C · 보안 기반·검증 작업 지침과 체크리스트

작업 브랜치는 `feat/c-security-platform`이다. **안전한 위험 연산·Registry·Scanner를 만들고, B 저장소와 보안 판정이 실제 전송 경로에서 집행됐음을 독립 검증하는 역할**이다. 2026-10-12 사용자 결정으로 SQLite 구현은 B로 이관했다. 현재 C는 문서 준비 단계이며, 구현 지시 전에는 소스·fixture·테스트·CI 뼈대를 만들지 않는다.

## 담당 범위

제품 경로는 향후 `src/guard_app/` 기준이다.

| 파일 / 영역 | 구현 책임 | 리뷰 |
| --- | --- | --- |
| `security/paths.py`, `endpoints.py`, `ids.py`, `secrets.py`, `redact.py`, `launcher.py` | 경로·URL·ID·비밀값·로그·프로세스 안전 연산 | B |
| `services/registry.py`, `adapters/cisco.py` | 등록 상태·도구 지문·Scanner 결과와 활성화 | B |
| `fixtures/*`, `attack_fixtures/*` | 정상/악성 MCP, 합성 자료, wire·수신 부작용 계수 | A |
| 계약·통합·공격·결함 주입·실험 테스트 | 독립 집행 검증, replay·지연 측정 | 관련 모듈 담당, C 소유 모듈 테스트는 B |
| CI·Semgrep·import-linter | 위험 API·계층·예외·취약점 검사를 강제 | B |
| `docs/weakness_register.md` | 실제 진단 결과·조치·재발 방지·규칙 예외 기록 | B |

`security/failclosed.py`·`security/digest.py`·`storage/*`는 B 소유다. C는 저장소를 리뷰하고 독립 계약·통합·결함 주입 테스트를 작성한다. SQLite·스키마·복구·저장소 단위 테스트는 [B6](b-sqlite-store.md)를 따른다. 정책·승인·응답 게이트 구현은 B, transport·AGT 연결·GUI는 A가 맡는다.

## 먼저 읽을 설계

개발문서 0–2장, 6–8장(커밋·승인·직렬화), 9.4절(계수), 10.1–10.3절(등록·검사·AGT), 11장(Store), 13–15장, 부록 A·B를 읽는다.

## 현재 진행 상태

아래 준비 완료는 현재 작업 디렉터리의 로컬 상태다. 다른 clone의 환경이나 제품 구현·검증 완료를 뜻하지 않는다.

- [x] `feat/c-security-platform` 전환 및 공통·C 담당 문서 읽기.
- [x] `.venv` 생성: Python 3.12.3, pip 26.2.1, 시스템 패키지 비공유. 활성화와 `pip check` 확인.
- [x] `.git/info/exclude`에 `/.venv/` 등록.
- [ ] 프로젝트·개발 의존성의 호환 조합 검증과 버전 고정. 현재 `.venv`에는 pip만 설치되어 있다.
- [x] C1.1 [Store 계약 요구 초안](c-store-requirements.md) 작성. B 검토·A·B·C 합의는 미완료다.
- [x] B `0c02b61` 검토·840개 테스트 재현·C-B-01 발견 후, 수정 커밋 `f3c2d2b`와 회귀 검증 확인. 최신 코드로 864개 테스트·Ruff·포맷·mypy 통과를 재현했다. [인수 검토 기록](c-b-review.md) 참조. 공통 계약 합의·실제 SQLite 인수는 남아 있다.
- [x] [Store 계약 처리 제안 v1](c-store-proposal.md) 작성. 보안 버전·원자 승인·ACK/취소·DB 시점 만료·복구 규칙과 B 변경 계획을 제안했다. 새 계약·구현·검증의 합의는 미완료다.
- [x] SQLite 구현의 B 이관과 C 독립 검증 배정을 [공통 계약](../contracts.md#담당-변경-기록)에 기록. API 세부 합의·원격 공유·B 착수 확인은 별도다.
- [ ] C1.2–C1.4 요구 초안과 공통 API 계약 합의.
- [ ] C2–C6 구현·테스트·스캔·실험. 모두 미착수·미실행이다.

Docker는 선택 도구다. 마지막 로컬 확인에서 CLI·Compose 설치와 서비스 실행은 확인했으나 소켓 접근 권한 오류가 있었고, 컨테이너 실행은 검증하지 않았다. 기본 가상환경 작업의 선행 조건으로 두지 않는다.

## 우선순위와 선행 조건

P0는 계약·환경 준비, P1은 담당 모듈 구현, P2는 연결된 실행 경로의 검증이다. 구현 태스크는 후속 구현 지시를 받은 뒤 착수한다. 각 태스크의 계약이 확정되면 독립 작업을 진행하고, 모든 담당의 전체 구현을 기다리지는 않는다.

| 태스크 | 우선순위 / 현재 상태 | 선행 조건·협업 | 완료 기준·산출물 |
| --- | --- | --- | --- |
| C1 · 계약·관측 요구 | P0 / Store 요구 초안 작성, 나머지 미착수·미합의 | 공통 계약 정의는 B, 확정은 A·B·C | Store·Scanner·Registry·계수·오류 요구 초안, 계약 검토와 실제 합의 기록 |
| 환경 후속 · 의존성 호환 검증 | P0 / 미착수 | 패키징·lock 편집 담당 합의. A의 MCP·AGT 연결 확인과 C의 Scanner 확인 | 동일 SDK로 stdio·HTTP 실제 왕복, AGT·Scanner 호환 근거, 검증된 버전 조합과 재설치 확인 |
| C2 · 위험 연산·정적 강제 | P1 / 미착수 | 사용할 domain 모델·전용 오류 계약 확인. B의 failclosed·digest 경계와 규칙 검토 | 경로·URL·프로세스·ID·비밀값·로그 모듈, S01–S04, 플랫폼·TOCTOU 한계, 정적 규칙 검증 |
| C3 · B SQLite 리뷰·독립 검증 | P1 / 미착수, 구현은 B 이관 | B6의 Store·스키마·원자 연산, 공통 API 계약 | 저장소 리뷰·계약·rollback·경쟁·감사 실패·복구 결함 주입. C 작성 테스트는 B 리뷰, T07·T12·T35·T37 |
| C4 · Registry·Scanner | P1 / 미착수 | Scanner·Registry 및 B 저장소 영속화 계약, B의 digest, A의 도구 metadata·변경 알림 | 등록 상태 전이·지문 변경·필수 검사·관리자 활성화, T17 및 연결 후 T10, B 리뷰 |
| C5 · fixture·계수 | P1 / 미착수 | 공통 MCP SDK, C2 안전 경로, A의 host_dispatch 관측 방식 | 합성 stdio·HTTP 서버, 요청별 네 계수, 악성 변형, A 리뷰. 관찰 불가 경로는 명시 |
| C6 · 통합·공격·결함 주입 | P2 / 미착수 | B6 저장소·C3 검증·C4–C5와 A transport·Runner, B Guard·승인·응답 게이트 연결 | 아래 C 담당 T/S 검증, dispatch·wire·부작용 및 미공개 증거, 모듈 담당 리뷰 |
| C6 · replay·지연 | P2 / 미착수 | 공통 실행 경로, 사전 정답·기대 판정·동일 설정·시계 | T23·T24·T28·T34, 모드별 최소 10회 반복, 판정·제한·과잉 제한·완료율·p50/p95 원자료 |
| C6 · CI·약점 기록 | P1부터 준비 / 미착수 | 도구·규칙 설정은 C2와 함께 준비. 전체 검사는 구현된 대상과 의존성 조합 필요 | 지정 검사 순서 실행, 실제 진단·조치·재검사·예외 근거를 약점 관리대장에 기록, B 리뷰 |

### C1 세부 태스크와 상태

- [x] **C1.1 · Store 요구 초안 작성:** [초안](c-store-requirements.md)에 `commit_observation`, `commit_send_outcome`, `consume_and_reserve`의 입력·결과·전용 오류 요구·상태 전이·트랜잭션 조건을 정리했다. 요청·Task·승인 관계, 해시·만료·기대 상태·버전·변경 행 수·rollback 및 U1–U8 검토 항목을 포함한다. B 검토·합의·검증은 미완료다. 관련 I3·I4·I5·I6·I8, T07·T09–T12·T35–T37.
- [ ] **C1.2 · Scanner·Registry 요구 초안:** metadata 입력 범위, 필수·완료·skip·failed 분석기, 위험도·지문 집합, 관리자 활성화, 지문 변경 시 승인 무효화를 정리한다. 관련 I7·I10, T10·T17.
- [ ] **C1.3 · 관측 요구 초안:** `approval_consumed`, `host_dispatch`, `wire_post`/`wire_tools_call`, `receiver_effect`의 위치·요청별 연결·초기화·기대값을 정리한다. 프로토콜 메시지와 관찰 불가 경로를 명시한다. 관련 I1·I2·I3·I8, T01–T03·T08·T12·T14–T16.
- [ ] **C1.4 · 환경·인수 요구 초안:** A와 맞출 SDK 왕복·관측 지점, C의 Scanner API/CLI·오프라인 분석기 확인, 패키징·lock 편집 담당, Fake·실제 어댑터 계약 테스트 인수 기준을 정리한다. 확인되지 않은 버전·시그니처·합의는 미확정으로 남긴다.

먼저 C1.1–C1.4의 요구를 작성하고 B 계약 초안 검토와 A 연결 검토에 사용할 수 있게 만든다. 공통 계약에 대한 동의는 `docs/contracts.md`의 실제 합의 기록으로 구분하며, 이 작업 목록 작성 자체를 합의 완료로 표시하지 않는다.

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

### C3 · B SQLite 리뷰와 독립 검증

SQLite 구현·스키마·마이그레이션·시작 복구·저장소 단위 테스트는 B6로 이관했다. C는 아래 인수 검증을 작성하며 저장소 구현을 복제하지 않는다.

- [ ] B의 단일 writer·WAL·foreign_keys·synchronous FULL·명시적 트랜잭션 설정과 실제 읽기 검증을 리뷰한다.
- [ ] 바인딩 SQL·외래키·UNIQUE·요청/Task/승인 관계·epoch·revision을 독립 계약으로 검증한다. SQL 제한 정적 규칙은 C가 작성한다.
- [ ] READ 관측의 Task·등급·승격 근거·감사·요청 원자성과 커밋 전 공개 0을 확인한다.
- [ ] SEND 성공에서 요청·감사만 갱신되고 Task 보안 상태가 바뀌지 않는지 확인한다.
- [ ] 승인 생성·종결·소비의 원자성, hash·UTC 만료·보안 버전·변경 행 수·중복 소비를 확인한다.
- [ ] 각 쓰기·감사·잠금·COMMIT/ROLLBACK 실패, 취소·프로세스 중단을 주입하고 DB 상태와 오류 결과를 대조한다.
- [ ] 원문·인자·인증정보·승인 사본이 DB·로그에 남지 않는지, 재시작 승인 복원·SEND 재전송이 없는지 검증한다.
- [ ] A 연결 후 승인 소비·host dispatch·wire·receiver effect를 요청별로 대조한다. 관찰하지 못한 경로는 검증 불가로 기록하고 결함 수정은 B에 전달한다.

### C4 · Registry와 Scanner

- [ ] B의 저장소 인터페이스로 등록·지문·검사 메타데이터를 영속화하고, 지문 변경 시 승인 무효화와 단일 writer 경계를 맞춘다.
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
| C 모듈 단위·안전 연산 | S01·S02·S04 및 Registry·Scanner 등 담당 모듈 단위 테스트. S03 저장소 단위는 B, 정적 강제·독립 검증은 C |

T04·T26·T30의 단위 부분은 B, 통합 부분은 C다. T18은 A의 UI 검증이다. B의 단위·성질 테스트를 독립 통합 검증 대신 쓰지 않는다.

## 인수 기준

- [ ] DENY와 승인 전/거절/만료 ASK는 dispatch·wire·부작용 0이다.
- [ ] 승인 1회 및 더블클릭은 consumed·dispatch·wire·부작용이 각각 1이다.
- [ ] 쓰기 응답 유실 후 UNKNOWN_OUTCOME으로 잠기며 새 호출 ID·SDK 재연결로 추가 전송이 없다.
- [ ] READ 커밋 실패·미분류·원시 로그 공격에서 결과·기밀 표지가 모델·UI·로그로 공개되지 않는다.
- [ ] C가 B 저장소를 리뷰하고, B가 C의 위험 연산·등록·Scanner·CI·독립 검증 테스트를 리뷰한다. fixture는 A가 리뷰한다.
- [ ] 측정 원자료·미실행/검증 불가 항목·약점 조치와 재현 방법을 남긴다.

이 역할은 I1–I10의 실제 집행 증거를 책임진다. 약점이 0이라고 보고하려면 해당 범위와 도구로 실제 검사한 근거가 있어야 한다.
