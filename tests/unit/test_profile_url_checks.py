"""URL-01: endpoint validation isolates an invalid server without altering policy."""

from copy import deepcopy

import pytest

from guard_app.domain.errors import ResolutionError
from guard_app.domain.reasons import Reason
from guard_app.services import catalog as catalog_module
from guard_app.services.catalog import CatalogConfigError, load_profile_settings
from tests.unit.test_profile_loading import _document, _Registry, _resolve


def _settings(url):
    document = _document()
    document["servers"][1]["url"] = url
    return document


@pytest.mark.parametrize("url", ["https://example.com/", "http://127.0.0.1:8080/mcp"])
def test_canonical_endpoint_keeps_server_available(url):
    loaded = load_profile_settings(_settings(url), _Registry())
    assert loaded.server_issue("remote") is None
    assert loaded.catalog.resolve_tool("remote__send").tool_name == "send"
    assert {view.tool_ref for view in loaded.catalog.tool_views(("docs", "remote"))} == {
        "docs__read",
        "remote__send",
    }


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://example.com", "URL_NOT_CANONICAL"),
        ("http://example.com/mcp", "URL_HTTP_NOT_LOOPBACK"),
        ("https://secret.example/mcp?password=PRIVATE_MARKER", "URL_QUERY"),
    ],
)
def test_invalid_endpoint_hides_only_server_and_reports_fixed_code(url, code):
    document = _settings(url)
    loaded = load_profile_settings(
        document, _Registry(), resource_resolver=lambda _tool, path: path
    )
    assert loaded.server_issue("remote") == code
    assert loaded.server_issue("docs") is None
    assert [view.tool_ref for view in loaded.catalog.tool_views(("docs", "remote"))] == [
        "docs__read"
    ]
    with pytest.raises(ResolutionError) as error:
        loaded.catalog.resolve_tool("remote__send")
    assert error.value.reason is Reason.UNKNOWN_SERVER
    assert _resolve(loaded.catalog, "docs__read", {"path": "public/a"}).tool.tool_name == "read"
    assert url not in repr(loaded)
    assert url not in repr(error.value)
    assert "PRIVATE_MARKER" not in repr(loaded) + repr(error.value)


def test_endpoint_issue_has_priority_over_missing_or_mismatched_registration():
    registry = _Registry()
    document = _settings("https://example.com")
    loaded = load_profile_settings(document, registry)
    del registry.records["remote"]
    assert loaded.server_issue("remote") == "URL_NOT_CANONICAL"
    assert loaded.catalog.tool_views(("remote",)) == ()


def test_unselected_server_issue_is_hidden():
    document = _settings("https://example.com")
    document["profile"]["servers"] = ["docs"]
    loaded = load_profile_settings(document, _Registry())
    assert loaded.server_issue("remote") is None
    assert loaded.server_issue("docs") is None


def test_url_validation_does_not_change_policy_version_or_mutate_document(monkeypatch):
    document = _settings("https://example.com")
    before = deepcopy(document)
    rejected = load_profile_settings(document, _Registry())
    assert rejected.server_issue("remote") == "URL_NOT_CANONICAL"
    monkeypatch.setattr(catalog_module, "canonical_endpoint", lambda url: url)
    accepted = load_profile_settings(document, _Registry())
    assert accepted.server_issue("remote") is None
    assert rejected.policy_version == accepted.policy_version
    assert document == before


@pytest.mark.parametrize(
    ("field", "value"),
    [("url", 123), ("url", None), ("command", "private"), ("args", ["unexpected"])],
)
def test_transport_field_format_errors_still_reject_entire_document(field, value):
    document = _settings("https://example.com/")
    document["servers"][1][field] = value
    with pytest.raises(CatalogConfigError):
        load_profile_settings(document, _Registry())
