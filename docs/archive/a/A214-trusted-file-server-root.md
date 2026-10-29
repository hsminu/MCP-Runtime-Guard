# A214 · 파일 서버 인정을 C의 `trusted_file_server_root`로

2026-10-30 · `wip/next` · PR #113. 출처: C2.11-2 "A가 할 것"(#111), 사용자 지시("이어서 해").
관련: #94(출처 규칙, B 답 3번), A199(파일 서버 실행 파일 조건), A207(`os` 대신 `pathlib` 비교), C2.11-2.

## 한 일

- `services/product_assembly.py`
  - A 쪽 `_file_server_root`(stdio·`command == sys.executable`·args 모양)를 지웠다.
  - `assemble_guard`가 C `servers/file_server.trusted_file_server_root(server.transport, server.command, server.args or ())`로 파일 서버 루트를 정한다. 인정 조건은 이제 C 함수 하나에만 있다.
  - `import sys`와 `from pathlib import Path`를 뺐다. 이 모듈에서 더는 쓰지 않는다.
- 비교 방식이 `Path` 같음(A207)에서 C의 `os.path.normcase` 문자열 비교로 바뀐다. Windows에서 대소문자·`/` 구분자를 맞추는 점은 같다. 빈 `command`는 C 함수가 따로 거절한다.
- A199 회귀 시험은 그대로 썼다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| `test_a199_file_server_command.py` | 4개 통과 |
| `test_a_architecture.py`(모듈 규칙 포함) | 9개 통과 |
| `product_assembly.py` `ruff check`·`ruff format --check`·`mypy`, import-linter | 통과, 계약 7개 유지 |

- 처음 실행에서 A199 시험 4개가 ERROR였다. 원인은 `--basetemp`의 상위 폴더가 없던 것이고, 폴더를 만든 뒤 다시 돌려 통과했다.
- 되돌림: `command` 대신 앱 Python을 넘겨 실행 파일 조건을 빼면 "다른 실행 파일이면 예약 전 거부" 시험 1개가 실패한다(복원 뒤 바이트 같음).

## 한계

- 실제 앱에서 파일 서버를 띄워 확인하지 않았다.
