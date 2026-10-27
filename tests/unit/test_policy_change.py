"""B3.10·I2/I3/I6/I8·T10: 준비 불변성, 정책 적용·알림·전송 경쟁의 B 단위 부분."""

import asyncio
import json
from copy import deepcopy
from itertools import count

import pytest

from guard_app.core.access_rules import evaluate_access, normalize_access_rules
from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, TrustTier
from guard_app.domain.models import Boundary, TaskState
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import PendingResultError
from guard_app.services.catalog import (
    CatalogConfigError,
    load_profile_settings,
    prepare_policy_change,
    prepare_server_access_change,
)
from guard_app.services.guard import GuardService, PolicyChangeError
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness


def _document():
    return {
        "profile": {
            "id": "p",
            "servers": ["docs"],
            "destinations": ["external"],
            "sources": [{"server": "docs", "tool": "read", "prefix": [], "label": "PUBLIC"}],
        },
        "servers": [
            {
                "id": "docs",
                "transport": "stdio",
                "command": "C:/PRIVATE_COMMAND/python.exe",
                "args": ["PRIVATE_ARGUMENT"],
                "tier": "internal",
                "max_label": "CONFIDENTIAL",
                "tools": [
                    {"name": "read", "kind": "read"},
                    {"name": "send", "kind": "send", "destination_arg": "dest"},
                ],
            }
        ],
        "destinations": [{"id": "external", "tier": "general_external", "max_label": "INTERNAL"}],
        "access_rules": {"allow": [["docs", "read"], ["docs", "send"]], "deny": []},
    }


class _Access:
    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def check_access(self, req, context):
        self.calls.append(
            (req.tool.tool_name, context.state.profile_id, context.state.policy_version)
        )
        return evaluate_access(
            self.rules, server_id=req.tool.server_id, tool_name=req.tool.tool_name
        )


class _PolicyHarness(_Harness):
    def __init__(self, *, label=Label.PUBLIC):
        super().__init__(label=label)
        self.document = _document()
        self.loaded = load_profile_settings(self.document, self.registry)
        self.store.states["task"] = self.store.states["task"].model_copy(
            update={"policy_version": self.loaded.policy_version}
        )
        self.access = _Access(self.loaded.access_rules)
        self.guard = self.build_guard(self.store)
        self.source_calls = 0

    def build_guard(self, store):
        return GuardService(
            store,
            self.registry,
            self.loaded.catalog,
            self.transport,
            self.access,
            profile_id=self.loaded.profile.profile_id,
            policy_version=self.loaded.policy_version,
            operator_id="operator",
            request_id_factory=lambda: f"request-{next(self.ids)}",
            approval_id_factory=lambda: f"approval-{next(self.ids)}",
            next_call_seq=self.sequence,
            failure_audit=self.failure_audit,
            utc_now=lambda: self.now,
            monotonic=lambda: self.monotonic,
        )

    async def source(self):
        self.source_calls += 1
        return tuple(self.store.states.values())

    def add_task(self, task_id, *, status=TaskStatus.RUNNING, loaded=None, label=Label.PUBLIC):
        selected = self.loaded if loaded is None else loaded
        state = TaskState(
            task_id=task_id,
            epoch=1,
            profile_id=selected.profile.profile_id,
            policy_version=selected.policy_version,
            status=status,
            high_watermark=label,
            last_read_label=label,
        )
        self.store.states[task_id] = state
        return state

    def prepare(self, permissions=None):
        return prepare_server_access_change(
            self.document,
            self.registry,
            server_id="docs",
            tool_permissions={"read": True, "send": False} if permissions is None else permissions,
        )

    async def apply(self, change):
        return await self.guard.apply_policy_change(
            change, _Access(change.loaded_profile.access_rules), task_source=self.source
        )


async def _turns():
    for _ in range(8):
        await asyncio.sleep(0)


def test_activation_replaces_only_target_rules_and_preserves_both_inputs():
    h = _PolicyHarness()
    other = deepcopy(h.document["servers"][0])
    other["id"] = "unused"
    h.document["servers"].append(other)
    h.document["access_rules"] = {
        "allow": [["docs", "send"], ["unused", "read"], ["docs", "read"], ["docs", "send"]],
        "deny": [["unused", "send"], ["docs", "read"]],
    }
    original = deepcopy(h.document)
    choices = {"read": True, "send": False}
    change = h.prepare(choices)
    assert h.document == original
    assert choices == {"read": True, "send": False}
    updated = change.document.to_dict()
    assert updated["access_rules"] == {
        "allow": [["unused", "read"], ["docs", "read"]],
        "deny": [["unused", "send"], ["docs", "send"]],
    }
    assert change.changed
    assert change.policy_version != change.previous_policy_version
    assert [server.id for server in change.loaded_profile.servers] == ["docs", "unused"]
    assert [server.id for server in change.loaded_profile.selected_servers] == ["docs"]
    updated["servers"][0]["command"] = "MUTATED"
    choices["read"] = False
    assert change.document.to_dict()["servers"][0]["command"] != "MUTATED"
    assert normalize_access_rules(change.loaded_profile.access_rules)["allow"] == [
        ["docs", "read"],
        ["unused", "read"],
    ]
    assert "PRIVATE_ARGUMENT" not in repr(change)
    assert "PRIVATE_COMMAND" not in repr(change)


@pytest.mark.parametrize("server", ["", "missing", "bad__id", "bad_", "\n", True])
def test_activation_bad_server_has_no_side_effect(server):
    h = _PolicyHarness()
    before = deepcopy(h.document)
    with pytest.raises(CatalogConfigError):
        prepare_server_access_change(
            h.document, h.registry, server_id=server, tool_permissions={"read": True, "send": False}
        )
    assert h.document == before
    assert h.store.trace == []
    assert h.transport.calls == []


@pytest.mark.parametrize(
    "choices",
    [
        {},
        {"read": True},
        {"read": True, "send": 1},
        {"read": "yes", "send": False},
        {"read": True, "send": True, "missing": True},
        None,
        [],
    ],
)
def test_activation_bad_checklist_has_no_side_effect(choices):
    h = _PolicyHarness()
    before = deepcopy(h.document)
    with pytest.raises(CatalogConfigError):
        prepare_server_access_change(
            h.document, h.registry, server_id="docs", tool_permissions=choices
        )
    assert h.document == before
    assert h.store.trace == []


def test_activation_other_tool_is_rejected_and_omitted_other_is_not_implicitly_allowed():
    h = _PolicyHarness()
    h.document["servers"][0]["tools"].append({"name": "other", "kind": "other"})
    with pytest.raises(CatalogConfigError):
        h.prepare({"read": True, "send": False, "other": True})
    assert h.prepare().changed


def test_general_preparation_is_immutable_and_uses_existing_full_hash_validation():
    h = _PolicyHarness()
    replacement = deepcopy(h.document)
    replacement["policy"] = {"taint_mode": "last_read", "approval_ttl_seconds": 10}
    replacement["profile"]["id"] = "new-profile"
    before = deepcopy(replacement)
    change = prepare_policy_change(h.document, replacement, h.registry)
    assert replacement == before
    assert change.loaded_profile.guard_settings.taint_mode == "last_read"
    assert change.loaded_profile.guard_settings.approval_ttl_seconds == 10
    assert change.policy_version == load_profile_settings(replacement, h.registry).policy_version
    replacement["policy"]["approval_ttl_seconds"] = 999
    assert change.document.to_dict()["policy"]["approval_ttl_seconds"] == 10
    with pytest.raises(CatalogConfigError) as error:
        prepare_policy_change(h.document, {"PRIVATE_ARGUMENT": True}, h.registry)
    assert "PRIVATE_ARGUMENT" not in str(error.value)
    assert h.store.trace == []


def test_noop_does_not_stop_tasks_or_replace_any_runtime_dependency():
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        old = h.guard._catalog, h.guard._access_checker, h.guard._gate
        before = h.store.states["task"], dict(h.store.approvals), list(h.store.trace)
        equivalent = json.loads(json.dumps(h.document, sort_keys=True, indent=4))
        equivalent["access_rules"]["allow"].reverse()
        change = prepare_policy_change(h.document, equivalent, h.registry)
        assert not change.changed

        async def unused_source():
            pytest.fail("같은 버전에 Task 조회를 호출했다")

        result = await h.guard.apply_policy_change(
            change, _Access(change.loaded_profile.access_rules), task_source=unused_source
        )
        assert not result.changed
        assert result.stopped_task_count == result.invalidated_approval_count == 0
        assert (h.guard._catalog, h.guard._access_checker, h.guard._gate) == old
        assert (h.store.states["task"], h.store.approvals, h.store.trace) == before
        await h.guard.aclose()

    asyncio.run(scenario())


def test_changed_policy_stops_unseen_and_waiting_tasks_and_notifies_once():
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        h.add_task("unseen-running")
        preserved = {
            status: h.add_task(status.value, status=status)
            for status in (
                TaskStatus.HALTED,
                TaskStatus.UNKNOWN_OUTCOME,
                TaskStatus.CLOSED,
                TaskStatus.READY,
            )
        }
        change = h.prepare()
        result = await h.apply(change)
        assert result.changed
        assert result.previous_policy_version == change.previous_policy_version
        assert result.policy_version == change.policy_version
        assert result.profile_id == "p"
        assert result.applied_at_utc == h.now
        assert result.stopped_task_ids == ("task", "unseen-running")
        assert result.stopped_task_count == 2
        assert result.invalidated_approval_count == 1
        for task_id in result.stopped_task_ids:
            assert h.store.states[task_id].status is TaskStatus.HALTED
        for status, state in preserved.items():
            assert h.store.states[status.value] is state
        assert h.store.approvals[pending.approval_id].status is ApprovalStatus.CANCELLED
        final = await handle.wait()
        assert final.cause == "invalidated"
        assert final.outcome.reasons == (Reason.POLICY_CHANGED,)
        await h.guard.human_control("operator").approve(pending.approval_id)
        with pytest.raises(PendingResultError):
            await handle.wait()
        old = await h.guard.submit("task", _call("read"))
        assert old.reasons == (Reason.POLICY_CHANGED,)
        assert h.transport.calls == []
        h.add_task("fresh", loaded=change.loaded_profile)
        assert isinstance(await h.guard.submit("fresh", _call("read")), Released)
        assert isinstance(await h.guard.submit("fresh", _call("send")), Denied)
        assert len(h.transport.calls) == 1
        assert "PRIVATE_COMMAND" not in repr(result)
        await h.guard.aclose()

    asyncio.run(scenario())


def test_profile_mode_ttl_gate_and_checker_are_replaced_together():
    async def scenario():
        h = _PolicyHarness(label=Label.CONFIDENTIAL)
        replacement = deepcopy(h.document)
        replacement["profile"]["id"] = "new-profile"
        replacement["policy"] = {"taint_mode": "last_read", "approval_ttl_seconds": 10}
        change = prepare_policy_change(h.document, replacement, h.registry)
        checker = _Access(change.loaded_profile.access_rules)
        await h.guard.apply_policy_change(change, checker, task_source=h.source)
        assert h.guard._profile_id == "new-profile"
        assert h.guard._mode == "last_read"
        assert h.guard._access_checker is checker
        assert h.guard._catalog is change.loaded_profile.catalog
        assert h.guard._gate._catalog is change.loaded_profile.catalog
        assert h.guard._approvals._catalog is change.loaded_profile.catalog
        assert h.store.states["task"].status is TaskStatus.HALTED
        h.add_task("fresh", loaded=change.loaded_profile, label=Label.INTERNAL)
        value = await h.guard.submit("fresh", _call("send"))
        assert isinstance(value, Pending)
        record = h.store.approvals[value.approval_id]
        assert (record.expires_at_utc - record.created_at_utc).total_seconds() == 10
        assert checker.calls == [("send", "new-profile", change.policy_version)]
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("late_task", [False, True])
def test_submit_waits_for_atomic_publish_and_late_old_tasks_are_rescanned(late_task):
    async def scenario():
        h = _PolicyHarness()
        change = h.prepare()
        h.add_task("fresh", loaded=change.loaded_profile)
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.set_task_status

        async def blocked(task_id, *args, **kwargs):
            if task_id == "task":
                entered.set()
                await release.wait()
            return await original(task_id, *args, **kwargs)

        h.store.set_task_status = blocked
        applying = asyncio.create_task(h.apply(change))
        await entered.wait()
        queued = asyncio.create_task(h.guard.submit("fresh", _call("read")))
        await _turns()
        assert not queued.done()
        assert h.guard._policy_version == change.previous_policy_version
        if late_task:
            h.add_task("late-old")
        release.set()
        result = await applying
        assert isinstance(await queued, Released)
        assert result.stopped_task_ids == (("late-old", "task") if late_task else ("task",))
        assert h.guard._policy_version == change.policy_version
        assert h.source_calls >= (3 if late_task else 2)
        assert h.guard._access_checker.calls == [("read", "p", change.policy_version)]
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["submit", "approval"])
def test_policy_change_during_pre_access_wait_never_uses_mixed_configuration(phase):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL if phase == "approval" else Label.PUBLIC)
        pending = None
        if phase == "approval":
            pending = await h.guard.submit("task", _call("send"))
            handle = h.guard.pending_result(pending.request_id)
        entered, release = asyncio.Event(), asyncio.Event()
        old_checker = h.access
        old_calls = list(old_checker.calls)
        original_sequence = h.guard._next_call_seq
        original_transition = h.store.transition_approval
        if phase == "submit":

            async def blocked_sequence(task_id):
                entered.set()
                await release.wait()
                return await original_sequence(task_id)

            h.guard._next_call_seq = blocked_sequence
        else:

            async def blocked_transition(approval_id, status, **kwargs):
                record = await original_transition(approval_id, status, **kwargs)
                if status is ApprovalStatus.APPROVED:
                    entered.set()
                    await release.wait()
                return record

            h.store.transition_approval = blocked_transition
        work = asyncio.create_task(
            h.guard.submit("task", _call("read"))
            if phase == "submit"
            else h.guard.human_control("operator").approve(pending.approval_id)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        change = h.prepare()
        applying = asyncio.create_task(h.apply(change))
        await _turns()
        assert h.guard._stop_requests["task"].reason is Reason.POLICY_CHANGED
        assert h.guard._stop_requests["task"].is_set()
        assert not applying.done()
        release.set()
        denied = await asyncio.wait_for(work, timeout=1)
        result = await asyncio.wait_for(applying, timeout=1)
        assert isinstance(denied, Denied)
        assert denied.reasons == (Reason.POLICY_CHANGED,)
        assert result.stopped_task_count >= 1
        assert old_checker.calls == old_calls
        assert h.transport.calls == []
        if phase == "approval":
            final = await handle.wait()
            assert final.cause == "invalidated"
            assert final.outcome.reasons == (Reason.POLICY_CHANGED,)
            assert result.invalidated_approval_count == 1
        h.guard._next_call_seq = original_sequence
        h.add_task("fresh", loaded=change.loaded_profile)
        assert isinstance(await h.guard.submit("fresh", _call("read")), Released)
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["read", "send"])
@pytest.mark.parametrize("approved", [False, True])
def test_policy_change_during_transport_withholds_read_and_preserves_send_unknown(kind, approved):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL if approved else Label.PUBLIC)
        if approved and kind == "read":
            h.document["servers"][0]["tier"] = "general_external"
            h.document["servers"][0]["max_label"] = "INTERNAL"
            h.registry.record = h.registry.record.model_copy(
                update={
                    "boundary": Boundary(
                        boundary_id="docs",
                        tier=TrustTier.GENERAL_EXTERNAL,
                        max_label=Label.INTERNAL,
                    )
                }
            )
            h.loaded = load_profile_settings(h.document, h.registry)
            h.store.states["task"] = h.store.states["task"].model_copy(
                update={"policy_version": h.loaded.policy_version}
            )
            h.access = _Access(h.loaded.access_rules)
            h.guard = h.build_guard(h.store)
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.transport.call

        async def blocked(req):
            entered.set()
            await release.wait()
            return await original(req)

        h.transport.call = blocked
        if approved:
            pending = await h.guard.submit("task", _call(kind))
            assert isinstance(pending, Pending)
            handle = h.guard.pending_result(pending.request_id)
            work = asyncio.create_task(
                h.guard.human_control("operator").approve(pending.approval_id)
            )
        else:
            work = asyncio.create_task(h.guard.submit("task", _call(kind)))
        await entered.wait()
        change = h.prepare()
        applying = asyncio.create_task(h.apply(change))
        await _turns()
        assert not applying.done()
        release.set()
        outcome = await work
        result = await applying
        assert isinstance(outcome, Withheld if kind == "read" else OutcomeUnknown)
        expected = TaskStatus.HALTED if kind == "read" else TaskStatus.UNKNOWN_OUTCOME
        assert h.store.states["task"].status is expected
        assert result.stopped_task_ids == ("task",)
        assert result.invalidated_approval_count == 0
        assert len(h.transport.calls) == 1
        if approved:
            assert (await handle.wait()).outcome == outcome
            assert h.store.consume_count == 1
        state = h.store.states["task"]
        denied = await h.guard.submit("task", _call(kind))
        assert denied.reasons == (Reason.POLICY_CHANGED,)
        assert h.store.states["task"] is state
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["status", "transition", "load"])
def test_state_record_failure_never_acknowledges_success_and_globally_halts(failure):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value = await h.guard.submit("task", _call("send"))
        handle = h.guard.pending_result(value.request_id)
        change = h.prepare()
        h.store.fail_at.add(failure)
        with pytest.raises(PolicyChangeError) as error:
            await h.apply(change)
        assert "PRIVATE_ARGUMENT" not in str(error.value)
        assert h.guard._global_halt
        assert h.guard._policy_version == change.previous_policy_version
        assert h.guard._policy_ready.is_set()
        final = await handle.wait()
        assert final.cause == "failed"
        assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert isinstance(await h.guard.submit("task", _call("send")), Denied)
        assert h.transport.calls == []
        h.store.fail_at.clear()
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_source", ["exception", "list", "duplicate", "malformed", "missing"])
def test_task_inventory_failure_is_sanitized_and_fail_closed(bad_source):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value = await h.guard.submit("task", _call("send"))
        handle = h.guard.pending_result(value.request_id)

        async def source():
            state = h.store.states["task"]
            if bad_source == "exception":
                raise ValueError("PRIVATE_ARGUMENT")
            if bad_source == "list":
                return [state]
            if bad_source == "duplicate":
                return state, state
            if bad_source == "malformed":
                return ({"PRIVATE_ARGUMENT": True},)
            return ()

        with pytest.raises(PolicyChangeError) as error:
            await h.guard.apply_policy_change(
                h.prepare(), _Access(h.loaded.access_rules), task_source=source
            )
        assert "PRIVATE_ARGUMENT" not in str(error.value)
        assert h.guard._global_halt
        assert (await handle.wait()).cause == "failed"
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_stale_preparation_is_rejected_without_overwriting_applied_policy():
    async def scenario():
        h = _PolicyHarness()
        first = h.prepare()
        stale = h.prepare({"read": False, "send": True})
        await h.apply(first)
        before = dict(h.store.states), list(h.store.trace)
        with pytest.raises(PolicyChangeError):
            await h.apply(stale)
        assert h.guard._policy_version == first.policy_version
        assert (h.store.states, h.store.trace) == before
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


def test_real_sqlite_inventory_stops_idle_and_waiting_and_preserves_terminal_rows(tmp_path):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        h.add_task("idle")
        for status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            h.add_task(status.value, status=status)
        ids = count(1)
        with SQLiteStore(
            tmp_path / "policy.db",
            utc_now=lambda: h.now,
            audit_id_factory=lambda: f"audit-{next(ids)}",
        ) as store:
            for state in h.store.states.values():
                await store.create_task(state, created_at_utc=h.now)
            h.guard = h.build_guard(store)
            value = await h.guard.submit("task", _call("send"))
            assert isinstance(value, Pending)
            handle = h.guard.pending_result(value.request_id)
            before = {state.task_id: state for state in await store.list_tasks()}
            change = h.prepare()
            result = await h.guard.apply_policy_change(
                change, _Access(change.loaded_profile.access_rules), task_source=store.list_tasks
            )
            assert result.stopped_task_ids == ("idle", "task")
            assert result.invalidated_approval_count == 1
            assert (await handle.wait()).outcome.reasons == (Reason.POLICY_CHANGED,)
            for state in await store.list_tasks():
                if state.task_id in result.stopped_task_ids:
                    assert state.status is TaskStatus.HALTED
                else:
                    assert state == before[state.task_id]
            rows = (
                store._owned_connection()
                .execute("SELECT reason_code FROM audit_events WHERE event_type='HALTED'")
                .fetchall()
            )
            assert [row[0] for row in rows] == [Reason.POLICY_CHANGED.value] * 2
            assert not store._approval_contexts
            assert not h.guard._approvals._memory_snapshots
            await h.guard.aclose()

    asyncio.run(scenario())


def test_stale_task_inventory_fails_closed_instead_of_looping():
    async def scenario():
        h = _PolicyHarness()
        old = h.store.states["task"]

        async def stale_source():
            return (old,)

        with pytest.raises(PolicyChangeError):
            await h.guard.apply_policy_change(
                h.prepare(), _Access(h.loaded.access_rules), task_source=stale_source
            )
        assert h.guard._global_halt
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_concurrent_changes_are_serialized_and_second_stale_change_does_not_publish():
    async def scenario():
        h = _PolicyHarness()
        first = h.prepare()
        second = h.prepare({"read": False, "send": True})
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_source():
            entered.set()
            await release.wait()
            return await h.source()

        applying = asyncio.create_task(
            h.guard.apply_policy_change(
                first, _Access(first.loaded_profile.access_rules), task_source=blocked_source
            )
        )
        await entered.wait()
        queued = asyncio.create_task(h.apply(second))
        await _turns()
        assert not queued.done()
        release.set()
        assert (await applying).policy_version == first.policy_version
        with pytest.raises(PolicyChangeError):
            await queued
        assert h.guard._policy_version == first.policy_version
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())
