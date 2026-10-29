# A09·A10·A15·A16 · C launcher 파이프 제품 Guard 조립

2026-10-29. 작업 위치: `.cache/a-work/wt-A09`, 브랜치 `wip/a09-v2` (A138 `92ba5e2` 위의 2차 최종 상태).
PR: 미정

## 한 일

- `services/product_assembly.py`: `ProductInputs(folders=AppFolders, ...)`에서 선택한 서버의 스트림을 직접 구성한다. stdio는 C `server_folder`와 `launch_mcp_server`를 사용하며, 설정의 command·args·env_allow·command_size·command_sha256을 그대로 넘긴다. 지문이 없으면 None을 넘겨 C가 거부한다. 연결 때 새 지문을 계산해 신뢰하지 않는다. HTTP는 기존 제한된 팩터리를 사용한다.
- `adapters/mcp_transport/launcher_streams.py`: 실제 프로세스 파이프를 기존 `validated_stdio_stream_factory`에 연결한다. 따라서 LF 프레임 수신·파싱 전 페이지/누적 바이트 상한을 유지한다. SDK 세션과 로컬 펌프가 닫힌 뒤 C `close()`를 부른다. AnyIO 취소 범위에서 정리를 보호하며, C의 유한한 grace/kill 대기와 asyncio shield를 그대로 사용한다. False는 `LAUNCH_EXIT_UNCONFIRMED`로 거부한다. 서버 폴더와 격리 stderr는 임의 삭제하지 않고 C의 다음 시작 정리에 맡긴다.
- `transport.py`: 제품 조립만 `isolate_start_failures=True`를 사용한다. 실패한 worker 정리를 확인한 뒤 다른 서버를 계속 시작한다. 종료 미확인은 서버 하나의 일반 연결 거부로 삼키지 않는다. 기존 시연·시험의 기본 시작 실패 동작은 그대로다.
- `worker.py`: SDK 문맥 진입을 마치기 전의 launcher 종료 미확인도 `SessionCloseFailed`로 보존한다. 넓은 예외 처리나 취소를 삼키는 처리는 추가하지 않았다.
- 연결 후 A86 지문 대조 → A129 `ServerRegistry.observe` → live registry 봉인 순서는 그대로다. 실제 launcher 시험에서 변경된 정의가 PENDING으로 저장되고 다시 읽히는 것을 확인했다.
- `server_streams`는 folders가 없는 시험·시연 조립에만 남겼다. `folders`와 함께 주면 `ProductAssemblyError("PRODUCT_INPUTS_INVALID")`로 거부한다. SDK 자체 실행 함수는 기존 시연 팩터리에만 남겼고 A 아키텍처 시험으로 제품 참조를 금지했다.

## 1차 실제 검증 (기존 작업 기록)

모든 명령은 worktree에서 실행했다. `PYTHONPATH=src`로 `guard_app.__file__`이 worktree의 `src/guard_app/__init__.py`임을 먼저 확인했다. Python은 원래 checkout의 `.venv/Scripts/python.exe`만 사용했다. 설치 환경은 Python 3.12.15, MCP 1.29.0, AnyIO 4.15.1, Pydantic 2.14.0이다. launcher 공개 시그니처와 종료 계약은 이 worktree의 설치된 소스로 확인했다.

셸은 Git Bash, `QT_QPA_PLATFORM=offscreen`, `TMP`·`TEMP`·`TMPDIR`는 worktree의 `.cache/a-work/A09-tmp-1`이었다. 아래 `P`는 `/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe`다.

```bash
$P -m pytest -q --tb=short -p no:cacheprovider --basetemp=.cache/a-work/A09-tmp-9 tests/unit/test_mcp_launcher_pipes.py tests/unit/test_a_architecture.py tests/unit/test_mcp_transport.py tests/unit/test_mcp_tool_listing.py tests/unit/test_mcp_ingress.py tests/unit/test_mcp_http.py tests/unit/test_mcp_http_ingress.py tests/unit/test_product_assembly.py tests/unit/test_product_settings.py
```

- 173 통과, 하위 시험 24 통과. 기존 합성 HTTP 서버의 pydantic-settings 경고 1개.
- 시작 정리 시험을 아래 회귀 대조 결과로 보강한 뒤 `--basetemp=.cache/a-work/A09-tmp-11`로 새 launcher 파일과 A 아키텍처 파일을 다시 실행: 16 통과.
- 새 launcher 시험 10개: 실제 프로세스 7개 시나리오(normal·changed·crash·oversize·unpinned·mismatch·mixed), 취소/종료 False 2개, SDK 문맥 진입 전 종료 미확인 1개.
- 실제 정상 호출은 Guard.submit을 거쳐 Released, 서버가 응답에 싣는 호출 수 1을 확인했다. 비정상 종료 READ는 Withheld, 해당 서버는 이번 실행 PENDING이다. 지문 없는/틀린 실행 파일은 프로세스 생성 0이다. 혼합 시나리오에서 잘못된 실행 파일 서버와 함께 정상 서버가 계속 호출된다.
- 실제 프로세스가 생성된 모든 시나리오에서 종료 코드 존재와 C `close()` True를 확인했다. 페이지 초과는 미완료 `TOOLS_LIST_PAGE_TOO_LARGE`, 도구 비공개다.
- `PYTHONPATH=src $P -m mypy`: 115 소스 통과.
- `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini`: 7 계약 유지, 위반 0.
- 변경한 Python 6파일의 `ruff check`·`ruff format --check`: 통과. 마지막 시험 보강 후 포맷의 빈 줄 한 곳을 고치고 재확인했다.
- `git diff --check`: 통과. 전체 단위 시험·C 독립 인수·GUI 실제 실행은 하지 않았다.

## 1차 회귀 대조 (기존 작업 기록)

제품 소스에 한 번에 한 회귀만 넣고 해당 새 시험을 실행했다. 매번 try/finally로 원래 바이트를 복원하고 같은 바이트인지 확인했다. 아래 최종 대조는 모두 pytest exit 1과 해당 시험 실패를 확인했다.

| 의도적 회귀 | 탐지한 새 시험 |
| --- | --- |
| 앱 폴더가 있어도 주입 스트림 사용 | normal |
| 실시간 지문 대조 생략 | changed |
| Registry.observe 생략 | normal·changed |
| 끊긴 세션을 정상으로 유지 | crash |
| 조립의 수신 상한을 기본값으로 덮음 | oversize |
| 실행 시 자동 재지문 계산으로 등록 지문 무시 | unpinned·mismatch |
| 서버별 시작 실패 격리 끔 | mixed |
| launcher close 호출 생략 | 종료 중 취소(True) |
| close False를 성공 취급 | 종료 확인(False) |
| 문맥 진입 전 launcher 정리 실패 판정 생략 | startup cleanup |
| 제품 조립에 SDK stdio 팩터리 참조 추가 | A 아키텍처 시험 |

첫 startup cleanup 대조는 기존 일반 종료 실패 검사도 통과하는 상황을 만들었기 때문에 회귀를 못 잡았다. SDK `__aenter__` 자체가 실패하도록 시험을 고쳐 진입 전 분기를 분리했고, 정상 소스 통과·해당 분기 제거 시 실패를 다시 확인했다.

## 남은 한계·요청

- C5.8(#21) 독립 인수는 C 몫이다. 실제 launcher 파이프의 파싱 전 수신 상한, 종료·취소, 실제 wire/부작용 계수와 Job 후손 종료를 독립 확인해 달라고 요청한다. 이 기록은 A 단위 조립 검증이며 C 인수를 대신하지 않는다.
- B는 transport/제품 조립과 Registry 지문 대조·기록 순서, C는 launcher 수명·취소 처리를 리뷰해야 한다. domain·ports·C launcher 공개 API 변경은 없다.
- A138과 합쳐 제품 시작의 설정 기반 `ProductInputs(..., folders=folders)` 연결도 완료했다. 실제 main → bootstrap → 설정 기반 Guard 조립을 대역 launcher로 검증했다. 실제 Codex 세션·전체 GUI 조작 인수는 별도다.
- EXE-01 등록·재확인 UI와 서버별 고정 오류 문구 연결은 별도 작업이다. 실패한 서버의 도구는 이번 실행에서 제외된다.
- EXE-01 지문은 인터프리터형 서버의 스크립트·패키지까지 덮지 않는다. OS 격리·stderr 용량 상한은 기존 C 한계 그대로다. 종료 취소는 이벤트로 제어한 C 형태의 대역에서 확인했다. 실제 하위 프로세스 정리도 확인했지만 취소 순간의 실제 Job 경쟁은 C 독립 인수에 남긴다.
- 관찰하지 않은 서버 내부 외부 호출·실제 외부 부작용은 검증 불가다. 정상 합성 READ 응답의 서버 수신 계수만 확인했다.

관련: I1·I6·I8·I9·I10, T10·T14·T16 의도의 A 단위 검증, 설계 2.2·9.1–9.4·10.1, EXE-01·FP-01·C2.6·C5.8, A86·A129.


## 2차 통합·리뷰 수정

- A138의 `on_global_halt`, `new_id`, 영속 `allocate_call_seq`·`durable_failure_audit`, Store 준비·복구·활성화 ACK와 전역 정지 감시를 유지했다. 충돌은 파일 편집으로만 풀었다.
- `ProductServerStreams`와 `AssembledGuard.close_servers`, bootstrap의 `servers.streams`·`servers.close`를 제거했다. 선택 서버마다 A09 launcher 문맥이 프로세스를 소유하며, transport 종료에서 프로세스 종료까지 확인한다. transport → STOP-01 settle → Guard → Store 종료 순서는 A134·A136을 유지한다.
- transport 종료 미확인의 `DispatchError`는 Runtime에서 고정 `BackendCleanupError`로 변환한다. 미확인을 admission에 남겨 두 번째 close도 성공으로 바뀌지 않는다. Store는 finally에서 종료 확인한다. 넓은 예외 처리는 추가하지 않았다.
- `ProductInputs`와 `bootstrap_product(product=...)` 문서에 시험·시연 전용 주입임을 명시했다. 제품 소스 전체의 AST 검사는 `ProductInputs` 호출의 `server_streams`, 위치 인자 주입, 별칭 import, 불투명한 `*args`·`**kwargs`를 검사한다. examples·tests는 검사 대상이 아니다. 동적 리플렉션 전체를 증명하는 검사는 아니다.
- `test_main_settings_start_uses_launcher_folders`: 실제 main이 앱 폴더를 잠그고 설정 JSON을 읽은 뒤 실제 bootstrap·SQLite·Guard를 구성한다. bridge 실행과 Codex 준비만 대역으로 두고, C 형태 launcher에 전달된 서버 폴더·등록 지문과 호출 1회·종료 1회를 확인한다. 도구 실행은 0회다.
- 삭제·이동 이름을 `rg`로 src/tests 전체에서 찾았다. `test_a138_product_startup`의 launcher patch를 `launcher_streams`로, `test_product_settings`의 stream patch를 `_server_streams`로 옮겼다. 최종 소스·시험에 `ProductServerStreams`·`close_servers`·옛 launcher patch 참조가 없다.

### HTTP 판단 근거

B `services/catalog.py:ServerSettings.check_server`는 URL이 존재하는지와 stdio/HTTP 필드 조합을 검사하지만 URL 정규화는 하지 않는다. C `security/endpoints.py:normalize_endpoint`는 URL-01을 검증하고 정규형을 만들며 `canonical_endpoint`는 비정규형을 거부한다. A `adapters/mcp_transport/streams.py:validate_http_endpoint`는 후자를 사용한다. C 모듈 설명에 설정 로더가 정규형만 받는다고 쓰여 있지만, 실제 B 로더에는 그 호출이 없다. 따라서 이번 합침은 A138의 `normalize_endpoint(server.url).url`을 유지해 HTTP transport에 정규형을 넘기고, A09의 `read_timeout_s=max(60.0, inputs.call_timeout_s)`를 함께 유지했다. 기본 포트가 들어 있는 A138 설정 시험이 정규형 endpoint 전달을 확인한다. B·C 코드와 정책 해시는 바꾸지 않았다. 이 설명과 B 구현의 차이는 B·C 리뷰 대상으로 남긴다.

### 2차 최종 검증

모든 명령의 cwd는 `C:/Project/sogaeding/MCP-Runtime-Guard/.cache/a-work/wt-A09`다. 첫 import 확인도 이 worktree의 `src/guard_app/__init__.py`였다. 원래 checkout의 src/tests/docs는 읽거나 고치지 않았다. 아래 P는 원래 checkout의 `.venv/Scripts/python.exe`다. Git Bash에서 `PYTHONPATH=src`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`, TMP/TEMP/TMPDIR는 worktree `.cache/a-work/A09b-tmp-1`로 지정했다.

```bash
$P -m pytest -q --tb=short -p no:cacheprovider --basetemp=.cache/a-work/A09b-tmp-4 tests/unit/test_mcp_launcher_pipes.py tests/unit/test_mcp_transport.py tests/unit/test_mcp_http.py tests/unit/test_product_assembly.py tests/unit/test_a138_product_startup.py tests/unit/test_session_runtime.py tests/unit/test_a143_writer_shutdown_race.py tests/unit/test_codex_preparation.py tests/unit/test_product_settings.py tests/unit/test_b28_integration.py tests/unit/test_sqlite_app.py tests/unit/test_a_architecture.py tests/unit/test_product_entry_logging.py tests/unit/test_mcp_tool_listing.py tests/unit/test_mcp_ingress.py tests/unit/test_mcp_http_ingress.py
```

- 284 passed, 108 subtests passed. 기존 합성 HTTP fixture의 pydantic-settings forward-reference 경고 1개.
- 회귀 복원 후 `--basetemp=.cache/a-work/A09b-tmp-40`으로 launcher·A138·아키텍처·A143 4파일 재실행: 54 passed.
- `PYTHONPATH=src $P -m mypy`: 117 소스 통과.
- `PYTHONPATH=src /c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini`: 7 계약 유지, 위반 0.
- 변경 Python 9파일 `ruff check`, `ruff format --check`: 통과. 파일은 launcher_streams·transport·worker·product_assembly·session_runtime와 test_mcp_launcher_pipes·test_a_architecture·test_a138_product_startup·test_product_settings다.
- `git diff --check`: 통과. 파일 내 충돌 표시는 제거했다. Git 상태 변경 명령은 실행하지 않았으므로 인덱스의 UU는 사용자의 git add 전까지 남는다.
- 초기 실행에서 중복 키워드와 시험의 서버 폴더 예상 경로를 수정했고, 종료 실패 분류 차이를 발견해 위 Runtime 변환을 추가했다. 이후 위 최종 결과를 얻었다.
- 전체 단위 시험·실제 Codex/외부 MCP·C5.8 독립 인수는 미실행이다.

### 2차 회귀 대조

1차 11종 중 변경 경로 5종을 다시 대조하고 새 보호·통합 경로 6종을 더해 총 11종을 실행했다. 한 번에 하나만 변경했고 모두 pytest exit 1, 지정 시험 실패를 확인했다. 매번 finally에서 원래 바이트로 되돌리고 일치와 SHA-256을 확인했다. 마지막 다중 행 치환은 CRLF 때문에 첫 실행 전에 중단돼 줄바꿈 매칭을 고친 뒤 별도 실행했고 탐지했다. 이는 미탐지가 아니다.

| 의도적 회귀 | 탐지 시험 | 결과 |
| --- | --- | --- |
| 제품 folders 분기 우회 | real launcher normal | 실패 탐지 |
| 서버 시작 실패 격리 끔 | real launcher mixed | 실패 탐지 |
| launcher close 생략 | close cancel True | 실패 탐지 |
| close False 무시 | close confirmation False | 실패 탐지 |
| SDK 진입 전 정리 미확인 무시 | unconfirmed startup cleanup | 실패 탐지 |
| folders·server_streams 동시 입력 허용 | product rejects folders and injected streams | 실패 탐지 |
| 제품 소스 ProductInputs에 server_streams 삽입 | product inputs AST | 실패 탐지 |
| 설정 기반 ProductInputs의 folders 제거 | main settings start uses launcher folders | 실패 탐지 |
| HTTP 정규화 제거 | A138 http True | 실패 탐지 |
| Runtime 종료 오류 변환 제거 | A138 stdio False | 실패 탐지 |
| Runtime 종료 미확인 latch 제거 | A138 stdio False의 두 번째 close | 실패 탐지 |

기존 1차의 나머지 6종은 이번에 다시 회귀 주입하지 않았다. 해당 정상 시험은 최종 시험 묶음에 포함했다. 로컬 실행 출력은 worktree `.cache/a-work/A09b-tmp-1/mutation-results.json` 및 각 회귀 이름의 txt에 있다.

추가 관련: REC-01–05·SEQ-01–03·STOP-01·URL-01, I5·I7·I11, A134·A136·A138·A143. A143 실제 writer 경합 시험 통과는 기존 종료 순서의 A 검증이며 C 전체 인수를 뜻하지 않는다.
