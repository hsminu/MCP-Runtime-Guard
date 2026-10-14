"""A08 loopback TCP server for SDK compatibility, not C's acceptance fixture.

Only fixed synthetic tools and integer counters. No arbitrary app URL, document,
command or real external destination is accepted by the demo.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from contextvars import copy_context
from dataclasses import dataclass, field

import uvicorn
from mcp.server.fastmcp import FastMCP

from guard_app.adapters.mcp_transport import _SDK_LOG_SCOPE, _install_sdk_log_containment


@dataclass
class HttpSpike:
    endpoint: str = ""
    counts: dict[str, int] = field(
        default_factory=lambda: {"receiver_calls": 0, "receiver_effect": 0}
    )


@asynccontextmanager
async def serve_loopback(app):
    """Hold the bound port throughout startup; close the server before returning."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
                lifespan="on",
                ws="none",
                timeout_graceful_shutdown=1,
            )
        )
        # The in-process synthetic server also emits session IDs at INFO. Its
        # task and TCP handlers inherit the same quarantine as SDK client tasks.
        _install_sdk_log_containment()
        server_context = copy_context()
        server_context.run(_SDK_LOG_SCOPE.set, True)
        task = asyncio.create_task(
            server.serve(sockets=[listener]), name="a-http-spike", context=server_context
        )
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("HTTP spike startup failed")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(task), 3)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise RuntimeError("HTTP spike shutdown unconfirmed") from None


@asynccontextmanager
async def http_spike(*, json_response=True, stateless=False):
    spike = HttpSpike()
    _install_sdk_log_containment()
    setup_context = copy_context()
    setup_context.run(_SDK_LOG_SCOPE.set, True)
    server = setup_context.run(
        FastMCP,
        "A synthetic HTTP spike",
        log_level="WARNING",
        host="127.0.0.1",
        json_response=json_response,
        stateless_http=stateless,
    )

    @server.tool()
    async def read() -> str:
        spike.counts["receiver_calls"] += 1
        return "합성 분기 보고서"

    @server.tool()
    async def send(dest: str) -> str:
        spike.counts["receiver_calls"] += 1
        spike.counts["receiver_effect"] += 1
        return "합성 수신 완료"

    async with serve_loopback(setup_context.run(server.streamable_http_app)) as endpoint:
        spike.endpoint = endpoint
        yield spike
