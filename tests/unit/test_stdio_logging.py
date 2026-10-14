"""I5/I9, T19: SDK parser/root logs and process stderr contain no raw marker."""

import logging
import os
import sys

import pytest

from guard_app.adapters.mcp_transport import (
    McpTransport,
    StdioServerParameters,
    stdio_stream_factory,
)


@pytest.mark.asyncio
async def test_malformed_stdio_and_notifications_are_contained(caplog, capfd):
    script = """
import json, sys
from mcp.server.fastmcp import FastMCP
print("SYNTHETIC_RAW_STDERR", file=sys.stderr, flush=True)
print("SYNTHETIC_RAW_JSON", flush=True)
print(json.dumps({"jsonrpc":"2.0", "method":"notifications/progress", "params":{"progressToken":"SYNTHETIC_RAW_NOTIFICATION","progress":"bad"}}), flush=True)
FastMCP("synthetic").run(transport="stdio")
"""
    caplog.set_level(logging.WARNING)
    with open(os.devnull, "w", encoding="utf-8") as sink:  # noqa: ASYNC230 - local null device
        transport = McpTransport(
            {
                "synthetic": stdio_stream_factory(
                    StdioServerParameters(command=sys.executable, args=["-c", script], env={}),
                    errlog=sink,
                )
            },
            startup_timeout_s=3,
            close_timeout_s=2,
        )
        try:
            await transport.start()
        finally:
            await transport.close()
    assert "MCP_LOG_WITHHELD" in caplog.text
    captured = capfd.readouterr()
    assert "SYNTHETIC_RAW" not in caplog.text + captured.out + captured.err
    logging.warning("ordinary host message")  # noqa: LOG015 - verify root SDK path containment
    assert "ordinary host message" in caplog.text


def test_stdio_requires_non_host_stderr_sink():
    parameters = StdioServerParameters(command=sys.executable)
    with pytest.raises(TypeError):
        stdio_stream_factory(parameters)
    with pytest.raises(ValueError):
        stdio_stream_factory(parameters, errlog=sys.stderr)
