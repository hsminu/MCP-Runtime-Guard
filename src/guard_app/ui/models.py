"""Read-only, public UI projections supplied by a trusted backend bridge.

The UI does not derive security decisions, registry state, or aggregate counts.
In particular, a page of rows must never be used to populate ``OverviewTotals``.
All free-text fields here must already be approved for ordinary UI display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
class OverviewTotals:
    """Counts from Registry, approvals, and audit, never UI row counts."""

    active_servers: int | None = None
    pending_approvals: int | None = None
    blocked_calls: int | None = None
    blocked_period_start: datetime | None = None
    blocked_period_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class ServerSummary:
    server_id: str
    display_name: str
    max_label: str | None = None
    tier: str | None = None
    registry_status: str | None = None
    session_status: str | None = None
    transport: str | None = None


@dataclass(frozen=True, slots=True)
class AttentionItem:
    item_id: str
    kind: str
    reason_code: str | None = None
    server_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    occurred_at: datetime | None = None
    waiting_since: datetime | None = None
    target_page: str | None = None
    target_id: str | None = None
    action_available: bool = False
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class RecentCall:
    call_id: str
    server_id: str
    tool_name: str
    occurred_at: datetime | None = None
    initial_decision: str | None = None
    current_status: str | None = None


@dataclass(frozen=True, slots=True)
class FlowBucket:
    starts_at: datetime
    call_count: int


@dataclass(frozen=True, slots=True)
class OverviewSnapshot:
    totals: Surface[OverviewTotals] = field(default_factory=Surface)
    servers: Surface[tuple[ServerSummary, ...]] = field(default_factory=Surface)
    attention: Surface[tuple[AttentionItem, ...]] = field(default_factory=Surface)
    recent_calls: Surface[tuple[RecentCall, ...]] = field(default_factory=Surface)
    flow: Surface[tuple[FlowBucket, ...]] = field(default_factory=Surface)
