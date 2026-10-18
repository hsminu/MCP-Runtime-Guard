"""SCAN-01·DESC-01 합의 전 제안: 원문을 바꾸지 않는 제한된 표시 텍스트."""

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

_LINK = re.compile(
    r"[a-z][a-z0-9+.-]*\s*://|"
    r"(?:https?|ftp|file|mailto|javascript|data|tel|sms|vbscript)\s*:|"
    r"www\.|[\w.+-]+@[\w.-]+|"
    r"(?:[\w-]+\.)+[^\W\d_]{2,63}\b|"
    r"\[[^\]]*\]\s*(?:\(|\[|:)",
    re.IGNORECASE,
)
_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);", re.IGNORECASE)


def _plain_characters(value: str) -> str:
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} and char not in "\n\t"
        for char in value
    ):
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
    ):
        raise ValueError("검사 설명에는 링크와 HTML을 허용하지 않는다")
    return value


type ThreatSummary = Annotated[str, Field(max_length=300), AfterValidator(_scanner_text)]
type ScanRisk = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_scanner_text)]
type ReviewedDescription = Annotated[str, Field(max_length=2000), AfterValidator(_plain_characters)]
