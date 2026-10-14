"""A08 HTTP/Guard SDK smoke; C's independent acceptance remains separate."""

import argparse
import asyncio

from .verify_stdio_spike import verify

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="실제 loopback HTTP MCP·Guard 검증")
    parser.add_argument("--agt-mode", choices=("real", "mock"), default="real")
    asyncio.run(verify(parser.parse_args().agt_mode, mcp_transport="http"))
