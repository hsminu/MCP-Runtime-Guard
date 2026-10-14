"""Contract 공통 대역: 외부 연결 차단, 사용자 캐시 격리, 제품 경로의 SDK import."""

import importlib
import socket
import threading
from unittest.mock import Mock

import pytest

from guard_app.security.secrets import scanner_sdk_import_environment

_REAL_CONNECT = socket.socket.connect
_REAL_SOCKETPAIR = socket.socketpair
_SELF_PIPE = threading.local()


def _socketpair(*args, **kwargs):
    # Windows asyncio는 이벤트 루프 self-pipe를 loopback connect로 만든다(socket.socketpair).
    # 이 내부 연결만 실제 connect를 쓰고, 그 밖의 connect는 loopback이라도 막는다.
    _SELF_PIPE.active = True
    try:
        return _REAL_SOCKETPAIR(*args, **kwargs)
    finally:
        _SELF_PIPE.active = False


def _install_block(patch, refuse):
    def guarded_connect(sock, *args):
        if getattr(_SELF_PIPE, "active", False):
            return _REAL_CONNECT(sock, *args)
        return refuse(*args)

    patch.setattr(socket.socket, "connect", guarded_connect)
    patch.setattr(socket.socket, "connect_ex", lambda sock, *args: refuse(*args))
    patch.setattr(socket, "socketpair", _socketpair)


@pytest.fixture(scope="session", autouse=True)
def scanner_sdk_loaded(tmp_path_factory):
    """SDK를 세션 시작 때 제품 import 환경으로 한 번, 외부 연결 없이 불러온다.

    litellm은 첫 import 때 tokenizer를 읽는다. 테스트 안의 직접 import가 먼저 실행돼도
    이미 불러온 모듈을 쓰므로 실행 순서·선택과 무관하게 같은 경로가 된다(C-CISCO-05).
    tokenizer 사본은 실제 사용자 캐시 대신 세션 임시 폴더에 둔다.
    """
    root = tmp_path_factory.mktemp("scanner-cache")
    refuse = Mock(side_effect=OSError("network blocked during SDK import"))
    with pytest.MonkeyPatch.context() as patch:
        for name in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
            patch.setenv(name, str(root))
        patch.delenv("CUSTOM_TIKTOKEN_CACHE_DIR", raising=False)
        with pytest.MonkeyPatch.context() as blocked:
            _install_block(blocked, refuse)
            with scanner_sdk_import_environment():
                for module in (
                    "mcpscanner.config",
                    "mcpscanner.core.analyzers.llm_analyzer",
                    "mcpscanner.core.analyzers.yara_analyzer",
                ):
                    importlib.import_module(module)
        assert refuse.call_count == 0
        yield root


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    connect = Mock(side_effect=AssertionError("Unexpected live network in contract test"))
    _install_block(monkeypatch, connect)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    yield connect
    assert connect.call_count == 0
