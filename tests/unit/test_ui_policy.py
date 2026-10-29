"""A91: policy screen shows Store-independent Profile identifiers and rules only.

The activation allow list follows PR #26: READ/SEND tools start checked, and
nothing can be saved until B's policy command (B3.10) exists.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from guard_app.services.bridge import PolicyServer, PolicyTool, PolicyView
from guard_app.ui.policy import NO_PROFILE, SAVE_UNAVAILABLE, STOP_WARNING, PolicyPage
from guard_app.ui.window import GuardWindow

from .test_ui_icons_style import app as app


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
    page = window.policy_page
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
    window.close()


def test_activation_dialog_defaults_and_cannot_save_without_b310(app) -> None:
    # B3.10 / I11: only READ/SEND choices reach the policy command.
    page = PolicyPage()
    requested = []
    page.allow_change_requested.connect(lambda *event: requested.append(event))
    for change in (False, True):
        page.set_view(view(change=change))
        dialog = page.open_allow_dialog(view().servers[0])
        assert dialog is not None
        boxes = dialog.boxes
        assert set(boxes) == {"read", "send", "misc"}
        assert boxes["read"].isChecked()
        assert boxes["send"].isChecked()
        assert not boxes["misc"].isChecked()
        assert not boxes["misc"].isEnabled()
        assert dialog.save_button.isEnabled() is change
        assert dialog.warning.text() == (STOP_WARNING if change else SAVE_UNAVAILABLE)
        if change:
            assert boxes["read"].isEnabled()
            assert boxes["send"].isEnabled()
            for forced in (True, False):
                boxes["misc"].setChecked(forced)
                assert dialog.selection() == (("read", "send"), ())
            boxes["send"].setChecked(False)
            dialog.save_button.click()
            assert requested == [("docs", ("read",), ("send",))]
        dialog.close()
    page.close()


def test_tools_left_out_of_codex_are_listed_with_a_fixed_reason(app) -> None:
    """Contracts 2026-10-18: excluded tools are shown with why; names are visualized."""
    from dataclasses import replace

    from guard_app.ui.policy import EXCLUDED_REASONS, EXCLUDED_UNKNOWN

    page = PolicyPage()
    page.set_view(view())
    assert not page.excluded.isVisibleTo(page)
    excluded = (
        ("docs__read.v2", "TOOL_NAME"),
        ("web__search", "NAMESPACE_RESERVED"),
        ("docs__x\u202eevil", "TOOL_NAME"),
        ("docs__odd", "SOMETHING_NEW"),
    )
    page.set_view(replace(view(), codex_excluded=excluded))
    assert page.excluded.isVisibleTo(page)
    rows = [
        (page.excluded.item(row, 0).text(), page.excluded.item(row, 1).text())
        for row in range(page.excluded.rowCount())
    ]
    assert rows[0] == ("docs__read.v2", EXCLUDED_REASONS["TOOL_NAME"])
    assert rows[1] == ("web__search", EXCLUDED_REASONS["NAMESPACE_RESERVED"])
    assert "\u202e" not in rows[2][0]
    assert rows[3][1] == EXCLUDED_UNKNOWN
    page.set_view(view())
    assert page.excluded.rowCount() == 0
    assert not page.excluded.isVisibleTo(page)
    page.close()


def _excluded_view() -> PolicyView:
    from dataclasses import replace

    return replace(view(), codex_excluded=(("crm__export.all", "TOOL_NAME"),))


def test_excluded_tool_name_and_reason_are_never_cut_off(app) -> None:
    """The name keeps its full width; a long reason wraps and its row grows."""
    from guard_app.ui.policy import EXCLUDED_REASONS

    page = PolicyPage()
    page.set_view(_excluded_view())
    table = page.excluded
    reason = EXCLUDED_REASONS["TOOL_NAME"]
    heights = []
    for width in (1000, 480):
        page.resize(width, 900)
        page.show()
        app.processEvents()
        # The view's own size hints: the name's one-line width, the reason's
        # wrapped height at the reason column's current width.
        assert table.columnWidth(0) >= table.sizeHintForColumn(0)
        assert table.rowHeight(0) >= table.sizeHintForRow(0)
        assert table.horizontalScrollBar().maximum() == 0
        heights.append(table.rowHeight(0))
        if width == 1000:
            for table in (page.rules, page.excluded):
                assert table.width() > 900
                assert table.horizontalHeader().length() == table.viewport().width()
                assert table.verticalScrollBar().maximum() == 0
                rows = sum(table.rowHeight(row) for row in range(table.rowCount()))
                assert table.viewport().height() >= rows
    assert heights[1] > heights[0]
    assert table.item(0, 0).text() == "crm__export.all"
    assert table.item(0, 1).text() == reason
    page.close()
