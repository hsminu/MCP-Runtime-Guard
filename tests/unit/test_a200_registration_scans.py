"""A200 / I7/I9/I10, T10/T18/T29: scan orchestration, not Scanner efficacy.

Real SQLite/Registry/settings with fake collection and scanning; no model, key
or Cisco SDK. Scanner warnings remain independent of activation and Guard.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import AnalyzerDiagnostic, Finding, ScanReport
from guard_app.domain.enums import RegStatus, ScanErrorCode
from guard_app.domain.errors import DispatchError
from guard_app.security.ids import new_id
from guard_app.services.bridge import BackendBridge, BridgeState
from guard_app.services.bridge_commands import BridgeOutlet, CommandExecutor
from guard_app.services.bridge_ingress import BridgeCommand
from guard_app.services.bridge_types import BackendBindings, RegistrationRequest, ScanState
from guard_app.services.catalog import load_profile_settings
from guard_app.services.registration_scans import RegistrationScans
from guard_app.services.scanner import ScanError, ScanOutcome
from guard_app.services.session_admission import SessionRuntimeError

from .test_approvals import _open, _setup
from .test_bridge import close_bridge, pump_until
from .test_bridge_registration import HTTP, confirm
from .test_bridge_registration import harness as registration_harness


class FakeScanner:
    def __init__(self, registry):
        self.registry = registry
        self.queue_gate = asyncio.Event()
        self.queue_gate.set()
        self.run_gate = asyncio.Event()
        self.run_gate.set()
        self.entered = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = []
        self.reports = []
        self.error = None
        self.closes = 0

    async def scan(self, server_id, listing, *, on_started=None):
        self.calls.append((server_id, listing))
        self.entered.set()
        await self.queue_gate.wait()
        if self.error is not None:
            raise self.error
        if on_started is not None:
            on_started()
        self.started.set()
        await self.run_gate.wait()
        tools = self.registry.entry(server_id).registration.tools
        report = ScanReport(
            server_id=server_id,
            fingerprint_set=tuple(dict.fromkeys(t.fingerprint for t in tools)),
            analyzers_required=("LLM", "YARA"),
            analyzers_completed=("YARA",),
            analyzers_skipped=(),
            analyzers_failed=("LLM",),
            analyzer_diagnostics=(
                AnalyzerDiagnostic(
                    analyzer="LLM", code=ScanErrorCode.SCAN_LLM_KEY_MISSING_OR_INVALID
                ),
            ),
            max_severity="HIGH" if tools else None,
            findings=(
                Finding(
                    analyzer="YARA",
                    source="YARA",
                    code="prompt injection",
                    severity="HIGH",
                    tool_name=tools[0].tool_name,
                    tool_fingerprint=tools[0].fingerprint,
                ),
            )
            if tools
            else (),
            scanned_at_utc=datetime.now(UTC),
            scanner_version="fake-a200",
        )
        scan_id = new_id("scan")
        entry = await self.registry.record_scan(scan_id, report)
        self.reports.append(report)
        return ScanOutcome(scan_id, report, entry)

    def close(self):
        self.closes += 1


@asynccontextmanager
async def harness(tmp_path, *, enabled=True, error=None):
    async with registration_harness(tmp_path) as h:
        scanner = FakeScanner(h.registry)
        scanner.error = error
        scans = RegistrationScans(h.registry, scanner, h.commands.collect)
        h.scanner = scanner
        h.scans = scans
        h.events = []
        scans.listen(h.events.append)
        h.commands.scans = scans if enabled else None
        try:
            yield h
        finally:
            await scans.aclose()


async def registered(h):
    return await confirm(h, await h.commands(HTTP))


async def settled(h):
    task = h.scans._tasks.get("remote")
    if task is not None:
        finished = asyncio.Event()
        task.add_done_callback(lambda _: finished.set())
        await asyncio.wait_for(finished.wait(), 5)
    return h.scans.state("remote")


def server(h):
    return load_profile_settings(h.policy.registration_document(), h.registry).servers[0]


async def activate(h, scan_id=None):
    preview = await h.commands(RegistrationRequest("activation_preview", "remote"))
    return await h.commands(
        RegistrationRequest(
            "activate",
            "remote",
            expected_row_revision=preview.row_revision,
            reviewed_scan_id=scan_id,
        )
    )


@pytest.mark.asyncio
async def test_confirm_records_report_once_and_activates_reviewed_revision(tmp_path):
    async with harness(tmp_path) as h:
        result = await registered(h)
        assert result.code == "REG_REGISTERED"
        assert result.scan.run == "queued"
        assert result.rows[0].status == "pending"
        state = await settled(h)
        view = await h.commands(RegistrationRequest("scan_status", "remote"))
        entry = h.registry.entry("remote")
        assert view.code == "SCAN_STATUS"
        assert view.scan == state
        assert state.run == "done"
        assert state.report == h.scanner.reports[0]
        assert state.scan_id == entry.last_scan_id
        assert state.tools == ("read",)
        assert len(h.collected) == len(h.scanner.calls) == 1
        assert state.report.analyzers_failed == ("LLM",)
        assert state.report.findings[0].severity == "HIGH"
        preview = await h.commands(RegistrationRequest("activation_preview", "remote"))
        assert preview.scan.scan_id == state.scan_id
        assert preview.row_revision == entry.registration.row_revision
        activated = await h.commands(
            RegistrationRequest(
                "activate",
                "remote",
                expected_row_revision=preview.row_revision,
                reviewed_scan_id=preview.scan.scan_id,
            )
        )
        assert activated.code == "REG_ACTIVATION_RESTART_REQUIRED"
        stored = await h.store.load_registration("remote")
        assert stored.registration.status is RegStatus.ACTIVE
        assert stored.reviewed_scan_id == state.scan_id
        reviewed = h.scans.reviewed("remote")
        assert reviewed.reviewed
        assert reviewed.report == state.report
        assert h.scans.reviewed("missing") is None


@pytest.mark.asyncio
async def test_wrong_scan_id_refuses_without_writes(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        await registered(h)
        await settled(h)
        writes = []

        async def unexpected_write(*args, **kwargs):
            writes.append((args, kwargs))
            raise AssertionError("stale activation wrote")

        monkeypatch.setattr(h.registry, "activate", unexpected_write)
        monkeypatch.setattr(h.policy, "_saver", unexpected_write)
        before = await h.store.load_registration("remote")
        assert (await activate(h, new_id("scan"))).code == "REG_ACTIVATION_STALE"
        assert await h.store.load_registration("remote") == before
        assert not writes


@pytest.mark.asyncio
async def test_activation_without_scan_remains_available(tmp_path):
    async with harness(tmp_path, enabled=False) as h:
        assert (await registered(h)).code == "REG_REGISTERED"
        assert (await activate(h)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        entry = await h.store.load_registration("remote")
        assert entry.registration.status is RegStatus.ACTIVE
        assert entry.reviewed_scan_id is None
        assert h.scans.reviewed("remote").code == "SCAN_NOT_REVIEWED"
        assert not h.scanner.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ScanError("SCAN_QUEUE_FULL"), "SCAN_QUEUE_FULL"),
        (ScanError("secret text"), "SCAN_FAILED"),
        (ScanError("X" * 65), "SCAN_FAILED"),
        (RuntimeError("secret text"), "SCAN_UNCONFIRMED"),
    ],
)
async def test_failures_do_not_change_registration_or_expose_text(tmp_path, error, code):
    async with harness(tmp_path, error=error) as h:
        assert (await registered(h)).code == "REG_REGISTERED"
        state = await settled(h)
        assert (state.run, state.code, state.report) == ("failed", code, None)
        assert all("secret text" not in str(getattr(state, f.name)) for f in fields(state))
        assert h.registry.entry("remote").registration.status is RegStatus.PENDING


@pytest.mark.asyncio
async def test_queue_running_done_events_and_duplicate_scan(tmp_path):
    async with harness(tmp_path) as h:
        h.scanner.queue_gate.clear()
        h.scanner.run_gate.clear()
        await registered(h)
        await asyncio.wait_for(h.scanner.entered.wait(), 3)
        duplicate = await h.commands(RegistrationRequest("scan", "remote"))
        assert duplicate.code == "SCAN_ALREADY_RUNNING"
        assert duplicate.scan.run == "queued"
        assert duplicate.scan.code == ""
        assert h.scans.start(server(h)) == duplicate.scan
        assert len(h.scanner.calls) == len(h.collected) == 1
        h.scanner.queue_gate.set()
        await asyncio.wait_for(h.scanner.started.wait(), 3)
        assert h.scans.state("remote").run == "running"
        assert (
            await h.commands(RegistrationRequest("scan", "remote"))
        ).code == "SCAN_ALREADY_RUNNING"
        h.scanner.run_gate.set()
        await settled(h)
        assert [e.run for e in h.events] == ["queued", "running", "done"]


@pytest.mark.asyncio
async def test_manual_scan_collects_again_and_invalid_targets_do_not(tmp_path):
    async with harness(tmp_path) as h:
        await registered(h)
        await settled(h)
        assert (await h.commands(RegistrationRequest("scan", "remote"))).code == "SCAN_STARTED"
        await settled(h)
        assert len(h.collected) == len(h.scanner.calls) == 2
        assert (
            await h.commands(RegistrationRequest("scan", "missing"))
        ).code == "SCAN_TARGET_INVALID"
        entry = h.registry.entry("remote")
        await h.registry.activate(
            "remote",
            expected_row_revision=entry.registration.row_revision,
            reviewed_scan_id=None,
            reviewed_by="local-user",
        )
        assert (
            await h.commands(RegistrationRequest("scan", "remote"))
        ).code == "SCAN_TARGET_INVALID"
        other = server(h).model_copy(update={"id": "unconfigured"})
        await h.registry.register(other, h.scanner.calls[0][1])
        assert (
            await h.commands(RegistrationRequest("scan", "unconfigured"))
        ).code == "SCAN_TARGET_INVALID"
        assert len(h.collected) == len(h.scanner.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DispatchError("SessionStartFailed"), "SCAN_SERVER_UNREACHABLE"),
        (DispatchError("SessionStartTimeout"), "SCAN_SERVER_UNREACHABLE"),
        (DispatchError("OtherFailure"), "SCAN_UNCONFIRMED"),
        (TimeoutError(), "SCAN_COLLECT_TIMEOUT"),
    ],
)
async def test_manual_scan_collection_failures(tmp_path, error, code):
    async with harness(tmp_path, enabled=False) as h:
        await registered(h)

        async def collect(_server):
            raise error

        scans = RegistrationScans(h.registry, h.scanner, collect)
        h.scans = h.commands.scans = scans
        try:
            assert (await h.commands(RegistrationRequest("scan", "remote"))).code == "SCAN_STARTED"
            state = await settled(h)
            assert (state.run, state.code, state.report) == ("failed", code, None)
            assert not h.scanner.calls
        finally:
            await scans.aclose()


@pytest.mark.asyncio
async def test_close_cancels_before_service_close_without_late_events(tmp_path):
    async with harness(tmp_path) as h:
        h.scanner.run_gate.clear()
        await registered(h)
        await asyncio.wait_for(h.scanner.started.wait(), 3)
        tasks = tuple(h.scans._tasks.values())
        events = list(h.events)
        await h.scans.aclose()
        assert all(task.cancelled() for task in tasks)
        assert h.scans.state("remote").code == "SCAN_CANCELLED"
        await h.scans.aclose()
        assert h.scanner.closes == 1
        state = h.scans.start(server(h))
        assert (state.run, state.code) == ("failed", "SCAN_SERVICE_CLOSED")
        assert h.events == events
        assert len(h.scanner.calls) == 1


@pytest.mark.asyncio
async def test_state_hides_outdated_reports_and_reviewed_history_is_explicit(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        await registered(h)
        state = await settled(h)
        original = h.registry.entry("remote")
        for report in (
            state.report.model_copy(update={"server_id": "other"}),
            state.report.model_copy(update={"fingerprint_set": ("a" * 64,)}),
        ):
            # A defensive projection check: B normally rejects cross-server entries.
            entry = SimpleNamespace(
                registration=original.registration,
                last_scan=original.last_scan.model_copy(update={"report": report}),
            )
            monkeypatch.setattr(h.registry, "entry", lambda _id, entry=entry: entry)
            stale = h.scans.state("remote")
            assert stale.outdated
            assert stale.report is None
            assert stale.scan_id is None
        entry = original.model_copy(update={"reviewed_scan_id": new_id("scan")})
        monkeypatch.setattr(h.registry, "entry", lambda _id: entry)
        assert h.scans.reviewed("remote").code == "SCAN_REVIEWED_UNAVAILABLE"
        monkeypatch.setattr(h.registry, "entry", lambda _id: None)
        assert h.scans.state("missing") == ScanState("missing")


@pytest.mark.asyncio
async def test_record_unknown_refreshes_before_fixed_failure(tmp_path, monkeypatch):
    async with harness(tmp_path, error=ScanError("SCAN_RECORD_UNKNOWN")) as h:
        refreshed = []
        original = h.registry.refresh

        async def refresh(server_id):
            refreshed.append(server_id)
            return await original(server_id)

        monkeypatch.setattr(h.registry, "refresh", refresh)
        await registered(h)
        state = await settled(h)
        assert state.code == "SCAN_RECORD_UNKNOWN"
        assert refreshed == ["remote"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "scan_id", "code"),
    [
        ("activate", 1, "POLICY_CHANGE_INVALID"),
        ("block", "scan-id", "POLICY_CHANGE_INVALID"),
        ("activate", "scan-id", "REG_ACTIVATION_STALE"),
    ],
)
async def test_activation_scan_input_validation(tmp_path, action, scan_id, code):
    async with harness(tmp_path, enabled=False) as h:
        await registered(h)
        result = await h.commands(RegistrationRequest(action, "remote", reviewed_scan_id=scan_id))
        assert result.code == code
        assert h.registry.entry("remote").registration.status is RegStatus.PENDING


@pytest.mark.asyncio
async def test_scan_status_works_when_execution_is_closed_and_scan_unavailable(tmp_path):
    async with harness(tmp_path, enabled=False) as h:
        assert (await h.commands(RegistrationRequest("scan", "remote"))).code == "SCAN_UNAVAILABLE"

        def blocked():
            raise SessionRuntimeError("CODEX_STORE_HALTED")

        h.commands.execution_check = blocked
        assert (
            await h.commands(RegistrationRequest("scan_status", "remote"))
        ).code == "SCAN_UNAVAILABLE"
        h.commands.scans = h.scans
        view = await h.commands(RegistrationRequest("scan_status", "remote"))
        assert view.code == "SCAN_STATUS"
        assert view.scan.run == "none"
        assert (
            await h.commands(RegistrationRequest("scan", "remote"))
        ).code == "CODEX_STORE_HALTED"


def test_bridge_accepts_scan_actions_and_relays_scan_events(tmp_path):
    app = QApplication.instance() or QApplication([])
    views, events = [], []
    context = None

    async def boot():
        nonlocal context
        context = harness(tmp_path)
        h = await context.__aenter__()
        await registered(h)
        await settled(h)

        async def close():
            await context.__aexit__(None, None, None)

        return BackendBindings(
            lambda _: None,
            None,
            close=close,
            stop_session=lambda *args: None,
            registration=h.commands,
            scan_events=h.scans.listen,
            scan_for_review=h.scans.reviewed,
        )

    bridge = BackendBridge(boot)
    bridge.registration_changed.connect(views.append)
    bridge.scan_changed.connect(events.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY, timeout=10)
        bridge.request_registration(RegistrationRequest("scan_status", "remote"))
        pump_until(app, lambda: bool(views))
        assert views[-1].code == "SCAN_STATUS"
        bridge.request_registration(RegistrationRequest("scan", "remote"))
        pump_until(app, lambda: any(v.code == "SCAN_STARTED" for v in views))
        pump_until(app, lambda: any(s.run == "done" for s in events))
        assert [s.run for s in events] == ["queued", "running", "done"]
    finally:
        close_bridge(app, bridge)


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [True, False])
async def test_approval_review_adds_tool_and_reviewed_scan_without_writes(connected):
    setup = _setup()
    service, store, host, *_ = setup
    approval = await _open(setup)
    snapshot = service._memory_snapshots[approval.approval_id]
    state = ScanState(snapshot.request.tool.server_id, reviewed=True)
    queries, reviews = [], []

    def lookup(server_id):
        queries.append(server_id)
        return state

    bindings = BackendBindings(
        lambda _: None,
        service.human_channel("operator-1"),
        scan_for_review=lookup if connected else None,
    )
    owner = SimpleNamespace(current=bindings)

    def noop(value):
        return None

    outlet = BridgeOutlet(noop, reviews.append, noop, noop, noop, noop, noop)
    executor = CommandExecutor(owner, None, None, None, outlet)
    code = await executor.execute(
        BridgeCommand("review-command", "control", "review", approval.approval_id)
    )
    assert code == "REVIEW_READY"
    assert reviews[0].tool_name == snapshot.request.tool.tool_name
    assert reviews[0].scan is (state if connected else None)
    assert queries == ([state.server_id] if connected else [])
    assert not store.consumed
    assert not host.dispatched


@pytest.mark.asyncio
async def test_restore_uses_collected_listing_for_scan(tmp_path, monkeypatch):
    async with harness(tmp_path, enabled=False) as h:

        async def failed_save(*args, **kwargs):
            raise SessionRuntimeError("POLICY_SAVE_FAILED")

        with monkeypatch.context() as patch:
            patch.setattr(h.policy, "save_registration", failed_save)
            assert (await registered(h)).code == "POLICY_SAVE_FAILED"
        h.commands.scans = h.scans
        restored = await confirm(h, await h.commands(replace(HTTP, restore=True)))
        assert restored.code == "REG_SETTINGS_RESTORED"
        assert restored.scan.run == "queued"
        assert (await settled(h)).run == "done"
        assert len(h.collected) == 2
        assert len(h.scanner.calls) == 1


@pytest.mark.asyncio
async def test_collection_timeout_cancels_wait_and_never_starts_scanner(tmp_path):
    async with harness(tmp_path, enabled=False) as h:
        await registered(h)
        ended = asyncio.Event()

        async def collect(_server):
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()

        scans = RegistrationScans(h.registry, h.scanner, collect, collect_timeout_s=0.01)
        h.scans = scans
        try:
            scans.start(server(h))
            assert (await settled(h)).code == "SCAN_COLLECT_TIMEOUT"
            assert ended.is_set()
            assert not h.scanner.calls
        finally:
            await scans.aclose()
