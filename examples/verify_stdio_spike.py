"""A-owned development spike, not C's launcher/SQLite/HTTP acceptance suite."""

import asyncio

from guard_app.domain.enums import TaskStatus

from .synthetic_app import bootstrap


async def verify():
    for scenario, action, expected in (
        ("allow", None, 1),
        ("deny", None, 0),
        ("ask", "approve", 1),
        ("ask", "reject", 0),
        ("ask", "cancel", 0),
        ("ask", "emergency_halt", 0),
    ):
        bindings = await bootstrap(scenario)
        try:
            runner = bindings.runner_for_task("synthetic-task")
            update = await runner.advance()
            before = await bindings.snapshot(runner.task_id)
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


if __name__ == "__main__":
    asyncio.run(verify())
