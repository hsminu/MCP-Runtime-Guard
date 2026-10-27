"""A-local receipts and resource ordering; I6/I7/I9/I11, T22/T43/T46 intent.

All preparation, process, path, and Store operations are injected counters. No
file/process/network operation or C acceptance claim is made by these tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.services.bridge import ControlAction, SessionOptions
from guard_app.services.catalog import GuardSettings
from guard_app.services.session_cleanup import CleanupLimits
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)
from guard_app.services.session_runtime import SessionRuntime, SessionRuntimeError

CONFIGURATION = ConfigurationReceipt("a" * 64, "b" * 64, "c" * 64)
CHANGED = replace(CONFIGURATION, config_digest="d" * 64)


class MemoryStore:
    def __init__(self, trace):
        self.trace = trace
        self.states = {}
        self.creates = 0
        self.fail_create = False

    async def create_task(self, state, **kwargs):
        self.creates += 1
        self.trace.append(("store", state.task_id))
        if self.fail_create:
            raise StoreError()
        self.states[state.task_id] = state

    async def load_task(self, task_id):
        return self.states[task_id]

    async def set_task_status(self, task_id, status, **kwargs):
        state = self.states[task_id].model_copy(update={"status": status})
        self.states[task_id] = state
        return state


class Session:
    def __init__(self, task_id, trace):
        self.task_id = task_id
        self.trace = trace
        self.closed = False

    async def send(self, text):
        return text

    async def interrupt(self):
        self.trace.append(("interrupt", self.task_id))

    async def aclose(self):
        self.trace.append(("close", self.task_id))
        self.closed = True


class Harness:
    def __init__(self):
        self.trace = []
        self.startups = 0
        self.verifications = 0
        self.factories = 0
        self.receipt = StartupReceipt(CONFIGURATION, CONFIGURATION, True)
        self.observed = CONFIGURATION
        self.resource_config = CONFIGURATION
        self.startup_error = self.verify_error = self.prepare_error = self.factory_error = None
        self.confirmed = True
        self.cleanup_result = None
        self.cleaned = []
        self.sessions = {}
        self.resources = {}
        self.store = MemoryStore(self.trace)
        self.preparation = SessionPreparation(
            self.startup, self.verify, self.prepare, self.confirm, self.cleanup
        )
        self.build(self.preparation)

    def build(self, preparation, *, factory=None, cleanup_limits=None):
        """A runtime over these counters. Tests rebuild it before it has run."""
        self.runtime = SessionRuntime(
            self.store,
            session_factory=factory or self.factory,
            preparation=preparation,
            guard_settings=GuardSettings(profile_id="unit-profile", policy_version="unit-v1"),
            cleanup_limits=cleanup_limits,
        )

    async def startup(self):
        self.startups += 1
        self.trace.append(("startup", None))
        if self.startup_error:
            raise self.startup_error
        await asyncio.sleep(0)
        return self.receipt

    async def verify(self, expected):
        assert expected == CONFIGURATION
        self.verifications += 1
        self.trace.append(("verify", None))
        if self.verify_error:
            raise self.verify_error
        return self.observed

    async def prepare(self, task_id, expected):
        self.trace.append(("prepare", task_id))
        if self.prepare_error:
            raise self.prepare_error
        resources = TaskResources(task_id, self.resource_config, task_id, object())
        self.resources[task_id] = resources
        return resources

    async def factory(self, state, options, resources):
        assert resources is self.resources[state.task_id]
        self.factories += 1
        self.trace.append(("factory", state.task_id))
        if self.factory_error:
            raise self.factory_error
        session = Session(state.task_id, self.trace)
        self.sessions[state.task_id] = session
        return session

    async def confirm(self, resources):
        self.trace.append(("confirm", resources.task_id))
        if resources.task_id in self.sessions:
            assert self.sessions[resources.task_id].closed
        return self.confirmed

    async def cleanup(self, resources):
        self.trace.append(("cleanup", resources.task_id))
        self.cleaned.append(resources.task_id)
        return self.cleanup_result


def test_configuration_receipts_are_immutable():
    with pytest.raises(FrozenInstanceError):
        CONFIGURATION.config_digest = "changed"


def test_shared_preparation_once_but_verification_for_every_task_and_before_factory():
    async def run():
        harness = Harness()
        runtime = harness.runtime
        first = await runtime.create_session(SessionOptions())
        second = await runtime.create_session(SessionOptions())
        assert harness.startups == 1
        assert harness.verifications == 4
        assert harness.factories == harness.store.creates == 2
        for state in (first, second):
            operations = [name for name, task_id in harness.trace if task_id == state.task_id]
            assert operations == ["prepare", "factory", "store"]
            position = harness.trace.index(("factory", state.task_id))
            assert harness.trace[position - 1] == ("verify", None)
        await runtime.close()
        for state in (first, second):
            operations = [name for name, task_id in harness.trace if task_id == state.task_id]
            assert operations[-4:] == ["interrupt", "close", "confirm", "cleanup"]
        assert not runtime.pending_cleanup

    asyncio.run(run())


@pytest.mark.parametrize(
    ("receipt", "code"),
    [
        (True, "CODEX_PREPARATION_FAILED"),
        (StartupReceipt(CONFIGURATION, None, True), "CODEX_L2_UNAVAILABLE"),
        (StartupReceipt(CONFIGURATION, CONFIGURATION, False), "CODEX_L2_FAILED"),
        (StartupReceipt(CONFIGURATION, CHANGED, True), "CODEX_CONFIG_MISMATCH"),
        (
            StartupReceipt(replace(CONFIGURATION, config_digest=""), CONFIGURATION, True),
            "CODEX_PREPARATION_FAILED",
        ),
        (
            StartupReceipt(CONFIGURATION, replace(CONFIGURATION, model_digest="x" * 64), True),
            "CODEX_PREPARATION_FAILED",
        ),
    ],
)
def test_missing_malformed_or_unbound_l2_never_creates_resources_session_or_store(receipt, code):
    async def run():
        harness = Harness()
        harness.receipt = receipt
        with pytest.raises(SessionRuntimeError, match=code):
            await harness.runtime.create_session(SessionOptions())
        assert harness.factories == harness.store.creates == 0
        assert not harness.resources
        await harness.runtime.close()

    asyncio.run(run())


def test_no_preparation_has_no_boolean_l2_bypass():
    async def run():
        harness = Harness()
        harness.build(None)
        with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_UNAVAILABLE"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.startups == harness.factories == harness.store.creates == 0
        await harness.runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["startup", "verify", "prepare", "factory"])
def test_callback_errors_use_fixed_codes_and_reclaim_only_owned_resources(stage):
    async def run():
        harness = Harness()
        setattr(harness, f"{stage}_error", OSError("private-file-and-token-text"))
        with pytest.raises(SessionRuntimeError, match="^CODEX_PREPARATION_FAILED$") as error:
            await harness.runtime.create_session(SessionOptions())
        assert str(error.value) == "CODEX_PREPARATION_FAILED"
        assert harness.store.creates == 0
        assert len(harness.cleaned) == (stage == "factory")
        await harness.runtime.close()

    asyncio.run(run())


def test_cached_startup_cannot_bypass_changed_task_configuration():
    async def run():
        harness = Harness()
        await harness.runtime.create_session(SessionOptions())
        harness.observed = CHANGED
        with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_MISMATCH"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.startups == 1
        assert harness.factories == harness.store.creates == 1
        await harness.runtime.close()

    asyncio.run(run())


def test_failed_shared_preparation_is_not_repeated_within_same_app():
    async def run():
        harness = Harness()
        harness.startup_error = OSError("private-error")
        for _ in range(2):
            with pytest.raises(SessionRuntimeError, match="CODEX_PREPARATION_FAILED"):
                await harness.runtime.create_session(SessionOptions())
        assert harness.startups == 1
        assert harness.factories == harness.store.creates == 0
        await harness.runtime.close()

    asyncio.run(run())


def test_change_during_resource_setup_is_rechecked_before_factory_and_reclaimed():
    async def run():
        harness = Harness()

        async def prepare(task_id, expected):
            resources = await harness.prepare(task_id, expected)
            harness.observed = CHANGED
            return resources

        harness.build(replace(harness.preparation, prepare_task=prepare))
        with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_MISMATCH"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.factories == harness.store.creates == 0
        assert len(harness.cleaned) == 1
        assert harness.trace[-2:] == [
            ("confirm", harness.cleaned[0]),
            ("cleanup", harness.cleaned[0]),
        ]
        await harness.runtime.close()

    asyncio.run(run())


def test_resource_receipt_configuration_must_match_startup():
    async def run():
        harness = Harness()
        harness.resource_config = CHANGED
        with pytest.raises(SessionRuntimeError, match="CODEX_CONFIG_MISMATCH"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.factories == harness.store.creates == 0
        assert len(harness.cleaned) == 1
        await harness.runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("shared_field", ["owner_token", "handle", "task_id"])
def test_shared_resource_receipt_never_closes_or_erases_another_task(shared_field):
    async def run():
        harness = Harness()
        owned = []

        async def prepare(task_id, expected):
            # The first Task gets its own receipt; later ones share a field of it.
            resource = await harness.prepare(task_id, expected)
            if not owned:
                return resource
            return replace(resource, **{shared_field: getattr(owned[0], shared_field)})

        harness.build(replace(harness.preparation, prepare_task=prepare))
        first = await harness.runtime.create_session(SessionOptions())
        owned.append(harness.resources[first.task_id])
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.create_session(SessionOptions())
        assert not harness.sessions[first.task_id].closed
        assert not harness.cleaned
        assert harness.factories == harness.store.creates == 1
        allocations = len(harness.resources)
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.create_session(SessionOptions())
        assert len(harness.resources) == allocations
        runner = harness.runtime.runner_for_task(first.task_id)
        runner.add_user_message("still-independent")
        assert (await runner.advance()).assistant_text == "still-independent"
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.close()
        assert harness.cleaned == [first.task_id]

    asyncio.run(run())


def test_store_failure_closes_confirms_then_cleans_before_releasing_ownership():
    async def run():
        harness = Harness()
        harness.store.fail_create = True
        with pytest.raises(StoreError):
            await harness.runtime.create_session(SessionOptions())
        task_id = next(iter(harness.sessions))
        assert harness.trace[-3:] == [
            ("close", task_id),
            ("confirm", task_id),
            ("cleanup", task_id),
        ]
        assert not harness.runtime.pending_cleanup
        await harness.runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("confirmation", [False, None, 1])
def test_unconfirmed_exit_retains_resources_blocks_new_tasks_and_preserves_other_task(confirmation):
    async def run():
        harness = Harness()
        first = await harness.runtime.create_session(SessionOptions())
        second = await harness.runtime.create_session(SessionOptions())
        harness.confirmed = confirmation
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
        assert not harness.cleaned
        assert first.task_id in harness.runtime.pending_cleanup
        assert (await harness.runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
        assert (await harness.runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        runner = harness.runtime.runner_for_task(second.task_id)
        runner.add_user_message("independent")
        assert (await runner.advance()).assistant_text == "independent"
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.factories == harness.store.creates == 2
        harness.confirmed = True
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.close()
        assert harness.cleaned == [second.task_id]

    asyncio.run(run())


@pytest.mark.parametrize("result", [False, True, "invalid"])
def test_cleanup_result_must_be_none_before_ownership_is_released(result):
    async def run():
        harness = Harness()
        state = await harness.runtime.create_session(SessionOptions())
        harness.cleanup_result = result
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.stop_session(state.task_id, state.epoch, ControlAction.CANCEL)
        assert state.task_id in harness.runtime.pending_cleanup
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.create_session(SessionOptions())
        assert harness.factories == harness.store.creates == 1
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await harness.runtime.close()
        assert harness.cleaned == [state.task_id]

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["startup", "prepare", "verify", "factory"])
def test_shutdown_reclaims_cancellation_ignoring_late_returns_in_order(stage):
    async def run():
        harness = Harness()
        entered, release = asyncio.Event(), asyncio.Event()
        factory = None

        async def delay(action):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return await action()

        if stage == "startup":
            preparation = replace(
                harness.preparation, prepare_startup=lambda: delay(harness.startup)
            )
        elif stage == "prepare":
            preparation = replace(
                harness.preparation,
                prepare_task=lambda task_id, expected: delay(
                    lambda: harness.prepare(task_id, expected)
                ),
            )
        elif stage == "verify":

            async def verify(expected):
                if harness.resources:
                    return await delay(lambda: harness.verify(expected))
                return await harness.verify(expected)

            preparation = replace(harness.preparation, verify_configuration=verify)
        else:
            preparation = harness.preparation

            async def delayed_factory(state, options, resources):
                return await delay(lambda: harness.factory(state, options, resources))

            factory = delayed_factory
        harness.build(preparation, factory=factory, cleanup_limits=CleanupLimits(session_s=0.01))
        creation = asyncio.create_task(harness.runtime.create_session(SessionOptions()))
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
            await asyncio.wait_for(harness.runtime.close(), 1)
        # A pending factory can still launch; shutdown must not delete its folder.
        assert not any(name in ("confirm", "cleanup") for name, _ in harness.trace)
        assert not creation.done()
        release.set()
        with pytest.raises(SessionRuntimeError, match="SESSION_UNAVAILABLE"):
            await creation
        assert harness.store.creates == 0
        if stage == "startup":
            assert not harness.resources
            assert harness.factories == 0
        else:
            assert len(harness.cleaned) == 1
            task_id = harness.cleaned[0]
            assert harness.trace[-2:] == [("confirm", task_id), ("cleanup", task_id)]
            if stage == "factory":
                assert harness.trace[-3:] == [
                    ("close", task_id),
                    ("confirm", task_id),
                    ("cleanup", task_id),
                ]
            else:
                assert harness.factories == 0
        assert not harness.runtime.pending_cleanup

    asyncio.run(run())


def test_honored_preparation_cancellation_remains_cancelled_and_never_creates_session():
    async def run():
        harness = Harness()
        entered = asyncio.Event()

        async def prepare(task_id, expected):
            entered.set()
            await asyncio.Future()

        harness.build(replace(harness.preparation, prepare_task=prepare))
        creation = asyncio.create_task(harness.runtime.create_session(SessionOptions()))
        await entered.wait()
        creation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creation
        assert harness.factories == harness.store.creates == 0
        assert not harness.cleaned
        await harness.runtime.close()

    asyncio.run(run())
