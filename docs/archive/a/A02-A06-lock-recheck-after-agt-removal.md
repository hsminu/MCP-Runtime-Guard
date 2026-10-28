# A02·A06 · AGT 제거 뒤 lock·새 환경 설치 재확인

2026-10-28. AGT는 2026-10-15 결정으로 제거했고(B2.11), [#47](https://github.com/hsminu/MCP-Runtime-Guard/pull/47)이 의존성과 코드를 지웠다. A02·A06은 AGT core 5.0.0이 들어간 lock으로 Python 3.12 새 환경 설치를 재현한 항목이었다([이전 기록](../tasks/a-agt-integration.md)). 지금 lock으로 같은 확인을 다시 했다. 코드와 lock 파일은 바꾸지 않았다.

## 확인한 것

도구는 저장소의 `.devtools`에 있는 uv 0.13.0과 CPython 3.12.15다(Windows x64).

1. **`uv lock --check`**: `pyproject.toml`과 `uv.lock`이 맞는다. 127개 패키지로 해석된다.
2. **`requirements.lock`이 `uv.lock`에서 나온 그대로인지.**
   - CI와 같은 명령(`uv export --locked --all-groups --no-emit-project`)으로 새로 만들어 비교했다.
   - 다른 곳은 머리 주석의 출력 경로 한 줄뿐이다(새로 만든 파일을 임시 폴더에 썼기 때문). 패키지·버전·해시는 같다.
3. **AGT가 남았는지.**
   - `pyproject.toml`, `uv.lock`, `requirements.lock`, `config/`에 AGT 패키지가 없다.
   - `src/`에는 ACCESS-01이 AGT를 대체했다는 설명 주석 한 줄만 있다(`adapters/access_rules_checker.py`).
4. **새 환경 설치.**
   - 새 폴더에 `UV_PROJECT_ENVIRONMENT`를 지정하고 `uv sync --locked --no-cache`로 설치했다. 캐시를 쓰지 않아 모든 배포판을 새로 받고 해시를 대조했다.
   - `uv pip check`: 설치된 126개 배포판의 선언된 의존성에 충돌이 없다.
   - 설치된 목록에 AGT 관련 배포판이 없다.
5. **새 환경에서 실행.**
   - `guard_app`·`mcp`·`PySide6` import와 `python -m guard_app --help`가 동작한다.
   - 단위 테스트와 contract 테스트 결과는 아래 표다.

| 검증(새 환경, `QT_QPA_PLATFORM=offscreen`) | 결과 |
| --- | --- |
| 단위 테스트 | 5,202개 통과, 4개 skip, subtest 20개, 실패 없음, 경고 1개 |
| contract 테스트 | 42개 통과 |

- 새 환경 테스트는 A08·A132·A133·A134를 작업하던 작업 트리(커밋 전)로 돌렸다. 그 뒤 A134에 반복 종료 처리를 더했다. 최종 작업 트리는 기존 `.venv`로 다시 돌렸고 개수가 같았다(각 기록의 검증 절).
- 경고 1개는 서드파티 경고다. `pydantic-settings`가 FastMCP 설정 필드 `lifespan`의 해석되지 않은 forward reference를 알린다(`IncompleteFieldDefinitionWarning`). 전체 실행에서 `tests/unit/test_access_rule_counts.py`의 HTTP 경우 한 곳에서만 난다. 새 환경과 기존 `.venv`에서 같은 테스트, 같은 경고였다. AGT와는 관계없다.

## 남은 것

- Scanner(`cisco-ai-mcp-scanner` 4.8.6)를 포함한 공통 제품 조합 인수는 C 몫이다(A02·A06 다음 줄, #6).
- CI는 같은 순서(locked 설치 → `requirements.lock` 일치 → Ruff → 포맷 → mypy → 단위 테스트)로 Windows에서 돈다. 이 확인은 로컬 한 대에서 한 것이다.
- PR: A 브랜치 PR [#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)
