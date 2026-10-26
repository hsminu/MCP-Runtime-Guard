# A46 · 첫 HTTP 연결의 httpcore 최초 import 정지 제거

[A46 TLS 기록](A46-tls-off-loop.md)에 남은 한계로 적었던 첫 연결 정지를 다룬 후속 작업이다.

## 한 일

- httpx는 `AsyncHTTPTransport()`를 처음 만들 때 그 안에서 `httpcore`를 import한다. 그래서 백엔드 루프의 첫 HTTP 연결이 이 import 시간만큼 멈췄다.
- `src/guard_app/adapters/mcp_transport.py`가 모듈을 불러올 때 `httpcore`를 함께 import하도록 바꿨다. `httpcore`는 httpx가 의존하는 패키지로 `uv.lock`에 이미 고정되어 있고, `pyproject.toml`은 바꾸지 않았다.
- 시연 진입점(`__main__.py`)은 백엔드 루프를 시작하기 전에 메인 스레드에서 `examples.synthetic_app`과 이 모듈을 불러온다. 그래서 import 비용이 루프 밖, 앱 시작 단계로 옮겨진다.
- 회귀 테스트 `test_httpcore_is_imported_with_the_transport_module`(`tests/unit/test_mcp_http.py`)를 추가했다.

## 실제 검증

- 측정 방법은 A46 TLS 기록과 같다. 새 프로세스에서 닫힌 loopback 포트로 6번 열고 닫으며 `sleep(0)` ticker로 최대 루프 간격을 쟀고, 프로세스 3개로 반복했다.

  | 구분 | 첫 연결 | 2–6번째 연결 |
  | --- | --- | --- |
  | TLS 수정 후, 이번 수정 전 | 75.8–81.3ms | 0.2–3.1ms |
  | 이번 수정 후 | 10.2–10.8ms | 0.1–1.5ms |

- `ruff check`·`ruff format --check`·`mypy` 통과. 전체 `pytest -q` 결과는 같은 커밋 메시지에 남긴다.

## 남은 한계

- 첫 연결에 약 10ms가 남아 있다. 원인은 분석하지 않았다.
- C 측정값(210–280ms)은 이 PC에서 재현되지 않았다. C 환경에서 다시 측정해야 한다.
- 제품 시작 경로(A24–A27)가 생기면, 이 모듈을 루프 시작 전에 불러오는지 그때 다시 확인해야 한다.

## 관련

- 가용성(제어·승인 처리 지연). I6와는 무관하다.
- 커밋: 이 기록과 같은 커밋. PR 없음.
