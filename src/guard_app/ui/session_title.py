"""Memory-only title derivation; never log or persist message text."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_SENSITIVE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk[-_]|rk_|pk_|gh[pous]_|github_pat_|glpat-|xox[abprs]-)"
    r"|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\."
    r"|-----BEGIN"
    r"|(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|client[_-]?secret|authorization|bearer|비밀번호|암호|토큰|인증키|api ?키)\s*[:=]"
    r"|Bearer\s+\S+"
    r"|[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+"
    r"|\d(?:[\s-]*\d){8,}"
    r"|://[^\s/:@]+:[^\s/@]+@",
    re.IGNORECASE,
)
_LONG_WORD = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_MARKDOWN = re.compile(r"^(?:`{3,}|#{1,6}|>|[-*+]|\d+\.)\s*")


def _clean(text: str) -> str:
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(text.split())


def clean_title(text: str) -> str:
    """Remove controls/formatting, collapse whitespace, and cap at 80 characters."""
    return _clean(text)[:80].rstrip()


def auto_title(text: str, now: datetime) -> str:
    """Use the first nonempty line unless the whole message resembles sensitive data."""
    fallback = f"작업 {now:%H:%M}"
    # Also inspect stripped controls so invisible formatting cannot split a token.
    if any(_SENSITIVE.search(value) for value in (text, _clean(text))):
        return fallback
    for match in _LONG_WORD.finditer(_clean(text)):
        word = match.group()
        if re.search(r"[A-Za-z]", word) and re.search(r"[0-9]", word):
            return fallback
    title = next((_clean(line) for line in text.splitlines() if _clean(line)), "")
    while _MARKDOWN.match(title):
        title = _MARKDOWN.sub("", title, count=1)
    if not re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9]", title):
        return fallback
    if len(title) > 28:
        shortened = title[:27]
        if " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0]
        title = shortened.rstrip() + "…"
    return title
