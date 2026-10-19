"""DESC-01·I7/I9의 B 단위 부분. A 입력·C Registry 영속화는 구현하지 않는다."""

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import RegisteredServer
from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.domain.models import ToolSpec
from guard_app.services.catalog import Catalog, CatalogConfigError, load_profile_settings
from tests.unit.test_catalog import _registration, _Registry, _tool
from tests.unit.test_policy_change import _PolicyHarness


@pytest.mark.parametrize(
    "text", ["", "가" * 2000, "🙂" * 2000, "검토된 설명\n다음 줄\t내용", "e\u0301"]
)
def test_reviewed_description_is_bounded_immutable_and_not_rewritten(text):
    tool = _tool(reviewed_description=text)
    assert tool.reviewed_description == text
    assert ToolSpec.model_validate_json(tool.model_dump_json()) == tool
    with pytest.raises(ValidationError):
        tool.reviewed_description = "changed"


@pytest.mark.parametrize(
    "text",
    [
        "가" * 2001,
        "\x00",
        "\x1b[31m",
        "\x7f",
        "\x85",
        "\u202e",
        "\u2066",
        "\u200b",
        "\ud800",
        None,
        1,
        True,
    ],
)
def test_invalid_reviewed_description_does_not_echo_input(text):
    value = f"PRIVATE_DESCRIPTION{text}" if isinstance(text, str) else text
    with pytest.raises(ValidationError) as error:
        _tool(reviewed_description=value)
    assert "PRIVATE_DESCRIPTION" not in str(error.value)


def test_default_is_empty_and_never_falls_back_to_unreviewed_schema_description():
    tool = _tool(
        input_schema={
            "type": "object",
            "description": "UNREVIEWED_ORIGINAL_DESCRIPTION",
            "properties": {},
        }
    )
    assert tool.reviewed_description == ""
    catalog = Catalog(_Registry(_registration(tools=(tool,))), profile_id="default")
    assert catalog.tool_views(("docs",))[0].description == ""


def test_catalog_exposes_only_the_reviewed_field_and_hides_it_in_repr():
    tool = _tool(reviewed_description="PRIVATE_REVIEWED_DESCRIPTION")
    record = _registration(tools=(tool,))
    catalog = Catalog(_Registry(record), profile_id="default")
    view = catalog.tool_views(("docs",))[0]
    assert view.description == "PRIVATE_REVIEWED_DESCRIPTION"
    for model in (tool, record, view):
        assert "PRIVATE_REVIEWED_DESCRIPTION" not in repr(model)
    assert view.input_schema["additionalProperties"] is False
    assert tool.fingerprint == "a" * 64
    assert RegisteredServer.model_validate_json(record.model_dump_json()) == record


def test_reviewed_description_cannot_make_inactive_or_unsupported_tools_visible_or_reorder_them():
    registry = _Registry(
        _registration(
            tools=(
                _tool(tool_name="z", reviewed_description="first"),
                _tool(tool_name="a", reviewed_description="second"),
                _tool(tool_name="other", kind=ToolKind.OTHER, reviewed_description="hidden"),
                _tool(tool_name="changed", reviewed_description="hidden"),
            )
        ),
        _registration(
            "pending",
            tools=(_tool("pending", reviewed_description="hidden"),),
            status=RegStatus.PENDING,
        ),
    )
    registry.fingerprints[("docs", "changed")] = "b" * 64
    views = Catalog(registry, profile_id="default").tool_views(("docs", "pending"))
    assert tuple(view.tool_ref for view in views) == ("docs__z", "docs__a")
    assert tuple(view.description for view in views) == ("first", "second")


def test_profile_registry_retains_reviewed_description_and_does_not_add_it_to_policy_document():
    h = _PolicyHarness()
    before = load_profile_settings(h.document, h.registry)
    h.registry.record = h.registry.record.model_copy(
        update={
            "tools": tuple(
                tool.model_copy(update={"reviewed_description": "검토 완료"})
                for tool in h.registry.record.tools
            )
        }
    )
    after = load_profile_settings(h.document, h.registry)
    assert after.policy_version == before.policy_version
    assert tuple(view.description for view in after.catalog.tool_views(("docs",))) == (
        "검토 완료",
        "검토 완료",
    )
    assert "reviewed_description" not in str(h.document)


def test_unvalidated_description_from_registry_fails_without_raw_metadata():
    valid = _tool()
    invalid = ToolSpec.model_construct(
        **{**valid.model_dump(), "reviewed_description": "PRIVATE_DESCRIPTION\x00"}
    )
    record = RegisteredServer.model_construct(
        **{**_registration().model_dump(), "tools": (invalid,)}
    )
    with pytest.raises(CatalogConfigError) as error:
        Catalog(_Registry(record), profile_id="default").tool_views(("docs",))
    assert "PRIVATE_DESCRIPTION" not in str(error.value)
