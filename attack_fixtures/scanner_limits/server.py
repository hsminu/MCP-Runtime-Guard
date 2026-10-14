"""C metadata-only MCP fixture: I1/I9/I10, T17/T19/S04.

Oversized definitions are synthetic. No managed tools are implemented.
Wire counts exclude stdio newlines and HTTP framing, and include JSON-RPC envelopes.
"""

import argparse
import json
import sys
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def tool(name, size=None, depth=1):
    value = {"name": name, "description": "", "inputSchema": {"type": "object"}}
    if size is not None:
        value["description"] = "x" * (size - len(encoded(value)))
        if len(encoded(value)) != size:
            raise ValueError("FIXTURE_SIZE_MISMATCH")
    if depth > 1:
        nested = "synthetic"
        for _ in range(depth - 1):
            nested = {"n": nested}
        value["_meta"] = nested
    return value


def reply(request, scenario):
    method = request["method"]
    if method.startswith("notifications/"):
        return None
    identifier = request["id"]
    if method == "initialize":
        if scenario == "slow_init":
            time.sleep(10.1)
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "C-scanner-bounds-fixture", "version": "1"},
            },
        }
    if method != "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": -32601, "message": "unsupported"},
        }
    cursor = request.get("params", {}).get("cursor")
    index = int(cursor or 0) if scenario != "repeat_cursor" else 0
    if scenario == "slow_page":
        time.sleep(10.1)
    result = {"tools": [tool(f"fixture_{index}")]}
    if scenario in {"page_at_limit", "page_over_limit"}:
        target = 1048576 + (scenario == "page_over_limit")
        envelope = {"jsonrpc": "2.0", "id": identifier, "result": result}
        result["tools"][0]["description"] = "x" * (target - len(encoded(envelope)))
        if len(encoded(envelope)) != target:
            raise ValueError("FIXTURE_ENVELOPE_SIZE_MISMATCH")
    elif scenario == "tools_257":
        result["tools"] = [tool(f"fixture_{i}") for i in range(257)]
    elif scenario == "pages_33":
        if index < 32:
            result["nextCursor"] = str(index + 1)
    elif scenario == "total_over_8mib":
        result["tools"] = [tool(f"fixture_{index:02}_{i:02}", 65500) for i in range(16)]
        if index < 8:
            result["nextCursor"] = str(index + 1)
    elif scenario == "duplicate_name":
        result["tools"] = [tool("same_name"), tool("same_name")]
    elif scenario in {"depth_64", "depth_65"}:
        result["tools"] = [tool("nested", depth=int(scenario.rsplit("_", 1)[1]))]
    elif scenario == "repeat_cursor":
        result["nextCursor"] = "repeat"
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


class Fixture:
    def __init__(self, scenario, counter_path):
        self.scenario = scenario
        self.counter_path = counter_path
        self.lock = Lock()

    def handle(self, request):
        response = reply(request, self.scenario)
        body = encoded(response) if response is not None else b""
        count = {
            "method": request["method"],
            "response_bytes": len(body),
            "tools_call": request["method"] == "tools/call",
        }
        with self.lock, self.counter_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(count) + "\n")
        return body


@contextmanager
def http_fixture(scenario, counter_path):
    fixture = Fixture(scenario, counter_path)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = fixture.handle(request)
            self.send_response(200 if body else 202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_DELETE(self):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--counter", required=True, type=Path)
    args = parser.parse_args()
    fixture = Fixture(args.scenario, args.counter)
    for line in sys.stdin:
        request = json.loads(line)
        body = fixture.handle(request)
        if body:
            sys.stdout.buffer.write(body + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
