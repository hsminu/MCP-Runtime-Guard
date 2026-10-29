"""A153 / I6 / REC-01: publication and the runtime-owned execution watch."""

from __future__ import annotations

import asyncio

import pytest

from guard_app.services import product_startup
from guard_app.services.session_runtime import SessionRuntime, bootstrap_product

from .product_folders import product_folders


@pytest.mark.asyncio
async def test_bindings_precede_single_watch_and_close_cancels_it(tmp_path, monkeypatch):
    events, timers, runtimes = [], [], []
    activate = product_startup.SQLiteStore.activate_run
    bindings = SessionRuntime.bindings
    call_later = asyncio.get_running_loop().call_later

    async def activated(store):
        await activate(store)
        events.append("activated")

    def bound(runtime):
        events.append("bindings")
        runtimes.append(runtime)
        return bindings(runtime)

    def scheduled(delay, callback, *args, **kwargs):
        timer = call_later(delay, callback, *args, **kwargs)
        if callback.__name__ == "_watch_execution":
            events.append("watch")
            timers.append(timer)
        return timer

    monkeypatch.setattr(product_startup.SQLiteStore, "activate_run", activated)
    monkeypatch.setattr(SessionRuntime, "bindings", bound)
    monkeypatch.setattr(asyncio.get_running_loop(), "call_later", scheduled)
    result = await asyncio.wait_for(
        bootstrap_product(product_folders(tmp_path / "boundary.db")), timeout=10
    )
    try:
        runtimes[0].start_execution_watch()
        runtimes[0].start_execution_watch()
        assert events == ["activated", "bindings", "watch"]
        assert len(timers) == 1
    finally:
        await asyncio.wait_for(result.close(), timeout=10)
    assert timers[0].cancelled()
