# A171 · #92 조회 연결 시험의 Qt 객체 정리

2026-10-30 · `wip/next`(기준 커밋 `ae3c6a3` 위). PR: [#92](https://github.com/hsminu/MCP-Runtime-Guard/pull/92) 후속.
관련: 개발문서 12.1절(스레드 모델), [#92 B 리뷰 3](https://github.com/hsminu/MCP-Runtime-Guard/pull/92#issuecomment-6501241060). Qt 객체 정리·중단 재현의 대응 T/S ID는 없다. 계약 변경 없음.

## 문제

`test_store_query_link.py` 뒤에 `test_store_recovery.py`를 실행하면 Python 프로세스가 중단된다는 C 리뷰가 있었다. 조회 연결 시험은 실제 SQLiteStore를 만들지 않지만, 화면·상세 대화상자를 만든 뒤 `close()`만 했다.

B는 남은 QObject 순환 참조가 다음 시험의 메인 아닌 스레드에서 수거되며 Qt가 중단될 가능성을 제시했다. 원인은 미확정이다. 이번 변경은 이 가설에 따른 시험 객체 정리이며, 제품 코드나 writer 수정이 아니다.

## 한 일

- `tests/unit/test_store_query_link.py`에 함수 범위 autouse fixture `cleanup_qt_objects`를 더했다.
  - 시작 전 최상위 위젯 집합을 보관하고, teardown에서 메인 스레드인지 단언한다.
  - 새 최상위 위젯에만 `deleteLater()`를 호출한다. 이어 `QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)`와 `processEvents()`로 삭제를 처리한다.
  - 그 뒤 `gc.collect()`로 순환 참조를 메인 스레드에서 수거한다. `QApplication`이 없으면 만들지 않고 GC만 한다.
  - 기존 위젯과 공용 `QApplication`을 유지하며, 시험 실패·예외를 잡거나 숨기지 않는다. 주석은 가설임을 나타내는 영어 두 줄이다.
- `docs/a-status.md`의 A170 뒤에 A171 완료 줄을 더했다. A151 MSIX 항목과 막힌 것 표는 사용자 결정에 따라 C가 경로 비교 수정을 맡고 C PR을 기다리는 상태로 바꿨다.
- 시험 파일·새 기록은 LF, 현황 문서는 CRLF를 유지했다. 기존 완료 기록은 바꾸지 않았다.

## 검증

Windows, Python 3.12.15, PySide6 6.12.0, pytest 9.1.1, 로컬 `.venv`를 썼다. Qt API는 설치된 바인딩으로 확인했다.

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
$env:QT_QPA_FONTDIR='C:/Windows/Fonts'
$env:PYTHONDONTWRITEBYTECODE='1'
$py='..\..\..\.venv\Scripts\python.exe'

& $py -m ruff check --no-cache tests/unit/test_store_query_link.py
& $py -m ruff format --check --no-cache tests/unit/test_store_query_link.py
git diff --check

& $py -X faulthandler -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a171/pt-41 tests/unit/test_store_query_link.py
& $py -X faulthandler -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a171/pt-42 tests/unit/test_store_query_link.py tests/unit/test_bridge.py tests/unit/test_ui_approvals.py tests/unit/test_call_logs.py
```

- Ruff 검사·포맷 검사·`git diff --check` 통과.
- 조회 연결 단독: 10 passed, 실패 0.
- QApplication 공유 화면 묶음: 82 passed, 실패 0.
- 별도 메모리 내 fixture 확인: QApplication 없는 경우 생성하지 않음, 닫힌 이벤트 루프 순환 참조 수거, 기존 위젯 보존, 새 위젯 실제 삭제, QApplication 재사용, GC의 메인 스레드 실행을 모두 확인했다. 추가 시험 파일은 만들지 않았다.

최소 재현은 다음 명령을 새 프로세스로 매회 실행했다. 수정 전은 `n=1..20`, 수정 후는 `n=21..40`이다.

```powershell
& $py -X faulthandler -m pytest -q -p no:cacheprovider --basetemp=.cache/codex-a171/pt-<n> tests/unit/test_store_query_link.py tests/unit/test_store_recovery.py
```

| 상태 | 정상 종료 | 중단 / 반복 | 종료 코드 | pytest 결과 |
| --- | ---: | ---: | --- | --- |
| 수정 전 HEAD, fixture 없음 | 0 | 20/20 | 매회 3 | 중단되어 최종 집계 없음 |
| 수정 후 | 20 | 0/20 | 매회 0 | 매회 47 passed, 실패 0(합계 940 passed) |

- 검수 재실행(Codex 샌드박스 밖, 같은 명령): HEAD 바이트로 잠시 되돌린 수정 전 3/3 중단, 수정 후 5/5 정상 종료(매회 47 passed). 되돌린 뒤 수정 파일의 SHA-256 일치를 확인했다. 화면 묶음 82 passed.
- 수정 전 20회를 모두 마친 뒤 fixture를 추가했다. 수정 전 파일의 SHA-256이 HEAD 바이트와 같고 반복 중 바뀌지 않았음을 확인했다. 수정 후 반복 중에도 파일의 SHA-256은 같았다.
- 수정 전 20회 모두 `Fatal Python error: Aborted`가 있었고, 모든 스레드 스택을 `.cache/codex-a171/abort-1.txt`부터 `abort-20.txt`까지 남겼다. 모두 `test_send_unknown_wins_over_later_read` 실행 중이었다. C 리뷰의 중단 위치와 다르다.
- 회차별 종료 코드·Fatal 여부·완료 집계와 파일 SHA-256은 `.cache/codex-a171/before-results.json`, `after-results.json`에 있다. 이 파일들과 스택은 gitignore된 로컬 검증 산출물이다. 예외 메시지 원문·인증정보·전체 도구 인자는 옮기지 않았다.

## 남은 것

- 이 환경의 지정된 순서에서는 중단이 수정 전 20/20에서 수정 후 0/20으로 줄었다. 어떤 QObject가 어느 스레드에서 수거됐는지는 직접 추적하지 않아 원인은 여전히 미확정이다. 두 정리 단계 중 어느 단계가 필요한지도 분리 측정하지 않았다.
- 전체 시험은 요청대로 실행하지 않았다. 다른 시험 순서·환경과 C의 독립 재검증, #92 병합 판단은 남아 있다. dispatch·wire·부작용 계수는 이번 범위에서 측정하지 않았다.
- `src/`, `test_store_recovery.py`를 포함한 B·C 소유 파일과 공통 문서는 바꾸지 않았다. A151의 C 경로 비교 수정과 수정 뒤 L2 재확인은 이번 작업에 없다.
