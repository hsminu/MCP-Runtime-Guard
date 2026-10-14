"""A-owned development spike, not C's launcher/SQLite/HTTP acceptance suite."""

import argparse
import asyncio

from guard_app.domain.enums import TaskStatus

from .synthetic_app import bootstrap


async def verify(agt_mode="real", *, mcp_transport="stdio"):
    for scenario, action, expected in (
        ("allow", None, 1),
        ("deny", None, 0),
        ("ask", "approve", 1),
        ("ask", "reject", 0),
        ("ask", "cancel", 0),
        ("ask", "emergency_halt", 0),
    ):
        bindings = await bootstrap(scenario, agt_mode=agt_mode, mcp_transport=mcp_transport)
        try:
            runner = bindings.runner_for_task("synthetic-task")
            update = await runner.advance()
            before = await bindings.snapshot(runner.task_id)
            assert before.agt_mode == agt_mode
            if action:
                assert update.status == "pending"
                assert before.receiver_effect == 0 and before.host_dispatch == 1
                if action in ("approve", "reject"):
                    snapshot = await bindings.human_control.review(update.approval_id)
                    assert snapshot is not None
                    assert snapshot.arguments_bytes == b'{"dest":"external"}'
                    result = await getattr(bindings.human_control, action)(update.approval_id)
                    await runner.resume_approval(
                        runner.task_id, runner.epoch, update.approval_id, result
                    )
                    if action == "approve":
                        duplicate = await bindings.human_control.approve(update.approval_id)
                        assert duplicate.kind == "denied"
                else:
                    await getattr(bindings.human_control, action)(runner.task_id)
            after = await bindings.snapshot(runner.task_id)
            assert after.receiver_effect == expected
            assert after.host_dispatch == 1 + expected
            if action in ("cancel", "emergency_halt"):
                assert after.status is TaskStatus.HALTED
            print(
                f"{scenario}/{action or 'run'}: host_dispatch={after.host_dispatch}, receiver_effect={after.receiver_effect}"
            )
        finally:
            await bindings.close()

    for policy, expected_dispatch in (("deny-send", 1), ("deny-all", 0)):
        bindings = await bootstrap(
            "allow", agt_mode=agt_mode, agt_policy=policy, mcp_transport=mcp_transport
        )
        try:
            runner = bindings.runner_for_task("synthetic-task")
            assert (await runner.advance()).status == "denied"
            snapshot = await bindings.snapshot(runner.task_id)
            assert snapshot.agt_mode == agt_mode
            assert snapshot.host_dispatch == expected_dispatch
            assert snapshot.receiver_effect == 0
            print(f"{agt_mode}/{policy}: host_dispatch={snapshot.host_dispatch}, receiver_effect=0")
        finally:
            await bindings.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실제 stdio 합성 서버·Guard 검증")
    parser.add_argument("--agt-mode", choices=("real", "mock"), default="real")
    asyncio.run(verify(parser.parse_args().agt_mode))
