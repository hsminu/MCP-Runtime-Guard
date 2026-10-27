"""A111: static rules over A-owned modules that restructuring must keep.

A's own guard while it refactors its code. Project-wide static enforcement
(import-linter, Semgrep, CI) stays C's (C6.3); these checks read only A modules.

- I1/T08: the MCP transport is built only inside Guard assembly, and the `mcp`
  SDK is imported only inside the transport package (technical design 2.2); the
  UI reaches the backend only through the bridge.
- 13.1: risky operations (processes, files, SQL, sockets) go through C's and B's
  designated modules, never A's.
- AGENTS 2026-10-18: broad exception handling only at the designated boundary.
- I9: no raw output channel besides the entry point's fixed lines; the UI never
  renders model or tool text as rich text.

A module added by the refactoring must be classified below, or the
classification test fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "guard_app"

A_SERVICES = frozenset(
    {
        "bridge",
        "codex_account",
        "codex_composition",
        "codex_preparation",
        "codex_prompt",
        "codex_session",
        "codex_turns",
        "guard_relay",
        "policy_view",
        "product_assembly",
        "runner",
        "session_admission",
        "session_cleanup",
        "session_policy",
        "session_preparation",
        "session_runtime",
        "session_startup",
        "session_stops",
        "task_tool_calls",
    }
)
A_ADAPTERS = frozenset(
    {
        "access_rules_checker",
        "codex_protocol",
        "codex_stream",
        "jsonrpc_client",
        "mcp_http_ingress",
        "mcp_ingress",
        "mcp_transport",
    }
)
# B and C modules are not checked here.
OTHER_SERVICES = frozenset(
    {
        "approvals",
        "catalog",
        "catalog_schema",
        "catalog_sources",
        "guard",
        "registry",
        "response_gate",
        "state_checks",
    }
)
OTHER_ADAPTERS = frozenset({"cisco", "cisco_aggregation", "cisco_llm"})

FORBIDDEN_IMPORTS = frozenset(
    {
        "ctypes",
        "marshal",
        "multiprocessing",
        "pickle",
        "shelve",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "tempfile",
    }
)
FORBIDDEN_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
# Where a broad handler is allowed: (module, class, function).
BROAD_EXCEPT_ALLOWED = frozenset({("adapters.mcp_transport.worker", "ServerWorker", "_execute")})
RICH_TEXT_NAMES = frozenset({"setHtml", "insertHtml", "RichText", "QTextBrowser", "QWebEngineView"})


# A's MCP package (A113). The `mcp` SDK is imported only inside it.
TRANSPORT = "adapters.mcp_transport"


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _modules(package: str, names: frozenset[str] | None = None) -> list[tuple[str, Path]]:
    """Modules directly in `package`; with `names`, only those (a package counts by name)."""
    folder = SRC.joinpath(*package.split("."))
    found = sorted(path for path in folder.glob("*.py") if path.stem != "__init__")
    found += sorted(
        path / "__init__.py" for path in folder.iterdir() if (path / "__init__.py").exists()
    )
    return [
        (_module_name(path), path)
        for path in found
        if names is None or _module_name(path).rsplit(".", 1)[1] in names
    ]


def a_modules() -> list[tuple[str, Path]]:
    return [
        ("__main__", SRC / "__main__.py"),
        *_modules("ui"),
        *_modules("services", A_SERVICES),
        *_modules("adapters", A_ADAPTERS),
        *_modules(TRANSPORT),
    ]


def _in_transport(module: str) -> bool:
    return module == TRANSPORT or module.startswith(f"{TRANSPORT}.")


def _imports(tree: ast.AST, module: str, *, is_package: bool = False) -> set[str]:
    """Imported module names; `from a import b` also counts as `a.b` (b may be a module)."""
    package = module if is_package else module.rsplit(".", 1)[0] if "." in module else ""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = f"guard_app.{package}" if package else "guard_app"
                source = f"{base}.{node.module}" if node.module else base
            elif node.module:
                source = node.module
            else:
                continue
            names.add(source)
            names.update(f"{source}.{alias.name}" for alias in node.names)
    return names


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text("utf-8"), filename=str(path))


A_MODULES = a_modules()
A_IDS = [name for name, _path in A_MODULES]


def test_every_service_and_adapter_module_is_classified() -> None:
    """A new module must be listed as A's (checked) or B's/C's (not checked)."""
    for package, ours, others in (
        ("services", A_SERVICES, OTHER_SERVICES),
        ("adapters", A_ADAPTERS, OTHER_ADAPTERS),
    ):
        present = {name.split(".")[1] for name, _ in _modules(package)}
        assert present == ours | others, package
        assert not ours & others


@pytest.mark.parametrize(("module", "path"), A_MODULES, ids=A_IDS)
def test_layer_imports(module: str, path: Path) -> None:
    imports = {
        name
        for name in _imports(_tree(path), module, is_package=path.stem == "__init__")
        if name.startswith("guard_app")
    }
    if module.startswith("ui."):
        allowed = ("guard_app.ui", "guard_app.services.bridge", "guard_app.domain")
        assert all(name.startswith(allowed) for name in imports), sorted(imports)
    if module.startswith("adapters."):
        assert not any(name.startswith(("guard_app.services", "guard_app.ui")) for name in imports)
    if module != "__main__":
        assert not any(name.startswith("guard_app.ui") for name in imports) or module.startswith(
            "ui."
        )


def _source_imports() -> dict[str, set[str]]:
    return {
        _module_name(path): _imports(
            _tree(path), _module_name(path), is_package=path.stem == "__init__"
        )
        for path in SRC.rglob("*.py")
    }


def test_only_guard_assembly_builds_the_mcp_transport() -> None:
    """I1/T08: outside its own package, no source module can reach the transport."""
    importers = {
        module
        for module, names in _source_imports().items()
        if not _in_transport(module)
        and any(_in_transport(name.removeprefix("guard_app.")) for name in names)
    }
    assert importers == {"services.product_assembly"}


def test_only_the_transport_package_imports_the_mcp_sdk() -> None:
    """Technical design 2.2 (I1): no raw MCP client path outside the transport."""
    importers = {
        module
        for module, names in _source_imports().items()
        if any(name == "mcp" or name.startswith("mcp.") for name in names)
    }
    assert importers  # the transport itself uses the SDK
    assert all(_in_transport(module) for module in importers), sorted(importers)


@pytest.mark.parametrize(("module", "path"), A_MODULES, ids=A_IDS)
def test_no_risky_operation_or_output_channel(module: str, path: Path) -> None:
    tree = _tree(path)
    roots = {
        name.split(".")[0] for name in _imports(tree, module, is_package=path.stem == "__init__")
    }
    assert not roots & FORBIDDEN_IMPORTS, sorted(roots & FORBIDDEN_IMPORTS)
    if module != "__main__":
        assert "os" not in roots  # the entry point's os._exit only (A110)
    if "logging" in roots:
        assert module == f"{TRANSPORT}.sdk_logging"  # installs the redacting record factory
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_CALLS, node.func.id
            if node.func.id == "print":
                assert module == "__main__", node.lineno


@pytest.mark.parametrize(("module", "path"), A_MODULES, ids=A_IDS)
def test_broad_exception_handling_only_at_the_designated_boundary(module: str, path: Path) -> None:
    def broad(handler: ast.ExceptHandler) -> bool:
        kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        return any(
            kind is None
            or (isinstance(kind, ast.Name) and kind.id in ("Exception", "BaseException"))
            for kind in kinds
        )

    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, owner: str, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name, function)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, owner, child.name)
            else:
                if isinstance(child, ast.ExceptHandler) and broad(child):
                    found.add((module, owner, function))
                visit(child, owner, function)

    visit(_tree(path), "", "")
    assert found <= BROAD_EXCEPT_ALLOWED, sorted(found)


@pytest.mark.parametrize(("module", "path"), _modules("ui"), ids=[n for n, _ in _modules("ui")])
def test_ui_never_renders_rich_text(module: str, path: Path) -> None:
    """Model, server and tool text is plain text only (A47, contracts 2026-10-15)."""
    for node in ast.walk(_tree(path)):
        name = (
            node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else None
        )
        assert name not in RICH_TEXT_NAMES, (module, getattr(node, "lineno", None))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setOpenExternalLinks"
        ):
            assert all(isinstance(arg, ast.Constant) and arg.value is False for arg in node.args)
