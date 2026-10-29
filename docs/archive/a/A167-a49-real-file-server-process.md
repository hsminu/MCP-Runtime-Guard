# A167 · A49 실제 로컬 파일 서버 프로세스로 등록·활성화·Guard 읽기(Codex 없음)

2026-10-29 · `wip/next`(A166 커밋 `4014768` 위). PR: 아직 없음(로컬 커밋).
관련: A49, C2.3·C2.11, EXE-01, I1·I4·I6·I7·I9·I10, T33(일치하는 경우만), T38.

## 한 일

`tests/unit/test_a49_registration.py`에 `test_a_real_file_server_process_lists_activates_and_reads_through_guard`를 더했다. 기존 A49 종단 시험은 프로세스 안의 대역(`Peer`)을 썼다. 이 시험은 다음 경로를 실제로 쓴다.

- 등록
  - 등록 명령은 제품과 같은 `collect_registration_listing`을 쓴다. C launcher로 실제 파일 서버(`python.exe -I -m guard_app.servers.file_server --root …`)를 띄워 그 서버가 낸 도구 목록을 받는다.
  - 사람이 확인하기 전에는 프로세스를 띄우지 않는다.
  - Registry에 등록된 도구는 서버의 `TOOLS` 세 개다.
- 활성화: `list_root`·`read_file`을 허용하고 `list_folder`는 거부했다. 결과는 `REG_ACTIVATION_RESTART_REQUIRED`다.
- 제품 조립
  - 저장된 설정 파일과 B SQLite에서 다시 읽은 C Registry로 `assemble_guard`를 불렀다. 주입 스트림 없이 앱 폴더와 C launcher를 썼다.
  - 파일 서버를 한 번 더 띄웠다(모두 2개).
- Guard 호출. 루트는 PUBLIC, 목록은 INTERNAL, `SecretCase`는 CONFIDENTIAL이다.

| 호출 | 결과 | Task 최고 등급 |
| --- | --- | --- |
| `read_file Open.txt` | 공개, 실제 파일 내용 | PUBLIC |
| `list_root` | 공개, 하위 폴더 이름 포함 | INTERNAL |
| `read_file SecretCase/Doc.txt` | 공개, 실제 파일 내용 | CONFIDENTIAL |
| `read_file SecretCase/Missing.txt` | 거부(전달 전) | 그대로 |
| `list_folder SecretCase` | 거부(허용 안 함) | 그대로 |

- 끝: `aclose` 뒤 두 프로세스 모두 끝났고, C launcher 닫기 확인이 참이다.

같은 파일의 `test_picker_preserves_case_and_invalidates_confirmation`도 고쳤다. 이 파일만 돌리면 `QApplication`이 생기기 전에 `BackendBridge`를 만들어 실패했다. 다른 시험이 앱을 먼저 만들 때만 통과하던 순서 의존이다. 폼(앱)을 먼저 만들도록 순서만 바꿨다. 단언은 그대로다.

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- `test_a49_registration.py`(13개), `test_file_server.py`, `test_mcp_launcher_pipes.py`: 63 passed. 처음 돌렸을 때 위 순서 의존 실패 1개가 나왔고, 고친 뒤에는 그 시험만 따로 돌려도 통과했다.
- 고의 회귀: 제품 조립의 파일 resolver를 끄면 이 시험이 실패했다. 없는 파일이 실제 서버까지 전달되어 거부 대신 미공개(`RESULT_UNCLASSIFIED`)로 끝났다. 원래 바이트로 되돌린 뒤 SHA-256 일치를 확인했다.
- `ruff check`·`ruff format --check`(바뀐 1개 파일), `git diff --check` 통과.
- 전체 단위 시험은 돌리지 않았다.

## 확인하지 않은 것과 조건

- 파일 서버 프로세스는 `-I`로 뜬다. 그래서 `PYTHONPATH`가 아니라 venv에 설치된 `guard_app`을 쓴다.
  - 이 PC의 venv는 메인 체크아웃 `src`를 가리킨다. 이 worktree의 시험도 메인 체크아웃의 `file_server.py`를 실행했다.
  - 두 파일과 의존하는 `security/paths.py`의 SHA-256이 같은 것을 확인했다.
  - CI는 `uv sync`로 같은 체크아웃을 설치하므로 같은 코드다.
- Codex는 거치지 않았다. 실제 로그인 세션이나 C의 대본 모델(C6.8-2)로 하는 Codex 제품 종단은 C와 함께 할 일로 남는다.
- T33의 불일치 경우(결과 `resource_id`가 요청 경로와 다름)와 실제 OS 부작용·wire 계수는 C 독립 인수 범위다. 이 시험은 일치하는 경우만 본다.
- 실제 파일 서버가 받은 호출 수는 세지 않았다. 거부 두 건이 전달되지 않았다는 것은 결과 종류(전달 전 거부)로만 본다.
- B·C 코드와 공통 문서는 바꾸지 않았다.
