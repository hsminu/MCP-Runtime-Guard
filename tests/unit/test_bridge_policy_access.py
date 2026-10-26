"""B3.10 from the policy screen: one bridge command, fixed result texts (I2/I6)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    SessionStartError,
    TaskProjection,
)
from guard_app.ui.policy import CHANGE_PENDING, CHANGE_RESULTS, CHANGE_UNCONFIRMED, PolicyPage

from .test_bridge import FakeControl, attach, close_bridge, pump_until
from .test_ui_policy import view


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


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
    return bridge, receiver, views


def result(receiver, command):
    stages = [e for e in receiver.stages(command) if e.stage.value == "backend_returned"]
    return stages[-1].code if stages else None


def test_access_change_runs_once_and_republishes_the_new_policy(app):
    calls = []
    applied = view(change=True)

    async def change(server_id, allowed, denied):
        calls.append((server_id, allowed, denied))
        return applied

    bridge, receiver, views = start(app, change)
    try:
        command = bridge.request_policy_access("docs", ("read",), ("send",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_CHANGED"
        assert calls == [("docs", ("read",), ("send",))]
        assert views[-1] is applied
    finally:
        close_bridge(app, bridge)


def test_refused_or_unavailable_change_keeps_the_policy_view(app):
    async def refuse(server_id, allowed, denied):
        raise SessionStartError("POLICY_SAVE_FAILED")

    bridge, receiver, views = start(app, refuse)
    try:
        command = bridge.request_policy_access("docs", (), ("read",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_SAVE_FAILED"
        assert len(views) == 1
    finally:
        close_bridge(app, bridge)
    bridge, receiver, views = start(app, None)
    try:
        command = bridge.request_policy_access("docs", (), ("read",))
        pump_until(app, lambda: result(receiver, command) is not None)
        assert result(receiver, command) == "POLICY_CHANGE_UNAVAILABLE"
    finally:
        close_bridge(app, bridge)


@pytest.mark.parametrize(
    ("server_id", "allowed", "denied"),
    [("", (), ()), ("docs", ["read"], ()), ("docs", (), (1,)), ("docs", ("",), ())],
)
def test_malformed_access_change_is_rejected_before_the_backend(app, server_id, allowed, denied):
    bridge, _receiver, _views = start(app, None)
    try:
        with pytest.raises(ValueError, match="Policy access change is invalid"):
            bridge.request_policy_access(server_id, allowed, denied)
    finally:
        close_bridge(app, bridge)


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
