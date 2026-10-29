"""B3.10 from the policy screen: one bridge command, fixed result texts (I2/I6)."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    SessionStartError,
    TaskProjection,
)
from guard_app.ui.policy import CHANGE_PENDING, CHANGE_RESULTS, CHANGE_UNCONFIRMED, PolicyPage

from .test_bridge import FakeControl, attach, bridge_cleanup, pump_until
from .test_bridge import app as app
from .test_ui_policy import view


@contextmanager
def start(app, policy_access):
    async def bootstrap():
        async def snapshot(task_id):
            return TaskProjection(task_id, 1, TaskStatus.RUNNING)

        return BackendBindings(
            lambda task_id: None,
            FakeControl(),
            snapshot=snapshot,
            policy=view(change=policy_access is not None),
            policy_access=policy_access,
        )

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    views = []
    bridge.policy_changed.connect(views.append)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY and bool(views))
    with bridge_cleanup(app, bridge):
        yield bridge, receiver, views


def result(receiver, command):
    stages = [e for e in receiver.stages(command) if e.stage.value == "backend_returned"]
    return stages[-1].code if stages else None


def test_access_change_runs_once_and_republishes_the_new_policy(app):
    calls = []
    applied = view(change=True)

    async def change(server_id, allowed, denied):
        calls.append((server_id, allowed, denied))
        return applied

    with start(app, change) as (bridge, receiver, views):
        command = bridge.request_policy_access("docs", ("read",), ("send",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_CHANGED"
        assert calls == [("docs", ("read",), ("send",))]
        assert views[-1] is applied


def test_refused_or_unavailable_change_keeps_the_policy_view(app):
    async def refuse(server_id, allowed, denied):
        raise SessionStartError("POLICY_SAVE_FAILED")

    with start(app, refuse) as (bridge, receiver, views):
        command = bridge.request_policy_access("docs", (), ("read",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_SAVE_FAILED"
        assert len(views) == 1
    with start(app, None) as (bridge, receiver, views):
        command = bridge.request_policy_access("docs", (), ("read",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_CHANGE_UNAVAILABLE"


def test_malformed_access_change_is_rejected_before_the_backend(app):
    failures = []
    with start(app, None) as (bridge, _receiver, _views):
        for case in (("", (), ()), ("docs", ["read"], ()), ("docs", (), (1,)), ("docs", ("",), ())):
            try:
                with pytest.raises(ValueError, match="Policy access change is invalid"):
                    bridge.request_policy_access(*case)
            except (AssertionError, pytest.fail.Exception) as error:
                failures.append((case, str(error)))
        assert failures == []


def test_policy_page_shows_fixed_text_for_its_own_command_only(app):
    page = PolicyPage()
    page.set_view(view(change=True))
    page.set_change_pending("mine")
    assert page.notice.text() == CHANGE_PENDING
    page.settle_change("other", "POLICY_CHANGED")
    assert page.notice.text() == CHANGE_PENDING
    page.settle_change("mine", "POLICY_APPLY_FAILED")
    assert page.notice.text() == CHANGE_RESULTS["POLICY_APPLY_FAILED"]
    page.set_change_pending("next")
    page.settle_change("next", "RAW backend text")
    assert page.notice.text() == CHANGE_UNCONFIRMED
