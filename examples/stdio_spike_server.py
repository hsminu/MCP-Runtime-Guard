"""A-only SDK spike. Synthetic data; no external send, files, or credentials.

The explicitly supplied temporary counter path records only integer counts.
This is not a C acceptance fixture or a validated production launcher.
"""

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("A synthetic stdio spike")
counter_path = Path(sys.argv[1])
counts = {"receiver_calls": 0, "receiver_effect": 0}


def record(send=False):
    counts["receiver_calls"] += 1
    counts["receiver_effect"] += int(send)
    counter_path.write_text(json.dumps(counts), encoding="utf-8")


@server.tool()
def read() -> str:
    record()
    return "합성 분기 보고서"


@server.tool()
def send(dest: str) -> str:
    record(send=True)
    return "합성 수신 완료"


if __name__ == "__main__":
    print("SYNTHETIC_STDERR_ISOLATION_MARKER", file=sys.stderr, flush=True)
    server.run(transport="stdio")
