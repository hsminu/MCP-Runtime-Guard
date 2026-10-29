"""C2.6/EXE-01 process ownership around the bounded MCP pipe adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio

from guard_app.adapters.mcp_transport.streams import StreamFactory, validated_stdio_stream_factory
from guard_app.security.launcher import ExecutablePin, LaunchError, launch_mcp_server


def launcher_stdio_stream_factory(
    server_dir: Path,
    command: str | None,
    args: tuple[str, ...],
    env_allow: tuple[str, ...],
    pin: ExecutablePin | None,
) -> StreamFactory:
    """Launch on context entry; close local pumps before C closes the whole Job.

    C owns executable/environment validation and the isolated stderr sink.
    The server folder remains owned by AppFolders (next-start cleanup).
    Cancellation still propagates, but cannot skip the bounded Job shutdown.
    """

    @asynccontextmanager
    async def connect() -> AsyncIterator[tuple[Any, ...]]:
        process = await launch_mcp_server(server_dir, command, args, env_allow, pin)
        try:
            streams = validated_stdio_stream_factory(
                process.read_bytes, process.write_bytes, process.drain
            )
            async with streams() as pair:
                yield pair
        finally:
            # The SDK uses AnyIO level cancellation. Shield this final bounded
            # C shutdown even when an outer SDK cancel scope is already cancelled.
            with anyio.CancelScope(shield=True):
                if not await process.close():
                    raise LaunchError("LAUNCH_EXIT_UNCONFIRMED")

    return connect


def launcher_teardown_failed(error: object) -> bool:
    """Startup can fail before the SDK session context finishes entering."""
    if isinstance(error, BaseExceptionGroup):
        return any(launcher_teardown_failed(item) for item in error.exceptions)
    return isinstance(error, LaunchError) and error.args == ("LAUNCH_EXIT_UNCONFIRMED",)
