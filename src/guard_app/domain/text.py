"""SCAN-01·DESC-01 합의 전 제안: 거부 검증과 호출자가 고르는 무해화를 분리한다."""

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

_LINK = re.compile(
    r"\b[a-z][a-z0-9+.-]*\s*://|"
    r"\b(?:https?|ftp|file|mailto|javascript|data|tel|sms|vbscript)\s*:(?=\S)|"
    r"\bwww\.|[\w.+-]+@[\w.-]+",
    re.IGNORECASE,
)
_DOMAIN = re.compile(
    r"(?<![\w@-])(?:[\w-]+\.)+"
    r"(?P<suffix>(?ai:[a-z]{2,63}|xn--[a-z0-9-]{2,59})|한국|中国|中國|рф)"
    r"(?![\w-])",
    re.IGNORECASE,
)
# TLD와 확장자를 완전히 구별하지는 않는다. 모호한 .com/.zip/.app/.py 등은 예외로 두지 않는다.
_FILE_SUFFIXES = frozenset(
    {"json", "yaml", "yml", "toml", "ini", "cfg", "conf", "txt", "csv", "xml", "log", "path"}
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\s*(?:\(|\[|:)")
_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);", re.IGNORECASE)
_HTTP_SCHEME = re.compile(r"\bhttps?(?=\s*:)", re.IGNORECASE)
_DEFANG_CHARACTERS = {
    "<": "‹",
    ">": "›",
    ".": "[.]",
    "@": "[@]",
    ":": "[:]",
    "&": "[&]",
    "[": "⟦",
    "]": "⟧",
}


def _forbidden_character(char: str) -> bool:
    return unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} and char not in "\n\t"


def _plain_characters(value: str) -> str:
    if any(_forbidden_character(char) for char in value):
        raise ValueError("표시 텍스트에 허용하지 않는 문자가 있다")
    return value


def _scanner_text(value: str) -> str:
    _plain_characters(value)
    # 정규화는 검사에만 사용한다. 승인 없이 절단·치환한 결과로 바꾸지 않는다.
    inspected = unicodedata.normalize("NFKC", value).replace("\u3002", ".")
    if (
        "<" in inspected
        or ">" in inspected
        or _HTML_ENTITY.search(inspected)
        or _LINK.search(inspected)
        or _MARKDOWN_LINK.search(inspected)
        or any(
            match["suffix"].casefold() not in _FILE_SUFFIXES
            for match in _DOMAIN.finditer(inspected)
        )
    ):
        raise ValueError("검사 설명에는 링크와 HTML을 허용하지 않는다")
    return value


def _truncate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else value[: max_length - 1] + "…"


def defang_scanner_text(value: str, *, max_length: int = 300) -> str:
    """SCAN-01 합의 전 제안: C가 명시적으로 선택하는 표시 전용 무해화.

    상한은 정확한 정수 1–300이다. 안전한 상한 이내 텍스트는 그대로 보존한다.
    위험 구문은 NFKC 검사 형태에서 꺾쇠·주소·엔티티·Markdown을 무력화하고,
    금지 문자는 공백으로 바꾼다. 절단한 결과는 …를 포함해 상한 이하다.
    반환값은 ThreatSummary로 검증 가능하다. 원문/결과를 로그에 남기지 않는다.
    ScanRisk에는 max_length=64로 호출하고 빈 반환은 호출자가 None으로 처리한다.
    C는 그래도 번역이 실패하면 summary만 비우고 기존 finding·실패 사실은 보존한다.
    """
    if type(value) is not str:
        raise ValueError("검사 표시 입력은 문자열이어야 한다")
    if type(max_length) is not int or not 1 <= max_length <= 300:
        raise ValueError("검사 표시 상한은 1부터 300까지의 정수여야 한다")
    candidate = _truncate(value, max_length)
    try:
        return _scanner_text(candidate)
    except ValueError:
        pass
    normalized = unicodedata.normalize("NFKC", value).replace("\u3002", ".")
    normalized = _HTTP_SCHEME.sub(
        lambda match: "hxxps" if match[0].casefold() == "https" else "hxxp", normalized
    )
    candidate = _truncate(
        "".join(
            " " if _forbidden_character(char) else _DEFANG_CHARACTERS.get(char, char)
            for char in normalized
        ),
        max_length,
    )
    # [.] 뒤의 ( 또는 [가 Markdown 링크처럼 보이는 결합도 무력화한다.
    if _MARKDOWN_LINK.search(candidate):
        candidate = candidate.replace("[", "⟦").replace("]", "⟧")
    return _scanner_text(candidate)


type ThreatSummary = Annotated[str, Field(max_length=300), AfterValidator(_scanner_text)]
type ScanRisk = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_scanner_text)]
type ReviewedDescription = Annotated[str, Field(max_length=2000), AfterValidator(_plain_characters)]
