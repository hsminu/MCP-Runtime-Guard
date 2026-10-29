# A207 · A199가 깨뜨린 A 구조 시험 고치기

2026-10-30 · `wip/next` · PR #110. 출처: 푸시 전 확인과 scanner-desc 세션 보고.
관련: A110(A 모듈 중 `__main__`만 `os`), A199, #94 F-01.

## 문제

- `tests/unit/test_a_architecture.py::test_module_security_rules`가 실패했다.
- `_no_risky_operation_or_output_channel` 규칙이 `services.product_assembly`의 `import os`를 거부한다. A 모듈 중 `os`를 import할 수 있는 것은 `__main__`(A110의 `os._exit`)뿐이다.
- A199(`8aacfe0`)가 실행 파일을 `os.path.normcase`로 비교하면서 `import os`를 넣었다.
- A199의 대상 시험에 이 구조 시험 파일이 빠져 있어 놓쳤다. A199는 이미 #110에 올라가 있다.

## 한 일

- `product_assembly.py`에서 `import os`를 뺐다.
- 비교는 이미 import된 `pathlib`로 바꿨다: `Path(server.command) != Path(sys.executable)`.
  - Windows에서 `WindowsPath` 비교는 대소문자와 `/`·`\`를 무시한다.
  - POSIX에서는 대소문자를 구분한다.
  - 파일 시스템은 읽지 않는다.
- A199 인정 조건은 그대로다. stdio이고, command가 있고, 앱 Python과 같은 경로일 때만 파일 서버 루트로 인정한다. 주석(#94, EXE-01)도 유지했다.
- `test_a199_file_server_command.py`에 Windows 전용 사례를 하나 더했다. `sys.executable`의 `\`를 `/`로 바꿔도 인정되는지 본다.
- #94·#110 회신에 적은 `os.path.normcase` 비교는 이제 `Path` 비교다. 뜻(대소문자 무시, 파일 시스템 미사용)은 같다.

## 검증

2026-10-30, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| 대상 6개 파일(`test_a_architecture`, `test_a199_file_server_command`, `test_product_assembly`, `test_product_settings`, `test_bridge_registration`, `test_a184_default_settings`) | 87 passed, 16 subtests passed |
| `ruff check`·`ruff format --check`(379개), `mypy src`(147개) | 통과 |
| 되돌림: 비교를 문자열 `!=`로 | 대소문자·`/` 시험 2개 실패 |
| 되돌림: command 조건 제거 | 다른 실행 파일 시험 실패 |
| 복원 뒤 | 바이트 같음 |

## 한계

- POSIX에서는 돌려 보지 않았다. Windows 전용 시험 2개는 그곳에서 건너뛴다.
- `Path` 비교는 `a/./b` 같은 `.` 구간과 겹친 구분자도 같게 본다. `..`는 풀지 않는다.
