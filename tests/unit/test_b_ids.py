"""C2.7: B modules use the shared ID source and preserve audit ID format."""

import ast
import asyncio
import re
import time
from pathlib import Path

from guard_app.storage import SQLiteStore


def _b_module_paths() -> list[Path]:
    package = Path(__file__).resolve().parents[2] / "src" / "guard_app"
    paths = [package / "ports.py"]
    for name in ("core", "domain", "storage"):
        paths.extend((package / name).glob("*.py"))
    services = package / "services"
    paths.extend((services / "guard.py", services / "approvals.py"))
    paths.extend(services.glob("catalog*.py"))
    return sorted(paths)


def _id_generation_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {"uuid", "secrets", "random"}
    os_names = {"os"}
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".", 1)[0]
                if module in forbidden:
                    violations.append(f"{path.name}:{node.lineno}: {module}")
                if module == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module.split(".", 1)[0]
            if module in forbidden:
                violations.append(f"{path.name}:{node.lineno}: {module}")
            if node.module == "os" and any(alias.name == "urandom" for alias in node.names):
                violations.append(f"{path.name}:{node.lineno}: os.urandom")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "urandom"
            and isinstance(node.value, ast.Name)
            and node.value.id in os_names
        ):
            violations.append(f"{path.name}:{node.lineno}: os.urandom")
    return violations


def test_b_modules_do_not_generate_ids_directly():
    paths = _b_module_paths()
    assert paths
    violations = [item for path in paths for item in _id_generation_violations(path)]
    assert not violations, "\n".join(violations)


def test_explicit_store_default_audit_ids_have_shared_format_and_are_unique(tmp_path):
    path = tmp_path / "audit.db"

    def unexpected_backup(_reason):
        raise AssertionError("a new v3 store should not need a backup")

    async def scenario():
        audit_ids = []
        for run in ("run-1", "run-2"):
            store = SQLiteStore(path, startup="explicit")
            try:
                await store.prepare_schema(
                    deadline=time.monotonic() + 20, begin_backup=unexpected_backup
                )
                await store.recover(deadline=time.monotonic() + 20, run_id=run)
                rows = await store._run_on_writer_async(
                    lambda connection: connection.execute(
                        "SELECT event_id FROM audit_events WHERE event_type='RECOVERY_COMMITTED' "
                        "ORDER BY rowid"
                    ).fetchall()
                )
                audit_ids = [row[0] for row in rows]
            finally:
                store.close()
                assert await store.wait_closed(5)
        assert len(audit_ids) == 2
        assert len(set(audit_ids)) == 2
        assert all(re.fullmatch(r"audit-[0-9a-f]{32}", item) for item in audit_ids)

    asyncio.run(scenario())
