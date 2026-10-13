"""C operational characterization, not an A transport or B ScanReport.

No tools/call is sent. Live API work requires --live; only sanitized summaries persist.
I1/I9/I10; T17/T19/S04 input/scan observations, not Guard integration acceptance.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import statistics
import sys
import time
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import anyio
import httpx
from attack_fixtures.scanner_limits.server import encoded, http_fixture, tool
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from guard_app.adapters.cisco import (
    CiscoMetadataAdapter,
    DefinitionInputError,
    prepare_tool_definition,
)
from guard_app.adapters.cisco_llm import ScannerLLMError, ScannerLLMSettings, connect_luna_scanner
from guard_app.security.secrets import scanner_sdk_import_environment

ROOT = Path(__file__).resolve().parents[2]
CALL_CONTEXT = ContextVar("operating_probe_id", default=None)


class ObservedStream:
    def __init__(self, inner, record):
        self.inner = inner
        self.record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.inner.aclose()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = await self.inner.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None
        if not isinstance(item, Exception):
            message = item.message.root
            if isinstance(message, types.JSONRPCResponse) and "tools" in message.result:
                self.record["pages"].append(message.result)
        return item

    async def send(self, item):
        message = item.message.root
        if isinstance(message, (types.JSONRPCRequest, types.JSONRPCNotification)):
            if message.method not in {
                "initialize",
                "tools/list",
                "notifications/initialized",
                "notifications/cancelled",
                "ping",
            }:
                raise ValueError("METADATA_ONLY_PROBE")
            self.record["methods"][message.method] += 1
        await self.inner.send(item)

    async def aclose(self):
        await self.inner.aclose()


async def inspect_stream(streams, record):
    async with ClientSession(
        ObservedStream(streams[0], record),
        ObservedStream(streams[1], record),
        read_timeout_seconds=timedelta(seconds=35),
    ) as session:
        started = time.perf_counter()
        initialized = await session.initialize()
        record["init_seconds"] = time.perf_counter() - started
        record["protocol"] = initialized.protocolVersion
        cursor = None
        seen = set()
        for _ in range(40):
            page_start = time.perf_counter()
            result = await session.list_tools(cursor=cursor)
            record["page_seconds"].append(time.perf_counter() - page_start)
            cursor = result.nextCursor
            if not cursor:
                record["sdk_returned_complete_list"] = True
                break
            if cursor in seen:
                record["probe_stopped_repeated_cursor"] = True
                break
            seen.add(cursor)


async def fixture_probe(scenario, transport, output):
    counters = output / f"{transport}-{scenario}-counters.jsonl"
    record = {
        "scenario": scenario,
        "transport": transport,
        "methods": Counter(),
        "pages": [],
        "page_seconds": [],
    }
    if transport == "stdio":
        with (output / f"{scenario}-stderr.log").open("w") as errors:
            async with stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=[
                        str(ROOT / "attack_fixtures/scanner_limits/server.py"),
                        "--scenario",
                        scenario,
                        "--counter",
                        str(counters),
                    ],
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                ),
                errlog=errors,
            ) as streams:
                await inspect_stream(streams, record)
    else:
        with http_fixture(scenario, counters) as endpoint:
            async with (
                httpx.AsyncClient(timeout=35, trust_env=False, follow_redirects=False) as client,
                streamable_http_client(endpoint, http_client=client) as streams,
            ):
                await inspect_stream(streams, record)
    pages = record.pop("pages")
    values = [entry for page in pages for entry in page["tools"]]
    wire = [json.loads(line) for line in counters.read_text().splitlines()]
    page_bytes = [entry["response_bytes"] for entry in wire if entry["method"] == "tools/list"]
    preserved = 0
    input_errors = Counter()
    for value in values:
        try:
            prepared = prepare_tool_definition(value, max_definition_bytes=65536)
        except DefinitionInputError as error:
            input_errors[str(error)] += 1
        else:
            preserved += json.loads(prepared.definition_json) == value
    record.update(
        methods=dict(record["methods"]),
        tools=len(values),
        pages=len(pages),
        largest_wire_page_bytes=max(page_bytes, default=0),
        all_wire_list_bytes=sum(page_bytes),
        prepared_definitions=preserved,
        input_errors=dict(input_errors),
        fixture_tools_call_count=sum(entry["tools_call"] for entry in wire),
        warning_registry_or_ui_connected=False,
        a_product_transport_used=False,
    )
    return record


class RealHTTPObserver(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self.events = []

    async def handle_async_request(self, request):
        start = time.perf_counter()
        response = await self.inner.handle_async_request(request)
        self.events.append(
            {
                "case": CALL_CONTEXT.get(),
                "status": response.status_code,
                "headers_received_seconds": time.perf_counter() - start,
            }
        )
        return response

    async def aclose(self):
        await self.inner.aclose()


def findings_summary(findings):
    return [
        {"severity": value.severity, "analyzer": value.analyzer, "category": value.threat_category}
        for value in findings
    ]


async def actual_metadata_scan(metadata_dir, output, live):
    with scanner_sdk_import_environment():
        from mcpscanner.core.analyzers.yara_analyzer import YaraAnalyzer
    raw_evidence = json.loads((metadata_dir / "evidence.json").read_text())
    cases = []
    seen = set()
    for server in raw_evidence["servers"]:
        if not server["complete"]:
            continue
        category = "github" if server["id"].startswith("github-") else server["id"]
        tools = json.loads((metadata_dir / f"{server['id']}-raw-tools.json").read_text())["tools"]
        for definition in tools:
            sha = hashlib.sha256(encoded(definition)).hexdigest()
            key = (category, definition["name"], sha)
            if key in seen:
                continue
            seen.add(key)
            prepared = prepare_tool_definition(definition, max_definition_bytes=65536)
            assert json.loads(prepared.definition_json) == definition
            cases.append(
                {
                    "id": len(cases),
                    "server": category,
                    "name": definition["name"],
                    "sha256": sha,
                    "bytes": len(prepared.definition_json.encode()),
                    "definition": definition,
                }
            )
    yara_adapter = CiscoMetadataAdapter(
        YaraAnalyzer(), input_format="yara", max_definition_bytes=65536
    )
    yara_gate = asyncio.Semaphore(1)
    llm_gate = asyncio.Semaphore(4)
    counts = {"active_llm": 0, "peak_llm": 0, "active_yara": 0, "peak_yara": 0}
    rows = [{key: value for key, value in case.items() if key != "definition"} for case in cases]

    async def yara_one(case):
        async with yara_gate:
            counts["active_yara"] += 1
            counts["peak_yara"] = max(counts["peak_yara"], counts["active_yara"])
            start = time.perf_counter()
            try:
                findings = await asyncio.to_thread(
                    lambda: asyncio.run(yara_adapter.analyze_tool_definition(case["definition"]))
                )
                result = {"complete": True, "findings": findings_summary(findings)}
            finally:
                counts["active_yara"] -= 1
            rows[case["id"]]["yara"] = {**result, "seconds": time.perf_counter() - start}

    async def llm_one(case, scanner):
        async with llm_gate:
            counts["active_llm"] += 1
            counts["peak_llm"] = max(counts["peak_llm"], counts["active_llm"])
            token = CALL_CONTEXT.set(case["id"])
            start = time.perf_counter()
            try:
                findings = await scanner.analyze_tool_definition(case["definition"])
                result = {"complete": True, "findings": findings_summary(findings)}
            except (ScannerLLMError, DefinitionInputError) as error:
                result = {"complete": False, "error_code": str(error)}
            finally:
                CALL_CONTEXT.reset(token)
                counts["active_llm"] -= 1
            rows[case["id"]]["llm"] = {**result, "seconds": time.perf_counter() - start}
            done = sum("llm" in row for row in rows)
            if done % 10 == 0 or done == len(rows):
                print(
                    json.dumps({"phase": "live", "completed": done, "total": len(rows)}), flush=True
                )

    start = time.perf_counter()
    observer = RealHTTPObserver() if live else None
    settings = None
    if live:
        loaded = ScannerLLMSettings.load(ROOT / ".env")
        settings = {
            "model": loaded.model,
            "reasoning_effort": loaded.reasoning_effort,
            "timeout_seconds": loaded.timeout_seconds,
            "max_completion_tokens": loaded.max_completion_tokens,
        }
        async with connect_luna_scanner(
            ROOT / ".env", max_definition_bytes=65536, transport=observer
        ) as scanner:
            await asyncio.gather(
                *(yara_one(case) for case in cases), *(llm_one(case, scanner) for case in cases)
            )
    else:
        await asyncio.gather(*(yara_one(case) for case in cases))
    evidence = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "metadata_observed_at_utc": raw_evidence["observed_at_utc"],
        "unique_definition_variants": len(rows),
        "live": live,
        "elapsed_seconds": time.perf_counter() - start,
        "probe_limits": {"llm_concurrency": 4, "yara_concurrency": 1, "definition_bytes": 65536},
        "actual_peak_concurrency": counts,
        "http_events": observer.events if observer else [],
        "cases": rows,
        "b_scanreport_connected": False,
        "product_joint_runner_used": False,
        "product_yara_5s_limit_used": False,
        "server_job_deadline_enforced": False,
        "settings": settings,
        "versions": {
            name: version(name) for name in ("mcp", "cisco-ai-mcp-scanner", "openai", "yara-python")
        },
    }
    if live:
        times = [row["llm"]["seconds"] for row in rows]
        evidence["llm_summary"] = {
            "complete": sum(row["llm"]["complete"] for row in rows),
            "failed": sum(not row["llm"]["complete"] for row in rows),
            "p50_seconds": statistics.median(times),
            "p95_seconds_nearest_rank": sorted(times)[int(len(times) * 0.95 + 0.999) - 1],
            "http_status_counts": dict(Counter(event["status"] for event in observer.events)),
        }
    (output / "actual-metadata-scans.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in [
                    "unique_definition_variants",
                    "elapsed_seconds",
                    "actual_peak_concurrency",
                ]
            }
        ),
        flush=True,
    )


async def synchronous_timeout_probe(output):
    with scanner_sdk_import_environment():
        from mcpscanner.core.analyzers.yara_analyzer import YaraAnalyzer
    analyzer = YaraAnalyzer()

    class BlockingRules:
        def match(self, **kwargs):
            time.sleep(0.08)
            return []

    analyzer._rules = BlockingRules()
    start = time.perf_counter()
    async with asyncio.timeout(0.01):
        findings = await analyzer.analyze("synthetic")
    result = {
        "fault": "blocking matcher 80ms",
        "async_timeout_seconds": 0.01,
        "elapsed_seconds": time.perf_counter() - start,
        "returned_empty_findings": findings == [],
        "native_matcher_timeout_applied": False,
    }
    (output / "yara-async-timeout.json").write_text(json.dumps(result, indent=2) + "\n")


async def definition_boundary_probe(output):
    class CounterAnalyzer:
        def __init__(self):
            self.calls = 0

        async def analyze(self, content, context=None):
            self.calls += 1
            return []

    observations = []
    for size in (65535, 65536, 65537):
        analyzer = CounterAnalyzer()
        adapter = CiscoMetadataAdapter(analyzer, input_format="llm", max_definition_bytes=65536)
        definition = tool("byte_boundary", size=size)
        result = {"definition_bytes": size}
        try:
            await adapter.analyze_tool_definition(definition)
            result["accepted"] = True
        except DefinitionInputError as error:
            result.update(accepted=False, error_code=str(error))
        result["analyzer_calls"] = analyzer.calls
        observations.append(result)
    assert observations[0]["accepted"]
    assert observations[1]["accepted"]
    assert not observations[2]["accepted"]
    assert observations[2]["analyzer_calls"] == 0
    assert observations[2]["error_code"] == "SCAN_METADATA_TOO_LARGE"
    (output / "definition-boundaries.json").write_text(json.dumps(observations, indent=2) + "\n")


async def main(args):
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    await definition_boundary_probe(args.output)
    await synchronous_timeout_probe(args.output)
    if args.metadata_dir:
        await actual_metadata_scan(args.metadata_dir, args.output, args.live)
    scenarios = [
        "page_at_limit",
        "page_over_limit",
        "tools_257",
        "pages_33",
        "total_over_8mib",
        "duplicate_name",
        "depth_64",
        "depth_65",
        "repeat_cursor",
        "slow_page",
        "slow_init",
    ]
    records = []
    for transport in ("stdio", "http"):
        for scenario in scenarios:
            record = await fixture_probe(scenario, transport, args.output)
            records.append(record)
            print(
                json.dumps(
                    {
                        "transport": transport,
                        "scenario": scenario,
                        "pages": record["pages"],
                        "tools": record["tools"],
                        "wire_total": record["all_wire_list_bytes"],
                    }
                ),
                flush=True,
            )
    (args.output / "mcp-fixture-observations.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metadata-dir", type=Path)
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    if arguments.live and not arguments.metadata_dir:
        parser.error("--live requires --metadata-dir")
    asyncio.run(main(arguments))
