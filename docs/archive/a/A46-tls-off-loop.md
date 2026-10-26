# A46 · HTTP transport TLS 문맥을 이벤트 루프 밖에서 생성

## 한 일

- `http_stream_factory`의 연결 단계에서 `httpx.AsyncHTTPTransport(verify=True)`가 루프 안에서 인증서 묶음을 읽어 SSL 문맥을 만들던 것을, `asyncio.to_thread(httpx.create_ssl_context, trust_env=False)`로 작업 스레드에서 만든 문맥을 넘기도록 바꿨다(`src/guard_app/adapters/mcp_transport.py`). `cisco_llm.py`의 C-CISCO-06 대응과 같은 방식이다.
- 문맥 설정은 이전과 같다: `trust_env=False`의 certifi 기본 문맥, `CERT_REQUIRED`, hostname 검사. redirect 비활성·`retries=0`·요청 제한 hook은 그대로다.
- 회귀 테스트 `test_tls_context_is_built_off_the_event_loop_and_still_verifies`(`tests/unit/test_mcp_http.py`): 문맥이 루프 스레드가 아닌 곳에서 한 번 만들어지고, 검증 설정을 유지하며, 그 문맥이 transport에 그대로 전달되는지 확인한다. 수정 전 코드에서는 실패함을 확인했다.

## 실제 검증

- 측정: 새 프로세스마다 닫힌 loopback 포트(`127.0.0.1:9`)로 stream factory를 6번 열고 닫으며 `asyncio.sleep(0)` ticker로 최대 루프 간격을 쟀다. 프로세스 3개 반복. Windows `sleep` 해상도(약 15.6ms)를 피하려고 0초 ticker를 썼다.

  | 구분 | 첫 연결 | 2–6번째 연결 |
  | --- | --- | --- |
  | 수정 전 | 90.6–92.4ms | 5.9–9.6ms |
  | 수정 후 | 75.8–81.3ms | 0.2–3.1ms |

- 첫 연결에 남은 약 75ms는 cProfile로 확인한 결과 `AsyncHTTPTransport.__init__`의 `httpcore` 최초 import다. TLS 문맥 생성이 아니며 프로세스당 한 번 생긴다.
- 이 PC의 수정 전 값은 a-status의 C 측정값(210–280ms)과 다르다. 하드웨어·측정 방법 차이로 보며 원인은 확인하지 않았다.
- 검사: `uv sync --locked` 환경에서 `ruff check`·`ruff format --check`·`mypy` 통과, `pytest -q` 2331 통과(경고 5, subtest 20).
- HTTP 회귀: `tests/unit/test_mcp_http.py` 50개 통과(redirect 비활성, 응답 유실 시 재전송 0, 쿠키·프록시 미사용 포함).

## 남은 한계

- 첫 연결의 `httpcore` import 정지(약 75ms)는 남아 있다. 필요하면 앱 시작 때 미리 import하는 방식으로 따로 다룬다.
- 측정은 TLS가 없는 loopback 경로다. httpx는 scheme과 관계없이 transport 생성 때 문맥을 만들므로 같은 지점이지만, 실제 HTTPS handshake 비용은 측정하지 않았다.

## 관련

- I6(판단 불가는 거부·정지)에 영향 없음. 루프 정지는 Guard 판정·승인·제어 처리 지연(가용성) 문제다.
- 커밋: 이 기록과 같은 커밋. PR 없음(미푸시).
