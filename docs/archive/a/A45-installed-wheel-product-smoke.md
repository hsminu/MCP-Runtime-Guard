# A45 · 설치된 wheel의 제품 시작 검증

2026-10-18. 제품 `__main__.py`는 A65부터 `--demo`일 때만 examples를 import한다. 저장소 밖 새 venv에 wheel을 설치해 현재 제품 진입점의 checkout 의존 오류가 해소됐음을 검증했다. 이번에는 제품 코드를 추가 변경하지 않았다.

## 실행

기존 Python 3.12.15로 `python -m pip wheel --no-deps --wheel-dir .test-artifacts/a69-packaging/wheels .`를 실행했다. 저장소 밖 임시 폴더에 새 venv를 만들고 wheel과 선언된 의존성을 설치했다. 공통 uv.lock을 재현한 실행은 아니며 설치 목록은 ignored `freeze.txt`에 남겼다. 최종 A67/A68 코드가 끝난 뒤 wheel을 다시 빌드하고 같은 외부 venv에 `--no-deps --force-reinstall`했다.

최종 wheel SHA-256: `c857c44a410bd6b72cd60cae6aaf6811e6f628d3c52b5a36b89c3bc7ec7b961e`.

외부 cwd에서 `PYTHONPATH`를 비우고 `QT_QPA_PLATFORM=offscreen`으로 실행했다.

- `guard-app --help`: exit 0.
- 설치된 실제 제품 `main`: READY → 새 세션 요청 → CODEX_L2_UNAVAILABLE → CLOSED, exit 0.
- backend thread_done=true, SQLite Task 0행. C L2가 없는 상태에서 실행 Task를 만들지 않았다.
- `examples`·`tests` 런타임 import 0, wheel 내부 해당 파일 0.
- 최종 wheel의 JSON-RPC·Task FIFO 모듈 포함 및 작업 트리 소스와 바이트 일치 확인.
- `pip check`: No broken requirements found.

가시 창 없이 실행했으며 실제 Codex·모델·MCP 도구는 호출하지 않았다. QA 스크립트와 로그는 ignored `.test-artifacts/a69-packaging/installed_smoke.py`, `installed-smoke.json`, `final-wheel-report.json`, `help.log`, `pip-check.log`, `final-reinstall.log`에 있다.

## 한계

패키징의 checkout 밖 실행과 I11 시작 거부 경로를 확인한 것이다. 실제 C launcher/L1/L2, 설정/Profile 로딩, 시작 복구, T39–T46과 독립 제품 인수는 여전히 대기한다. B 저장소·C 실행기·공통 설정/lock은 수정하지 않았다. PR은 생성하지 않았다.
