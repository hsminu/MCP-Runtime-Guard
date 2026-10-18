"""A91: policy screen shows Store-independent Profile identifiers and rules only.

The activation allow list follows PR #26: READ/SEND tools start checked, and
nothing can be saved until B's policy command (B3.10) exists.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import PolicyServer, PolicyTool, PolicyView
from guard_app.ui.policy import NO_PROFILE, SAVE_UNAVAILABLE, STOP_WARNING, PolicyPage
from guard_app.ui.window import GuardWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def view(*, change: bool = False) -> PolicyView:
    return PolicyView(
        profile_id="default",
        policy_version="f" * 64,
        approval_ttl_seconds=300,
        taint_mode="accumulate",
        allow=(("docs", "read"),),
        deny=(("mail", "send"),),
        servers=(
            PolicyServer(
                "docs",
                (
                    PolicyTool("read", "read", True),
                    PolicyTool("send", "send", True),
                    PolicyTool("misc", "other", False),
                ),
            ),
        ),
        change_available=change,
    )


def test_without_a_profile_the_policy_page_says_so(app) -> None:
    window = GuardWindow()
    window.navigate("policy")
    assert window.policy_page.isVisibleTo(window)
    assert window.policy_page.summary.text() == NO_PROFILE
    assert window.policy_page.rules.rowCount() == 0
    window.close()


def test_profile_rules_are_listed_as_plain_identifiers(app) -> None:
    page = PolicyPage()
    page.set_view(view())
    assert "Profile default" in page.summary.text()
    assert "f" * 12 in page.summary.text()
    assert "f" * 13 not in page.summary.text()
    assert "300초" in page.summary.text()
    rows = [
        tuple(page.rules.item(row, column).text() for column in range(3))
        for row in range(page.rules.rowCount())
    ]
    assert rows == [("docs", "read", "허용"), ("mail", "send", "거부")]
    assert set(page.server_buttons) == {"docs"}
    page.set_view(None)
    assert page.server_buttons == {}
    page.close()


def test_activation_dialog_defaults_and_cannot_save_without_b310(app) -> None:
    page = PolicyPage()
    page.set_view(view(change=False))
    dialog = page.open_allow_dialog(view().servers[0])
    assert dialog is not None
    boxes = dialog.boxes
    assert boxes["read"].isChecked()  # existing allow rule
    assert boxes["send"].isChecked()  # kind fixed: checked by default
    assert not boxes["misc"].isChecked()  # OTHER: not checked by default
    assert not dialog.save_button.isEnabled()
    assert dialog.warning.text() == SAVE_UNAVAILABLE
    dialog.close()
    page.close()


def test_activation_dialog_with_policy_command_warns_and_reports_the_choice(app) -> None:
    page = PolicyPage()
    page.set_view(view(change=True))
    requested = []
    page.allow_change_requested.connect(lambda *event: requested.append(event))
    dialog = page.open_allow_dialog(view().servers[0])
    assert dialog is not None
    assert dialog.warning.text() == STOP_WARNING
    dialog.boxes["send"].setChecked(False)
    dialog.save_button.click()
    assert requested == [("docs", ("read",), ("send", "misc"))]
    page.close()
