"""App-run registration scans using one C ScanService on the backend loop.

Jobs run outside command lanes and registration locks. Warnings never change
Guard decisions or human activation.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from guard_app.domain.errors import DispatchError
from guard_app.services.bridge_types import ScanState
from guard_app.services.catalog import ServerSettings
from guard_app.services.registry import (
    RegistryError,
    ServerRegistry,
    ToolListingView,
    listing_fingerprints,
)
from guard_app.services.scanner import ScanError, ScanOutcome


class ScanRunner(Protocol):
    async def scan(
        self,
        server_id: str,
        listing: ToolListingView,
        *,
        on_started: Callable[[], None] | None = None,
    ) -> ScanOutcome: ...

    def close(self) -> None: ...


class _ScanFailed(RuntimeError):  # noqa: N818 - internal fixed-code job outcome.
    """A fixed collection failure code, never server or exception text."""


class RegistrationScans:
    def __init__(
        self,
        registry: ServerRegistry,
        service: ScanRunner,
        collect: Callable[[ServerSettings], Awaitable[ToolListingView]],
        *,
        collect_timeout_s: float = 90.0,
    ) -> None:
        self._registry = registry
        self._service = service
        self._collect = collect
        self._collect_timeout_s = collect_timeout_s
        self._descriptions: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
        self._runs: dict[str, tuple[str, str]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._listener: Callable[[ScanState], None] | None = None
        self._closed = False

    def listen(self, callback: Callable[[ScanState], None]) -> None:
        self._listener = callback

    def state(self, server_id: str) -> ScanState:
        run, code = self._runs.get(server_id, ("none", ""))
        entry = self._registry.entry(server_id)
        if entry is None:
            return ScanState(server_id, run, code)
        tools = tuple(t.tool_name for t in entry.registration.tools)
        registered = {t.fingerprint for t in entry.registration.tools}
        stored = entry.last_scan
        if (
            stored is not None
            and stored.report.server_id == server_id
            and set(stored.report.fingerprint_set) == registered
        ):
            return ScanState(
                server_id, run, code, scan_id=stored.scan_id, report=stored.report, tools=tools
            )
        return ScanState(server_id, run, code, outdated=stored is not None, tools=tools)

    def server_descriptions(self, server_id: str) -> dict[str, str] | None:
        cached = self._descriptions.get(server_id)
        entry = self._registry.entry(server_id)
        if cached is None or entry is None:
            return None
        registered = {t.tool_name: t.fingerprint for t in entry.registration.tools}
        return dict(cached[1]) if cached[0] == registered else None

    def _cache_descriptions(self, server_id: str, listing: ToolListingView) -> None:
        try:
            fingerprints = listing_fingerprints(listing)
        except RegistryError:
            return  # Invalid listings never supply descriptions; Scanner handles its own failure.
        descriptions = {}
        for name, tool in zip(fingerprints, listing.tools, strict=True):
            value = tool.get("description")
            descriptions[name] = value if isinstance(value, str) else ""
        if not self._closed:
            self._descriptions[server_id] = (fingerprints, descriptions)

    def reviewed(self, server_id: str) -> ScanState | None:
        entry = self._registry.entry(server_id)
        if entry is None:
            return None
        tools = tuple(t.tool_name for t in entry.registration.tools)
        if entry.reviewed_scan_id is None:
            return ScanState(server_id, code="SCAN_NOT_REVIEWED", tools=tools)
        stored = entry.last_scan
        if stored is not None and stored.scan_id == entry.reviewed_scan_id:
            return ScanState(
                server_id, scan_id=stored.scan_id, report=stored.report, tools=tools, reviewed=True
            )
        return ScanState(server_id, code="SCAN_REVIEWED_UNAVAILABLE", tools=tools)

    def running(self, server_id: str) -> bool:
        task = self._tasks.get(server_id)
        return task is not None and not task.done()

    def start(self, server: ServerSettings, listing: ToolListingView | None = None) -> ScanState:
        if self._closed:
            self._set(server.id, "failed", "SCAN_SERVICE_CLOSED")
            return self.state(server.id)
        if self.running(server.id):
            return self.state(server.id)
        previous = self._tasks.get(server.id)
        if previous is not None:
            # Settle a done job before its queued callback can overwrite a new job.
            self._finished(server.id, previous)
        self._runs[server.id] = ("queued", "")
        task = asyncio.create_task(self._run(server, listing))
        self._tasks[server.id] = task
        task.add_done_callback(lambda finished: self._finished(server.id, finished))
        self._set(server.id, "queued", "")
        return self.state(server.id)

    async def _run(self, server: ServerSettings, listing: ToolListingView | None) -> None:
        if listing is None:
            try:
                async with asyncio.timeout(self._collect_timeout_s):
                    listing = await self._collect(server)
            except DispatchError as error:
                if error.exception_type in {"SessionStartFailed", "SessionStartTimeout"}:
                    raise _ScanFailed("SCAN_SERVER_UNREACHABLE") from None
                raise
            except TimeoutError:
                raise _ScanFailed("SCAN_COLLECT_TIMEOUT") from None
        self._cache_descriptions(server.id, listing)
        try:
            await self._service.scan(
                server.id, listing, on_started=lambda: self._set(server.id, "running", "")
            )
        except ScanError as error:
            if str(error) == "SCAN_RECORD_UNKNOWN":
                await self._registry.refresh(server.id)
            raise

    def _finished(self, server_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(server_id) is not task:
            return
        if task.cancelled():
            run, code = "failed", "SCAN_CANCELLED"
        else:
            # AGENTS 2026-10-18: consume task failures as fixed codes, without text/type output.
            error = task.exception()
            if error is None:
                run, code = "done", ""
            elif isinstance(error, (ScanError, _ScanFailed)):
                value = str(error)
                run, code = (
                    "failed",
                    value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) else "SCAN_FAILED",
                )
            else:
                run, code = "failed", "SCAN_UNCONFIRMED"
        del self._tasks[server_id]
        self._set(server_id, run, code)

    def _set(self, server_id: str, run: str, code: str) -> None:
        self._runs[server_id] = (run, code)
        if not self._closed and self._listener is not None:
            self._listener(self.state(server_id))

    async def aclose(self, timeout_s: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._descriptions.clear()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.wait(tasks, timeout=timeout_s)
        finally:
            self._service.close()
