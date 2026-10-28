"""security.ids(C2.7, 개발문서 13.1) 단위 테스트와 정적 검사.

정적 검사는 C2.9·C6.3의 Semgrep 규칙이 생기기 전까지의 임시 강제다.
- `src` 전체: `random` 모듈을 들이지 않는다.
- C 모듈: ID·토큰·임시 이름을 `security/ids.py`로만 만든다.
  `uuid`·표준 `secrets`·`os.urandom`을 직접 쓰지 않는다.
"""

from __future__ import annotations

import ast
import re
import secrets
from pathlib import Path

import pytest

from guard_app.security import ids
from guard_app.security.ids import new_id, new_suffix, new_token, token_matches

SRC = Path(__file__).resolve().parents[2] / "src" / "guard_app"
# C가 주 담당인 제품 모듈(team-workflow 파일 소유권). 새 C 모듈을 만들면 여기에 더한다.
C_MODULES = (
    "adapters/cisco.py",
    "adapters/cisco_aggregation.py",
    "adapters/cisco_llm.py",
    "security/codex_home.py",
    "security/codex_l2.py",
    "security/endpoints.py",
    "security/launcher.py",
    "security/paths.py",
    "security/redact.py",
    "security/secrets.py",
    "security/settings_io.py",
    "services/registry.py",
    "services/scanner.py",
)
HEX32 = re.compile(r"[0-9a-f]{32}")


@pytest.mark.parametrize("prefix", ["a", "l2", "scan", "request", "approval", "a" * 32])
def test_new_id_is_prefix_and_128_bit_hex(prefix):
    value = new_id(prefix)
    head, _, tail = value.partition("-")
    assert head == prefix
    assert HEX32.fullmatch(tail)


@pytest.mark.parametrize(
    "prefix",
    ["", "A", "Scan", "1a", "a-b", "a_b", "a b", "ä", "a" * 33, "scan\n", None, b"scan", 1],
)
def test_new_id_rejects_bad_prefix(prefix):
    with pytest.raises(ValueError, match="ID 접두어가 유효하지 않다"):
        new_id(prefix)


def test_new_suffix_is_128_bit_hex():
    assert HEX32.fullmatch(new_suffix())


@pytest.mark.parametrize(("nbytes", "length"), [(16, 22), (32, 43), (64, 86)])
def test_new_token_is_urlsafe_with_requested_entropy(nbytes, length):
    assert re.fullmatch(rf"[A-Za-z0-9_-]{{{length}}}", new_token(nbytes))


def test_new_token_default_is_32_bytes():
    assert len(new_token()) == 43


@pytest.mark.parametrize("nbytes", [0, 15, 65, -1, True, 32.0, "32", None])
def test_new_token_rejects_bad_length(nbytes):
    with pytest.raises(ValueError, match="토큰 길이가 유효하지 않다"):
        new_token(nbytes)


def test_values_do_not_repeat():
    count = 20_000
    assert len({new_id("x") for _ in range(count)}) == count
    assert len({new_suffix() for _ in range(count)}) == count
    assert len({new_token(16) for _ in range(count)}) == count


def test_generation_comes_from_secrets(monkeypatch):
    calls = []

    def token_hex(nbytes):
        calls.append(("token_hex", nbytes))
        return "0" * (nbytes * 2)

    def token_urlsafe(nbytes):
        calls.append(("token_urlsafe", nbytes))
        return "t" * nbytes

    monkeypatch.setattr(ids.secrets, "token_hex", token_hex)
    monkeypatch.setattr(ids.secrets, "token_urlsafe", token_urlsafe)
    assert new_id("scan") == "scan-" + "0" * 32
    assert new_suffix() == "0" * 32
    assert new_token(20) == "t" * 20
    assert calls == [("token_hex", 16), ("token_hex", 16), ("token_urlsafe", 20)]


def test_token_matches():
    token = new_token()
    assert token_matches(token, token)
    assert token_matches("Bearer 한글", "Bearer 한글")
    assert not token_matches(token, token[:-1])
    assert not token_matches(token, token + "x")
    assert not token_matches(token, token[:-1] + ("A" if token[-1] != "A" else "B"))
    assert not token_matches(token, "")


@pytest.mark.parametrize("received", [None, b"secret", 1, ["secret"]])
def test_token_matches_rejects_non_strings(received):
    assert not token_matches("secret", received)


@pytest.mark.parametrize("expected", ["", None, b"secret"])
def test_token_matches_never_accepts_empty_or_non_string_expected(expected):
    assert not token_matches(expected, expected)
    assert not token_matches(expected, "")


def test_token_matches_uses_compare_digest(monkeypatch):
    calls = []

    def compare_digest(a, b):
        calls.append((a, b))
        return False

    monkeypatch.setattr(ids.secrets, "compare_digest", compare_digest)
    assert not token_matches("abc", "abc")
    assert calls == [(b"abc", b"abc")]


def _module_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _random_uses(tree: ast.AST) -> list[str]:
    return sorted(
        name for name in _module_imports(tree) if name == "random" or name.startswith("random.")
    )


def _direct_generation(tree: ast.AST) -> list[str]:
    found = []
    for name in sorted(_module_imports(tree)):
        if name.split(".")[0] in ("uuid", "secrets") or name == "os.urandom":
            found.append(name)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "urandom"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            found.append("os.urandom")
    return found


@pytest.mark.parametrize(
    ("source", "random", "direct"),
    [
        ("import random", ["random"], []),
        ("from random import choice", ["random", "random.choice"], []),
        ("import uuid\nuuid.uuid4()", [], ["uuid"]),
        ("from uuid import uuid4", [], ["uuid", "uuid.uuid4"]),
        ("import secrets", [], ["secrets"]),
        ("from secrets import token_hex", [], ["secrets", "secrets.token_hex"]),
        ("import os\nos.urandom(16)", [], ["os.urandom"]),
        ("from os import urandom", [], ["os.urandom"]),
        ("from guard_app.security.secrets import load_scanner_key", [], []),
        ("from guard_app.security.ids import new_id", [], []),
        ("from . import secrets", [], []),
    ],
)
def test_static_checks_detect_what_they_claim(source, random, direct):
    tree = ast.parse(source)
    assert _random_uses(tree) == random
    assert _direct_generation(tree) == direct


def test_src_does_not_import_random():
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        uses = _random_uses(ast.parse(path.read_text(encoding="utf-8")))
        if uses:
            found[path.relative_to(SRC).as_posix()] = uses
    assert found == {}


def test_c_modules_generate_ids_only_through_ids_module():
    found = {}
    for name in C_MODULES:
        path = SRC / name
        uses = _direct_generation(ast.parse(path.read_text(encoding="utf-8")))
        if uses:
            found[name] = uses
    assert found == {}


def test_c_module_list_is_current():
    assert all((SRC / name).is_file() for name in C_MODULES)
    assert (SRC / "security" / "ids.py").is_file()
    # 이 시험이 보는 표준 `secrets`가 앱 모듈 `guard_app.security.secrets`와 섞이지 않았다.
    assert ids.secrets is secrets
