"""C2.10: Scanner LLM 키의 Windows 자격증명 관리자 저장·로딩(2026-10-15 결정).

실제 자격증명 관리자에 테스트마다 새 대상 이름으로 쓰고 지운다. 제품 대상(사용자 실제 키)은
건드리지 않는다. 로그온 세션이 없는 환경(일부 CI 서비스 계정)에서는 저장소를 쓸 수 없어 건너뛴다.
"""

import ctypes
import logging
import os
import sys
import uuid

import pytest

from guard_app.security import secrets
from guard_app.security.secrets import (
    SCANNER_KEY_TARGET,
    ScannerKeyError,
    delete_scanner_key,
    load_scanner_environment,
    load_scanner_key,
    save_scanner_key,
    scanner_key_environ,
    scanner_key_tail,
    store_scanner_key,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="자격증명 관리자는 Windows 전용이다"
)

KEY = "sk-proj-TESTONLY0123456789abcdefWXYZ"
OTHER = "sk-proj-TESTONLY9876543210zyxwvuQRST"


@pytest.fixture
def target():
    name = f"MCPRuntimeGuard/test-{uuid.uuid4().hex}"
    try:
        store_scanner_key(KEY, target=name)
    except ScannerKeyError as error:
        if str(error) == "SCAN_KEY_STORE_UNAVAILABLE":
            pytest.skip("이 환경에는 자격증명 관리자 로그온 세션이 없다")
        raise
    delete_scanner_key(target=name)
    try:
        yield name
    finally:
        delete_scanner_key(target=name)


def _raw_credential(name):
    pointer = secrets._CredentialPointer()
    assert secrets._CredReadW(name, 1, 0, ctypes.byref(pointer))
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return credential.Type, credential.Persist, blob
    finally:
        secrets._CredFree(pointer)


def _write_raw(name, blob: bytes):
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = secrets._Credential(
        Type=1,
        TargetName=name,
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=2,
    )
    assert secrets._CredWriteW(ctypes.byref(credential), 0)


def test_key_round_trips_through_the_user_credential_store(target):
    assert load_scanner_key(target=target) is None
    store_scanner_key(KEY, target=target)
    assert load_scanner_key(target=target) == KEY
    assert scanner_key_tail(target=target) == "WXYZ"
    kind, persist, blob = _raw_credential(target)
    assert (kind, persist) == (1, 2)  # 일반 자격증명, 이 컴퓨터의 현재 사용자
    assert blob == KEY.encode("ascii")
    store_scanner_key(OTHER, target=target)  # 덮어쓰기
    assert load_scanner_key(target=target) == OTHER
    assert delete_scanner_key(target=target) is True
    assert load_scanner_key(target=target) is None
    assert scanner_key_tail(target=target) is None
    assert delete_scanner_key(target=target) is True  # 없어도 True


@pytest.mark.parametrize(
    "key",
    [
        None,
        b"sk-proj-TESTONLY0123456789",
        "",
        "sk-short",
        "sk-proj TESTONLY0123456789abcdef",
        " sk-proj-TESTONLY0123456789abcdef",
        "sk-proj-TESTONLY0123456789abcdef\n",
        '"sk-proj-TESTONLY0123456789abcdef"',
        "sk-proj-TESTONLY0123456789abcdé",
        "s" * 513,
    ],
    ids=[
        "none",
        "bytes",
        "empty",
        "short",
        "space",
        "lead",
        "newline",
        "quotes",
        "non-ascii",
        "long",
    ],
)
def test_malformed_keys_are_refused_and_nothing_is_written(target, key):
    with pytest.raises(ScannerKeyError) as raised:
        store_scanner_key(key, target=target)
    assert str(raised.value) == "SCAN_KEY_INVALID"
    assert load_scanner_key(target=target) is None


@pytest.mark.parametrize(
    "name", ["Other/key", "MCPRuntimeGuard/", "MCPRuntimeGuard/a/b", "MCPRuntimeGuard/a b", 1]
)
def test_only_app_targets_are_used(name):
    for call in (
        lambda: store_scanner_key(KEY, target=name),
        lambda: load_scanner_key(target=name),
        lambda: delete_scanner_key(target=name),
    ):
        with pytest.raises(ScannerKeyError) as raised:
            call()
        assert str(raised.value) == "SCAN_KEY_TARGET_INVALID"


def test_foreign_stored_value_is_not_used(target):
    _write_raw(target, b"not a key value with spaces")
    with pytest.raises(ScannerKeyError) as raised:
        load_scanner_key(target=target)
    assert str(raised.value) == "SCAN_KEY_STORED_INVALID"
    assert scanner_key_environ(target=target) == {}
    _write_raw(target, b"\xff\xfe" + b"a" * 30)
    with pytest.raises(ScannerKeyError) as raised:
        load_scanner_key(target=target)
    assert str(raised.value) == "SCAN_KEY_STORED_INVALID"


def test_environ_for_the_scanner_comes_only_from_the_store(target, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", OTHER)
    assert scanner_key_environ(target=target) == {}
    store_scanner_key(KEY, target=target)
    values = scanner_key_environ(target=target)
    assert values == {"OPENAI_API_KEY": KEY}
    # 검사기 설정 로딩은 넘긴 값만 쓴다.
    assert load_scanner_environment(environ=values)["MCP_SCANNER_LLM_API_KEY"] == KEY


def test_key_never_reaches_logs_errors_or_the_process_environment(target, caplog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    caplog.set_level(logging.DEBUG)
    store_scanner_key(KEY, target=target)
    load_scanner_key(target=target)
    scanner_key_environ(target=target)
    with pytest.raises(ScannerKeyError) as raised:
        store_scanner_key(KEY + " ", target=target)
    assert KEY not in caplog.text
    assert KEY not in str(raised.value) + repr(raised.value)
    assert raised.value.__cause__ is None
    assert "OPENAI_API_KEY" not in os.environ
    assert KEY not in os.environ.values()


@pytest.mark.parametrize(
    ("error", "code"),
    [(1312, "SCAN_KEY_STORE_UNAVAILABLE"), (5, "SCAN_KEY_STORE_FAILED")],
    ids=["no-logon-session", "access-denied"],
)
def test_store_errors_become_fixed_codes(monkeypatch, error, code):
    def failing(*args):
        ctypes.set_last_error(error)
        return 0

    for name in ("_CredWriteW", "_CredReadW", "_CredDeleteW"):
        monkeypatch.setattr(secrets, name, failing)
    target = f"MCPRuntimeGuard/test-{uuid.uuid4().hex}"
    for call in (
        lambda: store_scanner_key(KEY, target=target),
        lambda: load_scanner_key(target=target),
        lambda: delete_scanner_key(target=target),
    ):
        with pytest.raises(ScannerKeyError) as raised:
            call()
        assert str(raised.value) == code
    assert scanner_key_environ(target=target) == {}


def test_screen_saver_reports_only_success(monkeypatch):
    """A `ScannerKeyForm`의 `KeySaver`. 제품 대상에 쓰지 않도록 저장 함수를 대역으로 바꾼다."""
    seen = []
    monkeypatch.setattr(secrets, "store_scanner_key", lambda key: seen.append(key))
    assert save_scanner_key(KEY) is True
    assert seen == [KEY]

    def refuse(key):
        raise ScannerKeyError("SCAN_KEY_INVALID")

    monkeypatch.setattr(secrets, "store_scanner_key", refuse)
    assert save_scanner_key("bad") is False


def test_product_target_name_is_the_app_one():
    assert SCANNER_KEY_TARGET == "MCPRuntimeGuard/ScannerLLMKey"
