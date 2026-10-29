"""#101 / A180, A155/A156: saved-file apply cancellation; I1/I6/I10, T10/T29/S05.

Registry.apply_settings precedes the saver (A173). The post-save B boundary
for every path here is Guard.apply_policy_change; cancellation must close Guard
and require a restart while preserving each outer failure code.
"""

import asyncio
from dataclasses import replace

import pytest

from guard_app.domain.contracts import Denied
from guard_app.domain.enums import Label
from guard_app.domain.models import ProposedCall
from guard_app.domain.reasons import Reason
from guard_app.services.bridge import RegistrationRequest
from guard_app.services.product_settings import read_settings
from guard_app.services.session_admission import SessionRuntimeError

from .test_a155_tool_settings import tools_harness
from .test_a156_destinations import ADD


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("access", "POLICY_APPLY_FAILED"),
        ("destinations", "POLICY_APPLY_FAILED"),
        ("tools", "REG_TOOLS_UNKNOWN"),
        ("activate", "REG_ACTIVATION_UNKNOWN"),
        ("block", "REG_ACTIVATION_UNKNOWN"),
        ("register", "POLICY_SAVE_UNKNOWN"),
        ("update", "POLICY_SAVE_UNKNOWN"),
    ],
)
async def test_saved_document_apply_cancel_requires_restart(
    tmp_path, monkeypatch, subtests, case, expected_code
):
    async with tools_harness(tmp_path, active=case == "block") as h:
        before = read_settings(h.folders).document
        before_view = h.policy.registration_policy_view()
        events, saved_changes, results, closes = [], [], [], []
        original_close = h.assembled.guard.aclose
        original_settings = h.registry.apply_settings
        cancellation = asyncio.CancelledError()

        async def close():
            closes.append(True)
            await original_close()

        async def apply_settings(*args, **kwargs):
            events.append("registry")
            assert not saved_changes  # A173: never mislabel this as post-save.
            return await original_settings(*args, **kwargs)

        async def save(change):
            assert await h.saver(change) is True
            saved_changes.append(change)
            events.append("saved")
            return True

        async def cancel_apply(change, *args, **kwargs):
            # A true saver ACK and a fresh disk read precede B's injected cancel.
            assert saved_changes == [change]
            assert read_settings(h.folders).document == change.document.to_dict()
            events.append("apply_cancel")
            raise cancellation

        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        monkeypatch.setattr(h.registry, "apply_settings", apply_settings)
        monkeypatch.setattr(h.assembled.guard, "apply_policy_change", cancel_apply)
        h.policy._saver = save

        async def change():
            if case == "access":
                return await h.policy.change_server_access("docs", ("second",), ("read",), h.stop)
            if case == "destinations":
                return await h.policy.destinations(ADD, h.stop)
            if case == "tools":
                return await h.commands(h.request)
            if case in ("activate", "block"):
                return await h.policy.set_server_active(
                    "docs",
                    h.preview.row_revision,
                    ("read",),
                    ("second",),
                    h.registry,
                    h.stop,
                    active=case == "activate",
                    operator_id="unit",
                )
            server = h.server.model_copy(
                update={"max_label": Label.INTERNAL} if case == "update" else {"id": "new"}
            )
            return await h.policy.save_registration(
                server, h.registry, h.stop, updating=case == "update"
            )

        with pytest.raises(asyncio.CancelledError) as caught:
            results.append(await change())
        assert caught.value is cancellation
        assert results == []  # No successful PolicyView or RegistrationView was returned.
        assert events == (["registry"] if case == "tools" else []) + ["saved", "apply_cancel"]
        saved = read_settings(h.folders).document
        assert saved != before
        assert saved == saved_changes[0].document.to_dict()
        if case == "access":
            assert saved["access_rules"]["deny"] == [["docs", "read"]]
        elif case == "destinations":
            assert saved["destinations"][0]["id"] == "vault"
        elif case == "tools":
            assert saved["servers"][0]["tools"][-1] == {"name": "unknown", "kind": "read"}
        elif case in ("activate", "block"):
            assert ("docs" in saved["profile"]["servers"]) == (case == "activate")
        elif case == "register":
            assert saved["servers"][-1]["id"] == "new"
        else:
            assert saved["servers"][0]["max_label"] == "INTERNAL"

        # Screen projections must keep the last applied policy, never the saved one.
        view = h.policy.registration_policy_view()
        assert (view.policy_version, view.allow, view.deny, view.destinations) == (
            before_view.policy_version,
            before_view.allow,
            before_view.deny,
            before_view.destinations,
        )
        assert h.stopped == []
        assert h.admission.restart_code == expected_code
        # Separate requirements cover restart admission, screen state, and Guard closure.
        with subtests.test(requirement="restart"):
            assert h.admission.restart_code is not None
        with subtests.test(requirement="screen_disabled"):
            assert not view.change_available
        with subtests.test(requirement="new_session_refused"), pytest.raises(SessionRuntimeError):
            h.admission.check()  # The same gate used before Task/session creation.
        with subtests.test(requirement="guard_closed"):
            outcome = await h.assembled.guard.submit(
                "task",
                ProposedCall(tool_ref="docs__read", arguments={}, model_call_id="after"),
                expected_epoch=1,
            )
            assert isinstance(outcome, Denied)
            # Other paths also close Guard in their existing outer finally.
            assert closes == [True] * (1 if case in ("access", "destinations") else 2)
            assert h.assembled.guard._global_halt
            assert outcome.reasons == (Reason.INTERNAL_ERROR,)

        async def no_more_save(change):
            pytest.fail("additional save admitted after apply cancellation")

        h.policy._saver = no_more_save
        # An identical saved edit must still be rejected, not treated as success.
        with (
            subtests.test(requirement="further_change_refused"),
            pytest.raises(SessionRuntimeError),
        ):
            await h.policy.change_server_access("docs", ("second",), ("read",), h.stop)
        assert len(saved_changes) == 1
        assert h.peer.calls == []
        if case == "tools":
            assert h.commands._unknown
            followup = await h.commands(replace(h.request, action="tool_settings_preview"))
            assert followup.code == "REG_TOOLS_UNKNOWN"
            assert not followup.tool_settings
        assert (await h.commands(RegistrationRequest("list"))).code == "REG_LIST"
