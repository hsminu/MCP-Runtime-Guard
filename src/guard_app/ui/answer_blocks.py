"""Small display-only block parser; input has already been visualized (T18)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Block:
    kind: str
    text: str
    level: int = 0
    marker: str = ""
    language: str = ""


def _inline(text: str) -> str:
    return re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", text)


def parse_blocks(text: str) -> tuple[Block, ...]:
    """Recognize blocks without HTML, links, escapes or Unicode interpretation."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    lines = text.split("\n")
    index = 0

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", _inline("\n".join(paragraph))))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence = re.fullmatch(r"(`{3,}|~{3,})(.*)", stripped)
        heading = re.fullmatch(r"(#{1,6})\s+(.*)", line)
        item = re.fullmatch(r"( *)([-*+]|\d+\.)\s+(.*)", line)
        if fence:
            flush()
            mark, language = fence.groups()
            code: list[str] = []
            index += 1
            while index < len(lines):
                if re.fullmatch(
                    re.escape(mark[0]) + "{" + str(len(mark)) + r",}\s*", lines[index].strip()
                ):
                    break
                code.append(lines[index])
                index += 1
            blocks.append(Block("code", "\n".join(code), language=language.strip()))
        elif not stripped:
            flush()
        elif stripped in ("---", "***"):
            flush()
            blocks.append(Block("rule", stripped))
        elif heading:
            flush()
            blocks.append(Block("heading", _inline(heading[2]), level=len(heading[1])))
        elif item:
            flush()
            blocks.append(
                Block(
                    "list",
                    _inline(item[3]),
                    level=len(item[1]) // 2,
                    marker=item[2] if item[2].endswith(".") else "•",
                )
            )
        elif line.startswith(">"):
            flush()
            blocks.append(Block("quote", _inline(line[1:].removeprefix(" "))))
        elif line.startswith("|"):
            flush()
            table = [line]
            while index + 1 < len(lines) and lines[index + 1].startswith("|"):
                index += 1
                table.append(lines[index])
            blocks.append(Block("table", "\n".join(table)))
        else:
            paragraph.append(line)
        index += 1
    flush()
    return tuple(blocks)
