"""A218 / I1,I2,I3,I6,I7,I8,I10,I11 / T10,T18,T29,T38; NOTIFY-01, ADR-4.

Real SQLite, C Registry, Guard and A105 byte peer. Codex launch is synthetic;
these are A orchestration checks, not C's independent process/wire acceptance.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.adapters.mcp_transport import validated_stdio_stream_factory
from guard_app.domain.contracts import Denied, Pending
from guard_app.domain.enums import Label, RegStatus, StorePhase, TaskStatus, TrustTier
from guard_app.domain.errors import DispatchError
from guard_app.domain.models import ProposedCall
from guard_app.domain.reasons import Reason
from guard_app.services import product_startup
from guard_app.services.bridge import (
    BackendBridge,
    RegistrationRequest,
    SessionOptions,
    ToolSettingsEdit,
)
from guard_app.services.bridge_types import BackendCleanupError
from guard_app.services.catalog import prepare_policy_change
from guard_app.services.product_assembly import (
    GlobalHaltRelay,
    ProductAssemblyError,
    assemble_guard,
)
from guard_app.services.product_settings import SaveOutcomeUnknownError, read_settings
from guard_app.services.session_admission import SessionRuntimeError
from guard_app.services.session_cleanup import CleanupLimits

from .test_a105_activation import harness, loop_barrier
from .test_product_assembly import Peer
from .test_session_runtime import Session, synthetic_preparation


@asynccontextmanager
async def product_harness(
    path, monkeypatch, *, selected=True, injected=False, stdio=False, asking=False
):
    async with harness(path, selected=selected) as base:
        if stdio:
            doc = base.doc
            doc["servers"][0].pop("url")
            doc["servers"][0].update(transport="stdio", command=sys.executable)
            assert await base.saver(prepare_policy_change(base.doc, doc, base.registry))
        if asking:
            doc = base.doc
            doc["servers"][0].update(tier="general_external", max_label="INTERNAL")
            server = base.server.model_copy(
                update={
                    "tier": TrustTier.GENERAL_EXTERNAL,
                    "max_label": Label.INTERNAL,
                }
            )
            blocked = await base.registry.block(
                "docs", expected_row_revision=base.registry.entry("docs").registration.row_revision
            )
            await base.registry.delete(
                "docs", expected_row_revision=blocked.registration.row_revision
            )
            await base.registry.register(server, base.listing)
            assert await base.saver(prepare_policy_change(base.doc, doc, base.registry))
        await base.assembled.aclose()
        base.store.close()
        assert await base.store.wait_closed(5)
        order, peers, active, assemblies, factories, sessions = [], [], set(), [], [], []
        dispatches = []

        @asynccontextmanager
        async def stream():
            index = len(peers)
            peer = Peer(base.definitions)
            peers.append(peer)
            active.add(index)
            order.append(("start", index))
            try:
                async with validated_stdio_stream_factory(
                    peer.read, peer.write, peer.drain
                )() as pipes:
                    yield pipes
            finally:
                active.remove(index)
                order.append(("stop", index))

        async def assemble(store, inputs):
            result = await assemble_guard(
                store, replace(inputs, folders=None, server_streams={"docs": stream})
            )
            for worker in result.transport._workers.values():
                worker._on_dispatch = dispatches.append
            assemblies.append(result)
            return result

        def factory(assembled, approvals, *, execution_allowed):
            factories.append((assembled, approvals, execution_allowed))

            async def launch(state, options, resources):
                assert execution_allowed()
                session = Session()
                session.task_id = state.task_id
                session.guard = assembled.guard
                sessions.append(session)
                return session

            return launch

        async def injected_factory(*args):
            return Session()

        monkeypatch.setattr(product_startup, "assemble_guard", assemble)
        monkeypatch.setattr(product_startup, "codex_session_factory", factory)
        bindings = await product_startup.bootstrap_product(
            base.folders,
            settings=read_settings(base.folders),
            preparation=synthetic_preparation(),
            session_factory=injected_factory if injected else None,
        )
        runtime = bindings.create_session.__self__
        commands = bindings.registration
        h = SimpleNamespace(
            **{k: v for k, v in locals().items() if k not in ("base", "path", "monkeypatch")},
            store=runtime.store,
            registry=commands.registry,
            policy=runtime._policy,
            folders=base.folders,
            admission=runtime._admission,
        )
        try:
            yield h
        finally:
            try:
                await bindings.close()
            except (SessionRuntimeError, BackendCleanupError):
                assert runtime._admission.cleanup_unconfirmed or runtime._stops_unconfirmed
            assert await runtime.store.wait_closed(5)
            assert not active


async def activate(h):
    preview = await h.commands(RegistrationRequest("activation_preview", "docs"))
    server = read_settings(h.folders).document["servers"][0]
    denied = tuple(
        tool["name"]
        for tool in server["tools"]
        if tool["kind"] in ("read", "send") and tool["name"] != "read"
    )
    return await h.commands(
        RegistrationRequest(
            "activate",
            "docs",
            expected_row_revision=preview.row_revision,
            allowed=("read",),
            denied=denied,
        )
    )


async def change(h, action):
    if action == "activate":
        return await activate(h)
    entry = h.registry.entry("docs")
    if action == "recheck":
        preview = await h.commands(RegistrationRequest("recheck", "docs"))
        assert preview.code == "REG_CONFIRM_REQUIRED"
        return await h.commands(RegistrationRequest("confirm", token=preview.token))
    return await h.commands(
        RegistrationRequest(
            action,
            "docs",
            expected_row_revision=entry.registration.row_revision,
            tool_settings=(ToolSettingsEdit("unknown", "read", "PUBLIC"),),
        )
    )


@pytest.mark.asyncio
async def test_activation_replaces_guard_tools_factory_bridge_and_policy(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    async with product_harness(tmp_path, monkeypatch, selected=False) as h:
        old = h.runtime._assembled
        old_approvals = h.runtime._approvals
        task = await h.runtime.create_session(SessionOptions())
        assert not h.runtime._tool_refs
        published = []

        async def boot():
            return h.bindings

        bridge = BackendBridge(boot)
        bridge.policy_changed.connect(published.append)
        host = bridge._host
        boot_task = asyncio.create_task(boot())
        await boot_task
        assert host._bindings.adopt(boot_task)
        host._announce_ready(h.bindings)
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        app.processEvents()
        new = h.runtime._assembled
        assert new is not old
        assert h.store.execution_state().phase is StorePhase.READY
        assert h.store.execution_state().execution_enabled
        old.release_held_halt()  # Successful replacement discarded the held halt.
        assert h.store.execution_state().phase is StorePhase.READY
        assert h.admission.restart_code is None
        assert h.runtime._approvals is not old_approvals
        assert h.runtime._events._approvals is h.runtime._approvals
        assert h.runtime._approvals._listener == host._views.task_event
        assert old_approvals._listener is None
        assert "docs__read" in h.runtime._tool_refs
        assert new.offers_tool("docs__read")
        assert h.sessions[0].closed == 1
        assert (await h.store.load_task(task.task_id)).status is TaskStatus.HALTED
        current = host._bindings.current
        assert current.human_control is not h.bindings.human_control
        assert current.human_control._service is new.guard.human_control(new.operator_id)._service
        assert current.registration is h.bindings.registration
        assert published[-1].policy_version == new.settings.policy_version
        assert len(published) == 2
        next_task = await h.runtime.create_session(SessionOptions())
        saved = read_settings(h.folders).document
        assert (await h.store.load_task(next_task.task_id)).policy_version == (
            prepare_policy_change(saved, saved, h.registry).loaded_profile.policy_version
        )
        assert h.sessions[-1].guard is new.guard
        assert h.runtime.creation_blocker() is None
        # Unknown/denied tool still never reaches the real MCP byte peer (I2).
        denied = await new.guard.submit(
            next_task.task_id,
            ProposedCall(tool_ref="docs__second", arguments={}, model_call_id="denied"),
            expected_epoch=1,
        )
        assert isinstance(denied, Denied)
        assert sum(len(peer.calls) for peer in h.peers) == 0
        bridge.deleteLater()


@pytest.mark.asyncio
async def test_old_transport_finishes_before_new_starts_and_shutdown_closes_current(
    tmp_path, monkeypatch
):
    async with product_harness(tmp_path, monkeypatch) as h:
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        assert h.order == [("start", 0), ("stop", 0), ("start", 1)]
        await h.bindings.close()
        assert h.order[-1] == ("stop", 1)
        assert not h.active


@pytest.mark.asyncio
async def test_active_tools_pending_then_activation_in_same_run(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        assert h.registry.status("docs") is RegStatus.ACTIVE
        assert (await change(h, "tool_settings_save")).code == "REG_TOOLS_RECONNECTED"
        assert h.registry.status("docs") is RegStatus.PENDING
        assert h.admission.restart_code is None
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        assert h.registry.status("docs") is RegStatus.ACTIVE
        assert h.runtime.creation_blocker() is None
        assert len(h.assemblies) == 4
        assert len(h.active) == 1


@pytest.mark.asyncio
async def test_deleted_server_never_starts_in_replacement(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        assert (await change(h, "delete")).code == "REG_DELETE_RECONNECTED"
        assert h.registry.entry("docs") is None
        assert not h.runtime._tool_refs
        assert h.order == [("start", 0), ("stop", 0)]
        assert h.runtime.creation_blocker() is None


@pytest.mark.asyncio
async def test_rechecked_pin_reconnects(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch, stdio=True) as h:
        assert (await change(h, "recheck")).code == "REG_PIN_RECONNECTED"
        assert h.admission.restart_code is None
        assert h.runtime._assembled is not h.assemblies[0]
        assert read_settings(h.folders).document["servers"][0]["command_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "code", "restart"),
    [
        ("activate", "REG_ACTIVATION_RESTART_REQUIRED", "REG_ACTIVATION_RESTART_REQUIRED"),
        ("delete", "REG_DELETED", "REG_DELETE_RESTART_REQUIRED"),
        ("tool_settings_save", "REG_TOOLS_RESTART_REQUIRED", "REG_TOOLS_RESTART_REQUIRED"),
        ("recheck", "REG_PIN_SAVED", "REG_PIN_RESTART_REQUIRED"),
    ],
)
@pytest.mark.parametrize(
    "failure", ["assembly", "transport", "execution", "cancel", "factory", "cancel_factory"]
)
async def test_failed_reconnect_refuses_sessions_and_leaves_no_new_connection(
    tmp_path,
    monkeypatch,
    action,
    code,
    restart,
    failure,
):
    async with product_harness(tmp_path, monkeypatch, stdio=action == "recheck") as h:
        attempts = []
        original = product_startup.assemble_guard

        async def assemble(store, inputs):
            attempts.append(1)
            if failure == "assembly":
                raise ProductAssemblyError("PROFILE_INVALID")
            if failure == "cancel":
                raise asyncio.CancelledError
            return await original(store, inputs)

        monkeypatch.setattr(product_startup, "assemble_guard", assemble)
        if failure == "transport":
            close = h.runtime._assembled.transport.close

            async def unconfirmed():
                await close()
                raise DispatchError("SessionCloseFailed")

            monkeypatch.setattr(h.runtime._assembled.transport, "close", unconfirmed)
        if failure == "execution":
            # Gate changes after writes, exactly when the old transport finishes.
            close = h.runtime._assembled.transport.close

            async def halted():
                await close()
                monkeypatch.setattr(h.runtime._execution_gate, "allowed", lambda: False)

            monkeypatch.setattr(h.runtime._assembled.transport, "close", halted)
        if failure in ("factory", "cancel_factory"):

            def factory(*args, **kwargs):
                if failure == "cancel_factory":
                    raise asyncio.CancelledError
                raise ProductAssemblyError("PROFILE_INVALID")

            monkeypatch.setattr(product_startup, "codex_session_factory", factory)
        if failure in ("cancel", "cancel_factory"):
            with pytest.raises(asyncio.CancelledError):
                await change(h, action)
        else:
            assert (await change(h, action)).code == code
        assert h.admission.restart_code == restart
        assert_faulted(h)
        assert len(attempts) == (0 if failure in ("transport", "execution") else 1)
        assert h.admission.cleanup_unconfirmed is (failure == "transport")
        assert not h.active
        with pytest.raises(SessionRuntimeError):
            await h.runtime.create_session(SessionOptions())
        assert sum(len(peer.calls) for peer in h.peers) == 0


@pytest.mark.asyncio
async def test_unknown_save_never_rebuilds(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:

        async def unknown(change):
            raise SaveOutcomeUnknownError

        monkeypatch.setattr(h.policy, "_saver", unknown)
        assert (await activate(h)).code == "POLICY_SAVE_UNKNOWN"
        assert len(h.assemblies) == 1
        assert h.admission.restart_code == "REG_ACTIVATION_UNKNOWN"
        with pytest.raises(SessionRuntimeError):
            await h.runtime.create_session(SessionOptions())


@pytest.mark.asyncio
async def test_injected_factory_retains_restart_path(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch, injected=True) as h:
        assert h.runtime._rebuild is None
        assert (await activate(h)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert len(h.assemblies) == 1


@pytest.mark.asyncio
async def test_reconnect_generation_rejects_session_launched_during_rebuild(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = product_startup.assemble_guard

        async def assemble(store, inputs):
            entered.set()
            await release.wait()
            return await original(store, inputs)

        monkeypatch.setattr(product_startup, "assemble_guard", assemble)
        reconnect = asyncio.create_task(activate(h))
        await asyncio.wait_for(entered.wait(), 3)
        launched = asyncio.Event()
        factory = h.runtime._session_factory

        async def launch(*args):
            session = await factory(*args)
            launched.set()
            return session

        monkeypatch.setattr(h.runtime, "_session_factory", launch)
        creating = asyncio.create_task(h.runtime.create_session(SessionOptions()))
        await asyncio.wait_for(launched.wait(), 3)
        assert len(h.sessions) == 1
        assert h.sessions[0].guard is h.assemblies[0].guard
        assert not creating.done()
        assert h.runtime._guard_generation == 1
        release.set()
        assert (await reconnect).code == "REG_ACTIVATION_RECONNECTED"
        assert h.runtime._guard_generation == 2
        with pytest.raises(SessionRuntimeError, match="^SESSION_UNAVAILABLE$"):
            await creating
        assert h.sessions[0].closed == 1
        assert not h.runtime.pending_cleanup
        assert h.sessions[0].task_id not in {row.task_id for row in await h.store.list_tasks()}
        assert (await h.runtime.create_session(SessionOptions())).status is TaskStatus.RUNNING
        assert h.sessions[-1].guard is h.runtime._assembled.guard


@pytest.mark.asyncio
async def test_stop_record_timeout_prevents_rebuild(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        h.runtime._limits = CleanupLimits(stop_record_s=0.01, session_s=0.01)
        release = asyncio.Event()
        h.runtime._records.start(release.wait())
        try:
            assert (await activate(h)).code == "REG_ACTIVATION_RESTART_REQUIRED"
            assert len(h.assemblies) == 1
            assert not h.active
            with pytest.raises(SessionRuntimeError):
                await h.runtime.create_session(SessionOptions())
        finally:
            release.set()
            await h.runtime._records.wait(1)


@pytest.mark.asyncio
async def test_old_pending_approval_is_not_restored_or_executable(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch, asking=True) as h:
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        state = h.policy.task_state("approval-task").model_copy(
            update={"high_watermark": Label.INTERNAL}
        )
        await h.store.create_task(state, created_at_utc=datetime.now(UTC))
        old = h.runtime._assembled
        pending = await old.guard.submit(
            state.task_id,
            ProposedCall(
                tool_ref="docs__read",
                arguments={},
                model_call_id="pending",
            ),
            expected_epoch=1,
        )
        assert isinstance(pending, Pending)
        assert sum(len(peer.calls) for peer in h.peers) == 0
        # ACTIVE cannot be activated again; a tool edit is a valid replacement trigger.
        assert (await change(h, "tool_settings_save")).code == "REG_TOOLS_RECONNECTED"
        human = h.runtime.bindings().human_control
        assert human is not old.guard.human_control(old.operator_id)
        assert isinstance(await human.approve(pending.approval_id), Denied)
        assert h.runtime._approvals.approval_for(state.task_id) is None
        assert sum(len(peer.calls) for peer in h.peers) == 0
        assert h.dispatches == []


@pytest.mark.asyncio
async def test_invalid_guard_bindings_are_rejected_without_changing_owner(tmp_path, monkeypatch):
    from guard_app.services.bridge_bindings import BindingsOwner

    async with product_harness(tmp_path, monkeypatch, injected=True) as h:
        owner = BindingsOwner(1, lambda task: task.exception())

        async def boot():
            return h.bindings

        boot_task = asyncio.create_task(boot())
        await boot_task
        assert owner.adopt(boot_task)
        with pytest.raises(ValueError, match="GUARD_BINDINGS_INVALID"):
            owner.change_guard(replace(h.runtime._guard_bindings(), human_control=object()))
        assert owner.current is h.bindings


@pytest.mark.asyncio
async def test_repeated_assembly_close_has_one_stream_exit(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch, injected=True) as h:
        assembled = h.runtime._assembled
        await assembled.aclose()
        revision = h.store.execution_state().state_revision
        await assembled.aclose()
        await assembled.guard.aclose()
        await assembled.transport.close()
        assert h.store.execution_state().state_revision == revision
        assert h.order == [("start", 0), ("stop", 0)]


@pytest.mark.asyncio
async def test_cancel_during_old_close_waits_for_exit_and_propagates(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.runtime._assembled.transport.close

        async def closing():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(h.runtime._assembled.transport, "close", closing)
        operation = asyncio.create_task(activate(h))
        await asyncio.wait_for(entered.wait(), 3)
        operation.cancel()
        await loop_barrier()
        assert not operation.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert h.admission.restart_code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert len(h.assemblies) == 1
        assert not h.active
        assert_faulted(h)
        with pytest.raises(SessionRuntimeError):
            await h.runtime.create_session(SessionOptions())


@pytest.mark.asyncio
async def test_remaining_session_uses_old_system_channel_before_replacement(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        created = await h.runtime.create_session(SessionOptions())

        async def no_tasks():
            return ()

        monkeypatch.setattr(h.policy, "_task_source", no_tasks)
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        assert h.sessions[0].closed == 1
        assert (await h.store.load_task(created.task_id)).status is TaskStatus.HALTED
        assert not h.runtime._stops_unconfirmed
        assert not h.runtime.pending_cleanup
        assert h.runtime._stops._system._guard is h.runtime._assembled.guard


def test_pin_reconnected_updates_window_settings_status():
    from guard_app.services.bridge import RegistrationView
    from guard_app.ui.window import GuardWindow

    app = QApplication.instance() or QApplication([])
    window = GuardWindow()
    statuses = []
    window.set_settings_status = statuses.append
    window._registration_rows(RegistrationView("REG_PIN_RECONNECTED"))
    assert statuses == ["SETTINGS_REGISTRATION_SAVED"]
    window.close()
    app.processEvents()


def assert_faulted(h, reason=Reason.GUARD_CLOSED):
    state = h.store.execution_state()
    assert state.phase is StorePhase.FAULTED
    assert not state.execution_enabled
    assert state.requires_restart
    assert state.reason is reason


@pytest.mark.parametrize("reason", list(Reason))
def test_replacement_relay_only_holds_guard_closed(reason):
    received = []
    relay = GlobalHaltRelay(received.append)
    relay.replacing = True
    relay(reason)
    assert received == ([] if reason is Reason.GUARD_CLOSED else [reason])
    relay.replacing = False
    relay.release_held_halt()
    relay.release_held_halt()
    assert received == [reason]


def test_relay_normal_close_and_discard():
    received = []
    relay = GlobalHaltRelay(received.append)
    relay(Reason.GUARD_CLOSED)
    assert received == [Reason.GUARD_CLOSED]
    relay.replacing = True
    relay(Reason.GUARD_CLOSED)
    relay.discard_held_halt()
    relay.release_held_halt()
    assert received == [Reason.GUARD_CLOSED]


@pytest.mark.asyncio
async def test_runtime_close_forwards_guard_closed_before_store_close(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        original = h.store.close
        observed = []

        def close():
            assert_faulted(h)
            observed.append(h.store.execution_state().reason)
            original()

        monkeypatch.setattr(h.store, "close", close)
        await h.runtime.close()
        assert observed == [Reason.GUARD_CLOSED]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["transport", "settle"])
@pytest.mark.parametrize("reason", [Reason.INTERNAL_ERROR, Reason.GUARD_CLOSED])
async def test_halt_before_guard_close_prevents_reassembly(tmp_path, monkeypatch, stage, reason):
    async with product_harness(tmp_path, monkeypatch) as h:
        old = h.runtime._assembled
        owner, method = (
            (old.transport, "close") if stage == "transport" else (h.runtime._records, "wait")
        )
        original = getattr(owner, method)
        attempts = []

        async def halt(*args):
            result = await original(*args)
            old.guard._halt_globally(reason)
            return result

        async def rebuild(document):
            attempts.append(1)
            return

        monkeypatch.setattr(owner, method, halt)
        monkeypatch.setattr(h.runtime, "_rebuild", rebuild)
        assert (await activate(h)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert attempts == []
        assert_faulted(h, reason)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "dispatch", "cancel", "publish", "publish_cancel"])
async def test_rebuild_or_publication_failure_releases_old_halt(tmp_path, monkeypatch, failure):
    async with product_harness(tmp_path, monkeypatch) as h:
        previous = h.runtime._assembled

        async def rebuild(document):
            if failure == "dispatch":
                raise DispatchError("SessionCloseFailed")
            if failure == "cancel":
                raise asyncio.CancelledError
            return

        def publish(bindings):
            if failure == "publish_cancel":
                raise asyncio.CancelledError
            raise ValueError("GUARD_BINDINGS_INVALID")

        if failure.startswith("publish"):
            h.runtime._listen_guard(publish)
        else:
            monkeypatch.setattr(h.runtime, "_rebuild", rebuild)
        if failure in ("cancel", "publish_cancel"):
            with pytest.raises(asyncio.CancelledError):
                await activate(h)
        else:
            assert (await activate(h)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert_faulted(h)
        assert h.admission.restart_code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert not h.active
        revision = h.store.execution_state().state_revision
        previous.release_held_halt()
        assert h.store.execution_state().state_revision == revision


@pytest.mark.asyncio
async def test_reconnect_rejects_creator_with_old_factory_and_closes_launched_session(
    tmp_path, monkeypatch
):
    async with product_harness(tmp_path, monkeypatch) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.runtime._session_factory

        async def launch(*args):
            session = await original(*args)
            entered.set()
            await release.wait()
            return session

        monkeypatch.setattr(h.runtime, "_session_factory", launch)
        creating = asyncio.create_task(h.runtime.create_session(SessionOptions()))
        await asyncio.wait_for(entered.wait(), 3)
        assert h.runtime._guard_generation == 0
        assert (await activate(h)).code == "REG_ACTIVATION_RECONNECTED"
        assert h.runtime._guard_generation == 2
        release.set()
        with pytest.raises(SessionRuntimeError, match="^SESSION_UNAVAILABLE$"):
            await creating
        assert h.sessions[0].guard is h.assemblies[0].guard
        assert h.sessions[0].closed == 1
        assert h.sessions[0].task_id not in {row.task_id for row in await h.store.list_tasks()}
        assert not h.runtime.pending_cleanup
        assert (await h.runtime.create_session(SessionOptions())).status is TaskStatus.RUNNING
        assert h.sessions[-1].guard is h.runtime._assembled.guard


@pytest.mark.asyncio
async def test_policy_save_finishes_while_session_launch_waits(tmp_path, monkeypatch):
    async with product_harness(tmp_path, monkeypatch) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.runtime._session_factory

        async def launch(*args):
            session = await original(*args)
            entered.set()
            await release.wait()
            return session

        monkeypatch.setattr(h.runtime, "_session_factory", launch)
        creating = asyncio.create_task(h.runtime.create_session(SessionOptions()))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            await asyncio.wait_for(
                h.runtime.change_server_access("docs", (), ("read", "second")), 3
            )
            assert not creating.done()
            assert read_settings(h.folders).document["access_rules"] == {
                "allow": [],
                "deny": [["docs", "read"], ["docs", "second"]],
            }
        finally:
            release.set()
        created = await creating
        assert (
            await h.store.load_task(created.task_id)
        ).policy_version == h.policy._settings.policy_version
