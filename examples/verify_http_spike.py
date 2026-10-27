"""A08 HTTP/Guard SDK smoke; C's independent acceptance remains separate."""

import argparse
import asyncio

from .verify_stdio_spike import verify

if __name__ == "__main__":
    argparse.ArgumentParser(description="실제 loopback HTTP MCP·Guard 검증").parse_args()
    asyncio.run(verify(mcp_transport="http"))
