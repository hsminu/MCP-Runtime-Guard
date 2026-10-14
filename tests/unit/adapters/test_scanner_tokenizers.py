"""I9: Scanner SDK import가 tokenizer를 외부에서 받지 않고 검증한 로컬 사본만 쓰는지 검증."""

import hashlib
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

from guard_app.security.secrets import (
    TokenizerCacheError,
    prepare_tokenizer_cache,
    scanner_tokenizer_cache_dir,
)

_EXPECTED = {
    "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    ),
    "fb374d419588a4632f3f557e76b4b70aebbca790": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    ),
    "ec7223a39ce59f226a68acc30dc1af2788490e15": (
        "94b5ca7dff4d00767bc256fdd1b27e5b17361d7b8a5f968547f9f23eb70d2069"
    ),
}
_SRC = Path(__file__).resolve().parents[3] / "src"

# 새 프로세스에서 네트워크를 막고 실제 SDK를 import한다. 한 번 import한 litellm은
# 같은 프로세스에서 다시 초기화되지 않으므로 별도 프로세스가 필요하다.
_IMPORT_SCRIPT = """
import importlib
import socket

attempts = []


def blocked(*args, **kwargs):
    attempts.append(args[1:2])
    raise OSError("network blocked by test")


socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.getaddrinfo = blocked

from guard_app.security.secrets import scanner_sdk_import_environment

with scanner_sdk_import_environment():
    importlib.import_module("mcpscanner.core.analyzers.yara_analyzer")
    importlib.import_module("mcpscanner.core.analyzers.llm_analyzer")

import tiktoken

for name in ("cl100k_base", "o200k_base", "p50k_base"):
    tiktoken.get_encoding(name)
print("ATTEMPTS", len(attempts))
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundled_listing() -> list[tuple[str, int]]:
    base = Path(str(distribution("litellm").locate_file("litellm/litellm_core_utils/tokenizers")))
    return sorted((item.name, item.stat().st_size) for item in base.iterdir() if item.is_file())


def _verified_content(key: str) -> bytes:
    data = Path(
        str(distribution("litellm").locate_file(f"litellm/litellm_core_utils/tokenizers/{key}"))
    ).read_bytes()
    if hashlib.sha256(data).hexdigest() != _EXPECTED[key]:
        data = data.replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == _EXPECTED[key]
    return data


@pytest.fixture
def cache_root(monkeypatch, tmp_path):
    for name in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    return tmp_path


def test_sdk_import_uses_verified_local_tokenizers_without_network(cache_root):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CUSTOM_TIKTOKEN_CACHE_DIR", "TIKTOKEN_CACHE_DIR", "PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(_SRC)
    before = _bundled_listing()
    result = subprocess.run(  # noqa: S603 - 고정 스크립트를 현재 인터프리터로 실행한다.
        [sys.executable, "-c", _IMPORT_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "ATTEMPTS 0"
    cache = scanner_tokenizer_cache_dir()
    assert cache.is_relative_to(cache_root)
    assert {item.name: _sha256(item) for item in cache.iterdir()} == _EXPECTED
    # 해시가 맞지 않는 SDK 내장 파일을 tiktoken이 지우고 다시 받는 경로를 타지 않는다.
    assert _bundled_listing() == before


def test_crlf_bundle_is_normalized_and_verified(tmp_path):
    def read_crlf(key: str) -> bytes:
        return _verified_content(key).replace(b"\n", b"\r\n")

    cache = tmp_path / "cache"
    prepare_tokenizer_cache(cache, read_bundled=read_crlf)
    assert {item.name: _sha256(item) for item in cache.iterdir()} == _EXPECTED


def test_unverifiable_bundle_fails_with_fixed_code_and_writes_nothing(tmp_path):
    cache = tmp_path / "SENSITIVE_PATH_MARKER"
    with pytest.raises(TokenizerCacheError) as error:
        prepare_tokenizer_cache(cache, read_bundled=lambda key: b"tampered\r\n")
    assert str(error.value) == "SCAN_SDK_TOKENIZER_INVALID"
    assert "SENSITIVE_PATH_MARKER" not in repr(error.value)
    assert not cache.exists() or list(cache.iterdir()) == []


def test_tampered_cache_file_is_replaced_and_valid_file_is_kept(tmp_path):
    cache = tmp_path / "cache"
    prepare_tokenizer_cache(cache, read_bundled=_verified_content)
    tampered, kept = list(_EXPECTED)[:2]
    (cache / tampered).write_bytes(b"tampered")
    kept_mtime = (cache / kept).stat().st_mtime_ns
    reads = []

    def counting_reader(key: str) -> bytes:
        reads.append(key)
        return _verified_content(key)

    prepare_tokenizer_cache(cache, read_bundled=counting_reader)
    assert reads == [tampered]
    assert _sha256(cache / tampered) == _EXPECTED[tampered]
    assert (cache / kept).stat().st_mtime_ns == kept_mtime
    assert sorted(item.name for item in cache.iterdir()) == sorted(_EXPECTED)
