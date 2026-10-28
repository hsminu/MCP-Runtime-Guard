"""The CFG-01 settings file at product start and on policy saves (A24–A27, A81, B3.12).

Reading is C then B: C's `read_settings_file` (C2.14) returns at most
SETTINGS_MAX_BYTES + 1 bytes and their generation, and B's
`parse_settings_bytes` (B3.11) removes the v1 envelope and judges the size.
No file is the first run: the app opens, refuses new Tasks and never saves an
empty draft on its own. A file that cannot be read or parsed refuses the app
start and is never overwritten (#33). Only the fixed code is reported. The
Profile in a parsed document is then checked on the backend loop against C's
Registry restored from B's store; an invalid one refuses new Tasks only.

Saving is B then C: B's `serialize_settings_bytes` of the prepared document,
then C's `replace_settings_file` against the generation last read or written.
The policy lock serializes saves, as C2.14 requires (#44 B review 2). A
failed or refused write leaves the file as it was, so the change is not
applied. A file changed, replaced or unreadable outside the app also refuses
every later save, so that case says to restart and read it again. A write
whose outcome is unknown may have left the new document in place, so the
caller must stop and ask for a restart (C2.14 table).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from guard_app.domain.errors import StoreError
from guard_app.ports import RegistryStore
from guard_app.security.paths import AppFolders, AppPathError
from guard_app.security.settings_io import (
    SettingsGeneration,
    SettingsIOError,
    read_settings_file,
    replace_settings_file,
)
from guard_app.services.catalog import (
    SETTINGS_MAX_BYTES,
    CatalogConfigError,
    LoadedProfile,
    PreparedPolicyChange,
    SettingsFileError,
    load_profile_settings,
    parse_settings_bytes,
    serialize_settings_bytes,
)
from guard_app.services.registry import ServerRegistry

_WRITE_UNKNOWN = "SETTINGS_FILE_WRITE_UNKNOWN"
# The file is not the one this run read or wrote, found while checking it
# before the write (C2.14 table). Nothing was written.
_FILE_CHANGED = frozenset(
    {"SETTINGS_FILE_CHANGED", "SETTINGS_FILE_INVALID", "SETTINGS_FILE_READ_FAILED"}
)

# What the start made of the file, shown on the settings screen. Every code but
# SETTINGS_LOADED also refuses new Tasks.
SETTINGS_LOADED = "SETTINGS_LOADED"
SETTINGS_FILE_MISSING = "SETTINGS_FILE_MISSING"
SETTINGS_PROFILE_INVALID = "SETTINGS_PROFILE_INVALID"
REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"


class SettingsStartError(RuntimeError):
    """Fixed C2.14/B3.11 code only; never a path or file content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SaveOutcomeUnknownError(RuntimeError):
    """The file may hold the new document. Only a restart shows which rules it has."""


class SettingsFileChangedError(RuntimeError):
    """The file changed outside the app; nothing was written. Only a restart reads it."""


def _code(error: Exception, fallback: str) -> str:
    code = error.args[0] if error.args else None
    return code if type(code) is str else fallback


@dataclass(frozen=True, slots=True, repr=False)
class SettingsFile:
    """What the start read. `document` is None when there was no file (first run)."""

    folders: AppFolders
    document: dict[str, object] | None
    generation: SettingsGeneration | None

    def __repr__(self) -> str:
        return "SettingsFile(<redacted>)"


def read_settings(folders: AppFolders) -> SettingsFile:
    """Read and parse the settings file once at start. Blocking; call before the loop."""

    try:
        snapshot = read_settings_file(folders, max_bytes=SETTINGS_MAX_BYTES)
    except (SettingsIOError, AppPathError) as error:
        raise SettingsStartError(_code(error, "SETTINGS_FILE_READ_FAILED")) from None
    if snapshot is None:
        return SettingsFile(folders, None, None)
    try:
        document = parse_settings_bytes(snapshot.data)
    except SettingsFileError as error:
        raise SettingsStartError(error.code) from None
    return SettingsFile(folders, document, snapshot.generation)


async def load_settings_profile(
    settings: SettingsFile, store: RegistryStore
) -> tuple[str, LoadedProfile | None]:
    """The status code, and the Profile when the document holds a valid one.

    The Profile is checked against C's Registry restored from B's store (C4.10
    `load`), the Registry the Guard will use. Nothing is written.
    """
    if settings.document is None:
        return SETTINGS_FILE_MISSING, None
    registry = ServerRegistry(store)
    try:
        await registry.load()
    except StoreError:
        return REGISTRY_UNAVAILABLE, None
    try:
        loaded = load_profile_settings(settings.document, registry)
    except CatalogConfigError:
        return SETTINGS_PROFILE_INVALID, None
    return SETTINGS_LOADED, loaded


class SettingsFileSaver:
    """`ProfileSaver` over the settings file. Call it only under the policy lock."""

    def __init__(self, settings: SettingsFile) -> None:
        self._folders = settings.folders
        self._generation = settings.generation

    async def __call__(self, change: PreparedPolicyChange) -> bool:
        """True once the file holds the change; False when it is unchanged.

        Raises SettingsFileChangedError when the file is not the one last read
        or written, and SaveOutcomeUnknownError when the replacement may have
        happened.
        """
        try:
            data = serialize_settings_bytes(change.document.to_dict())
        except SettingsFileError:
            return False
        try:
            written = await asyncio.to_thread(
                replace_settings_file,
                self._folders,
                data,
                expected=self._generation,
                max_bytes=SETTINGS_MAX_BYTES,
            )
        except (SettingsIOError, AppPathError) as error:
            code = _code(error, "")
            if code == _WRITE_UNKNOWN:
                raise SaveOutcomeUnknownError(_WRITE_UNKNOWN) from None
            if code in _FILE_CHANGED:
                raise SettingsFileChangedError(code) from None
            # Unchanged: the write failed, or it was refused before writing.
            return False
        self._generation = written
        return True
