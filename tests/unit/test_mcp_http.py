"""A08/A12 adapter unit probes over loopback TCP (I1/I8/I9, T14-T16/T19).

The protocol stub isolates client behavior; it is not C's acceptance fixture.
"""

import asyncio
import http.server
import json
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager

import httpx
import pytest

from examples.http_spike_server import http_spike, serve_loopback
from guard_app.adapters import mcp_transport
from guard_app.adapters.mcp_transport import McpTransport, http_stream_factory
from guard_app.domain.contracts import Denied, OutcomeUnknown, Released, Withheld
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import DispatchError

from .test_guard import _call, _Harness
from .test_mcp_transport import _request


class ProtocolProbe:
    """Synthetic server: count actual tools/call POSTs separately from effects."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.wire_calls = 0
        self.effects = 0
        self.requests = []
        self.redirect_url = ""
        self.effect_done = asyncio.Event()
        self.release = asyncio.Event()
        self.list_calls = 0
        self.output_schema = None
        self.delay = 0.0
        self.responded = asyncio.Event()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        headers = dict(scope["headers"])
        self.requests.append((scope["method"], headers))
        if scope["method"] != "POST":
            await self.respond(send, 204 if scope["method"] == "DELETE" else 405)
            return
        body = b""
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body += event.get("body", b"")
            assert len(body) < 65536
            if not event.get("more_body"):
                break
        message = json.loads(body)
        method = message.get("method")
        extra = []
        if method == "initialize":
            if self.mode == "startup-error":
                await self.respond(send, 401)
                return
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "A-probe", "version": "1"},
            }
            extra = [
                (b"mcp-session-id", b"synthetic-session"),
                (b"set-cookie", b"secret=RAW_COOKIE"),
            ]
        elif method == "tools/list":
            self.list_calls += 1
            schema = {} if self.output_schema is None else {"outputSchema": self.output_schema}
            result = {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object", "properties": {}}, **schema}
                    for name in ("read", "send", "tool")
                ]
            }
        elif method == "tools/call":
            self.wire_calls += 1
            if self.mode in ("307", "308", "401", "404", "500"):
                await self.respond(
                    send, int(self.mode), headers=[(b"location", self.redirect_url.encode())]
                )
                return
            self.effects += 1
            self.effect_done.set()
            if self.mode == "timeout":
                await self.release.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.mode == "lost":
                await self.respond(send, 200, b"RAW_RESPONSE_MARKER")
                return
            if self.mode == "sse-loss":
                await self.respond(
                    send,
                    200,
                    b"id: synthetic-event\nretry: 0\ndata:\n\n",
                    content_type=b"text/event-stream",
                )
                return
            result = {"content": [{"type": "text", "text": "synthetic-result"}], "isError": False}
            if self.output_schema is not None:
                result["structuredContent"] = {"value": "synthetic-structured"}
        else:
            await self.respond(send, 202)
            return
        encoded = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        await self.respond(send, 200, encoded, headers=extra)
        if method == "tools/call":
            self.responded.set()

    @staticmethod
    async def respond(send, status, body=b"", *, content_type=b"application/json", headers=()):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", content_type), *headers],
            }
        )
        await send({"type": "http.response.body", "body": body})


def transport_for(endpoint, dispatches, *, call_timeout=0.5):
    return McpTransport(
        {"docs": http_stream_factory(endpoint, io_timeout_s=0.3)},
        call_timeout_s=call_timeout,
        close_timeout_s=2,
        on_dispatch=dispatches.append,
    )


async def close_failed_channel(transport):
    # The SDK can tear down a session by raising a task-group HTTP failure.
    # The worker honestly reports that exceptional context exit as unconfirmed.
    try:
        await transport.close()
    except DispatchError as error:
        assert error.exception_type in {"SessionCloseFailed", "WorkerOwnerFailed"}
    assert all(worker._owner.done() for worker in transport._workers.values())


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://external.example/mcp",
        "http://localhost/mcp",
        "ftp://example/mcp",
        "https://user:RAW_SECRET@example/mcp",
        "https://example/mcp?key=RAW_SECRET",
        "https://example/mcp#fragment",
        "https://example/mcp\n",
        "https://example\\mcp",
        "/mcp",
        "https://[bad",
        "https://example/" + "x" * 2048,
    ],
)
def test_endpoint_envelope_rejects_unsupported_urls_without_echo(endpoint):
    with pytest.raises(ValueError) as caught:
        http_stream_factory(endpoint)
    assert "RAW_SECRET" not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, True, "5", float("nan"), float("inf"), 301, 10**400])
@pytest.mark.parametrize("name", ["io_timeout_s", "read_timeout_s"])
def test_http_timeouts_are_bounded(timeout, name):
    with pytest.raises(ValueError):
        http_stream_factory("https://example.test/mcp", **{name: timeout})


def test_http_read_timeout_cannot_be_shorter_than_call_timeout():
    """Q12: a read bound below the call bound would let a healthy slow tool end the session."""
    factory = http_stream_factory("https://example.test/mcp", io_timeout_s=1, read_timeout_s=4)
    with pytest.raises(ValueError, match="read timeout"):
        McpTransport({"docs": factory}, call_timeout_s=5)
    McpTransport({"docs": factory}, call_timeout_s=4)


@pytest.mark.asyncio
async def test_client_and_stream_lifetime_settings_and_request_guards(monkeypatch):
    seen = []
    real_transport = httpx.AsyncHTTPTransport

    def capture_transport(**kwargs):
        seen.append(kwargs)
        return real_transport(**kwargs)

    @asynccontextmanager
    async def fake_stream(endpoint, *, http_client, terminate_on_close):
        assert endpoint == "https://example.test/mcp" and terminate_on_close
        assert not http_client.follow_redirects and not http_client.trust_env
        assert http_client.auth is None and not http_client.is_closed
        check = http_client.event_hooks["request"][0]
        await check(httpx.Request("GET", endpoint))
        for request in (
            httpx.Request("GET", endpoint),
            httpx.Request("GET", endpoint, headers={"Last-Event-ID": "marker"}),
            httpx.Request("POST", "https://other.test/mcp"),
            httpx.Request("POST", endpoint, headers={"Authorization": "RAW_SECRET"}),
            httpx.Request("PUT", endpoint),
        ):
            with pytest.raises(httpx.RequestError):
                await check(request)
        request = httpx.Request("POST", endpoint, headers={"Cookie": "RAW_COOKIE"})
        await check(request)
        assert "cookie" not in request.headers
        seen.append(http_client)
        yield (None, None, None)

    monkeypatch.setattr(mcp_transport.httpx, "AsyncHTTPTransport", capture_transport)
    monkeypatch.setattr(mcp_transport, "streamable_http_client", fake_stream)
    async with http_stream_factory("https://example.test/mcp")():
        pass
    assert seen[0]["retries"] == 0 and not seen[0]["trust_env"] and seen[0]["verify"]
    assert seen[0]["limits"].max_connections == 4
    assert seen[1].is_closed


def test_httpcore_is_imported_with_the_transport_module():
    # A46: httpx would otherwise import httpcore on the loop at the first connection.
    import sys

    assert mcp_transport.httpcore is sys.modules["httpcore"]


@pytest.mark.asyncio
async def test_tls_context_is_built_off_the_event_loop_and_still_verifies(monkeypatch):
    # A46: 인증서 묶음 로딩이 이벤트 루프를 막지 않고, 검증 설정은 그대로다.
    import ssl

    built = []
    transports = []
    real_context = httpx.create_ssl_context
    real_transport = httpx.AsyncHTTPTransport
    loop_thread = threading.get_ident()

    def capture_context(**kwargs):
        context = real_context(**kwargs)
        built.append((threading.get_ident(), kwargs, context))
        return context

    def capture_transport(**kwargs):
        transports.append(kwargs)
        return real_transport(**kwargs)

    @asynccontextmanager
    async def fake_stream(endpoint, *, http_client, terminate_on_close):
        yield (None, None, None)

    monkeypatch.setattr(mcp_transport.httpx, "create_ssl_context", capture_context)
    monkeypatch.setattr(mcp_transport.httpx, "AsyncHTTPTransport", capture_transport)
    monkeypatch.setattr(mcp_transport, "streamable_http_client", fake_stream)
    async with http_stream_factory("https://example.test/mcp")():
        pass

    assert len(built) == 1
    thread_id, kwargs, context = built[0]
    assert thread_id != loop_thread
    assert kwargs == {"trust_env": False}
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname
    assert transports[0]["verify"] is context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "json_response,stateless", [(True, False), (False, False), (True, True), (False, True)]
)
async def test_real_fastmcp_json_and_sse_sessions_work_through_guard(
    json_response, stateless, caplog
):
    caplog.set_level(logging.DEBUG)
    async with http_spike(json_response=json_response, stateless=stateless) as spike:
        dispatches = []
        transport = transport_for(spike.endpoint, dispatches, call_timeout=2)
        h = _Harness()
        h.guard._transport = transport
        try:
            await transport.start()
            assert isinstance(
                await h.guard.submit("task", _call("send"), expected_epoch=1), Released
            )
            assert len(dispatches) == 1 and spike.counts["receiver_effect"] == 1
        finally:
            await transport.close()
    sdk_logs = [record for record in caplog.records if record.name.startswith(("mcp.", "uvicorn."))]
    assert sdk_logs and all(record.getMessage() == "MCP_LOG_WITHHELD" for record in sdk_logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["307", "308", "401", "404", "500", "lost", "timeout", "sse-loss"])
async def test_http_failure_never_replays_send_or_releases_unknown_result(mode, caplog):
    probe, redirect_target = ProtocolProbe(mode), ProtocolProbe()
    caplog.set_level(logging.DEBUG)
    async with serve_loopback(redirect_target) as redirect_url, serve_loopback(probe) as endpoint:
        probe.redirect_url = redirect_url
        dispatches = []
        transport = transport_for(endpoint, dispatches)
        h = _Harness()
        h.guard._transport = transport
        try:
            await transport.start()
            outcome = await asyncio.wait_for(
                h.guard.submit("task", _call("send"), expected_epoch=1), 2
            )
            assert isinstance(outcome, OutcomeUnknown)
            assert h.store.states["task"].status is TaskStatus.UNKNOWN_OUTCOME
            assert isinstance(await h.guard.submit("task", _call("send"), expected_epoch=1), Denied)
            assert len(dispatches) == probe.wire_calls == 1
            assert probe.effects == int(mode in {"lost", "timeout", "sse-loss"})
            assert redirect_target.requests == []
            assert all(b"last-event-id" not in headers for _, headers in probe.requests)
            assert all(
                b"cookie" not in headers and b"authorization" not in headers
                for _, headers in probe.requests
            )
        finally:
            probe.release.set()
            await close_failed_channel(transport)
    assert "RAW_RESPONSE_MARKER" not in caplog.text and "RAW_COOKIE" not in caplog.text


@pytest.mark.asyncio
async def test_http_ignores_proxy_environment_and_does_not_reuse_server_cookies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    probe = ProtocolProbe()
    async with serve_loopback(probe) as endpoint:
        transport = transport_for(endpoint, [])
        h = _Harness()
        h.guard._transport = transport
        try:
            await transport.start()
            assert isinstance(
                await h.guard.submit("task", _call("send"), expected_epoch=1), Released
            )
        finally:
            await transport.close()
    assert probe.wire_calls == probe.effects == 1
    assert all(b"cookie" not in headers for _, headers in probe.requests)
    assert any(method == "DELETE" for method, _ in probe.requests)


@pytest.mark.asyncio
async def test_initialization_failure_never_dispatches_and_settles_owner():
    probe = ProtocolProbe("startup-error")
    async with serve_loopback(probe) as endpoint:
        dispatches = []
        transport = transport_for(endpoint, dispatches)
        with pytest.raises(DispatchError):
            await asyncio.wait_for(transport.start(), 2)
        await close_failed_channel(transport)
        assert probe.wire_calls == 0 and dispatches == []


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_turn_remote_send_into_safe_cancellation():
    probe = ProtocolProbe("timeout")
    async with serve_loopback(probe) as endpoint:
        dispatches = []
        transport = transport_for(endpoint, dispatches)
        h = _Harness()
        h.guard._transport = transport
        await transport.start()
        task = asyncio.create_task(h.guard.submit("task", _call("send"), expected_epoch=1))
        try:
            await asyncio.wait_for(probe.effect_done.wait(), 1)
            task.cancel()
            assert isinstance(await task, OutcomeUnknown)
            assert h.store.states["task"].status is TaskStatus.UNKNOWN_OUTCOME
            assert isinstance(await h.guard.submit("task", _call("send"), expected_epoch=1), Denied)
        finally:
            probe.release.set()
            await transport.close()
        assert len(dispatches) == probe.wire_calls == probe.effects == 1


@pytest.mark.asyncio
async def test_invalid_http_read_is_withheld_without_label_or_raw_release(caplog):
    probe = ProtocolProbe("lost")
    caplog.set_level(logging.DEBUG)
    async with serve_loopback(probe) as endpoint:
        transport = transport_for(endpoint, [])
        h = _Harness()
        h.guard._transport = transport
        before = h.store.states["task"]
        try:
            await transport.start()
            outcome = await h.guard.submit("task", _call("read"), expected_epoch=1)
            assert isinstance(outcome, Withheld)
            after = h.store.states["task"]
            assert (
                after.status is TaskStatus.HALTED and after.high_watermark == before.high_watermark
            )
        finally:
            await close_failed_channel(transport)
    assert "RAW_RESPONSE_MARKER" not in caplog.text


@contextmanager
def _counting_schema_server(delay):
    """Thread-served $ref target: a fetch would block the backend loop for ``delay``."""
    gets = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            gets.append(self.path)
            time.sleep(delay)
            body = b'{"type": "object"}'
            self.send_response(200)
            self.send_header("content-type", "application/schema+json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="schema-ref-target", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/schema.json", gets
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_server_output_schema_ref_is_never_fetched_or_evaluated():
    """Q02 (I1/I9, T14): no tools/list, no $ref GET, no backend-loop stall on tools/call."""
    probe = ProtocolProbe()
    with _counting_schema_server(delay=1.0) as (ref_url, gets):
        probe.output_schema = {"$ref": ref_url}
        async with serve_loopback(probe) as endpoint:
            transport = McpTransport(
                {"docs": http_stream_factory(endpoint, io_timeout_s=0.5)},
                call_timeout_s=3,
                close_timeout_s=2,
            )
            loop = asyncio.get_running_loop()
            max_gap = 0.0
            stop = asyncio.Event()

            async def heartbeat():
                nonlocal max_gap
                last = loop.time()
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                    now = loop.time()
                    max_gap, last = max(max_gap, now - last), now

            beat = asyncio.create_task(heartbeat())
            try:
                await transport.start()
                raw = await transport.call(_request("docs", seq=1))
                assert raw.payload.to_dict()["structuredContent"] == {
                    "value": "synthetic-structured"
                }
            finally:
                stop.set()
                await beat
                await transport.close()
    assert gets == [] and probe.list_calls == 0 and probe.wire_calls == 1
    assert max_gap < 0.5


@pytest.mark.asyncio
async def test_json_tool_slower_than_io_timeout_succeeds_and_worker_stays_ready():
    """Q12 (I8, T16): a healthy JSON-mode tool within the call bound keeps the session."""
    probe = ProtocolProbe()
    probe.delay = 0.6
    async with serve_loopback(probe) as endpoint:
        dispatches = []
        transport = McpTransport(
            {"docs": http_stream_factory(endpoint, io_timeout_s=0.2, read_timeout_s=3)},
            call_timeout_s=2,
            close_timeout_s=2,
            on_dispatch=dispatches.append,
        )
        await transport.start()
        worker = transport._workers["docs"]
        try:
            assert (await transport.call(_request("docs", seq=1))).payload is not None
            assert worker._state is mcp_transport._State.READY
            probe.delay = 0.0
            assert (await transport.call(_request("docs", seq=2))).payload is not None
        finally:
            await transport.close()
        assert len(dispatches) == probe.wire_calls == 2


@pytest.mark.asyncio
async def test_json_tool_slower_than_call_timeout_times_out_and_worker_stays_usable():
    """Q12 (I6/I8, T16): the per-call timeout reports TimeoutError; no session teardown."""
    probe = ProtocolProbe()
    probe.delay = 0.6
    async with serve_loopback(probe) as endpoint:
        dispatches = []
        transport = McpTransport(
            {"docs": http_stream_factory(endpoint, io_timeout_s=0.2, read_timeout_s=3)},
            call_timeout_s=0.25,
            close_timeout_s=2,
            on_dispatch=dispatches.append,
        )
        await transport.start()
        worker = transport._workers["docs"]
        try:
            with pytest.raises(DispatchError) as timeout:
                await transport.call(_request("docs", seq=1))
            assert timeout.value.exception_type == "TimeoutError"
            # The late JSON response arrives for an abandoned request ID.
            await asyncio.wait_for(probe.responded.wait(), 2)
            await asyncio.sleep(0.05)
            assert worker._state is mcp_transport._State.READY
            probe.delay = 0.0
            assert (await transport.call(_request("docs", seq=2))).payload is not None
        finally:
            await transport.close()
        assert len(dispatches) == probe.wire_calls == 2
