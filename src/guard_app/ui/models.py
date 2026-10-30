"""Read-only, public UI projections supplied by a trusted backend bridge.

The UI does not derive security decisions, registry state, or aggregate counts.
All free-text fields here must already be approved for ordinary UI display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LoadState(StrEnum):
    UNAVAILABLE = "unavailable"
    LOADING = "loading"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


class RuntimeMode(StrEnum):
    CODEX = "codex"
    UNCONFIGURED = "unconfigured"
    MOCK = "mock"
    REAL = "real"


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Model mode supplied by trusted bootstrap, never inferred from UI or SDK."""

    model_mode: RuntimeMode = RuntimeMode.UNCONFIGURED


@dataclass(frozen=True, slots=True)
class Surface[T]:
    """A backend query result, distinct from an empty successful result."""

    state: LoadState = LoadState.UNAVAILABLE
    value: T | None = None
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class ServerSummary:
    server_id: str
    display_name: str
    max_label: str | None = None
    tier: str | None = None
    registry_status: str | None = None
    session_status: str | None = None
    transport: str | None = None
    executable_pinned: bool | None = None
    settings_issue: str | None = None
    # False: registered, but the settings file has no record of it (A128·A105).
    in_settings: bool | None = None
