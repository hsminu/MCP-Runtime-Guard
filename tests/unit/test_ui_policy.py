"""A91: policy screen shows Store-independent Profile identifiers and rules only.

The activation allow list follows PR #26: READ/SEND tools start checked, and
nothing can be saved until B's policy command (B3.10) exists.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from guard_app.services.bridge import PolicyDestination, PolicyServer, PolicyTool, PolicyView
from guard_app.ui.policy import (
    CHANGE_RESULTS,
    CHANGE_UNCONFIRMED,
    DESTINATION_EMPTY,
    DESTINATION_FAILED,
    DESTINATION_MISSING,
    DESTINATION_UNAVAILABLE,
    NO_PROFILE,
    SAVE_UNAVAILABLE,
    STOP_WARNING,
    PolicyPage,
)
from guard_app.ui.widgets import LabelPill, StatusPill
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
    fields = page.summary_fields.values
    assert fields["profile"].text() == "default"
    assert fields["version"].text() == "f" * 12
    assert fields["version"].toolTip() == "f" * 64
    assert fields["ttl"].text() == "5분"
    rows = [
        tuple(page.rules.item(row, column).text() for column in range(4))
        for row in range(page.rules.rowCount())
    ]
    assert rows == [
        ("misc", "docs", "기타 · 실행 불가 종류", "규칙 없음 · 거부"),
        ("read", "docs", "읽기", "허용"),
        ("send", "docs", "전송", "규칙 없음 · 거부"),
        ("send", "mail", "미등록", "거부"),
    ]
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


def test_summary_missing_counts_duration_and_taint(app):
    """I6/I7, T18/T29: absence is not zero; summaries use the displayed policy."""
    page = PolicyPage()
    fields = page.summary_fields.values
    assert {label.text() for label in fields.values()} == {"—"}
    assert page.profile_state.findChild(StatusPill).text() == "정책 미연결"
    assert page.profile_state.findChild(StatusPill).property("tone") == "neutral"
    assert page.tabs.tabText(0) == "도구 접근 규칙"
    assert page.tabs.tabText(1) == "전송 목적지"
    current = replace(
        view(),
        allow=(("docs", "read"), ("mail", "send"), ("gone", "tool")),
        destinations=(
            PolicyDestination("one", "internal", "PUBLIC", True),
            PolicyDestination("two", "internal", "INTERNAL", False),
        ),
        taint_mode="high_watermark",
    )
    page.set_view(current)
    assert fields["rules"].text() == "허용 2 · 거부 1 · 규칙 없음 2"
    assert fields["destinations"].text() == "2개 · 사용 1개"
    assert fields["taint"].text() == "최고 등급 유지"
    assert fields["taint"].toolTip() == "high_watermark"
    assert page.tabs.tabText(0) == "도구 접근 규칙 5"
    assert page.tabs.tabText(1) == "전송 목적지 2"
    for seconds, expected in ((300, "5분"), (90, "90초"), (5400, "1시간 30분")):
        page.set_view(replace(current, approval_ttl_seconds=seconds))
        assert fields["ttl"].text() == expected
    page.set_view(replace(current, taint_mode="future_mode"))
    assert fields["taint"].text() == fields["taint"].toolTip() == "future_mode"
    page.set_view(None)
    assert {label.text() for label in fields.values()} == {"—"}
    page.close()


def test_rule_union_deny_priority_filters_and_other(app):
    """I2/I6, design 10.3/T18: exact pairs, deny priority, no execution verdict."""
    page = PolicyPage()
    current = replace(
        view(change=True),
        allow=(("docs", "read"), ("docs", "misc"), ("mail", "send")),
    )
    page.set_view(current)
    assert page.rules.rowCount() == 4
    assert page.rules.item(0, 2).text() == "기타 · 실행 불가 종류"
    assert page.rules.item(0, 2).toolTip() == "OTHER 종류 도구는 Guard로 실행할 수 없음"
    pills = [page.rules.cellWidget(row, 3).findChild(StatusPill) for row in range(4)]
    assert [(p.text(), p.property("tone")) for p in pills] == [
        ("허용", "ok"),
        ("허용", "ok"),
        ("규칙 없음 · 거부", "neutral"),
        ("거부", "deny"),
    ]
    assert pills[2].toolTip() == "규칙이 없으면 기본으로 거부됨"
    assert pills[3].toolTip() == "거부 규칙이 허용보다 우선함"
    assert all("승인 필요" not in p.text() for p in pills)
    dialog = page.open_allow_dialog(current.servers[0])
    assert dialog.boxes["misc"].text().endswith("실행 불가")
    assert not dialog.boxes["misc"].isEnabled()
    assert not dialog.boxes["misc"].isChecked()
    assert dialog.boxes["read"].text().endswith("읽기 · READ")
    dialog.close()
    page.server_filter.setCurrentIndex(page.server_filter.findData("docs"))
    assert page.filter_count.text() == "3개 표시 · 전체 4개"
    page.status_filter.setCurrentIndex(page.status_filter.findData("missing"))
    assert page.rules.rowCount() == 1
    assert page.filter_count.text() == "1개 표시 · 전체 4개"
    page.server_filter.setCurrentIndex(page.server_filter.findData("mail"))
    assert page.rules.rowCount() == 0
    assert page.filter_count.text() == "0개 표시 · 전체 4개"
    page.status_filter.setCurrentIndex(page.status_filter.findData("deny"))
    assert page.rules.rowCount() == 1
    assert page.rules.item(0, 1).text() == "mail"
    assert (
        not page.allow_button.isEnabled()
    )  # No registered tools to edit for this rule-only server.
    assert "정책 전체" in page.server_filter.toolTip()
    page.close()


def test_destination_states_and_read_failure_recovery(app):
    """I6, T29: read failure must not look like an empty or updated policy."""
    page = PolicyPage()
    page.tabs.setCurrentIndex(1)
    assert page.destination_state.text() == DESTINATION_MISSING
    assert page.destinations.isHidden()
    assert not page.add_destination.isEnabled()
    page.set_view(view(change=True))
    assert page.destination_state.text() == DESTINATION_EMPTY
    assert page.add_destination.isEnabled()
    page.destination_read_requested.connect(lambda: page.set_change_pending("read"))
    assert page.progress_area.isHidden()
    page.read_destinations.click()
    assert not page.progress_area.isHidden()
    assert page.progress.text() == "목적지 목록 불러오는 중"
    page.settle_change("other", "POLICY_CHANGED")
    assert not page.progress_area.isHidden()
    page.settle_change("read", "POLICY_SAVE_FAILED")
    assert page.progress_area.isHidden()
    assert page.destination_state.text() == DESTINATION_FAILED
    assert page.destinations.isHidden()
    assert not page.retry_destinations.isHidden()
    assert page.notice.text() == CHANGE_RESULTS["POLICY_SAVE_FAILED"]
    page.retry_destinations.click()
    page.set_view(view(change=True))  # Actual bridge order: view, then command result.
    page.settle_change("read", "POLICY_READ")
    assert page.progress_area.isHidden()
    assert page.result_area.isHidden()
    assert page.destination_state.text() == DESTINATION_EMPTY
    page.set_view(view())
    assert page.destination_state.text() == DESTINATION_UNAVAILABLE
    assert page.add_destination.toolTip() == SAVE_UNAVAILABLE
    assert page.allow_button.toolTip() == SAVE_UNAVAILABLE
    assert not page.server_buttons["docs"].isEnabled()
    page.close()


def _markers(table):
    return [
        label
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if (cell := table.cellWidget(row, column)) is not None
        for label in cell.findChildren(QLabel, "policyChanged")
    ]


@pytest.mark.parametrize("view_first", [False, True])
@pytest.mark.parametrize("kind", ["allow", "destination"])
def test_change_progress_success_markers_and_event_order(app, view_first, kind):
    """I6/T29: never highlight before confirmation, in either event order."""
    page = PolicyPage()
    current = view(change=True)
    destination = PolicyDestination("mail.example", "general_external", "PUBLIC", True)
    page.set_view(current)
    assert page.progress_area.isHidden()
    if kind == "allow":
        page.allow_change_requested.connect(lambda *args: page.set_change_pending("mine"))
        dialog = page.open_allow_dialog(current.servers[0])
        dialog.boxes["read"].setChecked(False)
        updated = replace(current, allow=(), deny=current.deny + (("docs", "read"),))
        table = page.rules
        pending = "docs 도구 허용 규칙 저장·적용 중"
    else:
        page.destination_change_requested.connect(lambda edit: page.set_change_pending("mine"))
        dialog = page.open_destination_dialog(destination)
        updated = replace(current, destinations=(destination,))
        table = page.destinations
        pending = "목적지 mail.example 저장·적용 중"
    assert page.progress_area.isHidden()  # Opening a dialog does not send a request.
    dialog.save_button.click()
    assert page.progress.text() == pending
    assert not page.progress_area.isHidden()
    assert page.progress_area.findChild(StatusPill).property("tone") == "live"
    if view_first:
        page.set_view(updated)
        assert not _markers(table)
    page.settle_change("mine", "POLICY_CHANGED")
    assert page.progress_area.isHidden()
    assert page.result_pill.text() == "저장·적용됨"
    assert page.result_pill.property("tone") == "ok"
    assert page.notice.text() == CHANGE_RESULTS["POLICY_CHANGED"]
    if not view_first:
        assert not _markers(table)
        page.set_view(updated)
    assert len(_markers(table)) == 1
    assert _markers(table)[0].text() == "변경됨"
    page.set_view(updated)
    app.processEvents()
    assert not _markers(table)
    page.close()


@pytest.mark.parametrize("code", ["POLICY_SAVE_FAILED", "POLICY_SAVE_UNKNOWN", "untrusted result"])
def test_failed_change_never_marks_rows(app, code):
    """I6/T29: failed or unconfirmed writes do not become applied changes."""
    page = PolicyPage()
    page.set_view(view(change=True))
    page.allow_change_requested.connect(lambda *args: page.set_change_pending("mine"))
    dialog = page.open_allow_dialog(view().servers[0])
    dialog.save_button.click()
    page.settle_change("mine", code)
    assert page.progress_area.isHidden()
    assert page.result_pill.property("tone") == "deny"
    assert page.notice.text() == CHANGE_RESULTS.get(code, CHANGE_UNCONFIRMED)
    page.set_view(replace(view(change=True), allow=()))
    assert not _markers(page.rules)
    page.close()


def test_destination_grade_shape_and_editor_hint(app):
    """I7/T18: grade tags, operational states and raw command values stay distinct."""
    page = PolicyPage()
    row = PolicyDestination("destination", "internal", "CONFIDENTIAL", True)
    page.set_view(replace(view(change=True), destinations=(row,)))
    tag = page.destinations.cellWidget(0, 2).findChild(LabelPill)
    pill = page.destinations.cellWidget(0, 3).findChild(StatusPill)
    assert tag.objectName() == "labelPill"
    assert pill.objectName() == "statusPill"
    assert tag.property("labelLevel") == "confidential"
    assert tag.property("tone") is None
    assert pill.property("tone") == "ok"
    assert pill.property("labelLevel") is None
    assert tag.text() == "최대 수신 기밀"
    dialog = page.open_destination_dialog()
    assert not dialog.save_button.isEnabled()
    assert dialog.input_hint.text() == "ID·경계 종류·최고 수신 등급을 입력하세요"
    assert dialog.input_hint.objectName() == "secondary"
    dialog.identifier.setText("mail.example")
    dialog.tier.setCurrentIndex(dialog.tier.findData("internal"))
    dialog.label.setCurrentIndex(dialog.label.findData("PUBLIC"))
    assert dialog.label.currentText() == "공개 · PUBLIC"
    assert dialog.selection().max_label == "PUBLIC"
    assert dialog.save_button.isEnabled()
    assert dialog.input_hint.isHidden()
    dialog.close()
    page.close()


def test_rule_display_visualizes_identifiers_without_changing_filter_data(app):
    """I7/T18: hostile names are plain visible text; exact IDs remain command data."""
    page = PolicyPage()
    server, tool = "srv\u202eevil", "<b>read</b>\u202eevil"
    page.set_view(replace(view(), servers=(), allow=((server, tool),), deny=()))
    assert "\u202e" not in page.rules.item(0, 0).text()
    assert "\u202e" not in page.rules.item(0, 1).text()
    assert "<b>read</b>" in page.rules.item(0, 0).text()
    page.server_filter.setCurrentIndex(page.server_filter.findData(server))
    assert page.server_filter.currentData() == server
    assert "\u202e" not in page.server_filter.currentText()
    assert page.rules.rowCount() == 1
    page.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_policy_cell_widgets_fit_and_tabs_limit_page_height(app, theme):
    """T18: real styled geometry includes item padding and whole pill/tag heights."""
    from guard_app.ui.style import style_sheet

    page = PolicyPage()
    page.setStyleSheet(style_sheet(theme))
    page.set_view(
        replace(
            view(change=True),
            allow=tuple(("long_server_identifier", f"long_tool_identifier_{n}") for n in range(20)),
            destinations=(PolicyDestination("mail.example", "internal", "PUBLIC", True),),
        )
    )
    page.resize(1100, 900)
    page.show()
    app.processEvents()
    rules_height = page.stack.sizeHint().height()
    for tab, table in ((0, page.rules), (1, page.destinations)):
        page.tabs.setCurrentIndex(tab)
        app.processEvents()
        assert page.content.width() <= 960
        assert table.viewport().height() >= sum(table.rowHeight(r) for r in range(table.rowCount()))
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                cell = table.cellWidget(row, column)
                if cell is None:
                    continue
                assert cell.width() >= cell.minimumSizeHint().width()
                assert cell.height() >= cell.minimumSizeHint().height()
                for label in cell.findChildren(QLabel):
                    assert label.width() >= label.minimumSizeHint().width()
                    assert label.height() >= label.minimumSizeHint().height()
    assert page.stack.sizeHint().height() < rules_height
    page.close()
