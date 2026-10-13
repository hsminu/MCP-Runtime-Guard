# B6 · SQLite 첫 단계 구현과 검증

기준일 **2026-10-13**, 구현 전 B HEAD `5f8a003`, 브랜치 `feat/b-security-core`.
사용자가 연결·스키마·마이그레이션·Task 생성/조회와 테스트를 요청했다.
현재 변경은 로컬 구현·검증·add 대상이며 commit·push는 별도 승인이 필요하다.
이 문서는 첫 단계 당시의 범위/시험 기록이다. 후속 [상태·판정·ALLOW 예약](b6-sqlite-decisions.md)
추가 당시 5/12개·전체 1,193개를 검증했다. 현재는 [승인·응답 저장](b6-sqlite-lifecycle.md)
추가로 현행 12/12개·전체 1,395개까지 검증했으며 현재 범위와 한계는 해당 기록을 따른다.
최종 기준은 [기술설계](technical-design.md) 11장과 기존 [Store 포트](../src/guard_app/ports.py)다.
공유 모델·포트·서비스·lock·최종 설계 원문을 변경하지 않았다.

## 구현 범위

제품은 [storage/sqlite_store.py](../src/guard_app/storage/sqlite_store.py)의 `SQLiteStore`다.
첫 단계에서는 Store 12개 연산 중 **create_task/load_task 두 개를 구현**했다. 당시 전체 Store Protocol의
구현체로 취급하거나 Guard·승인 서비스에 주입하지 않는다. 없는 연산을 성공하는 stub으로
만들지 않았다. 상태·판정·예약·승인·응답은 후속 기록에 구현했고 시작 복구·영속 호출 순번은 남아 있다.

| 항목 | 현재 동작 |
| --- | --- |
| 연결 수명 | 생성·사용·close를 같은 백엔드 스레드에서 수행. 다른 스레드 사용 거부, close 반복 허용 |
| 설정 | 파일 DB, WAL·foreign_keys=ON·synchronous=FULL, isolation_level=None. 적용 값을 확인 |
| 잠금 대기 | busy_timeout_ms 기본 1000, 생성 시 조정·검증 가능. 측정 최적값/모든 디스크 I/O 상한 아님 |
| SQL 실행 | 제품의 SQL은 이 파일에 모음. 값은 바인딩하며 트랜잭션 안에는 await 없음 |
| Task 생성 | strict 모델·UTC 시각·SQLite 정수 범위·승격 근거 검사. Task와 근거를 BEGIN IMMEDIATE에서 함께 저장 |
| Task 조회 | Task와 근거를 하나의 읽기 트랜잭션에서 조회·도메인 모델로 검증. 없는 Task는 StoreError(STATE_MISSING) |
| 실패 | 고정 코드의 기존 StoreError. SQLite/검증 원문·경로를 오류 체인에 보관하지 않음. 확인된 실패는 rollback |
| COMMIT 오류 | 성공 ACK를 만들지 않고 연결 폐기·추가 사용 거부. 자동 재시도 없음. 새 커밋 불명 타입·Guard 처리는 공동 검토 후 |

호출자가 신뢰 설정으로 선택한 경로와 이미 존재하는 부모 디렉터리를 공급한다.
자동 디렉터리 생성·safe_path 대체 구현·사용자 DB 검색·UI 연결을 추가하지 않았다.
실행 DB는 가상환경/저장소/OneDrive 동기화 경로 밖의 로컬 앱 데이터 경로를 제안한 상태다.
이번 실행은 임시 파일 DB만 사용했고 실제 운영 DB를 설치하거나 생성하지 않았다.

## 스키마와 마이그레이션

11.2절의 **tasks, escalations, requests, approvals, audit_events, registrations,
scan_reports**를 v1 초기 스키마로 만들었다. 등록·검사·승인 테이블 생성은 해당 저장 API의
구현이나 원자 승인 계약 합의를 뜻하지 않는다. 원문용 컬럼을 추가하지 않았다.
AGT 감사 맥락, 요청 epoch/kind/revision 확장과 영속 순번 테이블은 제안 단계로 유지한다.

`PRAGMA user_version=1`, `application_id=0x4D435047`로 버전/앱을 구분한다.
**빈 v0 DB→v1**만 지원하고 테이블 생성·두 태그를 한 트랜잭션으로 커밋한다.
중간 생성 실패는 스키마·태그를 모두 rollback한다. v1 재개방은 정확한 스키마·태그를
대조하며 다른 앱/비어 있지 않은 무버전 DB·미래 버전·구조 변경은 자동 채택/덮어쓰기하지 않는다.
데이터가 있는 임의 옛 DB를 변환하거나 미래 v1→v2 마이그레이션을 구현한 상태는 아니다.

STRICT·CHECK·외래키·UNIQUE로 기본 저장 형식을 제한한다. STRICT는 SQLite 3.37.0 이상이
필요하며 SQLite 자체의 손실 없는 형변환을 모두 금지하는 기능은 아니다.
입력/조회에서는 별도로 strict 도메인 검증을 수행한다.
근거는 [SQLite STRICT 문서](https://sqlite.org/stricttables.html)다.
승격 근거는 순번 증가·등급 상승·앞 근거와의 연결·high-watermark 이하를 검사한다.
이는 상태 전이/security_version의 새 정책을 확정한 것이 아니다. 생성/조회는 입력된
정상 상태·epoch·버전·revision·누적 등급을 바꾸거나 종결 Task를 재개하지 않는다.

## 실행한 검증

Windows x64, Python **3.12.15**, SQLite **3.53.1**, B 기존 requirements.lock 환경.
초기 제한 환경에서는 캐시 폴더 접근/asyncio 실행에 문제가 있어 제한 없는 도구 실행과
저장소 안의 `.test-artifacts/` 임시 경로를 사용했다. 이 디렉터리는 Git에서 제외했다.
테스트 직접 SQL은 스키마 관찰·오염/장애 주입용이며 제품 SQL 지점을 추가하지 않는다.

| 검사 | 실제 결과 |
| --- | --- |
| 전체 B 단위·성질 테스트 | **1,045 passed, 11.40초**. 기존 971개+새 SQLite 74개 |
| Ruff check | 전체 src/tests 통과 |
| Ruff format check | Python 48개 파일 통과 |
| strict mypy | 소스 26개 파일 오류 없음 |
| pip check | 의존성 오류 없음 |

새 단위 검증은 설정·v0 생성/실패 rollback·v1 재개방·다른/미래/변조 DB 거부,
Task/승격 근거 보존·중복 ID·UTC/strict 입력·SQL 특수문자·DB 오염·관계 제약·잠금,
한 읽기 snapshot·다른 스레드/종료 후 사용 거부·실행 전 취소를 포함한다.
승격 근거 삽입 실패에서 Task도 rollback했고, COMMIT 성공 후 ACK 오류 주입에서는
연결을 폐기하며 DB에 Task가 한 번만 남는 것을 확인했다.
관련 기준은 **I4/I6/I9, S03 단위 부분, T07/T11/T19의 저장소 단위 기반**이다.
이 결과를 T07/T11/T19 전체 통과나 C 독립 결함/공개/wire 인수로 표시하지 않는다.

재현할 때 임시 경로의 부모를 먼저 만들고 지정한 --basetemp 경로는 테스트 전용으로 사용한다.
pytest가 이 경로의 내용을 정리하므로 사용자 파일을 넣지 않는다.

```powershell
New-Item -ItemType Directory -Force .test-artifacts | Out-Null
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp .test-artifacts/sqlite-stage1-full
& '.\.venv\Scripts\python.exe' -m ruff check src tests
& '.\.venv\Scripts\python.exe' -m ruff format --check src tests
& '.\.venv\Scripts\python.exe' -m mypy src/guard_app
& '.\.venv\Scripts\python.exe' -m pip check
```

## 다음 단계와 리뷰

1. 기존 포트의 Task 상태 전이·판정/감사·ALLOW 예약 구현과 단위 검증.
2. 기존 승인·소비·READ/SEND 커밋 구현. 기존 두 승인 저장 호출의 틈을 원자성 완료로 표시하지 않음.
3. [공동 요청](ac-contract-review-request.md)의 실제 합의 후 open/finish·오류·복구·Registry·AGT 계약과 호출부 연결.
4. C 저장소 리뷰·독립 실제 Store/Fake·프로세스 중단/결함 검증, A 시작/종료·UI·실제 계수 연결.

현재 사용자 권한은 이 첫 단계 구현·테스트·add다. 기존 문서의 push 승인을 이번 코드 push에
재사용하지 않는다. 새 API 공동 합의와 C 리뷰·정적 강제 규칙·독립 인수는 미완료다.
