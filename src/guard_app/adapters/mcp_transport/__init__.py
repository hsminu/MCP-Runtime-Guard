"""MCP session ownership and the Guard's queued Transport implementation.

Stream factories are supplied by the trusted composition root after C's
launcher/endpoint validation. The SDK factories below enforce transport settings;
they do not replace registration, endpoint approval or Guard's tools/call entry.

The `mcp` SDK is imported only inside this package (I1). Its modules:
`streams` (stdio and HTTP stream factories), `sdk_session` (the SDK client
session), `tool_listing` (tools/list collection), `result_budget` (the
pre-copy result bound), `sdk_logging` (SDK log containment), `worker`
(one server's session owner) and `transport` (McpTransport).
"""

from mcp.client.stdio import StdioServerParameters

from guard_app.adapters.mcp_transport.result_budget import ResultTooLarge
from guard_app.adapters.mcp_transport.streams import (
    StreamFactory,
    http_stream_factory,
    stdio_stream_factory,
    validated_stdio_stream_factory,
)
from guard_app.adapters.mcp_transport.tool_listing import (
    ToolListing,
    ToolListLimits,
    collect_tool_definitions,
)
from guard_app.adapters.mcp_transport.transport import McpTransport
from guard_app.adapters.mcp_transport.worker import ServerWorker

__all__ = [
    "McpTransport",
    "ResultTooLarge",
    "ServerWorker",
    "StdioServerParameters",
    "StreamFactory",
    "ToolListLimits",
    "ToolListing",
    "collect_tool_definitions",
    "http_stream_factory",
    "stdio_stream_factory",
    "validated_stdio_stream_factory",
]
