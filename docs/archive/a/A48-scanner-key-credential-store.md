# A48 · Scanner 키 자격증명 관리자 연결

## 한 일

- 제품 진입점 `_run`에서만 C `save_scanner_key`와 끝자리 조회 래퍼를 `GuardWindow`에 주입한다. 창은 `ScannerKeyForm`에 전달하고 UI는 security를 import하지 않는다.
- 창 생성 때 `scanner_key_tail()`을 한 번 부른다. `ScannerKeyError`·키 없음은 `저장된 키 없음`, 정상 값은 끝 4자리만 표시한다. 형식 밖 반환값도 표시하지 않는다.
- 저장 실패는 기존 `SAVE_FAILED`, 성공은 인자 없는 `key_saved` 신호다. 저장 전 입력 비움과 끝자리만 보관하는 기존 동작을 유지한다. 시연·기본 생성 창은 저장 연결이 없다.

## 실제 검증

2026-10-29, Windows, 로컬 `.venv/Scripts/python.exe`, `QT_QPA_PLATFORM=offscreen`.
`TMPDIR`·`TEMP`·`TMP`는 저장소의 `.cache/a-work/A48-tmp`로 지정했다.

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A48-tmp-3 tests/unit/test_product_key_and_log.py tests/unit/test_ui_scanner_key.py tests/unit/test_product_entry_logging.py tests/unit/test_codex_preparation.py tests/unit/test_product_settings.py tests/unit/test_ui_overview.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_model_settings.py tests/unit/test_a_architecture.py
```

- A147과 함께 92개·서브테스트 27개 통과. 새 창 연결 시험 5개는 `_run`이 만든 실제 Qt 창에서 가짜 saver·tail의 호출, 끝자리·고정 문구, 성공/실패, 신호 payload 없음, 입력·객체·라벨·로그 원문 비잔존을 확인한다. 백엔드 시작 직전 중단하며 실제 자격증명 관리자는 호출하지 않는다.
- 변경 Python 6개 파일의 `ruff check`·`ruff format --check` 통과. 전체 `python -m mypy` 116 source files 통과. `lint-imports.exe --config tools/ci/importlinter.ini` 7계약 통과. `git diff --check` 통과.

새 시험의 회귀 대조(각 실행 뒤 제품 파일을 원래 바이트로 복원):

| 고의 회귀 | 시험 | 결과 |
| --- | --- | --- |
| 저장된 끝자리 반환을 None으로 대체 | saved | 1 실패 |
| 없는 키를 저장됨으로 표시 | missing | 1 실패 |
| 조회 오류를 가짜 끝자리로 대체 | error | 1 실패 |
| 4자 초과 반환값도 표시 | invalid | 1 실패 |
| 시연 창에 saver 주입 | demo | 1 실패 |

## 남은 한계

- C 코드는 `CredReadW`·`CredWriteW`를 동기로 호출하며 timeout이나 작업 스레드가 없다. 창 생성·저장 버튼의 UI 스레드가 OS 호출을 기다릴 수 있다. 이번에는 스레드로 옮기지 않았고 지연 실측도 하지 않았다.
- Python 문자열·Qt 버퍼의 안전 삭제는 보장하지 않는다. 저장 후 화면/객체의 원문 참조 비잔존을 검사했으며 프로세스 메모리 소거를 증명하지 않는다.
- 실제 자격증명 왕복·Scanner 실호출·독립 공격 검증은 이번 범위 밖이다. C API와 I9/S04 연결부의 단위 검증이다.

관련: I9, S04, 개발문서 2.2·10.2·12.1, contracts 2026-10-15 Scanner 키 결정, C2.10 #60·#62.

PR: 미정
