"""SEQ-02 전역 정지 콜백의 단위 테스트. 실제 SQLite 이중 게이트 검증은 별도다."""

import asyncio

import pytest

from guard_app.domain.contracts import Denied
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardConfigError
from tests.unit.test_guard import _call, _Harness


def test_store_error_reports_its_reason_once_and_blocks_later_calls():
    async def scenario():
        observed = []
        h = _Harness(on_global_halt=observed.append)

        async def failed_load(task_id):
            raise StoreError(Reason.STATE_COMMIT_FAILED)

        h.store.load_task = failed_load
        first = await h.guard.submit("task", _call())
        assert isinstance(first, Denied)
        assert first.reasons == (Reason.STATE_COMMIT_FAILED,)
        assert observed == [Reason.STATE_COMMIT_FAILED]
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert h.transport.calls == []
        await h.guard.aclose()
        assert observed == [Reason.STATE_COMMIT_FAILED]

    asyncio.run(scenario())


def test_local_checker_failure_does_not_report_global_halt():
    async def scenario():
        observed = []
        h = _Harness(on_global_halt=observed.append)

        def failed_check(req, ctx):
            raise asyncio.CancelledError("private failure")

        h.access.check_access = failed_check
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert observed == []
        assert not h.guard._global_halt
        await h.guard.aclose()
        assert observed == [Reason.INTERNAL_ERROR]

    asyncio.run(scenario())


def test_global_halt_without_callback_preserves_existing_behavior():
    async def scenario():
        h = _Harness()
        h.store.fail_at.add("load")
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert h.guard._global_halt
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [False, object()])
def test_rejects_invalid_halt_callback(invalid):
    with pytest.raises(GuardConfigError):
        _Harness(on_global_halt=invalid)
