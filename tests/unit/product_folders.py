"""A composition double: real SQLite files, no claim about C's path/lock checks."""

from pathlib import Path
from typing import cast

from guard_app.security.paths import AppFolders


class _Folders:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def db_path(self) -> Path:
        return self.path

    def begin_backup(self, _label: str):
        raise AssertionError("This unit fixture only opens v3 or empty databases")


def product_folders(path: str | Path) -> AppFolders:
    return cast(AppFolders, _Folders(path))
