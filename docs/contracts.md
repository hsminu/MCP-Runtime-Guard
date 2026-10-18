# 공통 계약

[개발문서](technical-design.md)의 계약 요구 중 확정한 결정과 아직 정할 항목을 모은다. `domain/*`와 `ports.py`는 B가 정의하고, 바꾸려면 A·B·C가 합의해야 한다. 사용자가 이 문서에 기록한 결정만 확정으로 본다. 개발문서는 현재 기준서이므로, 결정을 여기에 적을 때 같은 변경에서 개발문서의 해당 절도 고친다(2026-10-17 사용자 결정). 이전 검토·구현 경과는 [archive](archive/)에 있다.

## 사용자 결정

| 날짜 | 결정 | 범위·영향 |
| --- | --- | --- |
| 2026-10-12 | `storage/*`, SQLite 스키마·마이그레이션·시작 복구·영속 순번·감사 저장과 그 단위 테스트는 B가 맡는다 | 저장소 리뷰·독립 계약·통합·결함 주입 검증은 C. 개발문서 0.5·2.1절의 C 표기를 대체한다 |
| 2026-10-13 | B6의 새 API·DTO·오류·버전·복구 규칙은 A·C 검토 후 확정한다 | 기존 Store 12개 연산의 의미를 유지하는 구현은 먼저 진행할 수 있다 |
| 2026-10-13 | Scanner LLM은 `openai/gpt-6-luna`, `reasoning_effort=low` | C Scanner에만 적용한다. A Runner 모델과 무관하다 |
| 2026-10-13 | Scanner는 LLM을 기본으로 하고, 위험을 발견해도 경고만 한다 | 자동 등록 차단·실행 중단 조건으로 쓰지 않는다. Finding 코드·심각도는 보존한다. 개발문서 10.2절의 "HIGH 이상 PENDING"보다 우선한다. Guard 경계·등급·승인 판정은 그대로다 |
| 2026-10-13 | 검사 실패·미완료(키 누락, 시간 초과, API·응답 오류)도 경고 후 사용을 계속한다 | 실패를 findings 0개·완료·안전으로 바꾸지 않고 고정 오류 코드를 남긴다. Guard·저장소·도구 실행의 fail-closed는 그대로다 |
| 2026-10-13 | YARA를 LLM과 함께 돌린다 | 같은 전체 정의를 각각 전달한다. 서로의 finding을 지우거나 판단 일치를 요구하지 않는다. 분석기별 완료·실패를 따로 기록한다 |
| 2026-10-13 | 도구 정의 하나의 검사 입력 상한은 기본 64KiB, 설정 가능 | 측정 기준 1. 초과하면 자르지 않고 `SCAN_METADATA_TOO_LARGE`와 미완료 경고를 남긴다 |
| 2026-10-13 | `tools/list` 한 페이지의 수신 상한은 기본 1MiB, 설정 가능 | 측정 기준 2. A transport가 파싱 전에 제한한다(미구현). 초과하면 목록 수집 미완료 경고 |
| 2026-10-13 | 서버 하나의 목록 조회 한 번의 누적 수신 상한은 기본 8MiB, 설정 가능 | 같은 조회의 모든 페이지를 합산하고 페이지마다 초기화하지 않는다. A transport가 제한한다(미구현). 초과하면 전체 목록 미완료 경고 |
| 2026-10-15 | AGT를 제거하고 접근 규칙은 자체 구현한다 | 범위는 (서버, 도구) 정확 일치 허용/거부 목록, 기본 deny, 신뢰 설정에서만 로드, 규칙 변경 시 `policy_version` 증가와 대기 승인 무효까지다. 인자 내용 검사·호출 횟수 제한·조건식·원격 정책·서명 규칙은 범위 밖이다. `AccessChecker` 포트와 Guard 합성(가장 엄격한 판정, 거부권만, 오류는 DENY)은 그대로다. 판정은 B가 `core`의 순수 함수로, 연결은 A가, 차단 계수 검증은 C가 맡는다. 개발문서 10.3절·ADR-3의 AGT를 대체한다 |
| 2026-10-15 | CI 2단계(import-linter·Bandit·Semgrep·pip-audit)를 재개하고 contract 테스트를 CI에 넣는다 | AGT 제거 후 진행한다. AGT의 `cryptography<50` 제한이 없어지므로 pip-audit 예외를 두지 않는다 |
| 2026-10-15 | Scanner LLM은 Luna low를 유지한다 | Sol 등 상위 모델은 같은 자료로 비교 실험한 뒤 다시 정한다 |
| 2026-10-15 | Scanner 위험도는 SDK 값을 그대로 쓰고, LLM 자체 평가(`overall_risk`)는 참고 정보로 따로 보존한다 | SDK는 LLM이 지목한 위협을 모두 HIGH로 고정하고 모델의 자체 평가를 버린다. 자체 평가는 표시만 하고 판정·정렬·위험도 하향에 쓰지 않는다. 검사 대상 도구 설명이 이 값을 조작할 수 있기 때문이다. ScanReport 필드 형식은 미결이다 |
| 2026-10-15 | Scanner 경고는 서버 등록 화면과 도구 호출 승인 창에 표시하고, 모델 설명(`threat_summary`)도 함께 보여준다 | 등록 화면은 도구별 위험도·출처(LLM/YARA)·LLM 자체 평가·미완료를 보여준다. 승인 창은 호출하는 도구의 경고를 보여주며 승인을 막지 않는다. 모델 설명은 신뢰하지 않는 텍스트로 다룬다: 일반 텍스트만, 링크·HTML 없음, 300자 제한, "검증되지 않은 내용" 표시, 로그 미기록 |
| 2026-10-15 | Scanner 경고가 있는 도구의 ALLOW 호출을 ASK로 올리지 않는다 | 경고 검토의 관문은 등록 단계의 사용자 활성화다. Guard 판정 규칙은 바꾸지 않는다 |
| 2026-10-15 | Scanner LLM API 키는 사용자가 각자 준비하고 Windows 자격증명 관리자(DPAPI)에 저장한다 | 제품 경로는 자격증명 관리자만 읽고 `.env`는 개발·실험용이다. 키는 앱 화면의 가려진 입력칸으로 받고 끝 4자리만 보여준다. 앱 전용 OpenAI 프로젝트 키와 사용 한도를 안내한다. 키를 우리 서버로 보내지 않는다. 저장·로딩은 C(`secrets.py`), 입력 화면은 A |
| 2026-10-15 | SQLite DB의 의도적 변조는 위협 모델 밖이고, 손상·불일치는 fail-closed로 처리한다 | DB를 고칠 수 있는 같은 사용자 권한의 공격자는 Guard 자체를 우회할 수 있으므로 행 서명·HMAC은 두지 않는다. DB 내용만으로 실행 권한이 생기지 않는다(승인 원문은 메모리에만, 재시작 후 승인·옛 Task 미복원). 관계가 깨진 행·모르는 상태값 등은 시작 점검에서 멈춘다 |
| 2026-10-15 | 시작 복구는 DB의 등급·상태를 그대로 믿지 않고 감사 기록과 대조한다 | 복구할 Task의 등급이 감사 기록의 승격 이력보다 낮거나 미분류 표시가 사라졌으면 복구하지 않고 멈춘다. 시작 복구(B6.8·REC 요청)의 조건이다 |
| 2026-10-15 | Registry는 DB의 ACTIVE 상태만으로 실행하지 않고, 연결할 때마다 실제 `tools/list` 지문과 등록 지문을 대조한다 | 개발문서 10.1절과 worker의 연결 직후 지문 검사를 따른다. 다르면 사람 개입 없이 즉시 PENDING으로 내리고 기존 승인을 무효로 한다(I10). Registry(C4.10)의 조건이다 |
| 2026-10-15 | Scanner 운영 상한은 C 운영 설정 추천안 v1의 값을 기본값(설정 가능)으로 확정한다 | 서버별 도구 256개·목록 32페이지·Tool JSON 중첩 64단계, 초기화 10초·페이지 10초·목록 수집 30초, 서버 검사 작업 10분, 도구별 LLM 60초·YARA 5초(native), 앱 전체 LLM 동시 4개·YARA worker 1개·서버 검사 1건·대기 8건, 자동 재시도 0회, 주기적 재검사 끔, 결과 캐시 재사용 끔. 초과하면 자르지 않고 해당 조회·작업을 미완료 경고로 끝낸다. 목록 수신 제한은 A transport, 검사 예산은 C. [추천안](archive/tasks/c-scanner-operating-proposal.md) |
| 2026-10-15 | 도구 지문은 정의 전체(Tool 객체의 모든 필드)로 계산한다 | 개발문서 10.1절의 4필드(name·description·inputSchema·annotations)를 대체한다. 검사 범위(C-CISCO-03)와 지문 범위를 같게 한다. 직렬화는 키 정렬·공백 없는 UTF-8 JSON, 배열 순서 변경도 변경으로 본다. float는 거부하지 않고 정해진 표기로 처리한다(B digest 리뷰). 실측: 공개 정의 189개의 전체 해시가 10-13·10-15 두 번 수집에서 모두 같았다. 자주 바뀌는 필드가 실제로 나오면 근거와 함께 제외 목록을 정한다 |
| 2026-10-15 | 실제 모델은 Codex를 하네스로 쓴다. Task마다 고정 버전 `codex app-server`(stdio JSON-RPC)를 띄우고 Guard 도구만 `dynamicTools`로 등록해, 모델의 도구 호출을 `item/tool/call`로 받아 `Guard.submit`으로 보낸다. Codex 자체 도구는 목록에서 빼고, 시작할 때마다 실제 상태를 시험해 확인하지 못하면 시작을 거부한다(T22) | Codex 자체 도구가 남으면 Guard 밖 읽기로 Task 등급이 틀어지고(파일 등급 규칙도 Guard가 처리한 읽기에만 적용된다) 자체 셸·MCP는 Guard를 우회한다. **L1 제거:** 앱 전용 CODEX_HOME의 config를 앱 시작 때 한 번 쓰고 Task 시작마다 해시로 확인한다(2026-10-18 변경, 이전: Task마다 새로 씀). 셸·unified exec·이미지 보기·goals·하위 agent·apps·plugins·이미지 생성·브라우저·computer use·tool suggest·skill search·code mode·hooks·memories를 끄고 `web_search="disabled"`, `project_root_markers=[]`, `project_doc_max_bytes=0`, 업데이트 확인·analytics·feedback·history를 끈다. thread/start는 `environments=[]`, `ephemeral`, `approvalsReviewer="user"`. 모델 정보는 `model_catalog_json`으로 고정한다(도구 직접 호출, 하위 agent 없음, 셸 disabled, skill·apps·plugin 안내 끔). ChatGPT 로그인 때 서버 모델 정보는 code mode 전용·하위 agent를 켜서 Guard 도구를 쓸 수 없었다. 기본 프롬프트는 `baseInstructions`로 우리 프롬프트로 바꾼다(보안 장치 아님, I7). **연결:** 도구 이름은 `서버이름.도구이름`, `deferLoading` 미사용. DENY·거절·만료는 `success:false`와 고정 문구. dynamicTools가 없어지면 Guard MCP 서버로 바꾼다. **고정:** codex.exe 버전·SHA-256(설치 스크립트가 npm에서 앱 전용 폴더로 받아 확인), 모델 정보 파일·해시(허용 모델 `gpt-6.1-sol`), config. 바꿀 때마다 C가 L2 통과 뒤 PR. **L2 시작 시험:** `configRequirements/read`가 null, `config/read`의 층·값 대조, 같은 exe·config·모델 정보로 가짜 모델 서버에 붙여 도구 목록 정확 대조, 금지 도구 7종 강제 호출 거부, 한 응답의 두 호출 순차 도착, 요청 프롬프트 확인. 앱 시작·버전 변경·CI에서 돈다. **L3 감시:** 허용 항목(userMessage·agentMessage·reasoning·contextCompaction·Guard 도구 호출) 밖의 항목이나 승인 요청은 거절하고 `turn/interrupt` 뒤 Task를 정지한다. `request_user_input`은 화면 없이 빈 응답으로 답하고 감사에는 요청 사실만 남긴다. **승인 대기:** ASK 5분 만료 등 기존 규칙. Codex는 대기 상한이 없어 Runner가 호출별 감시 상한(5분+MCP 실행 상한+여유)을 넘기면 interrupt하고 Task를 정지한다. SEND 전송 뒤면 UNKNOWN_OUTCOME, 재전송 없음. 호출은 받은 순서대로 하나씩 처리한다. **로그인:** ChatGPT 기기 코드 또는 API 키, Windows 자격증명 관리자(keyring) 저장, Task 시작 때 `account/read`, 사용량 한도면 정지. Scanner는 API 키를 따로 쓴다. Codex 내장 기기 코드 로그인은 Sign in with ChatGPT 약관의 앱 이름·사칭 조항과 맞지 않을 수 있으나 그대로 쓴다(2026-10-17 사용자 결정, C-CODEX-05). **원문:** Codex 로그 DB가 도구 결과 원문을 저장하고 끌 수 없어 `sqlite_home`을 Task 전용 폴더로 두고 Codex 종료 뒤·앱 시작 때 지운다(Task 종료 뒤 원문 비잔존). **L4:** codex.exe OS 격리는 하지 않는다(해시 고정 신뢰 구성요소). 한계는 [대장](weakness_register.md#발견-항목과-조치) C-CODEX-01–05. 세션 관리자·로그인 UI·프롬프트 문구는 A, Guard 도구 입구 계약은 B, L1 설정·모델 정보·L2·정리 검증은 C |
| 2026-10-15 | 로컬 파일 접근은 우리가 만든 읽기 전용 파일 MCP 서버(stdio)를 Guard에 등록해서만 허용한다. 모델이 보고하는 출처는 등급 판정에 쓰지 않는다 | 개발문서 4.2·14.2절의 Local Document MCP를 제품 서버로 키운다. 공식 `server-filesystem`은 쓰지 않는다(쓰기·이동 도구 포함, 결과에 `resource_id` 없음). 도구는 폴더 목록과 파일 읽기만 두고, 둘 다 READ로 대상 경로의 등급을 따른다. 사용자가 등록할 때 루트 폴더와 하위 폴더별 등급을 정하고(세그먼트 접두어 출처 규칙), 어느 규칙에도 맞지 않으면 Unclassified로 Task를 멈춘다. 서버는 `safe_path`로 경로를 정규화하고 결과 `structuredContent.resource_id`로 실제 읽은 경로를 보고한다. Guard는 요청 인자로 계산한 `expected_resource_id`와 대조하고 다르면 공개하지 않는다(T33). UTF-8 텍스트만 돌려주고 응답 게이트 크기 상한을 넘으면 정지한다. 이 서버가 유일한 파일 경로가 되려면 위 Codex 자체 도구 차단이 필요하다. 서버·`safe_path`·검증은 C, 경로 대조 연결(B3.3)은 B, 등록 화면의 폴더별 등급 입력은 A |
| 2026-10-17 | 제품은 개인 사용자 한 명이 자기 PC에서 쓰는 앱으로 정한다. 서버 등록·폴더 등급·정책 수정·승인을 모두 같은 사용자가 한다 | 관리자·팀원 같은 역할 구분, 계정, 원격 관리는 두지 않는다. 사용자 본인은 신뢰하고, 사용자의 의도적 정책 완화는 위협 모델 밖이다. 등급 이름(`INTERNAL` 등)과 `trust_tier` 값은 코드 열거형이라 그대로 두고 개인 자료 기준으로 설명한다. 판정 규칙·승인 프로토콜은 바뀌지 않는다. 개발문서 0장·8.5절 |
| 2026-10-17 | 사용자 화면은 A의 GUI 하나로 하고 Codex TUI는 쓰지 않는다 | Codex TUI(`codex --remote`)는 app-server에 직접 붙는 별도 클라이언트라 세션 관리자를 건너뛴다. 그러면 Guard 도구 등록·`item/tool/call` 중계·L3 감시가 빠지고, 클라이언트가 설정과 turn 권한을 바꿀 수 있다. 세션 관리자는 화면과 무관하게 필요하다. 터미널 화면이 필요하면 세션 관리자 위에 붙인다. 개발문서 10.4절·ADR-15 |
| 2026-10-17 | 개발문서를 현재 기준서로 한다. 결정은 이 문서에 기록하고 같은 변경에서 개발문서 해당 절을 고친다 | 이전 규칙("개발문서 원본은 고치지 않는다")을 대체한다. AGT 제거·Scanner 경고만·지문 전체·자격증명 관리자·하네스 결정에 밀린 개발문서 절을 이번에 맞췄다 |
| 2026-10-17 | 하네스 연결의 빈 곳 다섯 개를 정한다 | (1) 세션 관리자가 `namespace`와 `tool`을 `__`로 이어 `tool_ref`를 만들고, 서버 ID의 `__`와 끝의 `_`는 등록에서 거부한다(도구 이름은 제한 없음). 거부는 Registry(C)가 하고 등록 화면(A)은 입력 즉시 안내한다. Registry는 B의 `valid_server_id`를 그대로 쓰고, 등록 화면은 bridge가 넘겨주는 같은 검사 결과를 쓴다(2026-10-18 정정). Catalog 검사는 마지막 방어로 남긴다. (2) Guard는 `Pending` 요청마다 최종 결과 알림을 한 번 내고 세션 관리자가 이를 기다려 답한다(B 알림 계약, A 대기). (3) `Withheld`·`OutcomeUnknown`은 고정 문구로 답하고 바로 interrupt·종료한다. (4) codex.exe는 `security.launcher`로만 띄우고 실행할 때마다 해시를 대조한다. (5) 불변식 I11(모델이 쓸 수 있는 도구는 Guard 도구뿐)과 T39–T45를 추가하고 I9에 Codex 로그 DB 예외를 적는다. 개발문서 0.4·8.3·10.4·14.3절·부록 B |
| 2026-10-17 | 기존 AGT 코드 제거는 B가 맡는다 | 2026-10-15 AGT 제거 결정의 "연결은 A" 중 코드 제거 부분을 대체한다. 대상은 `adapters/agt.py`·`agt_real.py`, AGT 의존성, `--agt-mode`·`--agt-policy` 옵션, `__main__`·bridge·ui의 AGT 표시, 관련 테스트·예제, README의 AGT 설명이다. A 소유 파일(`__main__`, `services/bridge.py`, `ui/*`, `examples`)을 고친 부분은 A가 리뷰한다. 자체 접근 규칙을 Guard에 연결하는 일은 A 그대로다. 개발문서 10.3절 |
| 2026-10-18 | 모델 경로는 Codex 하네스 하나만 둔다. OpenAI API를 직접 부르는 모델 경로는 두지 않는다 | A64의 직접 API 어댑터(`adapters/openai_model.py`)와 새 세션의 API 선택지는 A가 제거한다. `ports.py`의 `ModelClient` 포트와 `adapters/model_client.py`·기존 Runner 모델 루프도 제거하고, A가 세션 관리자 인터페이스를 제안해 3인 합의로 대체한다(#20 5번). API 키 로그인은 Codex 안의 로그인 방식으로만 지원한다 |
| 2026-10-18 | Codex 고정 버전은 0.163.0이다(2026-10-15 배포, npm 확인) | 실험·약점 기록(C-CODEX-01–04)과 토큰 갱신 소스 확인은 0.162.1 기준이라, C가 0.163.0에서 L1 설정 키·L2 시험·로그 DB·인증 갱신 동작을 다시 확인한다. 바꿀 때는 C가 L2 통과 뒤 PR로 올린다(#21 1번) |
| 2026-10-18 | 여러 Task를 동시에 실행할 수 있다 | Task마다 codex 프로세스를 따로 띄운다. config는 모든 Task에 같은 내용으로 앱 시작 때 한 번 쓰고, Task 시작마다 해시로 확인만 한다. `sqlite_home` 등 Task별 값은 실행 인자로 넘긴다. Codex 0.162.1 소스 기준으로 ChatGPT 토큰 갱신은 프로세스 사이 잠금이 없어(`login/src/auth/manager.rs:2846`, 0.163.0 재확인 필요) 두 Task가 거의 동시에 갱신하면 한쪽이 "refresh token already used"로 실패할 수 있다. 이때 그 Task만 정지하고 새 Task로 다시 시작하라고 안내한다. 드물고 fail-closed라 받아들인다. 검증은 T46(C)(#19 3번) |
| 2026-10-18 | 하네스 연결의 담당 세부를 정한다 | L2 결과 입구는 A가 주입형 포트로 열고 C가 구현을 채운다. 입구가 없으면 시작을 거부하고 개발용 건너뛰기는 두지 않는다(#21 3번). Codex 자격증명은 Codex가 직접 keyring에 저장하고 `secrets.py`는 관여하지 않는다. CODEX_HOME에 인증 파일을 따로 쓰는지는 C가 T43에서 확인한다(#21 8번) |
| 2026-10-18 | 남은 담당을 정한다 | 설정 파일·Profile 로딩: B가 읽어 Catalog에 넣고, C가 경로·실행 파일을 검증하고, A가 앱 시작에 연결한다(#19 1번). 정책 변경: 화면은 A, 변경 명령과 Task 정지 처리는 B(#19 4번). 모델에 돌려줄 고정 문구표: B 작성, A 검토(#19 5번). C-CODEX-05 약관 재확인: C가 최종 제출 전에 한다(#19 8번). `pyproject.toml`·lock: ENV-01 참고(#19 2번) |
| 2026-10-18 | B 미결 요청 세 개를 정한다 | NOTIFY-01: 만료 확정은 Guard(B) 타이머, 요청마다 정확히 한 번 알림. ACCESS-01: 동기 `AccessChecker`, AGT용 대기 장치 제거(3인 합의). FP-01: 지문은 RFC 8785(JCS), B `digest.py`의 별도 함수. 세부는 미결 요청 표. 개발문서 3.4·8.1·8.3·10.1·10.3절 |

측정 기준

1. 도구 정의: 설명·입출력 schema·annotations·추가 필드를 포함한 Tool 객체 전체를 키 정렬·공백 없는 JSON으로 직렬화한 UTF-8 바이트.
2. 목록 수신: 프레이밍을 제거하고 HTTP 압축을 푼 뒤의 응답 JSON-RPC 메시지 UTF-8 바이트(공백·envelope 포함). stdio는 해당 메시지, HTTP는 JSON 응답 또는 SSE 이벤트 payload에 적용한다. 파싱 후 재직렬화한 크기로 대신하지 않는다. 오래 유지되는 SSE 스트림 전체에는 페이지 상한을 적용하지 않는다.
3. 상한 초과나 수집 실패를 도구 0개·검사 완료·안전으로 취급하지 않는다. 원문·인증정보를 경고에 넣지 않고, 수집되지 않은 정의를 추정해서 호출하지 않는다.

Scanner 결과는 아직 B ScanReport에 연결하지 않았다. `ScanReport` 필드 확장은 SCAN-01이다. 필수 분석기·완료/생략/실패·지문·severity 번역 계약은 미결이다. 서버별 도구 수·페이지 수·조회 시간·검사 동시성 상한은 2026-10-15 Scanner 운영 상한 결정으로 정했다.

## 합의할 계약

| 계약 | 정의 담당 / 구현 담당 | 필수 의미 | 근거 |
| --- | --- | --- | --- |
| domain 모델 | B / B | 신뢰하지 않는 `ProposedCall`과 신뢰 해석 `ResolvedRequest` 분리, frozen·strict·extra forbid | 3장 |
| Guard 입구와 결과 | B / B | 일반 도구 실행은 `submit`; 결과 `Denied`, `Pending`, `Released`, `Withheld`, `OutcomeUnknown`. `Pending` 요청은 `request_id`별 최종 결과 알림을 정확히 한 번 낸다 | 0.5·7장·8.3절 |
| Transport | B / A | 서버별 세션 소유 worker, 명령 future의 성공·예외 확정, 원시 결과 격리, 쓰기 자동 재전송 금지 | 9장 |
| AccessChecker | B / A | 요청·신뢰 맥락 입력, `AccessResult` 반환, 거절·오류는 DENY, 형식 불명·시간초과는 `error=True` | 10.3절 |
| Scanner | B / C | 정의 metadata만 입력, 필수·완료·skip·failed 분석기와 지문 집합을 명시 | 10.2절 |
| Store | B / B, C 독립 검증 | 상태·감사·예약·승인 조회와 아래 세 원자적 연산 | 8.3·11장, 2026-10-12 결정 |
| Codex 세션 관리자 | B·C / A | ACTIVE 등록 도구만 `dynamicTools`로 등록, `item/tool/call`을 `ProposedCall`로만 변환, Task마다 새 app-server, L1 설정·L2 통과 전 시작 금지 | 6.5·10.4절, 2026-10-15 하네스 결정 |
| Registry 조회 | B와 C 합의 / C | status·fingerprint는 저장된 등록 레코드가 근거, 변경 시 PENDING·승인 무효 | 10.1절 |
| UI 명령·이벤트 | B와 A·C 합의 / A | 불변 메시지, 실행·제어 큐 분리, 상태 확정은 백엔드, 원시 결과 공개 금지 | 8.5·12장 |
| 계수와 fixture | A·B·C 합의 / C 주도 | 승인 소비·host dispatch·wire tools/call·receiver effect를 요청과 연결 | 9.4·14장 |

## 저장소 원자성

- [ ] `commit_observation`: READ 등급 병합, security_version, 승격 근거, 감사, 요청 상태가 한 트랜잭션에서 확정된다.
- [ ] `commit_send_outcome`: SEND 요청 상태와 감사만 갱신하며 READ 등급 병합을 호출하지 않는다.
- [ ] `consume_and_reserve`: 승인 CONSUMED, 요청 DISPATCHING, Task RUNNING 전이가 한 트랜잭션이다. 해시·만료·상태·security_version과 각 변경 행 수를 검사한다.
- [ ] 연결은 백엔드 단일 writer가 소유하며 WAL·foreign_keys·synchronous FULL·명시적 트랜잭션을 사용한다.
- [ ] 저장소 오류·상태 충돌·감사 기록 실패가 도메인 결과로 바뀌는 경계를 합의한다. 원문을 오류 메시지로 공개하지 않는다.

## 상태·승인·응답

- [ ] `policy_version`, `security_version`, `row_revision`, 도구 `fingerprint`의 용도를 구분한다.
- [ ] 새 Task는 보안 상태뿐 아니라 모델 대화·캐시·클라이언트 세션 상태도 분리한다.
- [ ] 승인 사본은 요청·인자 해시·등급·버전·승인자·만료를 고정한다. 원문은 메모리에만 둔다.
- [ ] 승인 재평가의 ASK·해시 일치와 원자적 소비를 검사한다. 재평가부터 응답 커밋까지 동일 Task lock을 유지한다.
- [ ] 승인 대기 중 일반 submit을 거부하지만 제어 큐는 처리한다.
- [ ] 결과 없음인 READ 전송 실패와, 수신·변환 후 예상하지 못한 예외를 구분한다. 후자는 HALTED·미공개다.
- [ ] SEND 결과 불명은 UNKNOWN_OUTCOME으로 잠그고 새로운 모델 호출 ID로도 재전송하지 않는다.
- [ ] READ 출처 불명·resource_id 불일치·지원하지 않는 형식·커밋 실패 시 미공개 처리한다.
- [ ] monotonic 만료와 DB UTC 만료를 함께 확인하고 재시작 때 승인을 복원하지 않는다.

## 착수 때 확정할 항목

- [ ] `ports.py`의 전체 메서드·인자·반환형·오류 타입과 Guard 결과 모델을 B가 제안하고 A·C가 확인한다.
- [ ] UI 명령·이벤트 payload, 제어 주체, reason_code 표시를 확정한다. 모델에 돌려줄 고정 문구표는 B가 만들고 A가 검토한다(2026-10-18 결정).
- [ ] Python 3.12에서 동일 MCP SDK로 stdio·HTTP 왕복을 검증하고 Scanner 호환성을 기록한다.
- [ ] fixture의 `wire_tools_call`과 문서의 `wire_post`가 같은 tools/call 계수를 뜻함을 계약 테스트에서 확인한다. 프로토콜 메시지는 별도 계수다.
- [ ] 위 계약을 실제·Fake 어댑터에 동일하게 적용할 C의 계약 테스트 인수 기준을 확정한다.

## 미결 요청

질문과 제안의 세부는 [B6 계약 변경안](archive/b6-contract-proposal.md), [A·C 공동 검토 요청](archive/ac-contract-review-request.md), [영속 순번·복구 설계안](archive/b6-sequence-recovery-design.md)에 있다. 회신을 받으면 상태를 고치고, 확정한 내용은 아래 합의 기록에 남긴다. SEQ·REC에 대한 A 회신(2026-10-13)은 [A 회신](archive/tasks/a-seq-rec-reply-2026-10-13.md)에 있다.

| ID | 대상 | 상태 |
| --- | --- | --- |
| B6-01 | 승인 생성·종결 원자 API/DTO | 요청 초안 |
| B6-02 | 상태·보안 버전·객체 revision·요청 관계 | 요청 초안 |
| B6-03 | 내구성 ACK·충돌·커밋 불명·만료·취소 | 요청 초안 |
| B6-04 | SEND 실패 상태·시작 정리·recovery epoch | 요청 초안 |
| B6-05 | 권위 조회·사람 상세·명령 접수/적용 결과 | 요청 초안 |
| B6-06 | Registry 등록·지문 영속화·단일 writer | 요청 초안 |
| AGT-01 | 역할·관리 경로·규칙/버전·Task/승인 결속 | 2026-10-15 결정으로 대체: AGT 제거, 자체 규칙 목록·규칙 버전 결속 |
| AGT-02 | mode/규칙 버전 감사·migration·실패 | AGT 제거로 mode 감사는 불필요. 규칙 버전 감사만 남음(요청 초안) |
| AGT-03 | 대기/실행·worker 교체·퇴역 예산·실제 종료 | AGT 제거로 폐기. 자체 규칙은 같은 프로세스의 동기 판정이라 worker가 없다 |
| AGT-04 | AGT 제거에 따른 reason code(`AGT_DENY`·`AGT_ERROR`)와 domain 정리 | 요청 초안. `domain/*` 변경이라 3인 합의 필요 |
| SCHEMA-01 | 전체 스키마 검증과 C launcher 연결 | 요청 초안. B 방향은 정규식 기능을 유지하고 전체 검증을 종료 가능한 프로세스에서 실행하는 것이다([연결안](archive/b-schema-validation-proposal.md)) |
| ENV-01 | 통합 버전·lock·검사 도구·Scanner 조합 | 2026-10-18 결정으로 편집 절차 확정: 의존성은 각 담당이 main PR로 제안, C가 CI로 확인, 사용자가 병합. 버전 고정 묶음(Codex 설치 스크립트 포함)은 C. 조합 자체의 합의 기록은 없다 |
| SEQ-01 | Task ID 생애 계수·초기값·공백/커밋 배정 회수 금지 | 설계안 작성. A 동의, C 미회신 |
| SEQ-02 | 영속 콜백/Store 입구·epoch 인자·오류/ACK·할당 감사 | 설계안 작성. A 조건부 동의(할당 COMMIT 불명을 Store 조회로 노출, 고정 실패 Reason), C 미회신 |
| SEQ-03 | 계수 구조·schema 이관·legacy/삭제/백업 보장 범위 | 설계안 작성. A 동의, 이관 시점(자동/명시)·백업 책임을 B에 질문, C 미회신 |
| REC-01 | 이전 실행자 종료·DB 단일 소유·시작 gate/준비 ACK | 설계안 작성. A 동의(수정 요청: DB 단일 소유 잠금은 C 안전 연산으로), C 미회신 |
| REC-02 | 요청/승인/Task 상태 표·split 중단·v1 kind 유실·U8 충돌 | 설계안 작성. A 부분 동의, v1 kind 유실 행은 게이트 닫기 대신 격리 대안 선호, C 미회신 |
| REC-03 | epoch 증가와 새 Task의 영속 관계·과거 epoch 보존 | 설계안 작성. A는 B안(옛 epoch 보존·새 Task 관계 기록) 선호, C 미회신 |
| REC-04 | 버전·반복 적용·run 표식·UTC/감사·RecoveryReport | 설계안 작성. A 동의, RecoveryReport 필드 후보 제시, C 미회신 |
| REC-05 | 전체 복구 TX·불명 COMMIT·예산·백업/미확정 메모리 | 설계안 작성. A 동의, 복구 시간 예산 질문, C 미회신 |
| FP-01 | 도구 지문 정규화: float 표기 규칙, 승인 해시(`canonical_json`)와 분리한 함수의 위치·담당 | 2026-10-18 결정: RFC 8785(JCS) 표기, B가 `digest.py`에 승인 해시와 분리한 지문 함수를 둔다. C Registry·Scanner는 그 함수를 쓴다 |
| NOTIFY-01 | `Pending` 최종 결과 알림: 형식, 정확히 한 번, 아무 조작 없이 만료 시각이 지났을 때의 확정 주체, Runner 감시 상한과의 순서, 앱 종료 시 처리 | 2026-10-18 결정: 만료 확정은 Guard(B) 타이머. 거절·만료·취소·무효·지문 무효화·승인 실행 결과를 `request_id`마다 정확히 한 번 알리고 bridge 결과와 중복 전달하지 않는다(T45). 세션 관리자 감시 상한은 알림이 없을 때의 마지막 안전장치. 형식·앱 종료 시 처리는 B 계약 초안 |
| SCAN-01 | `ScanReport` 확장: `overall_risk`(참고)·`threat_summary`(300자) 필드 형식 | 요청 초안. 2026-10-15 Scanner 결정의 후속(B 정의, C 번역·C4.9) |
| ACCESS-01 | 접근 규칙 연결: `AccessChecker` 포트의 비동기·시간 제한 유지 여부, 구현체 형태(A가 B 판정 함수를 감싸는지), 규칙 설정의 `policy_version` 반영·계산 주체 | 2026-10-18 결정: `AccessChecker`를 동기 포트로 바꾸고 Guard의 AGT용 대기 장치(시간 제한·동시 수·정리·미완료 추적)를 제거한다. 판정기 안 I/O 금지, 예외는 `fail_closed`로 DENY, 형식 불명은 `error=True`. `policy_version`은 설정 로딩 담당 B가 계산. `ports.py` 변경이라 AGT-04와 함께 3인 합의 |
| STOP-01 | 세션 관리자용 Task 정지 입구: 감시 상한 초과(T42)·L3 허용 밖 항목(T41)·로그인 실패·사용량 한도·인증 갱신 충돌의 정지 API, 사유 코드, 감사(`request_user_input`은 사실만 기록) | 요청 초안. 사람용 `HumanControl.cancel`과 분리(8.5절). B 정의, A 사용(#20 2번) |
| DESC-01 | Catalog의 검토된 설명 필드: 서버 원본 설명 대신 사용자가 검토한 설명을 도구 목록에 쓰는 옵션(10.4절) | 요청 초안. 현재 `Catalog.tool_views`는 빈 설명 고정. B 정의, A 등록 화면 입력(#20 3번) |

## 합의 기록

A·B·C의 실제 확인을 받은 것만 적는다. 결정 기록 없이 개발문서를 고쳐 합의된 것으로 만들지 않는다.

| 날짜 | 대상 / 커밋 | 결정과 영향 | A | B | C |
| --- | --- | --- | --- | --- | --- |
