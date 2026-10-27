"""B4/B5 오케스트레이션 단위 대역. 실제 MCP·SQLite·wire 계수 검증이 아니다."""

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from guard_app.core.taint import merge
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    RawResult,
    RegisteredServer,
    Released,
    Withheld,
)
from guard_app.domain.enums import (
    ApprovalStatus,
    Label,
    RegStatus,
    RequestStatus,
    TaskStatus,
    ToolKind,
    TrustTier,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.models import AccessResult, Boundary, ProposedCall, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.guard import GuardService

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class _Store:
    def __init__(self, state):
        self.states = {state.task_id: state}
        self.requests = {}
        self.statuses = {}
        self.approvals = {}
        self.trace = []
        self.fail_at = set()
        self.observation_started = None
        self.observation_release = None
        self.consume_count = 0
        self.after_reserve = None
        self.consume_increments_security = False

    def _fail(self, operation):
        if operation in self.fail_at:
            raise StoreError()

    async def load_task(self, task_id):
        self._fail("load")
        if task_id not in self.states:
            raise StoreError(Reason.STATE_MISSING)
        return self.states[task_id]

    async def record_decision(self, req, decision, **kwargs):
        self._fail("decision")
        self.trace.append(("decision", req.request_id, decision.decision))
        self.requests[req.request_id] = req
        self.statuses[req.request_id] = RequestStatus.DECIDED
        assert "arguments_digest" in kwargs
        assert "arguments" not in kwargs

    async def reserve_dispatch(self, request_id, **kwargs):
        self._fail("reserve")
        self.trace.append(("reserve", request_id))
        self.statuses[request_id] = RequestStatus.DISPATCHING
        if self.after_reserve is not None:
            self.after_reserve()

    async def set_task_status(self, task_id, status, *, expected_row_revision, **kwargs):
        self._fail("status")
        state = self.states[task_id]
        assert expected_row_revision == state.row_revision
        assert not (
            state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME)
            and status is TaskStatus.RUNNING
        )
        updated = state.model_copy(
            update={"status": status, "row_revision": state.row_revision + 1}
        )
        self.states[task_id] = updated
        self.trace.append(("status", status))
        return updated

    async def create_approval(self, approval):
        self._fail("approval")
        self.approvals[approval.approval_id] = approval
        self.statuses[approval.request_id] = RequestStatus.WAITING_APPROVAL

    async def get_approval(self, approval_id):
        self._fail("get_approval")
        return self.approvals.get(approval_id)

    async def transition_approval(
        self, approval_id, status, *, expected_row_revision, approver_id, now_utc, reason
    ):
        self._fail("transition")
        appr = self.approvals[approval_id]
        assert appr.row_revision == expected_row_revision
        updated = appr.model_copy(
            update={
                "status": status,
                "decided_at_utc": now_utc,
                "row_revision": appr.row_revision + 1,
            }
        )
        self.approvals[approval_id] = updated
        return updated

    async def consume_and_reserve(self, **kwargs):
        self._fail("consume")
        appr = self.approvals[kwargs["approval_id"]]
        state = self.states[kwargs["task_id"]]
        if (
            appr.status is not ApprovalStatus.APPROVED
            or appr.snapshot_hash != kwargs["snapshot_hash"]
            or kwargs["now_utc"] >= appr.expires_at_utc
            or state.security_version != kwargs["expected_security_version"]
            or state.status is not TaskStatus.WAITING_APPROVAL
        ):
            return False
        self.consume_count += 1
        self.approvals[appr.approval_id] = appr.model_copy(
            update={"status": ApprovalStatus.CONSUMED, "consumed_at_utc": kwargs["now_utc"]}
        )
        self.statuses[appr.request_id] = RequestStatus.DISPATCHING
        self.states[state.task_id] = state.model_copy(
            update={
                "status": TaskStatus.RUNNING,
                "security_version": state.security_version + int(self.consume_increments_security),
            }
        )
        return True

    async def commit_observation(self, req, label, **kwargs):
        self._fail("observation")
        if self.observation_started is not None:
            self.observation_started.set()
            await self.observation_release.wait()
        state = merge(self.states[req.task_id], label, req.resource_id or "docs/read", req.call_seq)
        self.states[req.task_id] = state
        self.statuses[req.request_id] = (
            RequestStatus.SUCCEEDED if label is not None else RequestStatus.WITHHELD
        )
        self.trace.append(("read_commit", req.request_id))
        return state

    async def commit_send_outcome(self, req, **kwargs):
        self._fail("send_commit")
        self.statuses[req.request_id] = RequestStatus.SUCCEEDED
        self.trace.append(("send_commit", req.request_id))

    async def commit_request_failure(self, req, *, request_status, task_status, **kwargs):
        self._fail("failure")
        self.statuses[req.request_id] = request_status
        state = self.states[req.task_id].model_copy(update={"status": task_status})
        self.states[req.task_id] = state
        self.trace.append(("failure", request_status, task_status))
        return state


class _Registry:
    def __init__(self):
        schema = {"type": "object", "properties": {"dest": {"type": "string"}}}
        self.read = ToolSpec.model_validate(
            {
                "server_id": "docs",
                "tool_name": "read",
                "kind": ToolKind.READ,
                "input_schema": {"type": "object", "properties": {}},
                "destination_arg": None,
                "resource_arg": None,
                "fingerprint": "a" * 64,
            }
        )
        self.send = self.read.model_copy(
            update={
                "tool_name": "send",
                "kind": ToolKind.SEND,
                "input_schema": schema,
                "destination_arg": "dest",
            }
        )
        self.record = RegisteredServer(
            server_id="docs",
            status=RegStatus.ACTIVE,
            boundary=Boundary(
                boundary_id="docs",
                tier=TrustTier.INTERNAL,
                max_label=Label.CONFIDENTIAL,
            ),
            tools=(self.read, self.send),
        )
        self.current_fingerprint = "a" * 64

    def registration(self, server_id):
        return self.record if server_id == "docs" else None

    def status(self, server_id):
        return self.record.status if server_id == "docs" else None

    def fingerprint(self, tool):
        return self.current_fingerprint


class _Transport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.raw = RawResult.model_validate(
            {"payload": {"content": [{"type": "text", "text": "RAW_SECRET_MARKER"}]}}
        )

    async def call(self, req):
        self.calls.append(req)
        if self.error is not None:
            raise self.error
        return self.raw


class _Access:
    def __init__(self):
        self.calls = []
        self.result = AccessResult(allowed=True, reason="", error=False)
        self.error = None

    def check_access(self, req, ctx):
        self.calls.append((req, ctx))
        if self.error is not None:
            raise self.error
        return self.result


class _Harness:
    def __init__(self, *, label=Label.PUBLIC, source_label=Label.CONFIDENTIAL, **guard_kwargs):
        state = TaskState(
            task_id="task",
            epoch=1,
            profile_id="p",
            policy_version="v",
            status=TaskStatus.RUNNING,
            high_watermark=label,
        )
        self.store = _Store(state)
        self.registry = _Registry()
        self.transport = _Transport()
        self.access = _Access()
        self.audit = []
        self.audit_error = None
        self.ids = count(1)
        self.sequences = count(1)
        self.monotonic = 100.0
        self.now = NOW
        self.catalog = Catalog(
            self.registry,
            profile_id="p",
            destinations=(
                Boundary(
                    boundary_id="external",
                    tier=TrustTier.GENERAL_EXTERNAL,
                    max_label=Label.INTERNAL,
                ),
            ),
            source_rules=()
            if source_label is None
            else (
                SourceRule(
                    server_id="docs",
                    tool_name="read",
                    prefix=(),
                    label=source_label,
                ),
            ),
        )
        kwargs = {
            "profile_id": "p",
            "policy_version": "v",
            "operator_id": "operator",
            "request_id_factory": lambda: f"request-{next(self.ids)}",
            "approval_id_factory": lambda: f"approval-{next(self.ids)}",
            "next_call_seq": self.sequence,
            "failure_audit": self.failure_audit,
            "utc_now": lambda: self.now,
            "monotonic": lambda: self.monotonic,
        }
        kwargs.update(guard_kwargs)
        self.guard = GuardService(
            self.store, self.registry, self.catalog, self.transport, self.access, **kwargs
        )

    async def sequence(self, task_id):
        return next(self.sequences)

    async def failure_audit(self, event):
        if self.audit_error is not None:
            raise self.audit_error
        self.audit.append(event)


def _call(name="read"):
    return ProposedCall.model_validate(
        {
            "tool_ref": f"docs__{name}",
            "arguments": {"dest": "external"} if name == "send" else {},
            "model_call_id": "same-model-id",
        }
    )


@pytest.mark.parametrize("policy_version", ["v", "changed"])
@pytest.mark.parametrize("status", list(TaskStatus))
def test_submit_stale_caller_epoch_never_changes_replacement_t21(status, policy_version):
    async def scenario():
        h = _Harness()
        replacement = h.store.states["task"].model_copy(
            update={"epoch": 2, "status": status, "policy_version": policy_version}
        )
        h.store.states["task"] = replacement

        result = await h.guard.submit("task", _call(), expected_epoch=1)

        assert isinstance(result, Denied)
        assert result.request_id is None
        assert result.reasons == (Reason.TASK_NOT_RUNNING,)
        assert h.store.states["task"] == replacement
        assert h.store.trace == []
        assert h.store.requests == {}
        assert h.store.approvals == {}
        assert h.store.consume_count == 0
        assert h.access.calls == []
        assert h.transport.calls == []
        assert next(h.ids) == 1
        assert next(h.sequences) == 1

    asyncio.run(scenario())


def test_submit_checks_caller_epoch_after_waiting_for_task_lock_t21():
    async def scenario():
        h = _Harness()
        lock = h.guard._task_lock("task")
        await lock.acquire()
        queued = asyncio.create_task(h.guard.submit("task", _call(), expected_epoch=1))
        await asyncio.sleep(0)
        assert not queued.done()
        replacement = h.store.states["task"].model_copy(update={"epoch": 2})
        h.store.states["task"] = replacement
        lock.release()

        result = await queued

        assert isinstance(result, Denied)
        assert result.reasons == (Reason.TASK_NOT_RUNNING,)
        assert h.store.states["task"] == replacement
        assert h.store.trace == []
        assert h.access.calls == []
        assert h.transport.calls == []
        assert not lock.locked()

        current = await h.guard.submit("task", _call(), expected_epoch=2)
        assert isinstance(current, Released)
        assert len(h.transport.calls) == 1
        assert h.transport.calls[0].epoch == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("expected_epoch", [True, False, -1, 1.0, "1"])
def test_submit_rejects_invalid_trusted_epoch_without_dispatch_t21(expected_epoch):
    async def scenario():
        h = _Harness()
        state = h.store.states["task"]

        result = await h.guard.submit("task", _call(), expected_epoch=expected_epoch)

        assert isinstance(result, Denied)
        assert result.reasons == (Reason.STATE_MISSING,)
        assert h.store.states["task"] == state
        assert h.store.trace == []
        assert h.access.calls == []
        assert h.transport.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("epoch", [0, 1])
@pytest.mark.parametrize(
    ("label", "tool", "outcome"),
    [
        (Label.PUBLIC, "read", Released),
        (Label.INTERNAL, "send", Pending),
    ],
)
def test_submit_matching_epoch_preserves_allow_and_ask_t21(epoch, label, tool, outcome):
    async def scenario():
        h = _Harness(label=label)
        h.store.states["task"] = h.store.states["task"].model_copy(update={"epoch": epoch})

        result = await h.guard.submit("task", _call(tool), expected_epoch=epoch)

        assert isinstance(result, outcome)
        assert len(h.transport.calls) == int(outcome is Released)
        assert len(h.store.approvals) == int(outcome is Pending)

    asyncio.run(scenario())


def test_read_commit_precedes_release_and_next_send_t05_t25():
    async def scenario():
        h = _Harness()
        released = await h.guard.submit("task", _call())
        assert isinstance(released, Released)
        assert released.label is Label.CONFIDENTIAL
        assert h.store.trace[-1][0] == "read_commit"
        denied = await h.guard.submit("task", _call("send"))
        assert isinstance(denied, Denied)
        assert len(h.transport.calls) == 1
        assert len(h.access.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["load", "decision", "reserve", "status", "approval"])
def test_store_failure_before_dispatch_blocks_globally(stage):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        h.store.fail_at.add(stage)
        result = await h.guard.submit(
            "task", _call("send") if stage in ("status", "approval") else _call()
        )
        assert isinstance(result, Denied)
        assert not h.transport.calls
        h.store.fail_at.clear()
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "expected", "status"),
    [
        ("read", Withheld, TaskStatus.HALTED),
        ("send", OutcomeUnknown, TaskStatus.UNKNOWN_OUTCOME),
    ],
)
def test_post_dispatch_exception_contains_and_no_retry_t36(name, expected, status):
    async def scenario():
        h = _Harness()
        h.transport.error = RuntimeError("EXCEPTION_SECRET_MARKER")
        outcome = await h.guard.submit("task", _call(name))
        assert isinstance(outcome, expected)
        assert outcome.reasons == (Reason.POST_DISPATCH_ERROR,)
        assert h.store.states["task"].status is status
        assert h.audit[-1].exception_type == "RuntimeError"
        assert "EXCEPTION_SECRET_MARKER" not in repr(outcome) + repr(h.audit)
        assert isinstance(await h.guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("audit_fails", [False, True])
def test_containment_failure_global_halt(audit_fails):
    async def scenario():
        h = _Harness()
        h.transport.error = RuntimeError("hidden")
        if audit_fails:
            h.audit_error = RuntimeError("audit hidden")
        else:
            h.store.fail_at.add("failure")
        assert isinstance(await h.guard.submit("task", _call()), Withheld)
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})
        h.store.fail_at.clear()
        assert isinstance(await h.guard.submit("other", _call()), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_wrong_containment_return_state_blocks_other_tasks(name):
    async def scenario():
        h = _Harness()
        h.transport.error = RuntimeError("hidden")
        original = h.store.commit_request_failure

        async def invalid_return(req, **kwargs):
            state = await original(req, **kwargs)
            return state.model_copy(update={"task_id": "wrong-task"})

        h.store.commit_request_failure = invalid_return
        result = await h.guard.submit("task", _call(name))
        assert isinstance(result, Withheld if name == "read" else OutcomeUnknown)
        h.store.states["other"] = TaskState(
            task_id="other",
            epoch=1,
            profile_id="p",
            policy_version="v",
            status=TaskStatus.RUNNING,
        )
        assert isinstance(await h.guard.submit("other", _call()), Denied)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


def test_wrong_halt_return_state_is_not_acknowledged():
    async def scenario():
        h = _Harness()
        h.store.states["task"] = h.store.states["task"].model_copy(
            update={"policy_version": "changed"}
        )
        original = h.store.set_task_status

        async def invalid_return(task_id, status, **kwargs):
            state = await original(task_id, status, **kwargs)
            return state.model_copy(update={"status": TaskStatus.RUNNING})

        h.store.set_task_status = invalid_return
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.STATE_COMMIT_FAILED,)
        h.store.states["other"] = TaskState(
            task_id="other",
            epoch=1,
            profile_id="p",
            policy_version="v",
            status=TaskStatus.RUNNING,
        )
        assert isinstance(await h.guard.submit("other", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "foreign_status", [TaskStatus.RUNNING, TaskStatus.HALTED, TaskStatus.CLOSED]
)
def test_emergency_halt_rejects_a_foreign_task_from_store(foreign_status):
    async def scenario():
        h = _Harness()
        h.store.states["healthy"] = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        foreign = h.store.states["task"].model_copy(
            update={"task_id": "foreign", "status": foreign_status}
        )
        original = h.store.load_task

        async def wrong_lookup(task_id):
            if task_id == "task":
                return foreign
            return await original(task_id)

        h.store.load_task = wrong_lookup
        result = await h.guard.human_control("operator").emergency_halt("task")
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert not h.store.trace
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


def test_pre_failure_audit_error_is_contained():
    async def scenario():
        h = _Harness()
        h.access.error = ValueError("secret")
        h.audit_error = RuntimeError("secret")
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert not h.transport.calls
        h.access.error = None
        assert isinstance(await h.guard.submit("task", _call()), Denied)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_cancellation_after_reservation_is_never_denied(name):
    async def scenario():
        h = _Harness()
        h.transport.error = asyncio.CancelledError()
        outcome = await h.guard.submit("task", _call(name))
        assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


def test_cancellation_before_reservation_does_not_dispatch():
    async def scenario():
        h = _Harness()

        async def cancelled_sequence(task_id):
            raise asyncio.CancelledError()

        # Cancellation still can occur at an actual asynchronous boundary.
        h.guard._next_call_seq = cancelled_sequence
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert not h.transport.calls
        assert not h.access.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"), [("profile_id", "changed"), ("policy_version", "changed")]
)
def test_policy_change_halts_t29(field, value):
    async def scenario():
        h = _Harness()
        h.store.states["task"] = h.store.states["task"].model_copy(update={field: value})
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.POLICY_CHANGED,)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert not h.transport.calls
        assert not h.access.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("status", list(TaskStatus))
def test_non_running_task_never_dispatches(status):
    async def scenario():
        h = _Harness()
        h.store.states["task"] = h.store.states["task"].model_copy(update={"status": status})
        outcome = await h.guard.submit("task", _call())
        assert isinstance(outcome, Released if status is TaskStatus.RUNNING else Denied)

    asyncio.run(scenario())


def test_access_error_and_deny_do_not_dispatch():
    async def scenario():
        h = _Harness()
        h.access.result = AccessResult(allowed=False, reason="", error=True)
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert Reason.AGT_ERROR in result.reasons
        assert not h.transport.calls
        h.access.result = AccessResult(allowed=False, reason="RAW", error=False)
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert Reason.AGT_DENY in result.reasons
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("fingerprint", [None, "b" * 64])
def test_registry_change_during_sequence_wait_blocks_before_access(fingerprint):
    async def scenario():
        h = _Harness()
        entered, release = asyncio.Event(), asyncio.Event()
        original_sequence = h.guard._next_call_seq

        async def blocked_sequence(task_id):
            entered.set()
            await release.wait()
            return await original_sequence(task_id)

        h.guard._next_call_seq = blocked_sequence
        running = asyncio.create_task(h.guard.submit("task", _call()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        h.registry.current_fingerprint = fingerprint
        release.set()
        result = await asyncio.wait_for(running, timeout=1)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.TOOL_FINGERPRINT_CHANGED,)
        assert not h.access.calls
        assert not h.transport.calls

    asyncio.run(scenario())


def test_registry_change_after_reserve_is_contained_without_transport():
    async def scenario():
        h = _Harness()
        h.store.after_reserve = lambda: setattr(h.registry, "current_fingerprint", None)
        assert isinstance(await h.guard.submit("task", _call()), Withheld)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert not h.transport.calls

    asyncio.run(scenario())


def test_same_model_call_id_is_separate_guard_requests_t13():
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        results = [await h.guard.submit("task", _call()), await h.guard.submit("task", _call())]
        assert all(isinstance(result, Released) for result in results)
        assert results[0].request_id != results[1].request_id
        assert [req.call_seq for req in h.transport.calls] == [1, 2]

    asyncio.run(scenario())


def test_submit_waits_for_previous_read_commit():
    async def scenario():
        h = _Harness()
        h.store.observation_started = asyncio.Event()
        h.store.observation_release = asyncio.Event()
        reading = asyncio.create_task(h.guard.submit("task", _call()))
        await h.store.observation_started.wait()
        sending = asyncio.create_task(h.guard.submit("task", _call("send")))
        await asyncio.sleep(0)
        assert len(h.access.calls) == 1
        assert len(h.transport.calls) == 1
        h.store.observation_release.set()
        assert isinstance(await reading, Released)
        assert isinstance(await sending, Denied)

    asyncio.run(scenario())


def test_other_task_does_not_wait_for_read_commit():
    async def scenario():
        h = _Harness()
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})
        h.store.observation_started = asyncio.Event()
        h.store.observation_release = asyncio.Event()
        reading = asyncio.create_task(h.guard.submit("task", _call()))
        await h.store.observation_started.wait()
        sent = await h.guard.submit("other", _call("send"))
        assert isinstance(sent, Released)
        assert not reading.done()
        h.store.observation_release.set()
        assert isinstance(await reading, Released)
        assert h.store.states["other"].high_watermark is Label.PUBLIC

    asyncio.run(scenario())


@pytest.mark.parametrize("increment_security", [False, True])
def test_ask_unlocks_and_human_approval_executes_once(increment_security):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        h.store.consume_increments_security = increment_security
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        assert not h.transport.calls
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        control = h.guard.human_control("operator")
        outcomes = await asyncio.gather(
            control.approve(pending.approval_id), control.approve(pending.approval_id)
        )
        assert sum(isinstance(outcome, Released) for outcome in outcomes) == 1
        assert h.store.consume_count == 1
        assert len(h.transport.calls) == 1
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert h.store.states["task"].high_watermark is Label.INTERNAL

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action", ["approve", "reject", "expire", "approve_expired", "approve_recheck_denied"]
)
@pytest.mark.parametrize("identity", ["task_id", "epoch"])
@pytest.mark.parametrize(
    ("policy_matches", "returned_status"),
    [(False, TaskStatus.RUNNING), (True, TaskStatus.WAITING_APPROVAL)],
)
def test_approval_wrong_task_identity_never_changes_task_rows_t09_t11_t20(
    action, identity, policy_matches, returned_status
):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        snapshot_state = h.store.states["task"]
        returned = snapshot_state.model_copy(
            update={
                "task_id": "foreign" if identity == "task_id" else "task",
                "epoch": snapshot_state.epoch + int(identity == "epoch"),
                "profile_id": "p" if policy_matches else "other-profile",
                "status": returned_status,
            }
        )
        h.store.states[returned.task_id] = returned
        h.store.states["healthy"] = snapshot_state.model_copy(
            update={"task_id": "healthy", "status": TaskStatus.RUNNING}
        )
        original_load = h.store.load_task
        lookup_count = 0

        async def wrong_lookup(task_id):
            nonlocal lookup_count
            if task_id == "task":
                lookup_count += 1
                if action == "approve_recheck_denied" and lookup_count == 1:
                    return snapshot_state
                return returned
            return await original_load(task_id)

        h.store.load_task = wrong_lookup
        if action in ("expire", "approve_expired"):
            h.monotonic += 300
        if action == "approve_recheck_denied":
            h.access.result = AccessResult(allowed=False, error=False, reason="")
        states_before = dict(h.store.states)
        trace_before = list(h.store.trace)
        control = h.guard.human_control("operator")
        command = "approve" if action.startswith("approve") else action
        result = await getattr(control, command)(pending.approval_id)

        assert h.store.states == states_before
        assert h.store.trace == trace_before
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert h.store.approvals[pending.approval_id].status not in (
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        )
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._task_lock("task").locked()
        assert h.store.consume_count == 0
        assert not h.transport.calls
        assert h.audit[-1].task_id == "task"
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("trigger", ["record_hash", "record_operator", "policy_halt", "load_error"])
def test_approval_halt_paths_never_stop_replacement_epoch_t09_t20(trigger):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        snapshot_state = h.store.states["task"]
        replacement = snapshot_state.model_copy(
            update={"epoch": snapshot_state.epoch + 1, "status": TaskStatus.RUNNING}
        )
        h.store.states["task"] = replacement
        record = h.store.approvals[pending.approval_id]
        if trigger == "record_hash":
            h.store.approvals[pending.approval_id] = record.model_copy(
                update={"snapshot_hash": "b" * 64}
            )
        elif trigger == "record_operator":
            h.store.approvals[pending.approval_id] = record.model_copy(
                update={"approver_id": "different-operator"}
            )
        original_load = h.store.load_task
        lookup_count = 0

        async def rollover_lookup(task_id):
            nonlocal lookup_count
            lookup_count += 1
            if lookup_count == 1:
                if trigger == "policy_halt":
                    return snapshot_state.model_copy(update={"profile_id": "other-profile"})
                if trigger == "load_error":
                    raise ValueError("SYNTHETIC_STORE_ERROR")
            return await original_load(task_id)

        h.store.load_task = rollover_lookup
        trace_before = list(h.store.trace)
        result = await h.guard.human_control("operator").approve(pending.approval_id)

        assert h.store.states["task"] == replacement
        assert h.store.trace == trace_before
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert not h.guard._approvals._memory_snapshots
        assert h.store.approvals[pending.approval_id].status is ApprovalStatus.CANCELLED
        assert not h.guard._task_lock("task").locked()
        assert h.store.consume_count == 0
        assert not h.transport.calls
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


def test_approved_external_read_holds_lock_through_commit_t37():
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        h.registry.record = h.registry.record.model_copy(
            update={
                "boundary": Boundary(
                    boundary_id="docs",
                    tier=TrustTier.GENERAL_EXTERNAL,
                    max_label=Label.INTERNAL,
                )
            }
        )
        pending = await h.guard.submit("task", _call())
        assert isinstance(pending, Pending)
        h.store.observation_started = asyncio.Event()
        h.store.observation_release = asyncio.Event()
        approving = asyncio.create_task(
            h.guard.human_control("operator").approve(pending.approval_id)
        )
        await h.store.observation_started.wait()
        sending = asyncio.create_task(h.guard.submit("task", _call("send")))
        await asyncio.sleep(0)
        assert len(h.access.calls) == 2
        h.store.observation_release.set()
        assert isinstance(await approving, Released)
        assert isinstance(await sending, Denied)
        assert h.store.consume_count == 1
        assert len(h.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("clock", ["utc", "monotonic"])
@pytest.mark.parametrize("name", ["read", "send"])
def test_consume_ack_after_expiry_never_calls_transport(clock, name):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if name == "read":
            h.registry.record = h.registry.record.model_copy(
                update={
                    "boundary": Boundary(
                        boundary_id="docs",
                        tier=TrustTier.GENERAL_EXTERNAL,
                        max_label=Label.INTERNAL,
                    )
                }
            )
        pending = await h.guard.submit("task", _call(name))
        assert isinstance(pending, Pending)
        original = h.store.consume_and_reserve

        async def delayed_ack(**kwargs):
            consumed = await original(**kwargs)
            if clock == "utc":
                h.now += timedelta(seconds=300)
            else:
                h.monotonic += 300
            return consumed

        h.store.consume_and_reserve = delayed_ack
        outcome = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
        assert h.store.consume_count == 1
        assert not h.transport.calls
        assert h.store.states["task"].status is (
            TaskStatus.HALTED if name == "read" else TaskStatus.UNKNOWN_OUTCOME
        )

    asyncio.run(scenario())
