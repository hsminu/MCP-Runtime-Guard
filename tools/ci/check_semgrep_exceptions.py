"""Semgrep 예외가 약점 대장 SG-01–04와 설계 경계에 정확히 같은지 본다(C2.9).

CI가 `semgrep scan --disable-nosem --json src` 결과를 표준 입력으로 넘긴다. 예외 주석을 끈
검사가 정확히 아래 위치만 잡아야 한다. 규칙이 실제 코드에서 걸린다는 것과, 그 밖의 결과는
`# nosemgrep` 없이 0건이라는 것을 함께 확인한다. 출력에는 규칙과 경로만 쓴다.
"""

import json
import sys
from collections import Counter

EXPECTED = Counter(
    {
        ("broad-except-only-in-allowed", "src/guard_app/storage/sqlite_writer.py"): 1,  # SG-01
        ("no-swallow", "src/guard_app/storage/sqlite_store.py"): 1,  # SG-02
        ("no-swallow", "src/guard_app/domain/text.py"): 1,  # SG-03
        ("no-swallow", "src/guard_app/security/paths.py"): 1,  # SG-04
        # 설계 경계(AGENTS): fail_closed 2개·after_dispatch 3개의 try, ServerWorker._execute
        ("broad-except-only-in-allowed", "src/guard_app/security/failclosed.py"): 5,
        ("broad-except-only-in-allowed", "src/guard_app/adapters/mcp_transport/worker.py"): 1,
    }
)


def main() -> int:
    report = json.load(sys.stdin)
    if report.get("errors"):
        print(f"SEMGREP_ERRORS {len(report['errors'])}")
        return 1
    found = Counter(
        (result["check_id"].rsplit(".", 1)[-1], result["path"]) for result in report["results"]
    )
    for (rule, path), count in sorted((found - EXPECTED).items()):
        print(f"UNEXPECTED {rule} {path} x{count}")
    for (rule, path), count in sorted((EXPECTED - found).items()):
        print(f"MISSING {rule} {path} x{count}")
    if found != EXPECTED:
        return 1
    print(f"SEMGREP_EXCEPTIONS_OK {sum(found.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
