"""A17: inspect the published AGT evaluator without wiring it into the app.

Run in a disposable Python 3.12 environment with AGT core 5.0.0 installed.
Only synthetic metadata reaches the evaluator. This is not Guard integration.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import version


def main() -> None:
    io_attempts: list[str] = []

    def block_external_io(event: str, _args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
            io_attempts.append(event)
            raise RuntimeError("Candidate probe must not contact a service or execute a tool")

    sys.addaudithook(block_external_io)
    from agentmesh.governance.policy import Policy, PolicyEngine, PolicyRule

    assert version("agent-governance-toolkit-core") == "5.0.0"
    agent = "guard-synthetic"
    engine = PolicyEngine(conflict_strategy="deny_overrides")
    observations: list[dict[str, object]] = []

    def check(name: str, context: dict, expected: tuple[bool, str], who: str = agent) -> None:
        result = engine.evaluate(who, context, stage="pre_tool")
        assert (result.allowed, result.action) == expected, name
        observations.append({"case": name, "allowed": result.allowed, "action": result.action})

    check("no_policy", {"action": {"type": "read"}}, (False, "deny"))
    engine.load_policy(
        Policy(
            name="synthetic-only",
            agent=agent,
            default_action="deny",
            rules=[
                PolicyRule(name="allow-read", condition="action.type == 'read'", action="allow"),
                PolicyRule(name="deny-delete", condition="action.type == 'delete'", action="deny"),
                PolicyRule(name="deny-blocked", condition="server.id == 'blocked'", action="deny"),
                PolicyRule(
                    name="approval-result",
                    condition="action.type == 'send'",
                    action="require_approval",
                    approvers=["synthetic-operator"],
                ),
            ],
        )
    )
    check("allow", {"action": {"type": "read"}}, (True, "allow"))
    check("explicit_deny", {"action": {"type": "delete"}}, (False, "deny"))
    check("unknown_action", {"action": {"type": "unknown"}}, (False, "deny"))
    check("wrong_agent", {"action": {"type": "read"}}, (False, "deny"), "unregistered")
    check("missing_action", {}, (False, "deny"))
    check(
        "deny_overrides",
        {"action": {"type": "read"}, "server": {"id": "blocked"}},
        (False, "deny"),
    )
    check("approval_not_allow", {"action": {"type": "send"}}, (False, "require_approval"))
    assert not io_attempts
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "package": "agent-governance-toolkit-core",
                "version": version("agent-governance-toolkit-core"),
                "cases_passed": len(observations),
                "network_or_process_attempts_observed": len(io_attempts),
                "cases": observations,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
