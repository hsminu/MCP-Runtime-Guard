"""C4.14: YARA 도구별 native 5초 상한과 전용 작업자. 실제 yara-python으로 느린 규칙을 돌린다.

SDK 4.8.6 `YaraAnalyzer`는 `match`에 시간 제한을 넘기지 않는다. C가 규칙 객체를 감싸 넘긴다.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import yara

from guard_app.adapters.cisco import PreparedToolDefinition
from guard_app.adapters.cisco_aggregation import (
    YARA_TIMEOUT_S,
    ScannerAggregationError,
    _ThreadedYara,
    _TimedRules,
)

# 64KB 입력에서 수십억 번 도는 조건. 시간 제한이 없으면 사실상 끝나지 않는다.
SLOW_RULE = (
    'rule slow { strings: $a = "a" condition: '
    "for all i in (1..#a) : (for any j in (1..#a) : (@a[j] > @a[i] + 1000000)) }"
)


def test_default_limit_is_the_decided_five_seconds():
    assert YARA_TIMEOUT_S == 5


def test_timed_rules_stop_a_slow_native_match():
    rules = _TimedRules(yara.compile(source=SLOW_RULE), 1)
    started = time.perf_counter()
    with pytest.raises(yara.TimeoutError):
        rules.match(data=b"a" * 65536)
    assert time.perf_counter() - started < 3.0
    # 짧은 입력은 그대로 끝난다.
    assert rules.match(data=b"a" * 64) == []


def test_timed_rules_override_a_caller_timeout():
    seen = {}

    class Native:
        def match(self, *args, **kwargs):
            seen.update(kwargs)
            return []

    _TimedRules(Native(), 5).match(data=b"x", timeout=600)
    assert seen == {"data": b"x", "timeout": 5}


class _SlowAdapter:
    """SDK 분석기처럼 감싼 규칙의 `match`를 동기로 부른다."""

    def __init__(self, rules):
        self.rules = rules

    async def analyze_prepared(self, prepared):
        self.rules.match(data=prepared.definition_json.encode())
        return []


def test_timeout_becomes_the_fixed_code_on_the_given_worker():
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guard-yara")
    names = []

    class Recording(_SlowAdapter):
        async def analyze_prepared(self, prepared):
            names.append(threading.current_thread().name)
            return await super().analyze_prepared(prepared)

    rules = _TimedRules(yara.compile(source=SLOW_RULE), 1)
    provider = _ThreadedYara(Recording(rules), yara.Error, yara.TimeoutError, worker)
    prepared = PreparedToolDefinition("a" * 65536)
    try:
        with pytest.raises(ScannerAggregationError) as raised:
            asyncio.run(provider.analyze_prepared(prepared))
    finally:
        worker.shutdown(wait=True)
    assert str(raised.value) == "SCAN_YARA_TIMEOUT"
    assert names[0].startswith("guard-yara")
