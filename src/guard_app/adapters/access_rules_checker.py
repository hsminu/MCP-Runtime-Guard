"""A connection of B's pure access-rule decision to the current AccessChecker port.

ACCESS-01 (2026-10-18) replaces AGT with B's `core.access_rules.evaluate_access`.
`ports.AccessChecker` is still async until B2.9 lands the agreed synchronous
port; then only `check_access` loses `async`. The rules must come from the same
`LoadedProfile` whose `policy_version` the Guard uses, so the verdict and the
recorded policy version can never drift apart. No I/O, no wait, no fallback.
"""

from __future__ import annotations

from guard_app.core.access_rules import AccessRules, evaluate_access
from guard_app.domain.contracts import TrustedContext
from guard_app.domain.models import AccessResult, ResolvedRequest


class AccessRulesChecker:
    """Deny-by-default, exact (server_id, tool_name) rules; deny wins."""

    mode = "rules"

    def __init__(self, rules: AccessRules) -> None:
        if type(rules) is not AccessRules or not rules.is_valid:
            raise ValueError("valid access rules required")
        self._rules = rules

    async def check_access(self, req: ResolvedRequest, ctx: TrustedContext) -> AccessResult:
        if req.task_id != ctx.state.task_id or req.epoch != ctx.state.epoch:
            return AccessResult(allowed=False, reason="ACCESS_CONTEXT_MISMATCH", error=True)
        return evaluate_access(
            self._rules, server_id=req.tool.server_id, tool_name=req.tool.tool_name
        )
