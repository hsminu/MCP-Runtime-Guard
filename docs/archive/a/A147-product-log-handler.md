# A147 · 제품 로그 파일 handler

## 한 일

- C 공개 접근자 `codex.folders.paths.data`를 사용해 제품 `data/guard.log`에 `RotatingFileHandler`를 붙였다. 경로를 환경변수나 비공개 속성에서 다시 계산하지 않는다.
- C `install_log_redaction()` 설치 뒤, `codex.claim()`·설정 읽기·DB 경로 확인을 마친 제품 실행에서만 연다. UTF-8, 1 MiB, 백업 2개, INFO 수준이며 형식은 시각·수준·로거 이름·메시지다. root 수준도 실행 동안 INFO로 둔다.
- 정상 종료·`_run` 예외·정리 미확인 강제 종료 직전에 handler를 제거하고 닫으며 root의 이전 수준을 복원한다. 파일 열기 `OSError`는 경로/예외 원문 없는 고정 안내와 반환값 1로 처리하고 앱 잠금을 해제한다.
- 인자 오류·잠금 거부·시연은 파일을 만들지 않는다. 기존 `preserve_logging_state` fixture에 root handlers·수준 복원을 추가했다.
- A 구조 검사에서 `logging` import 허용 목록에 제품 진입점만 추가했다. C 규칙·코드는 바꾸지 않았다.

## 실제 검증

2026-10-29, Windows, `.venv/Scripts/python.exe`, offscreen, 임시 폴더는 `.cache/a-work/A48-tmp`.

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A48-tmp-3 tests/unit/test_product_key_and_log.py tests/unit/test_ui_scanner_key.py tests/unit/test_product_entry_logging.py tests/unit/test_codex_preparation.py tests/unit/test_product_settings.py tests/unit/test_ui_overview.py tests/unit/test_ui_keyboard.py tests/unit/test_ui_model_settings.py tests/unit/test_a_architecture.py
```

- 92개·서브테스트 27개 통과. 새 로그 시험 7개: 3개 종료 경로에서 실제 파일에 정리된 INFO/라이브러리 레코드 기록·`TEST_SECRET_` 부재, handler 옵션·닫힘·root 복원, 시연/잠금/인자 오류의 파일 부재, 파일 열기 실패의 고정 안내·잠금 해제.
- 첫 확대 실행은 A 구조 검사의 진입점 logging 금지로 1 실패·91 통과였다. 요청된 handler 소유권을 반영해 해당 허용 목록을 좁게 수정한 뒤 위 결과를 얻었다.
- 변경 Python 6개 파일 `ruff check`·`ruff format --check` 통과. 전체 `python -m mypy` 116 files 통과. `lint-imports.exe --config tools/ci/importlinter.ini` 7계약 통과. `git diff --check` 통과.

새 시험의 회귀 대조(각 실행 뒤 제품 파일을 원래 바이트로 복원):

| 고의 회귀 | 시험 | 결과 |
| --- | --- | --- |
| root handler 부착 제거 | normal/exception/unconfirmed | 3 실패 |
| 인자 검증·claim 전 로그 파일 생성 | demo/locked/arguments | 3 실패 |
| 로그 열기 실패를 성공 코드로 반환 | open failure | 1 실패 |

## 남은 한계

- 폴더 공개 접근자가 있으므로 C API 추가 요청 없이 구현했다. 사용자 전용 폴더 권한은 C `prepare_app_folders`의 보장을 사용한다. 새 시험은 가짜 폴더 조립이며 ACL 공격 검증은 아니다.
- 회전 옵션을 확인했으며 실제 1 MiB 경계 회전·디스크 부족·기존 로그 링크 공격은 시험하지 않았다. 로그 파일별 검증 API는 추가하지 않았다.
- 설정 읽기/DB 거부는 handler 생성 전이므로 기존 고정 stderr 안내를 유지한다. 로그 직접 출력·임의 문서 원문을 정화하는 보장은 C2.8 범위를 넘지 않는다.

관련: I9, S04, C2.8 #74 A 리뷰, C2.13 AppFolders, REC-01. A48과 같은 변경으로 검증했다.

PR: 미정
