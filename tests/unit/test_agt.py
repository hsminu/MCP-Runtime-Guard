"""A18 mock AccessChecker tests; real AGT integration remains unverified."""

import pytest

from guard_app.adapters.agt import MockRuleError, RuleBasedMockAccessChecker
from guard_app.domain.contracts import TrustedContext
from guard_app.domain.enums import Label, TaskStatus, ToolKind, TrustTier
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, ResolvedRequest, TaskState, ToolSpec


def _request(task_id: str = "task-1", *, tool_name: str = "read") -> ResolvedRequest:
    tool = ToolSpec(
        server_id="srv-1",
        tool_name=tool_name,
        kind=ToolKind.READ,
        input_schema=FrozenJsonObject({"type": "object"}),
        destination_arg=None,
        resource_arg=None,
        fingerprint="a" * 64,
    )
    return ResolvedRequest(
        request_id="req-1",
        task_id=task_id,
        epoch=0,
        call_seq=1,
        tool=tool,
        arguments=Arguments({"secret": "synthetic-marker"}),
        resource_id=None,
        server_boundary=Boundary(
            boundary_id="boundary-1", tier=TrustTier.INTERNAL, max_label=Label.PUBLIC
        ),
        destination=None,
    )


def _context(task_id: str = "task-1") -> TrustedContext:
    return TrustedContext(
        state=TaskState(
            task_id=task_id,
            epoch=0,
            profile_id="profile-1",
            policy_version="policy-1",
            status=TaskStatus.RUNNING,
        ),
        operator_id="operator-1",
    )


@pytest.mark.asyncio
async def test_explicit_mock_rules_allow_deny_and_missing() -> None:
    source = {("srv-1", "read"): True, ("srv-1", "write"): False}
    checker = RuleBasedMockAccessChecker(source)
    source[("srv-1", "read")] = False
    assert checker.mode == "mock"
    allow = await checker.check_access(_request(), _context())
    deny = await checker.check_access(_request(tool_name="write"), _context())
    missing = await checker.check_access(_request(tool_name="unknown"), _context())
    assert (allow.allowed, allow.error, allow.reason) == (
        True,
        False,
        "MOCK_RULE_ALLOW",
    )
    assert (deny.allowed, deny.error, deny.reason) == (False, False, "MOCK_RULE_DENY")
    assert (missing.allowed, missing.error, missing.reason) == (
        False,
        False,
        "MOCK_RULE_MISSING",
    )
    assert "synthetic-marker" not in repr(allow)


@pytest.mark.asyncio
async def test_mismatched_trusted_context_is_error_not_allow() -> None:
    checker = RuleBasedMockAccessChecker({("srv-1", "read"): True})
    result = await checker.check_access(_request(), _context("other-task"))
    assert (result.allowed, result.error, result.reason) == (
        False,
        True,
        "MOCK_CONTEXT_MISMATCH",
    )


@pytest.mark.parametrize(
    "document",
    [
        {"version": True, "rules": []},
        {
            "version": 1,
            "rules": [
                {
                    "server_id": "srv-1",
                    "tool_name": "read",
                    "decision": "allow",
                    "task_id": "forged",
                }
            ],
        },
        {
            "version": 1,
            "rules": [{"server_id": "srv-1", "tool_name": "read", "decision": "ALLOW"}],
        },
        {
            "version": 1,
            "rules": [
                {"server_id": "srv-1", "tool_name": "read", "decision": "allow"},
                {"server_id": "srv-1", "tool_name": "read", "decision": "deny"},
            ],
        },
    ],
)
def test_rule_document_rejects_ambiguous_or_trusted_fields(document: object) -> None:
    with pytest.raises(MockRuleError) as caught:
        RuleBasedMockAccessChecker.from_rule_document(document)
    assert "forged" not in str(caught.value)


@pytest.mark.asyncio
async def test_rule_document_can_create_explicit_mock() -> None:
    checker = RuleBasedMockAccessChecker.from_rule_document(
        {
            "version": 1,
            "rules": [{"server_id": "srv-1", "tool_name": "read", "decision": "allow"}],
        }
    )
    result = await checker.check_access(_request(), _context())
    assert result.allowed and not result.error
