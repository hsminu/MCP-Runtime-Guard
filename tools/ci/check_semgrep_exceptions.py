"""Semgrep 예외가 약점 대장 SG-01–04와 설계 경계에 정확히 같은지 본다(C2.9).

CI가 `semgrep scan --disable-nosem --json src` 결과를 표준 입력으로 넘긴다. 예외 주석을 끈
검사가 정확히 아래 위치만 잡아야 한다. 위치는 규칙·파일·결과를 감싼 함수(클래스 안이면
`클래스.함수`)다. 그래서 같은 파일의 다른 함수로 옮겨 간 예외도 잡는다(B77-N1). 줄 번호는 보지
않아 코드가 위아래로 움직여도 그대로다. 규칙이 실제 코드에서 걸린다는 것과, 그 밖의 결과는
`# nosemgrep` 없이 0건이라는 것을 함께 확인한다. 출력에는 규칙·경로·함수 이름만 쓴다.
Windows에서 나오는 `\\` 경로는 `/`로 바꿔 읽는다.
"""

import ast
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

EXPECTED = Counter(
    {
        (
            "broad-except-only-in-allowed",
            "src/guard_app/storage/sqlite_writer.py",
            "SQLiteWriter._execute",
        ): 1,  # SG-01
        (
            "no-swallow",
            "src/guard_app/storage/sqlite_store.py",
            "SQLiteStore._transaction",
        ): 1,  # SG-02
        ("no-swallow", "src/guard_app/domain/text.py", "defang_scanner_text"): 1,  # SG-03
        ("no-swallow", "src/guard_app/security/paths.py", "BackupTarget.discard"): 1,  # SG-04
        # 설계 경계(AGENTS): fail_closed 2개·after_dispatch 3개의 try, ServerWorker._execute
        ("broad-except-only-in-allowed", "src/guard_app/security/failclosed.py", "fail_closed"): 2,
        (
            "broad-except-only-in-allowed",
            "src/guard_app/security/failclosed.py",
            "after_dispatch",
        ): 3,
        (
            "broad-except-only-in-allowed",
            "src/guard_app/adapters/mcp_transport/worker.py",
            "ServerWorker._execute",
        ): 1,
    }
)


def enclosing(source: str, line: int) -> str:
    """`line`을 감싼 가장 안쪽 함수·클래스의 점 이름. 어디에도 들지 않으면 `<module>`이다."""
    names: list[str] = []

    def visit(node: ast.AST, stack: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if child.lineno <= line <= (child.end_lineno or child.lineno):
                    names[:] = [*stack, child.name]
                    visit(child, (*stack, child.name))
            else:
                visit(child, stack)

    visit(ast.parse(source), ())
    return ".".join(names) or "<module>"


def check(report: dict[str, Any], read: Callable[[str], str]) -> tuple[list[str], int]:
    """출력할 줄과 결과 수. 줄이 없으면 예외 위치가 기대와 정확히 같다."""
    if report.get("errors"):
        return [f"SEMGREP_ERRORS {len(report['errors'])}"], 0
    sources: dict[str, str] = {}
    found: Counter[tuple[str, str, str]] = Counter()
    for result in report["results"]:
        path = result["path"].replace("\\", "/")
        if path not in sources:
            sources[path] = read(path)
        owner = enclosing(sources[path], result["start"]["line"])
        found[(result["check_id"].rsplit(".", 1)[-1], path, owner)] += 1
    lines = [
        f"UNEXPECTED {rule} {path} {owner} x{count}"
        for (rule, path, owner), count in sorted((found - EXPECTED).items())
    ]
    lines += [
        f"MISSING {rule} {path} {owner} x{count}"
        for (rule, path, owner), count in sorted((EXPECTED - found).items())
    ]
    return lines, sum(found.values())


def main() -> int:
    lines, count = check(json.load(sys.stdin), lambda path: Path(path).read_text(encoding="utf-8"))
    for line in lines:
        print(line)
    if lines:
        return 1
    print(f"SEMGREP_EXCEPTIONS_OK {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
