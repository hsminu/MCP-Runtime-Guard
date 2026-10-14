"""I6/T17: native YARA timeout capability, separate from the Cisco product path.

Run with an external process deadline. This intentionally expensive trusted rule
does not read files, invoke tools, or contain real secrets.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import yara


def main(output):
    source = (
        "rule expensive_fixture { condition: "
        "for any i in (1..1000000000): (uint8(i % filesize) == 255) }"
    )
    rules = yara.compile(source=source)
    start = time.perf_counter()
    timed_out = False
    try:
        rules.match(data=b"a" * 1024, timeout=5)
    except yara.TimeoutError:
        timed_out = True
    evidence = {
        "requested_native_timeout_seconds": 5,
        "elapsed_seconds": time.perf_counter() - start,
        "native_timeout_raised": timed_out,
        "rule_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "input_bytes": 1024,
        "cisco_product_analyzer_used": False,
        "managed_tools_call_count": 0,
    }
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args().output)
