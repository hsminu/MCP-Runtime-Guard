"""HTTP MCP 서버 URL 검증과 정규형(C2.5, URL-01). 오류에 입력 원문을 싣지 않는다.

설정 파일 서버 레코드의 `url`(CFG-01 `ServerSettings.url`)과 transport가 붙을 주소는 이 모듈의
정규형 문자열 하나로 정한다. 등록 화면은 `normalize_endpoint`로 정규형을 만들어 보여 주고
저장한다. 설정 로더와 transport는 `canonical_endpoint`로 정규형만 받고 조용히 고쳐 쓰지 않는다.

- **scheme:** https가 원칙이다. http는 루프백 IP 리터럴(`127.0.0.0/8`, `[::1]`)에만 허용하고
  `localhost` 같은 이름은 받지 않는다.
- **거부:** 사용자 정보, 쿼리, fragment, 끝 점, `%`(퍼센트 인코딩 호스트·경로, IPv6 zone),
  호스트의 보이지 않는 문자(서식 문자, UTS #46이 지우는 문자), 숫자 IP 변형(`0x7f.1`,
  `2130706433`, `127.1`, `0177.0.0.1`), IPv4를 담은 IPv6(매핑·호환·SIIT·NAT64·6to4·Teredo),
  미지정·멀티캐스트·링크 로컬(169.254.x.x 메타데이터 포함)·예약 주소, 경로의 `.`·`..`·빈 세그먼트.
- **정규화:** scheme·호스트 소문자, 이름은 IDNA 2008(UTS #46, `idna` 패키지)의 A-label, IPv6는
  압축형, 기본 포트(80·443) 생략, 빈 경로는 `/`.

`urllib.parse`를 쓰지 않고 직접 나눈다. `urlsplit`은 탭·줄바꿈을 조용히 지우고, 파이썬 내장
`idna` 코덱은 IDNA 2003이라 `faß.de`를 다른 도메인인 `fass.de`로 바꾼다. 이름 해석·연결은 하지
않는다. 신뢰 판단은 등록 여부로만 한다(개발문서 4.3절).
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass

import idna

MAX_URL_LENGTH = 2048

ENDPOINT_REJECT_CODES = frozenset(
    {
        "URL_INVALID",
        "URL_SCHEME",
        "URL_USERINFO",
        "URL_QUERY",
        "URL_FRAGMENT",
        "URL_HOST",
        "URL_IDNA",
        "URL_PORT",
        "URL_PATH",
        "URL_ADDRESS_FORBIDDEN",
        "URL_HTTP_NOT_LOOPBACK",
        "URL_NOT_CANONICAL",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_PORT = re.compile(r"[1-9][0-9]{0,4}")
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
_IPV4 = re.compile(rf"{_OCTET}(?:\.{_OCTET}){{3}}")
# WHATWG URL은 마지막 라벨이 숫자(10진·0x 16진)인 호스트를 IPv4로 해석한다.
_NUMERIC_LABEL = re.compile(r"[0-9]+|0[xX][0-9A-Fa-f]*")
_LDH_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# RFC 3986 경로 문자에서 `%`를 뺐다. 인코딩 우회와 이중 디코딩을 따로 따지지 않기 위해서다.
_PATH = re.compile(r"/[A-Za-z0-9\-._~!$&'()*+,;=:@/]*")
_THIS_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")
# IPv4를 담는 IPv6 형태. 안의 IPv4로 특수 주소·루프백 검사를 우회하지 못하게 모두 막는다.
_IPV4_EMBEDDING = tuple(
    ipaddress.IPv6Network(network)
    for network in (
        "::/96",  # 호환(::a.b.c.d). ::1은 따로 허용
        "::ffff:0:0/96",  # 매핑
        "::ffff:0:0:0/96",  # 변환(SIIT)
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # NAT64 로컬
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)
_IPV6_LOOPBACK = ipaddress.IPv6Address("::1")


class EndpointError(ValueError):
    """입력 원문을 공개하지 않는 고정 거부 코드(`ENDPOINT_REJECT_CODES`)."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """`normalize_endpoint`가 만든 정규형. host는 ASCII 소문자 이름·점 십진 IPv4·압축 IPv6다."""

    scheme: str
    host: str
    port: int
    path: str

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        port = "" if self.port == _DEFAULT_PORTS[self.scheme] else f":{self.port}"
        return f"{self.scheme}://{host}{port}{self.path}"


def normalize_endpoint(url: object) -> Endpoint:
    """URL-01 규칙으로 검사하고 정규형을 돌려준다. 등록 화면이 저장 전에 쓴다."""
    if (
        type(url) is not str
        or not 1 <= len(url) <= MAX_URL_LENGTH
        or any(ord(char) <= 0x20 or ord(char) == 0x7F or char == "\\" for char in url)
    ):
        raise EndpointError("URL_INVALID")
    scheme_text, separator, rest = url.partition("://")
    if not separator:
        raise EndpointError("URL_INVALID")
    scheme = scheme_text.lower()
    if not scheme_text.isascii() or scheme not in _DEFAULT_PORTS:
        raise EndpointError("URL_SCHEME")
    if "#" in rest:
        raise EndpointError("URL_FRAGMENT")
    if "?" in rest:
        raise EndpointError("URL_QUERY")
    authority, slash, path_rest = rest.partition("/")
    if "@" in authority:
        raise EndpointError("URL_USERINFO")
    if "%" in authority:
        raise EndpointError("URL_HOST")
    host: ipaddress.IPv4Address | ipaddress.IPv6Address | str
    if authority.startswith("["):
        host_text, bracket, port_part = authority[1:].partition("]")
        if not bracket:
            raise EndpointError("URL_HOST")
        try:
            host = ipaddress.IPv6Address(host_text)
        except ValueError:
            raise EndpointError("URL_HOST") from None
    else:
        host_text, colon, port_digits = authority.partition(":")
        port_part = colon + port_digits
        host = _host(host_text)
    port = _port(port_part, scheme)
    if not isinstance(host, str):
        _check_address(host)
    if scheme == "http" and not (
        (isinstance(host, ipaddress.IPv4Address) and host.is_loopback) or host == _IPV6_LOOPBACK
    ):
        raise EndpointError("URL_HTTP_NOT_LOOPBACK")
    path = "/" + path_rest if slash else "/"
    segments = path[1:].split("/")
    if (
        not _PATH.fullmatch(path)
        or any(segment in (".", "..") for segment in segments)
        or "" in segments[:-1]
    ):
        raise EndpointError("URL_PATH")
    host_string = host.compressed if isinstance(host, ipaddress.IPv6Address) else str(host)
    return Endpoint(scheme=scheme, host=host_string, port=port, path=path)


def canonical_endpoint(url: object) -> str:
    """정규형 문자열만 통과시킨다. 설정 로더와 transport가 쓴다."""
    endpoint = normalize_endpoint(url)
    if endpoint.url != url:
        raise EndpointError("URL_NOT_CANONICAL")
    return endpoint.url


def _host(text: str) -> ipaddress.IPv4Address | str:
    if not text or text.endswith("."):
        raise EndpointError("URL_HOST")
    if text.isascii() and _ends_in_number(text):
        if not _IPV4.fullmatch(text):
            raise EndpointError("URL_HOST")
        return ipaddress.IPv4Address(text)
    # idna.encode는 UTS #46이 무시하는 문자(U+00AD, U+200B 등)를 조용히 지운다.
    if any(_invisible(char) for char in text if not char.isascii()):
        raise EndpointError("URL_HOST")
    try:
        name = idna.encode(text, uts46=True).decode("ascii")
    except UnicodeError:
        # idna 오류 메시지는 입력 문자를 담는다.
        raise EndpointError("URL_IDNA") from None
    # 전각 숫자·마침표처럼 UTS #46 매핑 뒤에야 숫자 호스트·끝 점이 되는 입력도 거부한다.
    if (
        name.endswith(".")
        or _ends_in_number(name)
        or len(name) > 253
        or not all(_LDH_LABEL.fullmatch(label) for label in name.split("."))
    ):
        raise EndpointError("URL_HOST")
    return name


def _invisible(char: str) -> bool:
    if unicodedata.category(char) == "Cf":
        return True
    try:
        return idna.uts46_remap(char, std3_rules=False, transitional=False) == ""
    except idna.IDNAError:
        # 금지 문자다. 뒤의 idna.encode가 URL_IDNA로 거부한다.
        return False


def _ends_in_number(host: str) -> bool:
    return _NUMERIC_LABEL.fullmatch(host.rpartition(".")[2]) is not None


def _port(part: str, scheme: str) -> int:
    if not part:
        return _DEFAULT_PORTS[scheme]
    if not part.startswith(":"):
        raise EndpointError("URL_HOST")
    digits = part[1:]
    if not _PORT.fullmatch(digits) or int(digits) > 65535:
        raise EndpointError("URL_PORT")
    return int(digits)


def _check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        raise EndpointError("URL_ADDRESS_FORBIDDEN")
    if isinstance(address, ipaddress.IPv4Address):
        # is_reserved는 240.0.0.0/4(255.255.255.255 포함)다.
        if address.is_reserved or address in _THIS_NETWORK:
            raise EndpointError("URL_ADDRESS_FORBIDDEN")
    # IPv6의 is_reserved는 ::1을 포함하는 ::/8이라 쓰지 않는다. IPv4를 담은 형태만 막는다.
    elif address != _IPV6_LOOPBACK and any(address in network for network in _IPV4_EMBEDDING):
        raise EndpointError("URL_ADDRESS_FORBIDDEN")
