"""A18 adapter boundary: I1/I3/I7/I9, T08/T20/T21. Not C wire acceptance."""

import asyncio
import re
import threading
from dataclasses import dataclass

import pytest

from examples import synthetic_app
from guard_app.adapters import agt_real
from guard_app.adapters.agt_real import (
    AgentGovernanceAccessChecker,
    AgtConfigError,
    AgtShutdownError,
    AgtStartupError,
)
from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import Label, ToolKind
from guard_app.domain.reasons import Reason

from .test_agt import _context, _request
from .test_guard import _call, _Harness


def checker(**kwargs):
    return AgentGovernanceAccessChecker(
        {("srv-1", "read", ToolKind.READ): True}, rule_version="unit-v1", **kwargs
    )


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.002)


@dataclass
class Decision:
    allowed: object = True
    action: object = "allow"
    reason: str = "RAW_SECRET_MARKER"


class Engine:
    def __init__(self, evaluate):
        self.evaluate = evaluate


def inject(monkeypatch, access, evaluate):
    monkeypatch.setattr(access, "_create_engine", lambda: (Engine(evaluate), Decision))


@pytest.mark.asyncio
async def test_actual_sdk_exact_rules_kind_default_deny_and_source_copy():
    rules = {("srv-1", "read", ToolKind.READ): True, ("srv-1", "write", ToolKind.READ): False}
    # This checks SDK rule semantics, not cold-import speed. Deadline behavior
    # is tested separately with a controlled blocked engine constructor.
    access = AgentGovernanceAccessChecker(rules, rule_version="unit-v1", startup_timeout_s=30)
    rules[("srv-1", "read", ToolKind.READ)] = False
    try:
        await access.start()
        assert access.mode == "real" and access.rule_version == "unit-v1"
        for name, allowed in (("read", True), ("write", False), ("unknown", False)):
            result = await access.check_access(_request(tool_name=name), _context())
            assert result.allowed is allowed and not result.error
        req = _request()
        different_kind = req.model_copy(
            update={"tool": req.tool.model_copy(update={"kind": ToolKind.SEND})}
        )
        result = await access.check_access(different_kind, _context())
        assert not result.allowed and not result.error
    finally:
        await access.close()
    # close() succeeds only after the worker thread itself has exited.
    assert not access._worker.alive()


@pytest.mark.asyncio
async def test_actual_sdk_serves_concurrent_allowed_requests_without_busy():
    # QA 2026-10-13: 10 concurrent allowed requests returned 1 allow + 9 AGT_BUSY.
    access = checker()
    try:
        await access.start()
        results = await asyncio.gather(
            *(access.check_access(_request(), _context()) for _ in range(10))
        )
        assert [r.reason for r in results] == ["AGT_ALLOW"] * 10
        assert all(r.allowed and not r.error for r in results)
        mixed = await asyncio.gather(
            *(
                access.check_access(_request(tool_name=name), _context())
                for name in ("read", "write") * 5
            )
        )
        assert [r.allowed for r in mixed] == [True, False] * 5
        assert access._admitted == 0
    finally:
        await access.close()
    assert not access._worker.alive()


@pytest.mark.asyncio
async def test_only_opaque_key_reaches_sdk_and_context_mismatch_never_evaluates(monkeypatch):
    seen = []
    access = checker()

    def evaluate(*args, **kwargs):
        seen.append((args, kwargs))
        return Decision()

    inject(monkeypatch, access, evaluate)
    try:
        await access.start()
        result = await access.check_access(_request(), _context("wrong-task"))
        assert result.error and not result.allowed and seen == []
        req = _request()
        stale = req.model_copy(update={"epoch": req.epoch + 1})
        assert (await access.check_access(stale, _context())).error
        assert seen == []
        assert (await access.check_access(req, _context())).allowed
        args, kwargs = seen[0]
        assert args[0] == "guard-local" and kwargs == {"stage": "pre_tool"}
        assert set(args[1]) == {"tool"} and set(args[1]["tool"]) == {"key"}
        assert re.fullmatch(r"[0-9a-f]{64}", args[1]["tool"]["key"])
        assert all(value not in repr(seen) for value in ("srv-1", "operator-1", "task-1", "secret"))
        assert result.reason == "AGT_CONTEXT_MISMATCH"
    finally:
        await access.close()


@pytest.mark.asyncio
async def test_expression_shaped_names_are_exact_data_for_real_sdk():
    name = "read' or true or '"
    access = AgentGovernanceAccessChecker({("srv-1", name, ToolKind.READ): True}, rule_version="v1")
    try:
        await access.start()
        assert (await access.check_access(_request(tool_name=name), _context())).allowed
        assert not (await access.check_access(_request(), _context())).allowed
    finally:
        await access.close()


@pytest.mark.parametrize("value", [0, -1, True, "1", float("inf"), float("nan"), 31, 10**400])
@pytest.mark.parametrize("field", ["evaluation_timeout_s", "startup_timeout_s", "close_timeout_s"])
def test_invalid_limits_are_fixed_config_errors(field, value):
    with pytest.raises(AgtConfigError, match="AGT 설정이 유효하지 않다"):
        checker(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_pending", 0),
        ("max_pending", 257),
        ("max_pending", True),
        ("max_pending", 4.0),
        ("max_stuck_workers", -1),
        ("max_stuck_workers", 9),
        ("max_stuck_workers", False),
    ],
)
def test_invalid_concurrency_bounds_are_fixed_config_errors(field, value):
    with pytest.raises(AgtConfigError, match="AGT 설정이 유효하지 않다"):
        checker(**{field: value})


@pytest.mark.parametrize(
    "rules,revision",
    [
        ({("srv", "tool", "read"): True}, "v1"),
        ({("srv", "tool", ToolKind.READ): 1}, "v1"),
        ({("srv", "tool"): True}, "v1"),
        ({("", "tool", ToolKind.READ): True}, "v1"),
        ({("x" * 257, "tool", ToolKind.READ): True}, "v1"),
        ({("srv", "\ud800", ToolKind.READ): True}, "v1"),
        ({("srv", str(i), ToolKind.READ): True for i in range(257)}, "v1"),
        ({}, "RAW_SECRET_MARKER\n"),
    ],
)
def test_rules_reject_ambiguous_unbounded_or_invalid_configuration(rules, revision):
    with pytest.raises(AgtConfigError) as caught:
        AgentGovernanceAccessChecker(rules, rule_version=revision)
    assert "RAW_SECRET_MARKER" not in str(caught.value)


@pytest.mark.parametrize(
    "decision",
    [
        Decision(True, "deny"),
        Decision(False, "allow"),
        Decision(1, "allow"),
        Decision(True, "unknown"),
        Decision(True, None),
        object(),
        {"allowed": True},
    ],
)
def test_malformed_decisions_cannot_allow(decision):
    result = AgentGovernanceAccessChecker._translate(decision, Decision)
    assert not result.allowed and result.error and result.reason == "AGT_RESULT_INVALID"


@pytest.mark.parametrize("action", ["deny", "warn", "log", "require_approval"])
def test_non_allow_action_is_veto_not_human_approval(action):
    result = AgentGovernanceAccessChecker._translate(Decision(False, action), Decision)
    assert not result.allowed and not result.error and result.reason == "AGT_DENY"


@pytest.mark.asyncio
async def test_wrong_distribution_version_startup_fails_without_fallback(monkeypatch, capsys):
    monkeypatch.setattr(agt_real, "version", lambda package: "wrong")
    access = checker()
    with pytest.raises(AgtStartupError):
        await access.start()
    assert (await access.check_access(_request(), _context())).error
    await access.close()
    assert not access._worker.alive()
    assert capsys.readouterr().err == ""


@pytest.mark.asyncio
async def test_sdk_exception_is_fixed_error_and_worker_stays_closed(monkeypatch, capsys, caplog):
    access = checker()

    def evaluate(*args, **kwargs):
        raise RuntimeError("RAW_SECRET_MARKER")

    inject(monkeypatch, access, evaluate)
    await access.start()
    result = await access.check_access(_request(), _context())
    assert not result.allowed and result.error and result.reason == "AGT_EVALUATION_ERROR"
    assert (await access.check_access(_request(), _context())).reason == "AGT_UNAVAILABLE"
    await access.close()
    output = capsys.readouterr()
    assert "RAW_SECRET_MARKER" not in output.out + output.err + caplog.text + result.reason


def blocking_first(calls, entered, release):
    """The first SDK call hangs until released; later calls answer at once."""

    def evaluate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return Decision()

    return evaluate


@pytest.mark.asyncio
async def test_slow_sdk_keeps_loop_responsive_queues_in_order_and_bounds_pending(monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []
    access = checker(evaluation_timeout_s=2, max_pending=2)
    inject(monkeypatch, access, blocking_first(calls, entered, release))
    await access.start()
    first = asyncio.create_task(access.check_access(_request(), _context()))
    try:
        await wait_for(entered.is_set)  # Requires the asyncio loop to keep making progress.
        queued = asyncio.create_task(access.check_access(_request(), _context()))
        await wait_for(lambda: access._admitted == 2)
        busy = await access.check_access(_request(), _context())
        assert busy.error and busy.reason == "AGT_BUSY" and calls == [1]
        release.set()
        assert (await first).reason == "AGT_ALLOW" and (await queued).reason == "AGT_ALLOW"
        assert calls == [1, 1] and access._admitted == 0 and access.restarts == 0
    finally:
        release.set()
        await access.close()


@pytest.mark.asyncio
async def test_cancel_frees_queued_slot_at_once_and_running_slot_only_after_sdk_returns(
    monkeypatch,
):
    entered, release, calls = threading.Event(), threading.Event(), []
    access = checker(evaluation_timeout_s=2, max_pending=2)
    inject(monkeypatch, access, blocking_first(calls, entered, release))
    await access.start()
    running = asyncio.create_task(access.check_access(_request(), _context()))
    try:
        await wait_for(entered.is_set)
        queued = asyncio.create_task(access.check_access(_request(), _context()))
        await wait_for(lambda: access._admitted == 2)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert access._admitted == 1
        job = access._worker.running
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert access._admitted == 1  # Still inside the SDK: the slot stays bounded.
        release.set()
        await wait_for(lambda: access._admitted == 0)
        assert job.result.result().reason == "AGT_CANCELLED"  # Late allow discarded.
        assert calls == [1]  # The cancelled queued request never reached the SDK.
        assert (await access.check_access(_request(), _context())).allowed
    finally:
        release.set()
        await access.close()


@pytest.mark.asyncio
async def test_timeout_replaces_stuck_worker_moves_queue_and_discards_late_allow(monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []
    access = checker(evaluation_timeout_s=0.4, close_timeout_s=0.05)
    inject(monkeypatch, access, blocking_first(calls, entered, release))
    await access.start()
    stuck_task = asyncio.create_task(access.check_access(_request(), _context()))
    try:
        await wait_for(entered.is_set)
        stuck = access._worker
        stuck_job = stuck.running
        # Leave the queued request enough of its own deadline for the restart.
        await asyncio.sleep(0.2)
        queued = asyncio.create_task(access.check_access(_request(), _context()))
        timed_out = await asyncio.wait_for(stuck_task, 2)
        assert timed_out.error and not timed_out.allowed and timed_out.reason == "AGT_TIMEOUT"
        # Before the fix one timeout closed the instance until app restart.
        assert access.restarts == 1 and access._worker is not stuck
        assert (await asyncio.wait_for(queued, 2)).reason == "AGT_ALLOW"
        assert (await access.check_access(_request(), _context())).reason == "AGT_ALLOW"
        assert stuck.alive() and access._retired == [stuck]
        with pytest.raises(AgtShutdownError):
            await access.close()  # A stuck thread is reported, never hidden.
        release.set()
        await wait_for(lambda: not stuck.alive())
        assert stuck_job.result.result().reason == "AGT_TIMEOUT"  # Late allow discarded.
        assert (await access.check_access(_request(), _context())).reason == "AGT_UNAVAILABLE"
    finally:
        release.set()
        await access.close()
    assert not stuck.alive() and not access._worker.alive()
    with pytest.raises(AgtStartupError):
        await access.start()


@pytest.mark.asyncio
async def test_inherited_backlog_timeout_does_not_retire_the_fresh_worker(monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []
    access = checker(evaluation_timeout_s=0.2)
    inject(monkeypatch, access, blocking_first(calls, entered, release))
    await access.start()
    stuck = asyncio.create_task(access.check_access(_request(), _context()))
    try:
        await wait_for(entered.is_set)
        # Admitted right behind the stuck call, so both deadlines nearly coincide.
        behind = await access.check_access(_request(), _context())
        assert (await stuck).reason == "AGT_TIMEOUT"
        assert behind.reason in ("AGT_ALLOW", "AGT_TIMEOUT")
        assert access.restarts == 1 and not access._closed
        assert (await access.check_access(_request(), _context())).reason == "AGT_ALLOW"
    finally:
        release.set()
        await access.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0, 1])
async def test_stuck_worker_budget_exhaustion_fails_closed(monkeypatch, budget):
    release, calls = threading.Event(), []

    def evaluate(*args, **kwargs):
        calls.append(1)
        assert release.wait(5)
        return Decision()

    access = checker(evaluation_timeout_s=0.1, max_stuck_workers=budget)
    inject(monkeypatch, access, evaluate)
    await access.start()
    try:
        for _ in range(budget):
            assert (await access.check_access(_request(), _context())).reason == "AGT_TIMEOUT"
            assert not access._closed
        assert (await access.check_access(_request(), _context())).reason == "AGT_TIMEOUT"
        assert access._closed and access.restarts == budget
        assert (await access.check_access(_request(), _context())).reason == "AGT_UNAVAILABLE"
        assert len(calls) == budget + 1
    finally:
        release.set()
        await wait_for(lambda: not any(w.alive() for w in (access._worker, *access._retired)))
        await access.close()


@pytest.mark.asyncio
async def test_close_during_sdk_evaluation_reports_closed_and_unconfirmed_cleanup(monkeypatch):
    entered, release, calls = threading.Event(), threading.Event(), []
    access = checker(evaluation_timeout_s=2, close_timeout_s=0.03)
    inject(monkeypatch, access, blocking_first(calls, entered, release))
    await access.start()
    task = asyncio.create_task(access.check_access(_request(), _context()))
    try:
        await wait_for(entered.is_set)
        with pytest.raises(AgtShutdownError):
            await access.close()
        result = await task
        assert result.error and result.reason == "AGT_CLOSED"
        assert access._worker.thread.daemon and calls == [1]
        assert (await access.check_access(_request(), _context())).reason == "AGT_UNAVAILABLE"
    finally:
        release.set()
        await wait_for(access._worker.finished.done)
        await access.close()
    assert not access._worker.alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_late_sdk_start_is_closed_and_never_becomes_ready(monkeypatch, stop):
    entered, release = threading.Event(), threading.Event()
    access = checker(startup_timeout_s=0.05 if stop == "timeout" else 2)

    def create():
        entered.set()
        assert release.wait(3)
        return Engine(lambda *args, **kwargs: Decision()), Decision

    monkeypatch.setattr(access, "_create_engine", create)
    task = asyncio.create_task(access.start())
    try:
        await wait_for(entered.is_set)
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AgtStartupError):
                await task
        assert (await access.check_access(_request(), _context())).error
    finally:
        release.set()
        await access.close()
    assert not access._worker.ready.result()


@pytest.mark.asyncio
async def test_real_bootstrap_failure_cannot_start_mcp_or_fallback(monkeypatch):
    started = []

    async def fail(self):
        raise AgtStartupError()

    async def mcp_start(self):
        started.append("MCP")

    monkeypatch.setattr(AgentGovernanceAccessChecker, "start", fail)
    monkeypatch.setattr(synthetic_app.McpTransport, "start", mcp_start)
    with pytest.raises(AgtStartupError):
        await synthetic_app.bootstrap(agt_mode="real")
    assert started == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,outcome",
    [(Label.PUBLIC, Released), (Label.INTERNAL, Pending), (Label.CONFIDENTIAL, Denied)],
)
async def test_real_agt_allow_cannot_weaken_guard_or_replace_approval(label, outcome):
    h = _Harness(label=label)
    access = AgentGovernanceAccessChecker(
        {("docs", "send", ToolKind.SEND): True}, rule_version="v1"
    )
    h.guard._access_checker = access
    try:
        await access.start()
        result = await h.guard.submit("task", _call("send"), expected_epoch=1)
        assert isinstance(result, outcome)
        assert len(h.transport.calls) == int(outcome is Released)
        if isinstance(result, Pending):
            control = h.guard.human_control("operator")
            approved = await control.approve(result.approval_id)
            duplicate = await control.approve(result.approval_id)
            assert isinstance(approved, Released) and isinstance(duplicate, Denied)
            assert h.store.consume_count == 1 and len(h.transport.calls) == 1
    finally:
        await access.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deny", "invalid", "exception", "timeout"])
async def test_agt_veto_and_errors_have_zero_guard_dispatch(monkeypatch, mode):
    h = _Harness(access_timeout_seconds=0.5)
    access = AgentGovernanceAccessChecker({}, rule_version="v1", evaluation_timeout_s=0.05)
    release = threading.Event()

    def evaluate(*args, **kwargs):
        if mode == "exception":
            raise ValueError("RAW_SECRET_MARKER")
        if mode == "timeout":
            assert release.wait(3)
            return Decision()
        return Decision(True, "deny")

    if mode != "deny":
        inject(monkeypatch, access, evaluate)
    h.guard._access_checker = access
    try:
        await access.start()
        result = await h.guard.submit("task", _call(), expected_epoch=1)
        assert isinstance(result, Denied) and h.transport.calls == []
        assert (Reason.AGT_DENY if mode == "deny" else Reason.AGT_ERROR) in result.reasons
    finally:
        release.set()
        await access.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
async def test_human_stop_during_sdk_evaluation_never_dispatches_late_allow(monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    access = checker(evaluation_timeout_s=2)
    h = _Harness(access_timeout_seconds=3)

    def evaluate(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return Decision()

    inject(monkeypatch, access, evaluate)
    h.guard._access_checker = access
    await access.start()
    pending = asyncio.create_task(h.guard.submit("task", _call(), expected_epoch=1))
    try:
        await wait_for(entered.is_set)
        control = h.guard.human_control("operator")
        await asyncio.wait_for(getattr(control, action)("task"), 0.5)
        assert isinstance(await asyncio.wait_for(pending, 0.5), Denied)
        assert h.transport.calls == []
    finally:
        release.set()
        await access.close()
    assert h.transport.calls == []


@pytest.mark.asyncio
async def test_guard_slots_share_real_adapter_queue_without_spurious_busy(monkeypatch):
    """I2/I6, T20: four Guard slots use A's queue; a fifth cannot dispatch."""
    h = _Harness(access_timeout_seconds=3)
    access = AgentGovernanceAccessChecker(
        {("docs", "send", ToolKind.SEND): True}, rule_version="unit-v1"
    )
    h.guard._access_checker = access
    task_ids = [f"task-{index}" for index in range(5)]
    for task_id in task_ids:
        h.store.states[task_id] = h.store.states["task"].model_copy(update={"task_id": task_id})
    entered, release = [], asyncio.Event()
    original = access.check_access

    async def hold(req, ctx):
        entered.append(req.task_id)
        await release.wait()
        return await original(req, ctx)

    monkeypatch.setattr(access, "check_access", hold)
    executions = []
    try:
        await access.start()
        executions = [
            asyncio.create_task(h.guard.submit(task_id, _call("send"), expected_epoch=1))
            for task_id in task_ids[:4]
        ]
        await wait_for(lambda: len(entered) == 4)
        assert len(h.guard._access_checks) == 4
        rejected = await h.guard.submit(task_ids[4], _call("send"), expected_epoch=1)
        assert isinstance(rejected, Denied) and Reason.AGT_ERROR in rejected.reasons
        assert len(entered) == 4 and h.transport.calls == []
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*executions), 3)
        assert all(isinstance(result, Released) for result in results)
        assert {req.task_id for req in h.transport.calls} == set(task_ids[:4])
        assert len(h.transport.calls) == 4 and h.store.consume_count == 0
        assert not h.guard._access_checks and not h.guard._unfinished_access_checks
    finally:
        release.set()
        await asyncio.gather(*executions, return_exceptions=True)
        await access.close()
