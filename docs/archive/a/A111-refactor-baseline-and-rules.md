# A111 · 리팩터링 0단계: 기준선과 A 모듈 규칙 테스트

2026-10-27. A 담당 코드를 객체 지향적으로, 캡슐화가 잘 되고, 테스트하기 쉽고, 시큐어 코딩에 맞게 다시 짜는 리팩터링의 0단계다. 동작은 바꾸지 않는다. 이 단계에서는 바꾸는 동안 지켜야 할 규칙을 테스트로 고정하고, 비교할 기준선을 남긴다.

## 한 일

- `tests/unit/test_a_architecture.py`: A 모듈만 읽는 정적 규칙 테스트
  - **분류:** `services/`·`adapters/`의 모든 모듈은 A 것(검사 대상)이거나 B·C 것(제외)으로 분류돼 있어야 한다. 리팩터링으로 새 모듈이 생기면 분류하기 전까지 실패한다.
  - **계층(I1·T08):**
    - UI는 `ui`·`services.bridge`·`domain`만 가져다 쓴다.
    - A adapters는 services·ui를 가져다 쓰지 않는다.
    - `__main__` 말고는 아무도 ui를 가져다 쓰지 않는다.
  - **transport(I1):** 소스 전체에서 MCP transport 모듈을 가져다 쓰는 곳은 Guard 조립(`services.product_assembly`)뿐이다. `from X import Y` 형태도 센다.
  - **위험 연산(13.1):**
    - A 모듈은 `subprocess`·`shutil`·`sqlite3`·`socket`·`tempfile`·`pickle`·`marshal`·`shelve`·`ctypes`·`multiprocessing`을 가져다 쓰지 않는다.
    - `os`는 `__main__`의 `os._exit`(A110)에서만, `logging`은 원문을 가리는 로그 레코드를 설치하는 `mcp_transport`에서만 쓴다.
    - `eval`·`exec`·`compile`·`__import__`는 쓰지 않는다.
  - **출력(I9):** `print`는 `__main__`의 고정 문구에서만 쓴다.
  - **넓은 예외 처리(AGENTS 2026-10-18):** 넓은 `except`(맨 except, `Exception`, `BaseException`)는 지정 경계 `ServerWorker._execute`에서만 허용한다.
  - **비신뢰 텍스트(A47):** UI에 `setHtml`·`insertHtml`·`RichText`·`QTextBrowser`가 없고, `setOpenExternalLinks`는 False로만 부른다.
- 기준선 측정: [근거](../evidence/a111-refactor-baseline-2026-10-27.json)

| 항목 | 기준선 |
| --- | --- |
| A 모듈(AGT·examples 제외) | 41개, 13,888줄 |
| 기준을 넘는 클래스(300줄 또는 메서드 20개 초과) | 12개. `BackendBridge` 1,135줄·49개, `SessionRuntime` 702줄·30개 등 |
| 기준을 넘는 함수(50줄 또는 분기 12 초과) | 64개 |
| A 단위 테스트 함수 | 582개 |
| 테스트의 비공개 속성 접근 | 242곳 |

## 검증

- 지금 코드에서 규칙 테스트 145개(모듈별 매개변수 포함)가 모두 통과한다.
- 위반을 하나씩 일부러 넣어 테스트가 잡는지 봤고, 모두 해당 테스트가 실패했다. 넣은 위반은 UI→transport, 서비스→transport, 서비스→ui, 넓은 except, `subprocess`, `print`, 분류 안 된 새 모듈이다.
  - 첫 시도에서 `from guard_app.adapters import mcp_transport` 형태를 transport 검사가 놓쳐서 고쳤다.
- 전체 검사는 1단계와 함께 돌린다.

## 남은 한계

- 프로젝트 전체의 정적 강제 규칙(import-linter·Semgrep·CI)은 C 몫(C6.3)이다. 이 테스트는 A가 리팩터링하는 동안 A 모듈만 지키는 장치다. C가 CI 규칙으로 옮겨 갈 수 있다.
- 커버리지 도구(`pytest-cov`)가 없어 줄 단위 커버리지는 재지 않았다. 공통 의존성이라 넣으려면 main PR로 제안해야 한다(ENV-01).
- AST 검사라 문자열로 우회하는 동적 import(`importlib.import_module("...")`)는 잡지 않는다. 지금 A 코드에는 그런 import가 없다.

관련: I1·I9, T08, 개발문서 13.1·13.2. PR은 아직 없다(A 브랜치 작업).
