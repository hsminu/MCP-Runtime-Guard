"""I9: Scanner 준비의 동기 작업이 호출한 이벤트 루프 밖에서 실행되는지 검증.

실제 SDK import(첫 연결 수 초), TLS 인증서 로딩, OpenAI 리소스 지연 import,
YARA 규칙 컴파일은 대역으로 바꾸고 실행 스레드만 기록한다.
"""

import asyncio
import os
import ssl
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from openai import AsyncOpenAI

from guard_app.adapters import cisco_aggregation, cisco_llm
from guard_app.security import secrets
from guard_app.security.secrets import scanner_sdk_import_environment


class SDKAnalyzer:
    async def analyze(self, content, context=None):
        return []


@pytest.fixture
def threads(monkeypatch):
    seen = {}

    def build_sdk_analyzer(settings):
        seen["sdk"] = threading.get_ident()
        return SDKAnalyzer(), frozenset()

    def create_ssl_context(**kwargs):
        seen["tls"] = threading.get_ident()
        assert kwargs == {"trust_env": False}
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def chat(client):
        seen["chat"] = threading.get_ident()
        return SimpleNamespace(completions=object())

    monkeypatch.setattr(cisco_llm, "_build_sdk_analyzer", build_sdk_analyzer)
    monkeypatch.setattr(cisco_llm.httpx, "create_ssl_context", create_ssl_context)
    monkeypatch.setattr(AsyncOpenAI, "chat", property(chat))
    return seen


def test_luna_setup_runs_blocking_steps_off_the_event_loop(threads):
    async def run():
        async with cisco_llm.connect_luna_scanner(
            environ={"OPENAI_API_KEY": "test_key"}, max_definition_bytes=10_000
        ):
            return threading.get_ident()

    loop_thread = asyncio.run(run())
    assert set(threads) == {"sdk", "tls", "chat"}
    assert loop_thread not in threads.values()


def test_test_transport_skips_tls_context(threads):
    async def run():
        async with cisco_llm.connect_luna_scanner(
            environ={"OPENAI_API_KEY": "test_key"},
            max_definition_bytes=10_000,
            transport=cisco_llm.httpx.MockTransport(lambda request: None),
        ):
            return threading.get_ident()

    loop_thread = asyncio.run(run())
    assert set(threads) == {"sdk", "chat"}
    assert loop_thread not in threads.values()


def test_yara_provider_is_built_off_the_event_loop(monkeypatch):
    seen = {}

    @asynccontextmanager
    async def unavailable_llm(*args, **kwargs):
        raise cisco_llm.ScannerLLMError("SCAN_LLM_KEY_MISSING_OR_INVALID")
        yield

    def build_yara_provider(max_definition_bytes, executor=None, timeout_s=None):
        seen["yara"] = threading.get_ident()
        return SimpleNamespace()

    monkeypatch.setattr(cisco_aggregation, "connect_luna_scanner", unavailable_llm)
    monkeypatch.setattr(cisco_aggregation, "_build_yara_provider", build_yara_provider)

    async def run():
        async with cisco_aggregation.connect_metadata_scanner(environ={}):
            return threading.get_ident()

    loop_thread = asyncio.run(run())
    assert seen["yara"] != loop_thread


def test_concurrent_sdk_setups_do_not_interleave_environment_restore(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "prepare_tokenizer_cache", lambda cache_dir, **kwargs: None)
    for name in ("PYTHON_DOTENV_DISABLED", "LITELLM_LOCAL_MODEL_COST_MAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CUSTOM_TIKTOKEN_CACHE_DIR", raising=False)
    for name in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    inside, release = threading.Event(), threading.Event()
    order = []

    def first():
        with scanner_sdk_import_environment():
            order.append("first-in")
            inside.set()
            assert release.wait(5)
            order.append("first-out")

    def second():
        with scanner_sdk_import_environment():
            order.append("second-in")

    one = threading.Thread(target=first)
    one.start()
    assert inside.wait(5)
    two = threading.Thread(target=second)
    two.start()
    two.join(0.2)
    # 첫 준비가 끝날 때까지 두 번째 준비는 환경변수를 건드리지 못한다.
    assert two.is_alive()
    assert order == ["first-in"]
    release.set()
    one.join(5)
    two.join(5)
    assert order == ["first-in", "first-out", "second-in"]
    assert "PYTHON_DOTENV_DISABLED" not in os.environ
