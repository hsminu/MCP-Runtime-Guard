# A+B 통합: main 반영 순서와 공통 환경·검사

2026-10-14 작성. 브랜치 `chore/integrate-a-b`의 정리 내용과 main 반영 절차를 기록한다.
환경·검사 부분은 [ENV-01](ac-contract-review-request.md)에 대한 제안이며, PR 리뷰 승인으로 확정한다.
`domain/*`·`ports.py` 계약 합의를 대신하지 않는다.

## 기준과 병합 순서

- 기준: A `631e116`. A가 B `3307f5d`를 이미 병합했으므로 B에만 있는 커밋은 없다.
  main `4179f98`은 A·B 양쪽의 조상이다.
- 순서: **B → main**, **A → main**, **이 정리 브랜치 → main**. 셋 다 충돌이 없다.
  협업 규칙의 "B 공통 계약 먼저 반영"을 따른다.
- 병합 방식: 병합 커밋 또는 빨리감기. **squash·rebase는 쓰지 않는다.** A가 B를 여러 번 병합한
  이력이 끊기면 이후 B → A·main 병합이 충돌한다.
- 병합 후 A·B·C는 각자 브랜치에 `main`을 병합하고 아래 검사를 실행한다.

## 공통 환경

| 파일 | 역할 |
| --- | --- |
| `pyproject.toml` | 의존성·개발 도구·Ruff·mypy·pytest 설정의 유일한 원본 |
| `uv.lock` | 설치 조합 원본. `uv lock`으로만 갱신한다 |
| `requirements.lock` | `uv.lock`에서 생성한 해시 포함 목록. 설계 부록 A·13.5절의 `pip install`·`pip-audit`용. 직접 고치지 않는다 |

- jsonschema는 B가 검증한 4.26.0으로 맞췄다. A+B 전체 테스트가 4.26.0에서 통과했다.
  A의 `model_client`와 B의 `catalog_schema`가 직접 쓰는 `referencing`도 의존성에 명시했다.
- B의 개발 도구(mypy·import-linter·types-jsonschema)를 dev 그룹에 합쳤다.
- `config/b-core/pyproject.toml`은 루트 설정으로 옮기고 삭제했다.

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
# 의존성을 바꾼 뒤
uv lock
uv export --locked --all-groups --no-emit-project -o requirements.lock
```

## 검사 기준과 A 기준선

- Ruff 규칙과 strict mypy는 B 설정을 저장소 전체 기본값으로 쓴다. B 파일은 예외 없이 통과한다.
- A 파일에는 **통합 시점에 이미 있던 위반만** 파일별로 면제했다(`pyproject.toml`의 "통합 기준선").
  새 파일에는 기준선이 없다. 담당자가 파일을 고치면 해당 항목을 지운다.
  - Ruff: 파일별 규칙 면제. Qt 재정의 이름(N802·N815)은 영구 예외다.
  - 포맷: 미포맷 A 파일 22개를 포맷 검사에서 제외했다. A 작업과 충돌하지 않도록 여기서 일괄 포맷하지 않았다.
    A가 원하는 시점에 목록에서 파일을 지우고 `uv run ruff format .`을 실행한다.
  - mypy: A 모듈은 미타입 함수를 허용하되 본문 타입 오류는 검사한다. 남은 오탐 2종
    (`ui/runtime.py` union-attr, `__main__.py` attr-defined)만 모듈별로 끈다.
    `mcp_transport.py`의 SDK 콜백 인자 이름만 SDK 프로토콜에 맞춰 고쳤다.
  - `examples/`·`tests/`는 mypy 대상이 아니다. src에서 그쪽 import를 따라가지 않는다.
- B 테스트 `test_compiled_acceptance_always_satisfies_original_constraints`는 `.hypothesis` 캐시가 없을 때
  유니코드 표 생성 시간 때문에 항상 실패했다. 이 테스트만 `too_slow` 검사를 끈다.

## CI

`.github/workflows/ci.yml`은 설계 13.5절 중 현재 통과 가능한 단계를 Windows에서 실행한다.
locked 설치 → `requirements.lock` 일치 → Ruff → 포맷 → mypy → 단위 테스트(Qt offscreen).
action은 커밋 SHA로 고정했다. 비공개 저장소의 Windows 실행 시간은 사용량에 2배로 집계된다.

## 통합 검증 (2026-10-14, 새 clone·캐시 없음)

| 검사 | 결과 |
| --- | --- |
| `uv sync --locked`, `requirements.lock` 재생성 일치 | 통과 |
| `requirements.lock`만으로 해시 검증 설치 + `pip check` | 74개 호환 |
| Ruff check·format, mypy (52개 모듈) | 통과 |
| pytest | 2,227 passed, 0 failed |
| 앱 화면 실행(real AGT): stdio·메모리 승인 | 전송 2 · 수신 1 |
| 앱 화면 실행(real AGT): HTTP·SQLite 긴급 차단 | 전송 1 · 수신 0 |
| `examples.verify_stdio_spike --agt-mode real` | 8경로 기대 계수 일치 |

## 남은 일

| 담당 | 항목 |
| --- | --- |
| A | 기준선 축소(포맷 22개, Ruff 면제, mypy 완화). 시연 앱의 `tests.unit.test_guard` 대역 import 제거. `guard-app` 콘솔 명령은 `examples`가 패키지에 없어 checkout 밖에서 실패 |
| B | 정규식 검증 격리(SCHEMA-01). SQLite 잠금 대기 중 이벤트 루프 정지와 전역 정지(B-QA-03). 영속 순번·시작 복구 |
| C | CI 후속 단계(import-linter 규칙, Bandit, Semgrep, pip-audit, 통합 테스트). C 브랜치 병합 시 `README.md`·`.gitignore` 충돌 해결, Scanner 의존성을 `pyproject.toml`·`uv.lock`에 반영 |
| 사용자 | `domain/*`·`ports.py`를 합의 전 초안 상태로 main에 반영할지 결정하고 `contracts.md`에 기록 |
