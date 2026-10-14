"""Explicit decision-only mock for the B AccessChecker port.

The real AGT adapter lives in agt_real. This mock
adapter is never chosen as a fallback after an AGT failure. A trusted caller
must supply the parsed rules document and explicitly construct mock mode.
Reading and validating a rules file path belongs to C's security boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from guard_app.domain.contracts import TrustedContext
from guard_app.domain.models import AccessResult, ResolvedRequest


class MockRuleError(ValueError):
    """Fixed-message configuration error; never includes rules or arguments."""


class RuleBasedMockAccessChecker:
    """Opt-in, deny-by-default mock; it never receives an execution callback.

    Rules are keyed only by the registered server and tool names.  A verdict
    does not grant dispatch: Guard still evaluates its own boundaries, Task
    state, registration, and any required human approval.
    """

    mode = "mock"

    def __init__(self, rules: Mapping[tuple[str, str], bool]) -> None:
        validated: dict[tuple[str, str], bool] = {}
        for key, allowed in rules.items():
            if (
                type(key) is not tuple
                or len(key) != 2
                or any(type(part) is not str or not part for part in key)
                or type(allowed) is not bool
            ):
                raise MockRuleError("AGT mock 규칙 형식이 유효하지 않다")
            validated[key] = allowed
        self._rules = MappingProxyType(validated)

    @classmethod
    def from_rule_document(cls, document: object) -> RuleBasedMockAccessChecker:
        """Validate an already safely loaded rule-file document.

        Format: {"version": 1, "rules": [{"server_id": ..., "tool_name": ...,
        "decision": "allow"|"deny"}]}.  Unknown fields and duplicate rules fail
        closed, including fields that purport to set a Task, label, or actor.
        """
        if type(document) is not dict or set(document) != {"version", "rules"}:
            raise MockRuleError("AGT mock 규칙 형식이 유효하지 않다")
        if type(document["version"]) is not int or document["version"] != 1:
            raise MockRuleError("AGT mock 규칙 버전이 유효하지 않다")
        entries = document["rules"]
        if type(entries) is not list:
            raise MockRuleError("AGT mock 규칙 형식이 유효하지 않다")
        rules: dict[tuple[str, str], bool] = {}
        for entry in entries:
            if type(entry) is not dict or set(entry) != {
                "server_id",
                "tool_name",
                "decision",
            }:
                raise MockRuleError("AGT mock 규칙 형식이 유효하지 않다")
            server_id, tool_name, decision = (
                entry["server_id"],
                entry["tool_name"],
                entry["decision"],
            )
            if (
                type(server_id) is not str
                or not server_id
                or type(tool_name) is not str
                or not tool_name
                or type(decision) is not str
                or decision not in {"allow", "deny"}
            ):
                raise MockRuleError("AGT mock 규칙 형식이 유효하지 않다")
            key = (server_id, tool_name)
            if key in rules:
                raise MockRuleError("AGT mock 규칙이 중복됐다")
            rules[key] = decision == "allow"
        return cls(rules)

    async def check_access(self, req: ResolvedRequest, ctx: TrustedContext) -> AccessResult:
        if req.task_id != ctx.state.task_id or req.epoch != ctx.state.epoch:
            return AccessResult(allowed=False, reason="MOCK_CONTEXT_MISMATCH", error=True)
        allowed = self._rules.get((req.tool.server_id, req.tool.tool_name))
        if allowed is None:
            return AccessResult(allowed=False, reason="MOCK_RULE_MISSING", error=False)
        return AccessResult(
            allowed=allowed,
            reason="MOCK_RULE_ALLOW" if allowed else "MOCK_RULE_DENY",
            error=False,
        )
