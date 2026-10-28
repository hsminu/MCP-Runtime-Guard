"""ID·토큰 생성의 단일 지점(C2.7, 개발문서 13.1). 운영체제 암호학적 난수(`secrets`)만 쓴다.

- `new_id(prefix)`: `<prefix>-<소문자 16진수 32자리>`(128비트). 요청·승인·감사·Task·검사처럼
  저장되고 다른 행과 이어지는 식별자다. 지금 저장된 ID 모양(`request-…`, `approval-…`)과 같다.
- `new_suffix()`: 접두어 없는 16진수 32자리. 임시 파일·로그 파일 이름처럼 이름 규칙이 따로 있는
  곳에 붙인다. 앱 시작 정리 규칙(`paths.SETTINGS_TEMP` 등)이 이 모양을 기대한다.
- `new_token(nbytes)`: URL에 넣을 수 있는 비밀 토큰. 실행마다 새로 만드는 인증값
  (L2 가짜 모델 등)이다.
- `token_matches(expected, received)`: 비밀 토큰을 일정 시간에 비교한다.

`random` 모듈은 쓰지 않는다. 다른 모듈의 `random.*`과 이 모듈 밖의 직접 생성은 C2.9·C6.3에서
Semgrep으로 막는다. 그 전까지는 `tests/unit/test_ids.py`가 C 모듈을 검사한다.
"""

from __future__ import annotations

import re
import secrets

_PREFIX = re.compile(r"[a-z][a-z0-9]{0,31}")
_ID_BYTES = 16
MIN_TOKEN_BYTES = 16
MAX_TOKEN_BYTES = 64


def new_id(prefix: str) -> str:
    """`<prefix>-<32자리 16진수>`. prefix는 소문자로 시작하는 소문자·숫자 1–32자다."""
    if type(prefix) is not str or not _PREFIX.fullmatch(prefix):
        raise ValueError("ID 접두어가 유효하지 않다")
    return f"{prefix}-{secrets.token_hex(_ID_BYTES)}"


def new_suffix() -> str:
    """접두어 없는 소문자 16진수 32자리(128비트)."""
    return secrets.token_hex(_ID_BYTES)


def new_token(nbytes: int = 32) -> str:
    """URL 안전 base64 비밀 토큰. nbytes는 16–64바이트다."""
    if type(nbytes) is not int or not MIN_TOKEN_BYTES <= nbytes <= MAX_TOKEN_BYTES:
        raise ValueError("토큰 길이가 유효하지 않다")
    return secrets.token_urlsafe(nbytes)


def token_matches(expected: str, received: object) -> bool:
    """비밀 토큰 비교. 내용에 대해 일정 시간이다(길이는 드러날 수 있다). 문자열이 아니면 False다."""
    if type(expected) is not str or not expected or type(received) is not str:
        return False
    return secrets.compare_digest(expected.encode("utf-8"), received.encode("utf-8"))
