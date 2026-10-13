"""A18: bounded, local AGT decisions for the existing AccessChecker port.

One daemon worker owns the SDK. Only an opaque tool key reaches it; no tool
arguments, Task state, actor identity, execution callback or approval capability
does. Rules are trusted exact matches, never caller-supplied expressions.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import queue
import re
import threading
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any

from guard_app.domain.contracts import TrustedContext
from guard_app.domain.enums import ToolKind
from guard_app.domain.models import AccessResult, ResolvedRequest
from guard_app.security.failclosed import fail_closed

AGT_PACKAGE = "agent-governance-toolkit-core"
AGT_VERSION = "5.0.0"
MAX_AGT_RULES = 256
_AGENT = "guard-local"


class AgtConfigError(ValueError):
    def __init__(self) -> None:
        super().__init__("AGT 설정이 유효하지 않다")


class AgtStartupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("AGT 시작을 확인할 수 없다")


class AgtShutdownError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("AGT 종료를 확인할 수 없다")


def _error(code: str) -> AccessResult:
    return AccessResult(allowed=False, reason=code, error=True)


def _tool_key(server: str, tool: str, kind: ToolKind) -> str:
    if (
        type(server) is not str
        or type(tool) is not str
        or not 1 <= len(server) <= 256
        or not 1 <= len(tool) <= 256
        or type(kind) is not ToolKind
    ):
        raise AgtConfigError()
    try:
        # Length prefixes avoid ambiguous concatenation. Hex keys cannot inject
        # syntax into AGT's expression parser or expose raw identifiers in logs.
        parts = [value.encode("utf-8") for value in (server, tool, kind.value)]
    except UnicodeError:
        raise AgtConfigError() from None
    return hashlib.sha256(b"".join(len(p).to_bytes(4, "big") + p for p in parts)).hexdigest()


@dataclass(slots=True)
class _Job:
    key: str
    result: Future[AccessResult] = field(default_factory=Future)
    abandoned: threading.Event = field(default_factory=threading.Event)


class AgentGovernanceAccessChecker:
    """Real AGT, deny by default; caller owns start/close on the backend loop.

    At most one queued/running evaluation exists. Cancellation abandons its result
    without admitting more work until it settles. A timeout poisons this instance;
    it cannot restart or silently fall back. A stuck thread reports unconfirmed
    cleanup and cannot hold interpreter shutdown through a default executor.
    """

    mode = "real"

    def __init__(
        self,
        rules: Mapping[tuple[str, str, ToolKind], bool],
        *,
        rule_version: str,
        evaluation_timeout_s: float = 1.0,
        startup_timeout_s: float = 5.0,
        close_timeout_s: float = 1.0,
    ) -> None:
        if not isinstance(rules, Mapping) or len(rules) > MAX_AGT_RULES:
            raise AgtConfigError()
        if type(rule_version) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", rule_version):
            raise AgtConfigError()
        for value in (evaluation_timeout_s, startup_timeout_s, close_timeout_s):
            if type(value) not in (int, float) or not 0 < value <= 30 or not math.isfinite(value):
                raise AgtConfigError()
        compiled: list[tuple[str, bool]] = []
        for key, allowed in rules.items():
            if len(compiled) >= MAX_AGT_RULES or type(key) is not tuple or len(key) != 3:
                raise AgtConfigError()
            if type(allowed) is not bool:
                raise AgtConfigError()
            compiled.append((_tool_key(*key), allowed))
        self._rule_version = rule_version
        self._rules = tuple(compiled)
        self._evaluation_timeout = float(evaluation_timeout_s)
        self._startup_timeout = float(startup_timeout_s)
        self._close_timeout = float(close_timeout_s)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready: Future[bool] = Future()
        self._finished: Future[None] = Future()
        self._queue: queue.Queue[_Job] = queue.Queue(maxsize=1)
        self._job: _Job | None = None
        self._thread: threading.Thread | None = None

    @property
    def rule_version(self) -> str:
        return self._rule_version

    async def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise AgtStartupError()
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main, name="guard-agt", daemon=True
                )
                try:
                    self._thread.start()
                except (RuntimeError, OSError):
                    self._stop.set()
                    self._ready.set_result(False)
                    self._finished.set_result(None)
                    raise AgtStartupError() from None
        try:
            ready = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(self._ready)), self._startup_timeout
            )
        except TimeoutError:
            self._request_stop()
            raise AgtStartupError() from None
        except asyncio.CancelledError:
            self._request_stop()
            raise
        if not ready or self._stop.is_set():
            raise AgtStartupError()

    async def check_access(self, req: ResolvedRequest, ctx: TrustedContext) -> AccessResult:
        if req.task_id != ctx.state.task_id or req.epoch != ctx.state.epoch:
            return _error("AGT_CONTEXT_MISMATCH")
        try:
            key = _tool_key(req.tool.server_id, req.tool.tool_name, req.tool.kind)
        except AgtConfigError:
            return _error("AGT_INPUT_INVALID")
        with self._lock:
            if self._stop.is_set() or not self._ready.done() or not self._ready.result():
                return _error("AGT_UNAVAILABLE")
            if self._job is not None:
                return _error("AGT_BUSY")
            job = _Job(key)
            self._job = job
            self._queue.put_nowait(job)
        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(job.result)), self._evaluation_timeout
            )
        except TimeoutError:
            job.abandoned.set()
            self._request_stop()
            return _error("AGT_TIMEOUT")
        except asyncio.CancelledError:
            job.abandoned.set()
            raise

    def _request_stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._job is not None:
                self._job.abandoned.set()
                if not self._job.result.done():
                    self._job.result.set_result(_error("AGT_CLOSED"))
            if self._thread is None:
                if not self._ready.done():
                    self._ready.set_result(False)
                if not self._finished.done():
                    self._finished.set_result(None)

    async def close(self) -> None:
        self._request_stop()
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(self._finished)), self._close_timeout
            )
        except TimeoutError:
            raise AgtShutdownError() from None

    def _create_engine(self) -> tuple[Any, type]:
        # Import, version check, policy construction and evaluation all remain
        # on the worker. No SDK auto-discovery, file loading or external backend.
        if version(AGT_PACKAGE) != AGT_VERSION:
            raise AgtStartupError()
        from agentmesh.governance.policy import Policy, PolicyDecision, PolicyEngine, PolicyRule

        engine = PolicyEngine(conflict_strategy="deny_overrides")
        engine.load_policy(
            Policy(
                name="guard-exact-tools",
                version=self.rule_version,
                agent=_AGENT,
                default_action="deny",
                rules=[
                    PolicyRule(
                        name=f"tool-{index}",
                        condition=f"tool.key == '{key}'",
                        action="allow" if allowed else "deny",
                        stage="pre_tool",
                    )
                    for index, (key, allowed) in enumerate(self._rules)
                ],
            )
        )
        return engine, PolicyDecision

    @staticmethod
    def _translate(result: object, decision_type: type) -> AccessResult:
        if type(result) is not decision_type:
            return _error("AGT_RESULT_INVALID")
        allowed, action = getattr(result, "allowed", None), getattr(result, "action", None)
        if (
            type(allowed) is not bool
            or type(action) is not str
            or action not in {"allow", "deny", "warn", "log", "require_approval"}
            or allowed != (action == "allow")
        ):
            return _error("AGT_RESULT_INVALID")
        # SDK reason/metadata/approvers never become UI or audit content.
        return AccessResult(
            allowed=allowed, reason="AGT_ALLOW" if allowed else "AGT_DENY", error=False
        )

    async def _serve(self) -> None:
        engine, decision_type = self._create_engine()
        with self._lock:
            self._ready.set_result(not self._stop.is_set())
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.02)
            except queue.Empty:
                continue
            result = _error("AGT_CANCELLED")
            if not self._stop.is_set() and not job.abandoned.is_set():
                result = self._translate(
                    engine.evaluate(_AGENT, {"tool": {"key": job.key}}, stage="pre_tool"),
                    decision_type,
                )
            with self._lock:
                if not job.result.done():
                    job.result.set_result(
                        _error("AGT_CANCELLED") if job.abandoned.is_set() else result
                    )
                self._job = None

    async def _worker_failed(self, _exc: BaseException) -> None:
        # Reuse the repository's sole pre-dispatch broad containment boundary.
        # No exception object or its text crosses into a Future, log or UI.
        with self._lock:
            self._stop.set()
            if self._job is not None and not self._job.result.done():
                self._job.result.set_result(_error("AGT_EVALUATION_ERROR"))

    def _thread_main(self) -> None:
        try:
            asyncio.run(fail_closed(self._serve, self._worker_failed))
        finally:
            with self._lock:
                self._stop.set()
                if not self._ready.done():
                    self._ready.set_result(False)
                if self._job is not None and not self._job.result.done():
                    self._job.result.set_result(_error("AGT_UNAVAILABLE"))
                self._job = None
                self._finished.set_result(None)
