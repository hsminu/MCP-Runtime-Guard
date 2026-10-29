"""T28 정책 평가 지연(C, 14.4 `decide_ns`): 판정 구간과 submit 전체를 따로 잰다.

`uv run python -m tests.experiments.policy_latency_probe [--n 500] [--kind stdio]`

- 판정 구간(`decide_ns`): `Catalog.resolve` + `GuardService._evaluate_request`(Registry 상태·지문,
  사전 판정, 접근 규칙, 최종 판정). 개발문서 14.4의 "resolve + 접근 규칙 + evaluate 구간"이다.
- submit 전체: 위 구간에 Task 조회, 영속 call_seq, 판정 기록 커밋(SQLite `synchronous=FULL`),
  ASK면 승인 생성 커밋, ALLOW면 예약과 실제 MCP 호출 왕복이 더해진다.
- 경로: DENY(CONFIDENTIAL→일반 외부), ASK(INTERNAL→승인된 외부, 매번 거절), ALLOW(PUBLIC 읽기,
  실제 서버 왕복 포함). 모드는 `accumulate` 하나다. 모드 비교는 C6.11 replay에서 한다.

하니스는 `tests.unit.c_counting`(제품 조립 Guard·C Registry·B SQLiteStore·실제 서버)이다.
결과는 표준 출력에 JSON 한 줄로 낸다. 원문 인자·경로는 싣지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from tests.unit.c_counting import executable_pin, harness, read, send

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.services.catalog import Catalog
from guard_app.services.guard import GuardService


def p50_p95(ns: list[int]) -> dict[str, float]:
    q = statistics.quantiles(ns, n=100, method="inclusive")
    return {"p50_ms": statistics.median(ns) / 1e6, "p95_ms": q[94] / 1e6, "n": len(ns)}


class DecideClock:
    """resolve와 `_evaluate_request`의 시간을 호출마다 더한다(submit 단계만)."""

    def __init__(self) -> None:
        self.current = 0
        self._resolve = Catalog.resolve
        self._evaluate = GuardService._evaluate_request

    def install(self) -> None:
        clock = self
        resolve, evaluate = self._resolve, self._evaluate

        def timed_resolve(catalog, *args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return resolve(catalog, *args, **kwargs)
            finally:
                clock.current += time.perf_counter_ns() - start

        async def timed_evaluate(guard, req, state, *, phase):
            start = time.perf_counter_ns()
            try:
                return await evaluate(guard, req, state, phase=phase)
            finally:
                if phase == "submit":
                    clock.current += time.perf_counter_ns() - start

        Catalog.resolve = timed_resolve  # type: ignore[method-assign]
        GuardService._evaluate_request = timed_evaluate  # type: ignore[method-assign]

    def uninstall(self) -> None:
        Catalog.resolve = self._resolve  # type: ignore[method-assign]
        GuardService._evaluate_request = self._evaluate  # type: ignore[method-assign]

    def take(self) -> int:
        value, self.current = self.current, 0
        return value


async def measure(root: Path, kind: str, n: int, warmup: int) -> dict[str, object]:
    clock = DecideClock()
    clock.install()
    results: dict[str, dict[str, list[int]]] = {
        path: {"decide": [], "submit": []} for path in ("deny", "ask", "allow")
    }
    try:
        async with harness(root, kind, executable_pin(), []) as run:
            guard, human = run.guard, run.human()
            await guard.submit("task-1", read("private/a.txt", "p0"), expected_epoch=1)
            await guard.submit("task-2", read("internal/a.txt", "p1"), expected_epoch=1)
            for i in range(warmup + n):
                record = i >= warmup
                for path in ("deny", "ask", "allow"):
                    clock.take()
                    start = time.perf_counter_ns()
                    if path == "deny":
                        outcome = await guard.submit(
                            "task-1", send(f"d{i}", to="issue"), expected_epoch=1
                        )
                        assert type(outcome) is Denied
                    elif path == "ask":
                        outcome = await guard.submit("task-2", send(f"a{i}"), expected_epoch=1)
                        assert type(outcome) is Pending
                    else:
                        # 내부 서버의 READ는 누적 등급과 관계없이 ALLOW다(5.1 행렬).
                        outcome = await guard.submit(
                            "task-1", read(f"public/{i}.txt", f"r{i}"), expected_epoch=1
                        )
                        assert type(outcome) is Released
                    elapsed = time.perf_counter_ns() - start
                    decided = clock.take()
                    if path == "ask":
                        await human.reject(outcome.approval_id)
                    if record:
                        results[path]["decide"].append(decided)
                        results[path]["submit"].append(elapsed)
    finally:
        clock.uninstall()
    # 같은 Task에 호출이 쌓일수록 느려지는지 보려고 처음·마지막 50회의 중앙값을 함께 낸다.
    return {
        path: {
            "decide": p50_p95(values["decide"]),
            "submit": p50_p95(values["submit"]),
            "decide_first50_p50_ms": statistics.median(values["decide"][:50]) / 1e6,
            "decide_last50_p50_ms": statistics.median(values["decide"][-50:]) / 1e6,
        }
        for path, values in results.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--kind", choices=("stdio", "http"), default="stdio")
    args = parser.parse_args()
    if sys.platform != "win32":
        print(json.dumps({"skipped": "C2.6 launcher는 Windows 전용이다"}))
        return 0
    with tempfile.TemporaryDirectory(prefix="t28-") as tmp:
        summary = asyncio.run(measure(Path(tmp), args.kind, args.n, args.warmup))
    print(
        json.dumps(
            {
                "probe": "T28",
                "mode": "accumulate",
                "kind": args.kind,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "results": summary,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
