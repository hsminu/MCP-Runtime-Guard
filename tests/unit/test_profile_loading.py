"""B3.9·I2/I6/I7/I9: 메모리 설정 검증과 정책 버전. 실제 C/A 연결은 제외한다."""

import json
from collections import UserDict
from copy import deepcopy
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from guard_app.core.access_rules import AccessRules, evaluate_access, normalize_access_rules
from guard_app.domain.contracts import RegisteredServer
from guard_app.domain.enums import Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import Boundary, ProposedCall, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.security.digest import digest, jcs_canonicalize
from guard_app.services import catalog as catalog_module
from guard_app.services.catalog import Catalog, CatalogConfigError, load_profile_settings
from guard_app.services.catalog_sources import SourceRule


def _document():
    return {
        "profile": {
            "id": "default",
            "servers": ["docs", "remote"],
            "destinations": ["issue-public"],
            "sources": [
                {"server": "docs", "tool": "read", "prefix": ["public"], "label": "PUBLIC"}
            ],
        },
        "servers": [
            {
                "id": "docs",
                "transport": "stdio",
                "command": "C:/unvalidated/python.exe",
                "args": ["-m", "private-module-contents"],
                "env_allow": ["LANG"],
                "tier": "internal",
                "max_label": "CONFIDENTIAL",
                "tools": [{"name": "read", "kind": "read", "resource_arg": "path"}],
            },
            {
                "id": "remote",
                "transport": "streamable_http",
                "url": "https://unvalidated.invalid/mcp",
                "tier": "general_external",
                "max_label": "INTERNAL",
                "tools": [{"name": "send", "kind": "send", "destination_arg": "target"}],
            },
        ],
        "destinations": [
            {"id": "issue-public", "tier": "general_external", "max_label": "INTERNAL"},
            {"id": "vault", "tier": "internal", "max_label": "CONFIDENTIAL"},
        ],
        "policy": {"taint_mode": "accumulate", "approval_ttl_seconds": 300},
        "access_rules": {"allow": [["docs", "read"], ["remote", "send"]], "deny": []},
    }


class _Registry:
    def __init__(self):
        self.records = {}
        self.queries = []
        self.current_status = RegStatus.ACTIVE
        self.changed_fingerprint = False
        for server, name, kind, argument, tier, label in (
            ("docs", "read", ToolKind.READ, "path", TrustTier.INTERNAL, Label.CONFIDENTIAL),
            ("remote", "send", ToolKind.SEND, "target", TrustTier.GENERAL_EXTERNAL, Label.INTERNAL),
        ):
            tool = ToolSpec(
                server_id=server,
                tool_name=name,
                kind=kind,
                input_schema={
                    "type": "object",
                    "properties": {argument: {"type": "string"}},
                    "required": [argument],
                    "additionalProperties": False,
                },
                resource_arg=argument if kind is ToolKind.READ else None,
                destination_arg=argument if kind is ToolKind.SEND else None,
                fingerprint="a" * 64,
            )
            self.records[server] = RegisteredServer(
                server_id=server,
                status=RegStatus.ACTIVE,
                boundary=Boundary(boundary_id=f"{server}-boundary", tier=tier, max_label=label),
                tools=(tool,),
            )

    def registration(self, server_id):
        self.queries.append(server_id)
        return self.records.get(server_id)

    def status(self, server_id):
        return self.current_status

    def fingerprint(self, tool):
        return "b" * 64 if self.changed_fingerprint else tool.fingerprint


def _resolve(catalog, tool_ref, arguments):
    return catalog.resolve(
        TaskState(
            task_id="task",
            epoch=1,
            profile_id="default",
            policy_version="version",
            high_watermark=Label.PUBLIC,
            unclassified=False,
            status=TaskStatus.RUNNING,
            security_version=0,
        ),
        ProposedCall(tool_ref=tool_ref, arguments=arguments, model_call_id="model"),
        request_id="request",
        call_seq=1,
    )


def test_loaded_catalog_matches_direct_construction():
    registry = _Registry()
    loaded = load_profile_settings(_document(), registry, resource_resolver=lambda _t, p: p)
    direct = Catalog(
        registry,
        profile_id="default",
        destinations=(
            Boundary(
                boundary_id="issue-public",
                tier=TrustTier.GENERAL_EXTERNAL,
                max_label=Label.INTERNAL,
            ),
        ),
        source_rules=(
            SourceRule(server_id="docs", tool_name="read", prefix=("public",), label=Label.PUBLIC),
        ),
        resource_resolver=lambda _t, p: p,
    )
    assert loaded.profile.profile_id == "default"
    assert loaded.guard_settings.profile_id == "default"
    assert loaded.guard_settings.taint_mode == "accumulate"
    assert loaded.guard_settings.approval_ttl_seconds == 300
    assert loaded.catalog.tool_views(("docs", "remote")) == direct.tool_views(("docs", "remote"))
    for ref, arguments in (
        ("docs__read", {"path": "public/report.txt"}),
        ("remote__send", {"target": "issue-public"}),
    ):
        request = _resolve(loaded.catalog, ref, arguments)
        assert request == _resolve(direct, ref, arguments)
        assert loaded.catalog.classify_read(request, request.resource_id) == direct.classify_read(
            request, request.resource_id
        )
    assert evaluate_access(loaded.access_rules, server_id="docs", tool_name="read").allowed


def test_profile_scope_excludes_other_servers_and_destinations():
    document = _document()
    document["profile"]["servers"] = ["remote"]
    document["profile"]["sources"] = []
    registry = _Registry()
    loaded = load_profile_settings(document, registry)
    assert registry.queries == []
    assert loaded.catalog.tool_views(("docs",)) == ()
    assert registry.queries == []
    with pytest.raises(ResolutionError) as error:
        loaded.catalog.resolve_tool("docs__read")
    assert error.value.reason is Reason.UNKNOWN_SERVER
    with pytest.raises(ResolutionError) as error:
        _resolve(loaded.catalog, "remote__send", {"target": "vault"})
    assert error.value.reason is Reason.UNKNOWN_DESTINATION


def test_registry_status_remains_live(subtests):
    for case_index, status in enumerate([RegStatus.PENDING, RegStatus.BLOCKED, None]):
        with subtests.test(case=case_index):
            registry = _Registry()
            loaded = load_profile_settings(_document(), registry)
            registry.current_status = status
            assert loaded.catalog.tool_views(("docs",)) == ()
            with pytest.raises(ResolutionError) as error:
                loaded.catalog.resolve_tool("docs__read")
            assert error.value.reason is Reason.REG_NOT_ACTIVE


def test_registry_fingerprint_remains_live():
    registry = _Registry()
    loaded = load_profile_settings(_document(), registry)
    registry.changed_fingerprint = True
    with pytest.raises(ResolutionError) as error:
        loaded.catalog.resolve_tool("docs__read")
    assert error.value.reason is Reason.TOOL_FINGERPRINT_CHANGED


def test_registry_metadata_mismatch_hides_only_affected_server(subtests):
    for case_index, (status, change) in enumerate(
        (status, change)
        for status in [RegStatus.ACTIVE, RegStatus.PENDING, RegStatus.BLOCKED]
        for change in ["tier", "max_label", "kind", "resource_arg", "destination_arg"]
    ):
        with subtests.test(case=case_index):
            registry = _Registry()
            loaded = load_profile_settings(_document(), registry)
            server = "remote" if change == "destination_arg" else "docs"
            other = "docs" if server == "remote" else "remote"
            record = registry.records[server].model_copy(update={"status": status})
            if change in {"tier", "max_label"}:
                boundary = Boundary(
                    boundary_id="different-id-is-allowed",
                    tier=TrustTier.INTERNAL
                    if change == "max_label"
                    else TrustTier.GENERAL_EXTERNAL,
                    max_label=Label.INTERNAL,
                )
                record = record.model_copy(update={"boundary": boundary})
            else:
                tool = record.tools[0].model_copy(
                    update={change: ToolKind.OTHER if change == "kind" else "another"}
                )
                record = record.model_copy(update={"tools": (tool,)})
            registry.records[server] = record
            assert loaded.server_issue(server) == "REG_SETTINGS_MISMATCH"
            assert loaded.server_issue(other) is None
            with pytest.raises(ResolutionError) as error:
                loaded.catalog.resolve_tool(f"{server}__{'send' if server == 'remote' else 'read'}")
            assert error.value.reason is Reason.UNKNOWN_SERVER
            assert [view.tool_ref for view in loaded.catalog.tool_views(("docs", "remote"))] == [
                f"{other}__{'send' if other == 'remote' else 'read'}"
            ]
            assert loaded.catalog.resolve_tool(
                f"{other}__{'send' if other == 'remote' else 'read'}"
            )


def test_registry_wrong_id_and_invalid_record_remain_configuration_errors():
    registry = _Registry()
    loaded = load_profile_settings(_document(), registry)
    registry.records["docs"] = registry.records["remote"]
    for get in (loaded.server_issue, loaded.catalog.resolve_tool):
        with pytest.raises(CatalogConfigError):
            get("docs" if get == loaded.server_issue else "docs__read")
    registry.records["docs"] = {"invalid": "record"}
    with pytest.raises(CatalogConfigError):
        loaded.server_issue("docs")


def test_server_issue_ignores_absent_and_unselected_registration():
    document = _document()
    document["profile"]["servers"] = ["remote"]
    document["profile"]["sources"] = []
    registry = _Registry()
    del registry.records["remote"]
    loaded = load_profile_settings(document, registry)
    assert loaded.server_issue("docs") is None
    assert loaded.server_issue("remote") is None
    assert loaded.server_issue("unknown") is None


def test_startable_servers_filter_urls_but_keep_selection_order_and_policy():
    document = _document()
    document["servers"][1]["url"] = "https://example.com"
    registry = _Registry()
    for server_id in ("healthy-one", "healthy-two"):
        settings = deepcopy(document["servers"][0])
        settings["id"] = server_id
        document["servers"].append(settings)
        original = registry.records["docs"]
        tools = tuple(tool.model_copy(update={"server_id": server_id}) for tool in original.tools)
        registry.records[server_id] = original.model_copy(
            update={"server_id": server_id, "tools": tools}
        )
    document["profile"]["servers"] = ["healthy-two", "remote", "healthy-one", "docs"]
    before_document = deepcopy(document)
    before_version = load_profile_settings(document, registry).policy_version
    docs = registry.records["docs"]
    registry.records["docs"] = docs.model_copy(
        update={"boundary": docs.boundary.model_copy(update={"max_label": Label.INTERNAL})}
    )
    loaded = load_profile_settings(document, registry)
    selected = loaded.selected_servers

    assert loaded.server_issue("remote") == "URL_NOT_CANONICAL"
    assert loaded.server_issue("docs") == "REG_SETTINGS_MISMATCH"
    assert loaded.server_issue("healthy-one") is None
    assert loaded.server_issue("healthy-two") is None
    assert [server.id for server in selected] == ["docs", "remote", "healthy-one", "healthy-two"]
    assert [server.id for server in loaded.startable_servers] == [
        "docs",
        "healthy-one",
        "healthy-two",
    ]
    assert loaded.selected_servers == selected
    assert loaded.policy_version == before_version
    assert document == before_document


def test_unconfigured_discovered_tools_are_hidden():
    registry = _Registry()
    record = registry.records["docs"]
    extra = record.tools[0].model_copy(update={"tool_name": "unconfigured"})
    registry.records["docs"] = record.model_copy(update={"tools": (*record.tools, extra)})
    loaded = load_profile_settings(_document(), registry)
    assert [view.tool_ref for view in loaded.catalog.tool_views(("docs",))] == ["docs__read"]
    with pytest.raises(ResolutionError) as error:
        loaded.catalog.resolve_tool("docs__unconfigured")
    assert error.value.reason is Reason.TOOL_UNSUPPORTED


def _set(document, path, value):
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_invalid_configuration_is_rejected_with_sanitized_errors(subtests):
    for case_index, (path, value) in enumerate(
        [
            (("unknown",), "sensitive-input"),
            (("profile", "unknown"), "sensitive-input"),
            (("servers", 0, "unknown"), "sensitive-input"),
            (("servers", 0, "tools", 0, "unknown"), "sensitive-input"),
            (("destinations", 0, "unknown"), "sensitive-input"),
            (("profile", "sources", 0, "unknown"), "sensitive-input"),
            (("policy", "unknown"), "sensitive-input"),
            (("access_rules", "unknown"), "sensitive-input"),
            (("policy", "approval_ttl_seconds"), True),
            (("policy", "approval_ttl_seconds"), "300"),
            (("policy", "approval_ttl_seconds"), 300.0),
            (("policy", "approval_ttl_seconds"), 0),
            (("policy", "taint_mode"), "unknown"),
            (("servers", 0, "max_label"), 2),
            (("servers", 0, "max_label"), True),
            (("servers", 1, "max_label"), "CONFIDENTIAL"),
            (("destinations", 0, "max_label"), "CONFIDENTIAL"),
            (("servers", 0, "tier"), "unknown"),
            (("servers", 0, "transport"), "unknown"),
            (("servers", 0, "command"), None),
            (("servers", 0, "url"), "https://sensitive-input.invalid"),
            (("servers", 1, "url"), None),
            (("servers", 1, "args"), ["sensitive-input"]),
            (("servers", 0, "args"), [123]),
            (("servers", 0, "tools", 0, "kind"), "send"),
            (("servers", 0, "tools", 0, "destination_arg"), "target"),
            (("servers", 1, "tools", 0, "resource_arg"), "path"),
            (("servers", 1, "tools", 0, "destination_arg"), None),
            (("servers", 0, "tools", 0, "name"), "bad\nname"),
            (("profile", "id"), ""),
            (("profile", "servers"), ["missing"]),
            (("profile", "destinations"), ["missing"]),
            (("profile", "sources", 0, "server"), "missing"),
            (("profile", "sources", 0, "tool"), "missing"),
            (("profile", "sources", 0, "prefix"), [".."]),
            (("profile", "sources", 0, "label"), "unknown"),
            (("access_rules", "allow"), [["docs", "missing"]]),
            (("access_rules", "deny"), [["missing", "read"]]),
            (("access_rules", "allow"), [["docs"]]),
            (("access_rules", "deny"), [["docs", "read", "extra"]]),
            (("access_rules", "allow"), [["docs", ""]]),
            (("access_rules", "allow"), "sensitive-input"),
        ]
    ):
        with subtests.test(case=case_index):
            document = _document()
            _set(document, path, value)
            with pytest.raises(CatalogConfigError) as error:
                load_profile_settings(document, _Registry())
            assert "sensitive-input" not in str(error.value)
            assert "sensitive-input" not in repr(error.value)


def test_every_server_reference_checks_namespace(subtests):
    for case_index, (bad_id, location) in enumerate(
        (bad_id, location)
        for bad_id in ["", "ambiguous__id", "trailing_", "line\nfeed", "\u200b"]
        for location in ["server", "profile", "source", "allow", "deny"]
    ):
        with subtests.test(case=case_index):
            document = _document()
            if location == "server":
                document["servers"][0]["id"] = bad_id
            elif location == "profile":
                document["profile"]["servers"] = [bad_id]
            elif location == "source":
                document["profile"]["sources"][0]["server"] = bad_id
            else:
                document["access_rules"][location] = [[bad_id, "read"]]
            with pytest.raises(CatalogConfigError):
                load_profile_settings(document, _Registry())


def test_duplicate_ids_are_rejected(subtests):
    for case_index, path in enumerate(
        [
            ("servers",),
            ("destinations",),
            ("profile", "servers"),
            ("profile", "destinations"),
            ("servers", 0, "tools"),
        ]
    ):
        with subtests.test(case=case_index):
            document = _document()
            target = document
            for key in path:
                target = target[key]
            target.append(deepcopy(target[0]))
            with pytest.raises(CatalogConfigError):
                load_profile_settings(document, _Registry())


def test_non_json_input_is_rejected(subtests):
    for case_index, invalid in enumerate([[], None, "sensitive-input", {"bad": object()}]):
        with subtests.test(case=case_index), pytest.raises(CatalogConfigError):
            load_profile_settings(invalid, _Registry())


def test_non_json_nested_values_are_rejected(subtests):
    for case_index, bad_value in enumerate(
        [float("nan"), float("inf"), 2**60, "\ud800", ("tuple",)]
    ):
        with subtests.test(case=case_index):
            document = _document()
            document["servers"][0]["args"] = [bad_value]
            with pytest.raises(CatalogConfigError):
                load_profile_settings(document, _Registry())


def test_cyclic_mapping_is_rejected():
    document = _document()
    document["cycle"] = document
    with pytest.raises(CatalogConfigError):
        load_profile_settings(document, _Registry())


def test_invalid_core_access_rules_fail_loading(monkeypatch):
    invalid = AccessRules(allow=None)
    monkeypatch.setattr(catalog_module, "AccessRules", lambda **_kwargs: invalid)
    with pytest.raises(CatalogConfigError, match="접근 규칙"):
        load_profile_settings(_document(), _Registry())


def test_root_mapping_and_key_order_and_whitespace_do_not_change_hash(subtests):
    for case_index, mapping_type in enumerate([dict, UserDict, MappingProxyType]):
        with subtests.test(case=case_index):
            document = _document()
            loaded = load_profile_settings(document, _Registry())
            rearranged = json.loads(json.dumps(document, sort_keys=True, indent=4))
            assert load_profile_settings(mapping_type(rearranged), _Registry()).policy_version == (
                loaded.policy_version
            )
            assert len(loaded.policy_version) == 64


def test_access_rule_order_and_duplicates_are_normalized_in_hash():
    document = _document()
    first = load_profile_settings(document, _Registry())
    document["access_rules"]["allow"].reverse()
    document["access_rules"]["allow"].append(["docs", "read"])
    second = load_profile_settings(document, _Registry())
    assert first.policy_version == second.policy_version
    assert normalize_access_rules(first.access_rules) == normalize_access_rules(second.access_rules)
    document["access_rules"]["deny"] = [["docs", "read"]]
    third = load_profile_settings(document, _Registry())
    assert first.policy_version != third.policy_version
    assert not evaluate_access(third.access_rules, server_id="docs", tool_name="read").allowed


def test_whole_configuration_changes_policy_version(subtests):
    for case_index, (path, value) in enumerate(
        [
            (("profile", "id"), "another"),
            (("profile", "destinations"), ["issue-public", "vault"]),
            (("profile", "sources", 0, "label"), "INTERNAL"),
            (("policy", "taint_mode"), "none"),
            (("policy", "approval_ttl_seconds"), 301),
            (("servers", 0, "command"), "another-command"),
            (("servers", 0, "args"), ["new", "arguments"]),
            (("servers", 0, "env_allow"), ["TZ"]),
            (("servers", 1, "url"), "https://another.invalid"),
            (("servers", 0, "max_label"), "INTERNAL"),
            (("destinations", 1, "max_label"), "INTERNAL"),
            (("access_rules", "allow"), [["docs", "read"]]),
            (("access_rules", "deny"), [["docs", "read"]]),
        ]
    ):
        with subtests.test(case=case_index):
            document = _document()
            original = load_profile_settings(document, _Registry()).policy_version
            _set(document, path, value)
            assert load_profile_settings(document, _Registry()).policy_version != original


def test_defaults_have_same_version_as_explicit_values():
    document = _document()
    explicit = load_profile_settings(document, _Registry())
    del document["policy"]
    assert load_profile_settings(document, _Registry()).policy_version == explicit.policy_version
    document["access_rules"] = {}
    implicit_rules = load_profile_settings(document, _Registry())
    del document["access_rules"]
    assert (
        load_profile_settings(document, _Registry()).policy_version == implicit_rules.policy_version
    )
    assert not evaluate_access(
        implicit_rules.access_rules, server_id="docs", tool_name="read"
    ).allowed


def test_policy_version_uses_jcs_sha256_of_normalized_whole_configuration():
    document = _document()
    expected = deepcopy(document)
    for server in expected["servers"]:
        server.setdefault("command", None)
        server.setdefault("args", [])
        server.setdefault("env_allow", [])
        server.setdefault("url", None)
        server["max_label"] = int(Label[server["max_label"]])
        for tool in server["tools"]:
            tool.setdefault("destination_arg", None)
            tool.setdefault("resource_arg", None)
    for destination in expected["destinations"]:
        destination["max_label"] = int(Label[destination["max_label"]])
    for source in expected["profile"]["sources"]:
        source["label"] = int(Label[source["label"]])
    expected["access_rules"] = normalize_access_rules(AccessRules(**document["access_rules"]))
    assert load_profile_settings(document, _Registry()).policy_version == digest(
        jcs_canonicalize(expected)
    )


def test_loading_takes_immutable_snapshot_and_does_no_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("로딩 중 I/O가 호출되었다")

    monkeypatch.setattr("builtins.open", forbidden)
    document = _document()
    registry = _Registry()
    loaded = load_profile_settings(document, registry)
    original = loaded.policy_version
    assert registry.queries == []
    document["servers"][0]["args"].append("mutated")
    document["profile"]["servers"].clear()
    document["access_rules"]["allow"].clear()
    assert loaded.policy_version == original
    assert loaded.profile.server_ids == ("docs", "remote")
    assert loaded.servers[0].args == ("-m", "private-module-contents")
    assert evaluate_access(loaded.access_rules, server_id="docs", tool_name="read").allowed
    with pytest.raises(ValidationError):
        loaded.servers[0].command = "mutated"


def test_representations_redact_commands_arguments_and_urls():
    document = _document()
    document["servers"][1]["url"] += "?secret=value"
    loaded = load_profile_settings(document, _Registry())
    for value in (loaded, loaded.profile, loaded.guard_settings, *loaded.servers):
        assert "<redacted>" in repr(value)
        assert "private-module-contents" not in repr(value)
        assert "unvalidated" not in str(value)
        assert "secret=value" not in repr(value)
