# A44 · 시연 앱에서 테스트 대역 import 제거

2026-10-28. `examples/synthetic_app.py`가 B 단위 테스트(`tests.unit.test_guard`)의 `_Store`·`_Registry`를 가져다 썼다. 시연 앱이 테스트 패키지에 기대면 테스트를 바꿀 때 시연이 같이 깨지고, `tests`가 없는 설치본에서는 시연을 띄울 수 없다. B AGT 제거([PR #47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47))가 병합돼 A 차례가 됐다.

## 한 일

- **Store:** 메모리 모드도 B `SQLiteStore`를 쓴다.
  - 이 실행만의 임시 폴더(`TemporaryDirectory`)에 DB 파일을 두고, Store를 닫은 뒤 지운다. 임시 폴더를 먼저 등록해 Store 닫기보다 나중에 정리된다.
  - 폴더 지우기 실패는 무시한다(`ignore_cleanup_errors=True`). Store 정리 실패(`BackendCleanupError`)를 가리지 않게 하려는 것이다.
  - 메모리 모드의 Task ID(`synthetic-task`)와 SQLite 모드의 새 Task ID 규칙은 그대로다. 두 모드 모두 Task를 Store에 만들고 다시 읽어 확인한다.
- **Registry:** 시연 파일 안에 `SyntheticRegistry`를 둔다. `Registry` 포트의 세 메서드만 있고, `docs` 서버 하나(ACTIVE, `read`·`send`, 고정 지문)를 돌려준다. 등록·저장은 하지 않는다. 이전 대역과 같은 도구 정의·경계·지문이다.
- 모듈 설명을 "B SQLiteStore, 고정 합성 Registry"로 고쳤다.

## 검증

- `tests/unit/test_runtime.py`: 메모리 모드 기본 `bootstrap()`으로 실제 stdio·HTTP MCP 승인·SEND, 접근 거절, 정지 흐름이 그대로 통과한다.
- `tests/unit/test_sqlite_app.py`: SQLite 모드의 재시작·시작 실패 때 외부 자원 0·잘못된 옵션 거부가 그대로 통과한다.
- `tests/unit/test_ui_demo.py`: `examples/ui_demo.py`의 합성 백엔드("ask")가 그대로 뜬다.
- 전체(`QT_QPA_PLATFORM=offscreen`, Windows, 로컬 `.venv`, A44·A106·A123과 #40 주석을 함께 고친 상태)
  - 단위 5,139개 통과(A106 새 테스트 2개 포함), 4개 skip, subtest 20개, 실패 없음
  - contract 42개 통과
  - ruff check·ruff format --check(236개 파일)·mypy(101개 소스 파일) 통과

## 남은 것

- 메모리 모드라는 이름은 그대로다. 실제로는 실행마다 지우는 임시 SQLite 파일이다. `--store memory` 옵션 이름을 바꿀지는 정하지 않았다.
- `SyntheticRegistry`는 C `ServerRegistry`가 아니다. 실제 등록·지문 대조는 제품 조립(A86·A129) 경로에서만 본다.

관련: A43, B2.11. PR: A 브랜치([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)에 더할 예정).
