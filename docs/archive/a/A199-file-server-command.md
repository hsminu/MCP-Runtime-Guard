# A199 · #94 B 답 3번: 파일 서버 인정에 실행 파일 조건 추가

2026-10-30 · `wip/next`(UI·Scanner merge 뒤). 출처: [#94 B 답 3번](https://github.com/hsminu/MCP-Runtime-Guard/issues/94#issuecomment-6505157878).
관련: I2·I6·I7, #94 F-01, EXE-01(C2.22).

## 문제

`assemble_guard`는 파일 서버 루트를 설정의 `args` 모양(`-I -m guard_app.servers.file_server --root <abs>`)으로만 인정했다. `command`(실행 파일)는 보지 않았다. 그래서 설정 파일을 직접 고쳐 다른 실행 파일에 이 args를 준 stdio 서버도 파일 서버 루트로 인정됐다. 그러면 Guard는 자원형 READ 분류를 그 서버에 적용한다. EXE-01은 실행 파일을 지문으로 고정할 뿐이고, 그 실행 파일이 C 파일 서버 코드를 돈다는 보장은 아니다.

A 등록 화면(`bridge_registration.py`)은 파일 서버를 등록할 때 `command = sys.executable`로 고정한다.

## 한 일

- `product_assembly.py`에 `_file_server_root(server)`를 두었다. 다음 세 조건을 모두 만족할 때만 루트를 돌려준다.
  - `server.transport`가 stdio다.
  - `server.command`가 있다.
  - `os.path.normcase(server.command) == os.path.normcase(sys.executable)`이다. 문자열 비교만 하고 파일 시스템은 읽지 않는다.
- 조건에 맞지 않는 서버는 루트가 없는 일반 서버다. 그 서버의 자원형 READ는 지금처럼 `file_resource_resolver`가 예약 전에 `INTERNAL_ERROR`로 거부한다.
- 경로가 같아도 실제로 그 파일이 도는지는 EXE-01이 실행 때마다 지문으로 대조한다. `-I` 실행이라 `guard_app` 모듈도 앱 설치본에서만 찾는다.
- 앱 위치가 바뀌면 기존 파일 서버의 루트 인정이 꺼져서 자원형 READ가 거부된다. 같은 경우 지문도 달라져 실행이 먼저 막힌다.

## 검증

2026-10-30, Windows / Python 3.12 / 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

새 시험 `test_a199_file_server_command.py`는 주입 stream(Peer)과 실제 SQLite·Guard로 확인한다. 시험 도우미의 허용 resolver는 빈 `file_resource_resolver({})`로 바꿔서, 인정받지 못한 루트가 실패로 닫히는지 본다.

| 확인 | 결과 |
| --- | --- |
| 파일 서버 args + `C:\Windows\System32\where.exe` | `Denied(INTERNAL_ERROR)`, `reserve_dispatch` 호출 0, `tools/call` 0, `before_dispatch` 감사 1건, Task RUNNING 유지 |
| 대조군: 같은 레코드, command = `sys.executable` | `Released(PUBLIC)`, 예약 1, `tools/call` 1, 자원 ID `public/a.txt` |
| command = `sys.executable.upper()`(Windows) | 인정됨 |
| 대상 시험 8개 파일(새 시험·조립·설정·A49 실제 파일 서버·등록·C 파일 서버·Codex 제품 흐름·A184) | 131 passed, 20 subtests passed |
| `ruff check`·`ruff format --check`(바꾼 2개), `mypy src`(148개), import-linter(계약 7개 유지) | 통과 |
| 되돌림: command 조건 제거 | 첫 시험 실패. 복원 뒤 바이트·`git diff` 같음 |

## 한계

- C `file_server_root`에는 같은 조건을 넣지 않았다(C 파일). 다른 호출부가 생기면 그쪽도 같은 조건이 필요하다. C에 넣을지는 C가 정한다.
- 실제 다른 실행 파일을 띄워 보지는 않았다. 주입 stream으로 조립 판단만 확인했다.
- 전체 시험은 돌리지 않았다.
