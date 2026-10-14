"""A18: bounded, local AGT decisions for the existing AccessChecker port.

One daemon worker at a time owns an SDK engine. Only an opaque tool key reaches
it; no tool arguments, Task state, actor identity, execution callback or
approval capability does. Rules are trusted exact matches, never caller-supplied
expressions.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
import time
from collections import deque
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


def _now() -> float:
    # High-resolution monotonic clock: the ordering of admission and SDK start
    # decides retirement, and ``time.monotonic`` can tick at 15.6 ms on Windows.
    return time.perf_counter()


@dataclass(slots=True, eq=False)
class _Job:
    key: str
    # True when another job was queued or running ahead of it at admission.
    behind: bool
    result: Future[AccessResult] = field(default_factory=Future)
    admitted_at: float = field(default_factory=_now)
    abandoned: bool = False  # Guarded by the checker lock.


class _Worker:
    """One SDK engine and the daemon thread that owns it.

    ``jobs``, ``running`` and ``stop`` are guarded by the checker lock that also
    backs ``wake``. A stopped worker never settles or counts a job again.
    """

    def __init__(self, lock: threading.Lock) -> None:
        self.jobs: deque[_Job] = deque()
        self.wake = threading.Condition(lock)
        self.running: _Job | None = None
        self.stop = False
        self.ready: Future[bool] = Future()
        self.finished: Future[None] = Future()
        self.thread: threading.Thread | None = None
        # Start of the engine build or of the running job; None while idle.
        self.busy_since: float | None = _now()

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class AgentGovernanceAccessChecker:
    """Real AGT, deny by default; caller owns start/close on the backend loop.

    Up to ``max_pending`` evaluations may be queued or running; the current
    worker serves them in order, so concurrent Tasks are not rejected below that
    bound. A cancelled request leaves the queue at once, or keeps its slot until
    the SDK returns if it was already running.

    A timeout retires the current worker when it stayed on one engine build or
    job for that request's whole wait. A request with no job ahead of it counts
    its own SDK call. Then its running result is discarded, its queued requests
    move to a fresh worker, and the stuck thread counts against
    ``max_stuck_workers``. A request that waited behind other jobs only times
    out: once in the SDK it keeps its slot until the SDK returns, and that late
    result is discarded. Past that bound, on an SDK exception, or on a
    failed restart, the instance stays closed.
    Nothing falls back to allow or mock.
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
        max_pending: int = 16,
        max_stuck_workers: int = 2,
    ) -> None:
        if not isinstance(rules, Mapping) or len(rules) > MAX_AGT_RULES:
            raise AgtConfigError()
        if type(rule_version) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", rule_version):
            raise AgtConfigError()
        for value in (evaluation_timeout_s, startup_timeout_s, close_timeout_s):
            if type(value) not in (int, float) or not 0 < value <= 30 or not math.isfinite(value):
                raise AgtConfigError()
        # B's Guard admits 4 concurrent access checks by default; the adapter
        # must not be the narrower bound.
        if type(max_pending) is not int or not 1 <= max_pending <= MAX_AGT_RULES:
            raise AgtConfigError()
        if type(max_stuck_workers) is not int or not 0 <= max_stuck_workers <= 8:
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
        self._max_pending = max_pending
        self._max_stuck = max_stuck_workers
        self._lock = threading.Lock()
        self._closed = False
        self._available = False
        self._admitted = 0  # Jobs queued on or running in the current worker.
        self._worker: _Worker | None = None
        self._retired: list[_Worker] = []
        self._start_failed = False
        self.restarts = 0
        # Set by close() when a thread exit stayed unconfirmed after a failed start.
        self.shutdown_unconfirmed = False

    @property
    def rule_version(self) -> str:
        return self._rule_version

    async def start(self) -> None:
        with self._lock:
            if self._closed:
                raise AgtStartupError()
            if self._worker is None:
                self._worker = self._spawn_locked()
                if self._worker is None:
                    self._close_locked("AGT_UNAVAILABLE")
                    raise AgtStartupError()
            worker = self._worker
        try:
            ready = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(worker.ready)), self._startup_timeout
            )
        except TimeoutError:
            self._start_failed = True
            self._shutdown()
            raise AgtStartupError() from None
        except asyncio.CancelledError:
            self._start_failed = True
            self._shutdown()
            raise
        with self._lock:
            if not ready or self._closed:
                self._start_failed = True
                raise AgtStartupError()
            self._available = True

    async def check_access(self, req: ResolvedRequest, ctx: TrustedContext) -> AccessResult:
        if req.task_id != ctx.state.task_id or req.epoch != ctx.state.epoch:
            return _error("AGT_CONTEXT_MISMATCH")
        try:
            key = _tool_key(req.tool.server_id, req.tool.tool_name, req.tool.kind)
        except AgtConfigError:
            return _error("AGT_INPUT_INVALID")
        with self._lock:
            worker = self._worker
            if self._closed or not self._available or worker is None:
                return _error("AGT_UNAVAILABLE")
            if self._admitted >= self._max_pending:
                return _error("AGT_BUSY")
            job = _Job(key, behind=bool(worker.jobs) or worker.running is not None)
            worker.jobs.append(job)
            self._admitted += 1
            worker.wake.notify()
        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(job.result)), self._evaluation_timeout
            )
        except TimeoutError:
            self._timed_out(job)
            return _error("AGT_TIMEOUT")
        except asyncio.CancelledError:
            self._abandon(job)
            raise

    def _abandon(self, job: _Job) -> None:
        with self._lock:
            job.abandoned = True
            worker = self._worker
            if worker is not None and job in worker.jobs:
                # It never reached the SDK; free its slot now.
                worker.jobs.remove(job)
                self._admitted -= 1
                job.result.set_result(_error("AGT_CANCELLED"))

    def _timed_out(self, job: _Job) -> None:
        with self._lock:
            job.abandoned = True
            worker = self._worker
            if self._closed or worker is None or job.result.done():
                return
            if job in worker.jobs:
                worker.jobs.remove(job)
                self._admitted -= 1
                job.result.set_result(_error("AGT_TIMEOUT"))
            elif worker.running is job:
                # Settle it now; the slot stays until the SDK returns and the
                # late result is discarded there (or by retirement/close).
                job.result.set_result(_error("AGT_TIMEOUT"))
            # Replace the worker only if it stayed on one build or job for this
            # request's whole wait, not when it inherited an old backlog or the
            # request reached the SDK late after queueing behind other jobs.
            # Its late result is discarded; the instance itself stays usable.
            since = worker.busy_since
            own_call = worker.running is job and not job.behind
            if own_call or (since is not None and since <= job.admitted_at):
                self._retire_locked(worker)

    def _retire_locked(self, worker: _Worker) -> None:
        worker.stop = True
        worker.wake.notify_all()
        running, worker.running = worker.running, None
        if running is not None:
            # Its slot moves into the stuck-worker budget below.
            running.abandoned = True
            self._admitted -= 1
            if not running.result.done():
                running.result.set_result(_error("AGT_TIMEOUT"))
        queued = list(worker.jobs)
        worker.jobs.clear()
        self._retired = [w for w in self._retired if w.alive()]
        if worker.alive():
            self._retired.append(worker)
        replacement = None
        if len(self._retired) <= self._max_stuck:
            replacement = self._spawn_locked()
        if replacement is None:
            # Budget exhausted or no thread: fail closed for good.
            worker.jobs.extend(queued)
            self._close_locked("AGT_UNAVAILABLE")
            return
        self._worker = replacement
        self.restarts += 1
        replacement.jobs.extend(queued)
        replacement.wake.notify()

    def _spawn_locked(self) -> _Worker | None:
        worker = _Worker(self._lock)
        thread = threading.Thread(
            target=self._thread_main, args=(worker,), name="guard-agt", daemon=True
        )
        try:
            thread.start()
        except (RuntimeError, OSError):
            worker.stop = True
            worker.ready.set_result(False)
            worker.finished.set_result(None)
            return None
        worker.thread = thread
        return worker

    def _close_locked(self, code: str) -> None:
        self._closed = True
        self._available = False
        worker = self._worker
        for each in (worker, *self._retired):
            if each is not None:
                each.stop = True
                each.wake.notify_all()
        if worker is not None:
            running, worker.running = worker.running, None
            if running is not None:
                running.abandoned = True
                if not running.result.done():
                    running.result.set_result(_error(code))
            for job in worker.jobs:
                if not job.result.done():
                    job.result.set_result(_error(code))
            worker.jobs.clear()
        self._admitted = 0

    def _shutdown(self) -> tuple[_Worker, ...]:
        with self._lock:
            self._close_locked("AGT_CLOSED")
            return tuple(w for w in (self._worker, *self._retired) if w is not None)

    async def close(self) -> None:
        """Succeed only when every worker thread, retired ones included, exited.

        Exception: after a failed ``start()`` the AgtStartupError already
        reported the failure, and that never-ready worker only finishes its
        engine build before it stops without evaluating. Its unconfirmed exit
        sets ``shutdown_unconfirmed`` instead of masking the startup error with
        AgtShutdownError during the caller's cleanup.
        """

        workers = self._shutdown()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._close_timeout
        for worker in workers:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(worker.finished)),
                    max(0.0, deadline - loop.time()),
                )
            except TimeoutError:
                self._exit_unconfirmed()
                return
            # ``finished`` is the thread's last action, not its exit. Confirm
            # the exit itself so a successful close never leaves it running.
            while worker.alive():
                if loop.time() >= deadline:
                    self._exit_unconfirmed()
                    return
                await asyncio.sleep(0.001)
        self.shutdown_unconfirmed = False

    def _exit_unconfirmed(self) -> None:
        self.shutdown_unconfirmed = True
        if not self._start_failed:
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

    async def _serve(self, worker: _Worker) -> None:
        engine, decision_type = self._create_engine()
        with self._lock:
            worker.ready.set_result(not worker.stop)
            worker.busy_since = None
        while True:
            with self._lock:
                while not worker.stop and not worker.jobs:
                    worker.wake.wait()
                if worker.stop:
                    return
                job = worker.jobs.popleft()
                worker.running = job
                worker.busy_since = _now()
            result = self._translate(
                engine.evaluate(_AGENT, {"tool": {"key": job.key}}, stage="pre_tool"),
                decision_type,
            )
            with self._lock:
                if worker.stop:
                    return  # Retired or closed: the job was already settled.
                worker.running = None
                worker.busy_since = None
                self._admitted -= 1
                if not job.result.done():
                    job.result.set_result(_error("AGT_CANCELLED") if job.abandoned else result)

    async def _worker_failed(self, worker: _Worker, _exc: BaseException) -> None:
        # Reuse the repository's sole pre-dispatch broad containment boundary.
        # No exception object or its text crosses into a Future, log or UI.
        with self._lock:
            job = worker.running
            if not worker.stop and job is not None and not job.result.done():
                job.result.set_result(_error("AGT_EVALUATION_ERROR"))

    def _thread_main(self, worker: _Worker) -> None:
        try:
            asyncio.run(
                fail_closed(
                    lambda: self._serve(worker),
                    lambda exc: self._worker_failed(worker, exc),
                )
            )
        finally:
            with self._lock:
                if self._worker is worker and not worker.stop:
                    # The current worker died (SDK exception or failed start).
                    self._close_locked("AGT_UNAVAILABLE")
                worker.stop = True
                if not worker.ready.done():
                    worker.ready.set_result(False)
                worker.finished.set_result(None)
