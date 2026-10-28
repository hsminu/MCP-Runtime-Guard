"""security.endpoints(C2.5, URL-01) 단위 테스트. 이름 해석·연결 없이 문자열만 검사한다."""

import socket
from urllib.parse import urlsplit

import httpx
import idna
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from guard_app.security.endpoints import (
    ENDPOINT_REJECT_CODES,
    MAX_URL_LENGTH,
    Endpoint,
    EndpointError,
    canonical_endpoint,
    normalize_endpoint,
)

CANONICAL = [
    "https://example.com/mcp",
    "https://example.com/",
    "https://example.com/v1/mcp/",
    "https://example.com:8443/mcp",
    "https://localhost/mcp",
    "https://10.0.0.5/mcp",
    "https://192.168.0.10:8443/mcp",
    "https://[2001:db8::1]/mcp",
    "https://[2001:db8::1]:8443/mcp",
    "https://[2003::1]/mcp",
    "https://xn--fa-hia.de/mcp",
    "https://a-b.example/x;y=1,z@w:~!$&'()*+-._",
    "http://127.0.0.1:8765/mcp",
    "http://127.0.0.1/mcp",
    "http://127.5.6.7:8765/mcp",
    "http://[::1]:8765/mcp",
]

NORMALIZED = [
    ("HTTPS://Example.COM:443/mcp", "https://example.com/mcp"),
    ("http://127.0.0.1:80/mcp", "http://127.0.0.1/mcp"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com:8443", "https://example.com:8443/"),
    ("http://[0:0:0:0:0:0:0:1]:8765/mcp", "http://[::1]:8765/mcp"),
    ("https://[2001:DB8:0:0::1]/mcp", "https://[2001:db8::1]/mcp"),
    # IDNA 2008. 내장 idna 코덱(IDNA 2003)은 다른 도메인 fass.de를 만든다.
    ("https://faß.de/mcp", "https://xn--fa-hia.de/mcp"),
    ("https://XN--FA-HIA.de/mcp", "https://xn--fa-hia.de/mcp"),
    # 동형 문자(키릴 а)는 xn-- 형태로 드러난다(개발문서 4.3절).
    ("https://my-nаs.home/", "https://xn--my-ns-7ve.home/"),
    ("https://ｅｘａｍｐｌｅ．ｃｏｍ/mcp", "https://example.com/mcp"),
]

REJECTED = [
    # 형식
    (None, "URL_INVALID"),
    (b"https://example.com/", "URL_INVALID"),
    ("", "URL_INVALID"),
    ("https://example.com/" + "a" * MAX_URL_LENGTH, "URL_INVALID"),
    (" https://example.com/", "URL_INVALID"),
    ("https://example.com/m cp", "URL_INVALID"),
    ("https://ev\til.com/", "URL_INVALID"),
    ("https://example.com/\n", "URL_INVALID"),
    ("https://exa\x00mple.com/", "URL_INVALID"),
    ("https://exa\x7fmple.com/", "URL_INVALID"),
    ("https://evil.com\\@good.com/", "URL_INVALID"),
    ("https:\\\\example.com/", "URL_INVALID"),
    ("https:/example.com/", "URL_INVALID"),
    ("example.com/mcp", "URL_INVALID"),
    # scheme
    ("ftp://example.com/", "URL_SCHEME"),
    ("file://example.com/c:/x", "URL_SCHEME"),
    ("ws://example.com/", "URL_SCHEME"),
    ("javascript://example.com/", "URL_SCHEME"),
    ("httpѕ://example.com/", "URL_SCHEME"),
    # 사용자 정보·쿼리·fragment
    ("https://user:pass@example.com/", "URL_USERINFO"),
    ("https://my-nas.home@evil.example/", "URL_USERINFO"),
    ("https://@example.com/", "URL_USERINFO"),
    ("https://example.com/mcp?key=1", "URL_QUERY"),
    ("https://example.com/mcp?", "URL_QUERY"),
    ("https://example.com/mcp#x", "URL_FRAGMENT"),
    ("https://example.com#", "URL_FRAGMENT"),
    # 호스트
    ("https:///mcp", "URL_HOST"),
    ("https://:443/mcp", "URL_HOST"),
    ("https://example.com./mcp", "URL_HOST"),
    ("https://exa%6dple.com/", "URL_HOST"),
    ("https://[fe80::1%25eth0]/", "URL_HOST"),
    ("http://2130706433/", "URL_HOST"),
    ("http://0x7f.1/", "URL_HOST"),
    ("http://0x7f000001/", "URL_HOST"),
    ("http://0X7F.0.0.1/", "URL_HOST"),
    ("http://127.1/", "URL_HOST"),
    ("http://0177.0.0.1/", "URL_HOST"),
    ("http://127.0.0.01/", "URL_HOST"),
    ("http://127.0.0.256/", "URL_HOST"),
    ("https://1.2.3.4.5/", "URL_HOST"),
    ("https://foo.123/", "URL_HOST"),
    ("https://foo.0x/", "URL_HOST"),
    ("https://１２７.０.０.１/", "URL_HOST"),
    ("https://::1/", "URL_HOST"),
    ("https://[::1/", "URL_HOST"),
    ("https://[zz::1]/", "URL_HOST"),
    ("https://[127.0.0.1]/", "URL_HOST"),
    ("https://[::1]x/", "URL_HOST"),
    # 보이지 않는 문자. idna.encode는 UTS #46이 무시하는 문자를 지워 example.com을 만든다.
    ("https://exa\u200bmple.com/", "URL_HOST"),
    ("https://exa\u00admple.com/", "URL_HOST"),
    ("https://exa\u2060mple.com/", "URL_HOST"),
    ("https://exa\ufeffmple.com/", "URL_HOST"),
    ("https://exa\u034fmple.com/", "URL_HOST"),
    ("https://exa\ufe0fmple.com/", "URL_HOST"),
    ("https://exa\u180emple.com/", "URL_HOST"),
    ("https://exa\u3164mple.com/", "URL_HOST"),
    ("https://exa\U000e0101mple.com/", "URL_HOST"),
    ("https://faß\u200b.de/", "URL_HOST"),
    # 서식 문자(Cf)는 지워지지 않아도 거부한다.
    ("https://exa\u200cmple.com/", "URL_HOST"),
    ("https://exa\u200dmple.com/", "URL_HOST"),
    ("https://exa\u202emple.com/", "URL_HOST"),
    # 이름(IDNA 2008)
    ("https://ex!ample.com/", "URL_IDNA"),
    ("https://a_b.example.com/", "URL_IDNA"),
    ("https://\U0001f600.com/", "URL_IDNA"),
    ("https://xn--a.com/", "URL_IDNA"),
    ("https://xn--abc-.com/", "URL_IDNA"),
    ("https://ab--c.com/", "URL_IDNA"),
    ("https://-ab.com/", "URL_IDNA"),
    ("https://a..b/", "URL_IDNA"),
    ("https://" + "a" * 64 + ".com/", "URL_IDNA"),
    ("https://" + ("a" * 63 + ".") * 4 + "com/", "URL_IDNA"),
    ("https://a[b.com/", "URL_IDNA"),
    ("https://a／b.com/", "URL_IDNA"),
    # 포트
    ("https://example.com:0/", "URL_PORT"),
    ("https://example.com:65536/", "URL_PORT"),
    ("https://example.com:080/", "URL_PORT"),
    ("https://example.com:/", "URL_PORT"),
    ("https://example.com:+80/", "URL_PORT"),
    ("https://example.com:-1/", "URL_PORT"),
    ("https://example.com:８０/", "URL_PORT"),
    ("https://example.com:80:90/", "URL_PORT"),
    # 경로
    ("https://example.com/a/../mcp", "URL_PATH"),
    ("https://example.com/./mcp", "URL_PATH"),
    ("https://example.com/..", "URL_PATH"),
    ("https://example.com//mcp", "URL_PATH"),
    ("https://example.com/a//b", "URL_PATH"),
    ("https://example.com/%2e%2e/mcp", "URL_PATH"),
    ("https://example.com/%41", "URL_PATH"),
    ("https://example.com/a%5cb", "URL_PATH"),
    ("https://example.com/mcpé", "URL_PATH"),
    ('https://example.com/a"b', "URL_PATH"),
    ("https://example.com/a<b>", "URL_PATH"),
    ("https://example.com/a|b", "URL_PATH"),
    ("https://example.com/a{b}", "URL_PATH"),
    ("https://example.com/a[b]", "URL_PATH"),
    # 특수 주소
    ("https://0.0.0.0/", "URL_ADDRESS_FORBIDDEN"),
    ("https://0.1.2.3/", "URL_ADDRESS_FORBIDDEN"),
    ("https://169.254.169.254/latest", "URL_ADDRESS_FORBIDDEN"),
    ("https://224.0.0.1/", "URL_ADDRESS_FORBIDDEN"),
    ("https://240.0.0.1/", "URL_ADDRESS_FORBIDDEN"),
    ("https://255.255.255.255/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[::]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[ff02::1]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[fe80::1]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[::ffff:169.254.169.254]/", "URL_ADDRESS_FORBIDDEN"),
    ("http://[::ffff:127.0.0.1]/", "URL_ADDRESS_FORBIDDEN"),
    ("http://[::127.0.0.1]/", "URL_ADDRESS_FORBIDDEN"),
    ("http://[::ffff:0:7f00:1]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[::ffff:0:a9fe:a9fe]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[64:ff9b::a9fe:a9fe]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[64:ff9b:1::a9fe:a9fe]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[2002:a9fe:a9fe::1]/", "URL_ADDRESS_FORBIDDEN"),
    ("https://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/", "URL_ADDRESS_FORBIDDEN"),
    ("http://0.0.0.0:8765/mcp", "URL_ADDRESS_FORBIDDEN"),
    # http는 루프백 IP 리터럴만
    ("http://localhost/mcp", "URL_HTTP_NOT_LOOPBACK"),
    ("http://localhost.:8765/mcp", "URL_HOST"),
    ("http://example.com/", "URL_HTTP_NOT_LOOPBACK"),
    ("http://10.0.0.5/", "URL_HTTP_NOT_LOOPBACK"),
    ("http://[2001:db8::1]/", "URL_HTTP_NOT_LOOPBACK"),
    ("http://xn--fa-hia.de/", "URL_HTTP_NOT_LOOPBACK"),
]


def _rejected(url):
    with pytest.raises(EndpointError) as raised:
        normalize_endpoint(url)
    return raised.value


@pytest.mark.parametrize("url", CANONICAL)
def test_canonical_urls_pass_unchanged(url):
    assert normalize_endpoint(url).url == url
    assert canonical_endpoint(url) == url


@pytest.mark.parametrize(("url", "expected"), NORMALIZED)
def test_variants_normalize_but_are_not_canonical(url, expected):
    assert normalize_endpoint(url).url == expected
    assert canonical_endpoint(expected) == expected
    with pytest.raises(EndpointError) as raised:
        canonical_endpoint(url)
    assert str(raised.value) == "URL_NOT_CANONICAL"


@pytest.mark.parametrize(("url", "code"), REJECTED)
def test_rejections_carry_one_fixed_code(url, code):
    error = _rejected(url)
    assert error.args == (code,)
    with pytest.raises(EndpointError) as raised:
        canonical_endpoint(url)
    assert str(raised.value) == code


def test_every_code_is_reachable():
    codes = {code for _, code in REJECTED} | {"URL_NOT_CANONICAL"}
    assert codes == ENDPOINT_REJECT_CODES


def test_errors_do_not_chain_the_input():
    """I9: idna 오류 메시지는 입력 문자를 담는다. 원인으로도 달지 않는다."""
    error = _rejected("https://secret-\U0001f600.example/")
    assert str(error) == "URL_IDNA"
    assert error.__cause__ is None
    assert error.__suppress_context__


def test_no_character_is_silently_dropped_from_the_host():
    """idna.encode가 호스트에서 지우는 문자는 모두 URL_HOST다. 전체 코드 포인트를 훑는다."""
    dropped = []
    for code_point in range(0x80, 0x110000):
        if 0xD800 <= code_point <= 0xDFFF:
            continue
        char = chr(code_point)
        try:
            if idna.encode(f"a{char}b.example", uts46=True) == b"ab.example":
                dropped.append(char)
        except UnicodeError:
            pass
    assert {"\u00ad", "\u200b", "\ufeff"} <= set(dropped)
    for char in dropped:
        assert str(_rejected(f"https://a{char}b.example/")) == "URL_HOST"


def test_fields_are_the_parsed_parts():
    assert normalize_endpoint("https://[2001:db8::1]/mcp") == Endpoint(
        scheme="https", host="2001:db8::1", port=443, path="/mcp"
    )
    assert normalize_endpoint("http://127.0.0.1:8765/") == Endpoint(
        scheme="http", host="127.0.0.1", port=8765, path="/"
    )


def test_no_name_lookup(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("이름 해석을 하면 안 된다")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)
    assert canonical_endpoint("https://example.com/mcp") == "https://example.com/mcp"
    assert str(_rejected("http://localhost/mcp")) == "URL_HTTP_NOT_LOOPBACK"


_LABELS = st.from_regex(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,8}[a-zA-Z0-9])?", fullmatch=True)
_HOSTS = st.one_of(
    st.lists(_LABELS, min_size=1, max_size=4).map(".".join),
    st.ip_addresses(v=4).map(str),
    st.ip_addresses(v=6).map(lambda address: f"[{address}]"),
    st.sampled_from(["localhost", "0x7f.1", "127.1", "[::1]", "faß.de", "ｅｘａｍｐｌｅ.com"]),
    st.text(min_size=1, max_size=12),
)
_PORTS = st.one_of(
    st.just(""),
    st.integers(min_value=0, max_value=70000).map(lambda port: f":{port}"),
    st.sampled_from([":", ":080", ":+1", ":443", ":80"]),
)
_PATHS = st.one_of(
    st.just(""),
    st.text(alphabet="ab/.%:@;=-_~!$&'()*+,?#[]\\é ", max_size=12).map(lambda path: "/" + path),
)


@st.composite
def _urls(draw):
    scheme = draw(st.sampled_from(["http", "https", "HTTP", "Https", "ftp"]))
    return f"{scheme}://{draw(_HOSTS)}{draw(_PORTS)}{draw(_PATHS)}"


@given(url=st.one_of(_urls(), st.text(max_size=40)))
@settings(max_examples=500)
def test_accepted_urls_mean_the_same_to_httpx_and_urllib(url):
    """우리가 검사한 주소와 transport(httpx)가 실제로 붙는 주소가 갈리지 않는다."""
    rejected = None
    try:
        endpoint = normalize_endpoint(url)
    except EndpointError as error:
        rejected = error.args[0]
    if rejected is not None:
        assert rejected in ENDPOINT_REJECT_CODES
        return
    canonical = endpoint.url
    assert normalize_endpoint(canonical) == endpoint
    assert canonical_endpoint(canonical) == canonical
    default = 443 if endpoint.scheme == "https" else 80

    parsed = httpx.URL(canonical)
    assert parsed.scheme == endpoint.scheme
    assert parsed.raw_host.decode("ascii") == endpoint.host
    assert (parsed.port or default) == endpoint.port
    assert parsed.raw_path.decode("ascii") == endpoint.path
    assert not parsed.userinfo

    split = urlsplit(canonical)
    assert split.hostname == endpoint.host
    assert (split.port or default) == endpoint.port
    assert split.path == endpoint.path
    assert (split.username, split.query, split.fragment) == (None, "", "")
