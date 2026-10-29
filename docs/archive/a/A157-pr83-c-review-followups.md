# A157 · #83 C 리뷰 후속

[#83 C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/83#issuecomment-6493041009)의 막지 않는 참고 1–3과, 그 리뷰의 전체 시험에서 깨진 `test_stdio_logging`의 원인을 #83에서 고쳤다.

## 한 일

- **시작하지 못한 서버의 등록(참고 1):**
  - `RegistrationCommands`가 목록 수집의 `DispatchError` 중 `SessionStartFailed`·`SessionStartTimeout`만 쓰기 전 거부 `REG_SERVER_UNREACHABLE`로 돌려준다. 이 둘은 서버가 준비되지 않았고 종료가 확인된 경우다. Registry·설정 파일에는 아무것도 쓰지 않고, 확인 토큰은 이미 써서 다시 등록하려면 처음부터 확인한다.
  - 종료를 확인하지 못한 경우(`SessionCloseFailed` 등)는 지금처럼 명령 결과 미확인으로 둔다(I6).
  - 등록 화면에 `REG_SERVER_UNREACHABLE`과 `REG_LISTING_INVALID` 문구를 넣었다. `REG_LISTING_INVALID`는 `__call__`이 Registry 등록·목록 거부를 묶는 코드이고, 모두 쓰기 전 거부다.
- **설정 파일 URL(참고 2):** `_server_streams`에서 C `normalize_endpoint`가 거부한 URL은 그 서버만 연결 시작에서 `EndpointError`를 내는 stream으로 둔다. 제품 transport는 시작 실패를 서버별로 격리하므로, 조립 전체가 실패하지 않고 그 서버만 이번 실행에서 막힌다. 오류 원문은 없고 C 고정 코드만 쓴다.
- **시작 예산(참고 3):** `PRODUCT_STARTUP_TIMEOUT_S`를 `STORE_STARTUP_S`(60) + `SERVER_ASSEMBLY_S`(41) + L2 50 + 취소 20 + 여유 10 = 181초로 나눴다.
  - `SERVER_ASSEMBLY_S`는 서버 시작 한 묶음(동시 4개)의 목록 상한 30초 + 1과 시작 실패 서버의 C 종료(유예 5 + 강제 5)다. 다섯 번째 서버부터의 다음 묶음은 포함하지 않는다.
  - 시험이 transport·목록·launcher의 기본 상한에서 이 값을 다시 계산해 어긋나면 실패한다.
- **`test_stdio_logging` 실패 원인:** 시작 상한(3초) 문제가 아니었다. #83의 `test_main_settings_start_uses_launcher_folders`가 실제 `main()`을 불러 C 로그 가림(A146)을 프로세스 전체에 켠 채 끝나서, 같은 실행의 뒤 시험이 자기 로그를 `LOG_WITHHELD`로 받았다. 그 시험에 기존 `preserve_logging_state`를 붙였다. `test_stdio_logging`은 바꾸지 않았다.

## 실제 검증

2026-10-29, Windows 11, Python 3.12, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`.

- 실제 연결:
  - 닫힌 loopback 포트의 HTTP 서버로 `collect_registration_listing`을 돌리면 `SessionStartFailed`다(새 시험).
  - 설정 파일에 C2.5 거부 URL(`http://example.com/mcp`) 서버를 더한 실제 launcher 조립은 성공하고, 정상 stdio 서버의 Guard 호출이 통과하며, 거부된 서버의 목록은 없다(`test_mcp_launcher_pipes` `badurl`).
  - `test_mcp_launcher_pipes.py` 다음에 `test_stdio_logging.py`를 돌리면 고치기 전에는 실패하고 고친 뒤 15개가 통과했다.
- 관련 시험 9개 파일(등록·launcher·A151·로그·조립·서버 추가·A105·A138·Codex 준비)을 같이 돌렸을 때 실패는 위 로그 시험 하나였다. 원인을 고친 뒤 전체 시험에서 통과했다.
- 고의 회귀 6개를 하나씩 넣고 되돌렸다. 모두 시험이 잡았다.

  | 고의 회귀 | 잡은 시험 |
  | --- | --- |
  | 모든 `DispatchError`를 거부로 | 등록: 종료 미확인이 미확인으로 남는지 |
  | `SessionCloseFailed`를 시작 실패에 포함 | 같은 시험 |
  | 설정 파일 URL 거부를 다시 예외로 | launcher `badurl` |
  | `SERVER_ASSEMBLY_S` 30초 | 시작 예산 시험 |
  | `REG_LISTING_INVALID` 문구 삭제 | 등록 화면 문구 |
  | `preserve_logging_state` 제거 | launcher 다음 로그 시험 |

- `ruff check .`, `ruff format --check .`(306개), `mypy`(118개 소스), `lint-imports --config tools/ci/importlinter.ini`(계약 7개 유지), `git diff --check` 통과.
- 전체 단위·contract 시험(이 브랜치, `0e33404`): 5,152개 통과, 6개 실패, 5개 건너뜀, subtests 374개(6분 4초).
  - 실패 6개는 C5.6 `test_write_loss_counts`다. #83의 전역 정지 연결 때문에 깨지는 C 시험이고, C가 [#85](https://github.com/hsminu/MCP-Runtime-Guard/pull/85)로 main에서 고쳤다. 이 브랜치에는 아직 #85가 없다.
  - main(`fa36962`)을 합친 임시 사본에서 C5.6·T11 계수 시험과 이 기록의 바뀐 시험 6개 파일을 돌려 67개가 통과했다.
  - `test_stdio_logging`은 전체 실행에서 통과했다.

## 남은 한계

- **initialize 중 끝나는 stdio 서버(새로 발견, A158):**
  - 실제 launcher로 `-c pass`처럼 바로 끝나는 서버를 띄우면, SDK 세션 종료가 `McpError('Connection closed')`로 끝난다. C launcher가 프로세스 종료를 확인해도(`LAUNCH_EXIT_UNCONFIRMED` 없음) worker는 `SessionCloseFailed`로 본다.
  - 그래서 그 서버의 등록은 여전히 "확인하지 못함"이다. 제품 시작에서는 그 서버만 격리되지 않고 transport 시작 전체가 `SessionCloseFailed`로 실패한다(가짜 폴더·실제 launcher로 확인).
  - A132(HTTP 요청 오류만으로 끝난 세션은 종료 완료)의 stdio 판이 필요하다. 종료 판정을 바꾸는 일이라 이 기록에서는 고치지 않았다.
- `SERVER_ASSEMBLY_S`는 상한의 합이고 측정값이 아니다. 서버가 다섯 개 이상이면 묶음마다 최대 41초가 더 들 수 있다.
- L2 실패 코드 사본(참고 4)은 C가 `codex_l2`에 상수를 넣은 뒤 지운다.

관련: I1, I6, I9, EXE-01, URL-01, REC-01, T19, C2.5, C2.8, A09, A128, A146, A151.

PR: [#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83)
