"""services.registry(C4.12) 단위 테스트: 도구 지문 계산과 SDK 경로 고정."""

import asyncio
import hashlib
import json

import pytest
from mcp.types import ListToolsResult

from guard_app.adapters.mcp_transport import ToolListing, ToolListLimits
from guard_app.adapters.mcp_transport.tool_listing import collect_tool_definitions
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.security.digest import tool_fingerprint
from guard_app.services.registry import RegistryError, listing_fingerprints

# 서버가 보낸 tools/list 응답 JSON(합성). FP-01 실측에서 본 경우를 담았다.
RAW_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    # 모르는 annotations 키는 남고, SDK가 readOnlyHint 1을 true로 바꾼다.
    {
        "name": "list_dir",
        "inputSchema": {"type": "object"},
        "annotations": {"title": "List", "readOnlyHint": 1, "vendorHint": "x"},
    },
    {
        "name": "send_mail",
        "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}}},
        "outputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
        "_meta": {"vendor/x": {"v": 2}},
    },
    # 스키마 안의 실수: JCS로 0.1, 1, 1e+21이 된다.
    {
        "name": "set_rate",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rate": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1e21}
            },
        },
    },
    {
        "name": "summarize",
        "description": "한글 설명과   줄 구분 문자",
        "inputSchema": {"type": "object"},
    },
    # 명시적 null과 icons·title
    {
        "name": "fetch",
        "title": "Fetch",
        "description": None,
        "inputSchema": {"type": "object"},
        "icons": [{"src": "https://example.invalid/i.png", "mimeType": "image/png"}],
    },
    # 모르는 최상위 필드. 배열 순서도 지문에 들어간다.
    {
        "name": "x_extra",
        "inputSchema": {"type": "object"},
        "vendorField": {"a": [3, 2, 1]},
    },
]

# mcp 1.29.0(uv.lock)에서 계산한 값. 이 테스트가 깨지면 SDK 파싱이나 A transport의 변환,
# B의 JCS가 바뀐 것이다. 그러면 등록된 모든 서버의 지문이 달라져 재등록 대기(PENDING)가 된다.
# 원인을 기록에 남긴 뒤에만 값을 바꾼다(FP-01, C4.12).
PINNED = {
    "read_file": "ca14907d97445b04f178cdb81293bb1b8a67a1b9c39d690dc874c19c81239915",
    "list_dir": "24b7399a2038d0c0b5bceef73cda64adbd9cd746b412ce88b706e1481593f2d3",
    "send_mail": "7db1a72512414f02486a63b8c199bb9ba7b8def13009e2593d7e2e2af6338492",
    "set_rate": "7c933657fbb44f28b076d978a15b1ab168fa9d36abc847df8b1a10dec266bcaa",
    "summarize": "4f3e110ed349c6b2b7d260ace1988771be61c99d6a7dafd2a6a5de058268ea45",
    "fetch": "24cec630fbf3c540b50c21445b2f4406f12a02f7402167a8f6471bf67581703b",
    "x_extra": "1db249899bf20ef67bc5c8659051e758dac6fc9acf8e6fb9103119733b3486ea",
}


class _Session:
    """tools/list 한 페이지를 돌려주는 대역. 실제 클라이언트처럼 응답 JSON을 SDK로 파싱한다."""

    def __init__(self, raw_tools: list[dict[str, object]]) -> None:
        wire = json.loads(json.dumps({"tools": raw_tools}))
        self._page = ListToolsResult.model_validate(wire)

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        return self._page


def _listing(raw_tools: list[dict[str, object]]) -> ToolListing:
    return asyncio.run(collect_tool_definitions(_Session(raw_tools), ToolListLimits()))


def test_sdk_path_fingerprints_stay_pinned():
    listing = _listing(RAW_TOOLS)
    assert listing.complete
    assert listing_fingerprints(listing) == PINNED


def test_pins_match_hand_written_canonical_json():
    """B의 JCS와 독립으로 쓴 정규형. 실수 표기와 SDK 변환(1 → true)을 함께 확인한다."""
    canonical = {
        "set_rate": '{"inputSchema":{"properties":{"rate":{"default":1e+21,"maximum":1,'
        '"minimum":0.1,"type":"number"}},"type":"object"},"name":"set_rate"}',
        "list_dir": '{"annotations":{"readOnlyHint":true,"title":"List","vendorHint":"x"},'
        '"inputSchema":{"type":"object"},"name":"list_dir"}',
    }
    for name, text in canonical.items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == PINNED[name]


def test_same_computation_as_the_live_comparison():
    """A `live_fingerprints`가 도구마다 계산하는 값과 같다."""
    listing = _listing(RAW_TOOLS)
    expected = {
        definition["name"]: tool_fingerprint(definition.to_dict()) for definition in listing.tools
    }
    assert listing_fingerprints(listing) == expected


def test_key_order_does_not_change_the_fingerprint():
    first = RAW_TOOLS[0]
    reordered = {key: first[key] for key in reversed(list(first))}
    assert listing_fingerprints(_listing([reordered])) == {"read_file": PINNED["read_file"]}


def test_array_order_and_one_character_do_change_it():
    changed = json.loads(json.dumps(RAW_TOOLS[6]))
    changed["vendorField"]["a"] = [1, 2, 3]
    edited = json.loads(json.dumps(RAW_TOOLS[0]))
    edited["description"] += "."
    result = listing_fingerprints(_listing([changed, edited]))
    assert result["x_extra"] != PINNED["x_extra"]
    assert result["read_file"] != PINNED["read_file"]


def _frozen(*definitions: dict[str, object]) -> tuple[FrozenJsonObject, ...]:
    return tuple(FrozenJsonObject(definition) for definition in definitions)


@pytest.mark.parametrize(
    ("listing", "code"),
    [
        (
            ToolListing(complete=False, tools=(), reason="TOOLS_LIST_TIMEOUT"),
            "REG_LISTING_INCOMPLETE",
        ),
        (None, "REG_LISTING_INVALID"),
        (ToolListing(complete=True, tools=({"name": "x"},), reason=None), "REG_LISTING_INVALID"),
        (
            ToolListing(complete=True, tools=_frozen({"inputSchema": {}}), reason=None),
            "REG_TOOL_NAME_INVALID",
        ),
        (
            ToolListing(complete=True, tools=_frozen({"name": ""}), reason=None),
            "REG_TOOL_NAME_INVALID",
        ),
        (
            ToolListing(complete=True, tools=_frozen({"name": 7}), reason=None),
            "REG_TOOL_NAME_INVALID",
        ),
        (
            ToolListing(complete=True, tools=_frozen({"name": "a"}, {"name": "a"}), reason=None),
            "REG_TOOL_NAME_DUPLICATE",
        ),
        (
            ToolListing(
                complete=True, tools=_frozen({"name": "big", "maximum": 2**60}), reason=None
            ),
            "REG_FINGERPRINT_FAILED",
        ),
    ],
)
def test_unusable_listing_gives_no_fingerprints(listing, code):
    with pytest.raises(RegistryError) as raised:
        listing_fingerprints(listing)
    assert str(raised.value) == code


def test_errors_carry_no_server_text():
    listing = ToolListing(
        complete=True,
        tools=_frozen({"name": "marker-tool-7f3a"}, {"name": "marker-tool-7f3a"}),
        reason=None,
    )
    with pytest.raises(RegistryError) as raised:
        listing_fingerprints(listing)
    assert "marker" not in repr(raised.value)
