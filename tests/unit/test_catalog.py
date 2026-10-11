"""B3 Catalog의 T04/T06/T32/T38 단위 검증.

Registry와 경로 해석기는 이 파일의 단위 대역이다. 실제 C 경로 검증·등록,
MCP 결과 처리·HALTED·공개 차단 검증을 대체하지 않는다.
"""

from collections.abc import Callable

import pytest

from guard_app.domain.contracts import RegisteredServer
from guard_app.domain.enums import Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import Boundary, ProposedCall, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.services.catalog import Catalog, CatalogConfigError
from guard_app.services.catalog_sources import SourceRule


class _Registry:
    def __init__(self, *registrations: RegisteredServer) -> None:
        self.servers = {registration.server_id: registration for registration in registrations}
        self.fingerprints: dict[tuple[str, str], str | None] = {}
        self.statuses: dict[str, RegStatus | None] = {}

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self.servers.get(server_id)

    def status(self, server_id: str) -> RegStatus | None:
        if server_id in self.statuses:
            return self.statuses[server_id]
        record = self.servers.get(server_id)
        return None if record is None else record.status

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return self.fingerprints.get((tool.server_id, tool.tool_name), tool.fingerprint)


def _boundary(boundary_id: str = "docs-boundary", **updates: object) -> Boundary:
    values: dict[str, object] = {
        "boundary_id": boundary_id,
        "tier": TrustTier.INTERNAL,
        "max_label": Label.CONFIDENTIAL,
    }
    values.update(updates)
    return Boundary.model_validate(values)


def _tool(server_id: str = "docs", tool_name: str = "search", **updates: object) -> ToolSpec:
    values: dict[str, object] = {
        "server_id": server_id,
        "tool_name": tool_name,
        "kind": ToolKind.READ,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "destination_arg": None,
        "resource_arg": None,
        "fingerprint": "a" * 64,
    }
    values.update(updates)
    return ToolSpec.model_validate(values)


def _registration(
    server_id: str = "docs", *, tools: tuple[ToolSpec, ...] | None = None, **updates: object
) -> RegisteredServer:
    values: dict[str, object] = {
        "server_id": server_id,
        "status": RegStatus.ACTIVE,
        "boundary": _boundary(f"{server_id}-boundary"),
        "tools": tools or (_tool(server_id),),
    }
    values.update(updates)
    return RegisteredServer.model_validate(values)


def _state(**updates: object) -> TaskState:
    values: dict[str, object] = {
        "task_id": "trusted-task",
        "epoch": 3,
        "profile_id": "default",
        "policy_version": "policy-1",
        "status": TaskStatus.RUNNING,
        "high_watermark": Label.CONFIDENTIAL,
    }
    values.update(updates)
    return TaskState.model_validate(values)


def _call(tool_ref: str = "docs__search", **arguments: object) -> ProposedCall:
    return ProposedCall.model_validate(
        {
            "tool_ref": tool_ref,
            "arguments": arguments or {"query": "example"},
            "model_call_id": "model-1",
        }
    )


def _resolve(
    catalog: Catalog, call: ProposedCall | None = None, state: TaskState | None = None
) -> ResolvedRequest:
    return catalog.resolve(
        state or _state(), call or _call(), request_id="guard-request", call_seq=11
    )


def _assert_reason(action: Callable[[], object], reason: Reason) -> None:
    with pytest.raises(ResolutionError) as raised:
        action()
    assert raised.value.reason is reason
    assert str(raised.value) == reason.value


@pytest.mark.parametrize("tool_ref", ["search", "__search", "docs__"])
def test_malformed_namespace_is_unsupported(tool_ref: str) -> None:
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    _assert_reason(lambda: catalog.resolve_tool(tool_ref), Reason.TOOL_UNSUPPORTED)


def test_unregistered_server_and_unregistered_tool_have_distinct_reasons() -> None:
    """T04: 미등록 도구·서버를 추측해서 허용하지 않는다."""
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    _assert_reason(lambda: catalog.resolve_tool("missing__search"), Reason.UNKNOWN_SERVER)
    _assert_reason(lambda: catalog.resolve_tool("docs__missing"), Reason.TOOL_UNSUPPORTED)


@pytest.mark.parametrize("status", [RegStatus.PENDING, RegStatus.BLOCKED])
def test_only_active_registration_can_resolve(status: RegStatus) -> None:
    catalog = Catalog(_Registry(_registration(status=status)), profile_id="default")
    _assert_reason(lambda: catalog.resolve_tool("docs__search"), Reason.REG_NOT_ACTIVE)


def test_registration_changes_are_rechecked_after_catalog_creation() -> None:
    registry = _Registry(_registration())
    catalog = Catalog(registry, profile_id="default")
    assert catalog.resolve_tool("docs__search").kind is ToolKind.READ
    registry.servers["docs"] = _registration(status=RegStatus.BLOCKED)
    _assert_reason(lambda: catalog.resolve_tool("docs__search"), Reason.REG_NOT_ACTIVE)


@pytest.mark.parametrize("current_status", [None, RegStatus.PENDING, RegStatus.BLOCKED])
def test_separate_current_status_cannot_weaken_registration_check(
    current_status: RegStatus | None,
) -> None:
    registry = _Registry(_registration())
    registry.statuses["docs"] = current_status
    catalog = Catalog(registry, profile_id="default")
    _assert_reason(lambda: catalog.resolve_tool("docs__search"), Reason.REG_NOT_ACTIVE)
    assert catalog.tool_views(("docs",)) == ()


def test_registry_lookup_cannot_substitute_another_server_record() -> None:
    registry = _Registry()
    registry.servers["docs"] = _registration("other")
    catalog = Catalog(registry, profile_id="default")
    with pytest.raises(CatalogConfigError):
        catalog.resolve_tool("docs__search")


@pytest.mark.parametrize("current_fingerprint", [None, "b" * 64])
def test_current_fingerprint_must_match_registered_tool(current_fingerprint: str | None) -> None:
    registry = _Registry(_registration())
    registry.fingerprints[("docs", "search")] = current_fingerprint
    catalog = Catalog(registry, profile_id="default")
    _assert_reason(lambda: catalog.resolve_tool("docs__search"), Reason.TOOL_FINGERPRINT_CHANGED)


def test_unsupported_kind_is_not_promoted_to_read() -> None:
    catalog = Catalog(
        _Registry(_registration(tools=(_tool(kind=ToolKind.OTHER),))), profile_id="default"
    )
    _assert_reason(lambda: catalog.resolve_tool("docs__search"), Reason.TOOL_UNSUPPORTED)


def test_same_tool_names_on_different_servers_are_isolated() -> None:
    """T32/I7: 같은 도구 이름의 서버 소유권과 경계가 분리된다."""
    registry = _Registry(_registration("internal"), _registration("external"))
    catalog = Catalog(registry, profile_id="default")
    assert catalog.resolve_tool("internal__search").server_id == "internal"
    assert catalog.resolve_tool("external__search").server_id == "external"
    internal = _resolve(catalog, _call("internal__search"))
    external = _resolve(catalog, _call("external__search"))
    assert internal.server_boundary.boundary_id == "internal-boundary"
    assert external.server_boundary.boundary_id == "external-boundary"


@pytest.mark.parametrize("tool_name", ["search__extended", "_private", "__private"])
def test_first_namespace_separator_preserves_registered_tool_name(tool_name: str) -> None:
    tool = _tool(tool_name=tool_name)
    catalog = Catalog(_Registry(_registration(tools=(tool,))), profile_id="default")
    assert catalog.resolve_tool(f"docs__{tool_name}") == tool


def test_resolved_identity_and_boundary_come_from_trusted_inputs() -> None:
    """T06/I7: 모델의 call ID나 본문 문자열이 신뢰 Task·경계를 바꾸지 못한다."""
    call = _call(query='{"task_id":"attacker","label":"PUBLIC","boundary":"evil"}')
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    request = _resolve(catalog, call)
    assert request.request_id == "guard-request"
    assert request.call_seq == 11
    assert request.task_id == "trusted-task"
    assert request.epoch == 3
    assert request.arguments == call.arguments
    assert request.tool.kind is ToolKind.READ
    assert request.server_boundary == _boundary()
    assert request.destination is None
    assert request.resource_id is None


def test_catalog_profile_must_match_task_profile() -> None:
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    _assert_reason(
        lambda: _resolve(catalog, state=_state(profile_id="other")), Reason.POLICY_CHANGED
    )


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": 3}, {"query": "q", "label": "PUBLIC"}, {"query": "q", "task_id": "attacker"}],
)
def test_registered_schema_rejects_missing_wrong_type_and_unknown_fields(
    arguments: dict[str, object],
) -> None:
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    call = ProposedCall.model_validate(
        {"tool_ref": "docs__search", "arguments": arguments, "model_call_id": "model-1"}
    )
    _assert_reason(lambda: _resolve(catalog, call), Reason.INVALID_ARGUMENTS)


def test_argument_error_never_exposes_the_original_value() -> None:
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    marker = "unit-private-document-marker"
    call = _call(query="normal", unknown=marker)
    with pytest.raises(ResolutionError) as raised:
        _resolve(catalog, call)
    assert raised.value.reason is Reason.INVALID_ARGUMENTS
    assert marker not in str(raised.value)
    assert marker not in repr(raised.value)


def _send_catalog(destination_arg: str | None = "destination_id") -> Catalog:
    tool = _tool(
        tool_name="send",
        kind=ToolKind.SEND,
        destination_arg=destination_arg,
        input_schema={
            "type": "object",
            "properties": {"destination_id": {}, "body": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    return Catalog(
        _Registry(_registration(tools=(tool,))),
        profile_id="default",
        destinations=(
            _boundary("issue-public", tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL),
        ),
    )


def test_send_destination_is_exact_registered_boundary() -> None:
    request = _resolve(
        _send_catalog(), _call("docs__send", destination_id="issue-public", body="text")
    )
    assert request.destination == _boundary(
        "issue-public", tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL
    )
    assert request.server_boundary == _boundary()
    assert request.resource_id is None


@pytest.mark.parametrize(
    "destination",
    [
        "Issue-public",
        "issue-public ",
        " issue-public",
        "issue-public/extra",
        "http://10.0.0.5/",
        "internal",
        1,
        None,
        {"id": "issue-public"},
    ],
)
def test_destination_names_ips_and_non_strings_do_not_establish_trust(destination: object) -> None:
    catalog = _send_catalog()
    _assert_reason(
        lambda: _resolve(catalog, _call("docs__send", destination_id=destination)),
        Reason.UNKNOWN_DESTINATION,
    )


def test_missing_destination_argument_or_configuration_is_denied() -> None:
    _assert_reason(
        lambda: _resolve(_send_catalog(), _call("docs__send", body="text")),
        Reason.UNKNOWN_DESTINATION,
    )
    _assert_reason(
        lambda: _resolve(_send_catalog(None), _call("docs__send", destination_id="issue-public")),
        Reason.UNKNOWN_DESTINATION,
    )


def _resource_catalog(resolver: Callable[[ToolSpec, str], str] | None = None) -> Catalog:
    tool = _tool(
        tool_name="read",
        resource_arg="path",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    return Catalog(
        _Registry(_registration(tools=(tool,))),
        profile_id="default",
        resource_resolver=resolver,
        source_rules=(
            SourceRule(
                server_id="docs", tool_name="read", prefix=("company",), label=Label.INTERNAL
            ),
        ),
    )


def test_resource_id_is_supplied_by_trusted_resolver() -> None:
    seen: list[tuple[ToolSpec, str]] = []

    def resolve_resource(tool: ToolSpec, path: str) -> str:
        seen.append((tool, path))
        return "company/plan.md"

    request = _resolve(
        _resource_catalog(resolve_resource), _call("docs__read", path="operator-path")
    )
    assert request.resource_id == "company/plan.md"
    assert len(seen) == 1
    assert seen[0][0].server_id == "docs"
    assert seen[0][0].resource_arg == "path"
    assert seen[0][1] == "operator-path"


def test_resource_read_fails_without_c_resolver() -> None:
    _assert_reason(
        lambda: _resolve(_resource_catalog(), _call("docs__read", path="company/plan.md")),
        Reason.INTERNAL_ERROR,
    )


@pytest.mark.parametrize(
    "invalid_id",
    [
        "",
        "/company/plan.md",
        "company/../secret",
        "company\\plan.md",
        "company//plan.md",
        "company/./plan.md",
    ],
)
def test_catalog_rejects_noncanonical_resolver_output(invalid_id: str) -> None:
    catalog = _resource_catalog(lambda _tool, _path: invalid_id)
    _assert_reason(
        lambda: _resolve(catalog, _call("docs__read", path="company/plan.md")),
        Reason.INVALID_ARGUMENTS,
    )


@pytest.mark.parametrize(
    "invalid_id", [None, 1, True, ["company/plan.md"], {"id": "company/plan.md"}]
)
def test_catalog_rejects_non_string_resolver_output(invalid_id: object) -> None:
    catalog = _resource_catalog(lambda _tool, _path: invalid_id)
    _assert_reason(
        lambda: _resolve(catalog, _call("docs__read", path="company/plan.md")),
        Reason.INVALID_ARGUMENTS,
    )


def test_resolver_domain_rejection_is_preserved() -> None:
    def reject(_tool: ToolSpec, _path: str) -> str:
        raise ResolutionError(Reason.INVALID_ARGUMENTS)

    _assert_reason(
        lambda: _resolve(_resource_catalog(reject), _call("docs__read", path="secret")),
        Reason.INVALID_ARGUMENTS,
    )


def test_unexpected_resolver_error_propagates_to_future_failclosed_boundary() -> None:
    error = RuntimeError("unit fault")

    def fail(_tool: ToolSpec, _path: str) -> str:
        raise error

    with pytest.raises(RuntimeError) as raised:
        _resolve(_resource_catalog(fail), _call("docs__read", path="company/plan.md"))
    assert raised.value is error


@pytest.mark.parametrize("actual_resource", [None, "public/plan.md", "company/other.md"])
def test_resource_read_requires_matching_actual_resource(actual_resource: str | None) -> None:
    catalog = _resource_catalog(lambda _tool, _path: "company/plan.md")
    request = _resolve(catalog, _call("docs__read", path="company/plan.md"))
    assert catalog.classify_read(request, actual_resource) is None
    assert catalog.classify_read(request, "company/plan.md") is Label.INTERNAL


def test_source_rules_use_segments_and_highest_matching_label() -> None:
    catalog = Catalog(
        _Registry(_registration()),
        profile_id="default",
        source_rules=(
            SourceRule(
                server_id="docs", tool_name="search", prefix=("company",), label=Label.INTERNAL
            ),
            SourceRule(
                server_id="docs",
                tool_name="search",
                prefix=("company", "public"),
                label=Label.PUBLIC,
            ),
            SourceRule(
                server_id="docs", tool_name="search", prefix=("company",), label=Label.CONFIDENTIAL
            ),
        ),
    )
    assert (
        catalog.classify_source("docs", "search", "company/public/notice.md") is Label.CONFIDENTIAL
    )
    assert catalog.classify_source("docs", "search", "companyX/notice.md") is None
    assert catalog.classify_source("other", "search", "company/notice.md") is None
    assert catalog.classify_source("docs", "other", "company/notice.md") is None
    assert catalog.classify_source("docs", "search", "unmatched/notice.md") is None


@pytest.mark.parametrize("actual_resource", [None, "company/plan.md", "attacker-controlled"])
def test_tool_unit_read_matches_only_empty_prefix_rule(actual_resource: str | None) -> None:
    """T38: 원격이 주장한 경로는 도구 단위형 READ의 등급 결정권이 없다."""
    catalog = Catalog(
        _Registry(_registration()),
        profile_id="default",
        source_rules=(
            SourceRule(server_id="docs", tool_name="search", prefix=(), label=Label.PUBLIC),
            SourceRule(
                server_id="docs", tool_name="search", prefix=("company",), label=Label.CONFIDENTIAL
            ),
        ),
    )
    assert catalog.classify_read(_resolve(catalog), actual_resource) is Label.PUBLIC


def test_tool_unit_read_without_empty_prefix_is_unclassified() -> None:
    catalog = Catalog(
        _Registry(_registration()),
        profile_id="default",
        source_rules=(
            SourceRule(
                server_id="docs", tool_name="search", prefix=("company",), label=Label.INTERNAL
            ),
        ),
    )
    assert catalog.classify_read(_resolve(catalog), "company/plan.md") is None


def test_send_response_has_no_source_classification() -> None:
    catalog = _send_catalog()
    request = _resolve(catalog, _call("docs__send", destination_id="issue-public"))
    assert catalog.classify_read(request, "company/plan.md") is None


def test_tool_views_exclude_inactive_unsupported_and_changed_tools() -> None:
    registry = _Registry(
        _registration(
            "active",
            tools=(
                _tool("active"),
                _tool("active", "other", kind=ToolKind.OTHER),
                _tool("active", "changed"),
            ),
        ),
        _registration("pending", status=RegStatus.PENDING),
        _registration("blocked", status=RegStatus.BLOCKED),
    )
    registry.fingerprints[("active", "changed")] = "b" * 64
    catalog = Catalog(registry, profile_id="default")
    views = catalog.tool_views(("active", "pending", "blocked", "missing"))
    assert tuple(view.tool_ref for view in views) == ("active__search",)
    assert views[0].description == ""
    schema_copy = views[0].input_schema.to_dict()
    schema_copy["additionalProperties"] = True
    assert views[0].input_schema["additionalProperties"] is False
    assert registry.servers["active"].tools[0].input_schema["additionalProperties"] is False


@pytest.mark.parametrize("server_id", ["bad__server", "bad_"])
def test_ambiguous_server_namespace_configuration_is_rejected(server_id: str) -> None:
    catalog = Catalog(_Registry(_registration(server_id)), profile_id="default")
    with pytest.raises(CatalogConfigError):
        catalog.tool_views((server_id,))


def test_caller_mutation_does_not_change_arguments_or_registered_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "additionalProperties": False,
    }
    tool = _tool(input_schema=schema)
    original = {"query": "before"}
    call = ProposedCall.model_validate(
        {"tool_ref": "docs__search", "arguments": original, "model_call_id": "model-1"}
    )
    catalog = Catalog(_Registry(_registration(tools=(tool,))), profile_id="default")
    original["query"] = "after"
    schema["additionalProperties"] = True
    request = _resolve(catalog, call)
    assert request.arguments["query"] == "before"
    assert request.tool.input_schema["additionalProperties"] is False


def test_validation_closes_root_and_nested_objects_without_changing_registered_schema() -> None:
    raw_schema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "additionalProperties": True,
            }
        },
        "additionalProperties": True,
    }
    tool = _tool(input_schema=raw_schema)
    catalog = Catalog(_Registry(_registration(tools=(tool,))), profile_id="default")
    for arguments in (
        {"payload": {"title": "okay"}, "unknown": "root"},
        {"payload": {"title": "okay", "unknown": "nested"}},
    ):
        call = ProposedCall.model_validate(
            {"tool_ref": "docs__search", "arguments": arguments, "model_call_id": "model-1"}
        )
        _assert_reason(lambda call=call: _resolve(catalog, call), Reason.INVALID_ARGUMENTS)
    valid_call = _call(payload={"title": "okay"})
    assert _resolve(catalog, valid_call).arguments["payload"]["title"] == "okay"
    view = catalog.tool_views(("docs",))[0]
    exposed = view.input_schema.to_dict()
    assert exposed["additionalProperties"] is False
    assert exposed["properties"]["payload"]["additionalProperties"] is False
    original = tool.input_schema.to_dict()
    assert original["additionalProperties"] is True
    assert original["properties"]["payload"]["additionalProperties"] is True
    assert tool.fingerprint == "a" * 64


def test_config_error_is_value_error() -> None:
    assert issubclass(CatalogConfigError, ValueError)


def test_identical_calls_keep_separate_guard_request_ids_and_sequence() -> None:
    """T13의 해석 부분: 같은 내용과 model_call_id를 요청 중복으로 간주하지 않는다."""
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    call = _call()
    first = catalog.resolve(_state(), call, request_id="guard-request-1", call_seq=1)
    second = catalog.resolve(_state(), call, request_id="guard-request-2", call_seq=2)
    assert first.arguments == second.arguments
    assert first.tool == second.tool
    assert first.request_id != second.request_id
    assert (first.call_seq, second.call_seq) == (1, 2)


def test_returned_public_label_cannot_lower_configured_source_label() -> None:
    """T06의 분류 부분: 반환 label을 사용하지 않고 신뢰 출처 규칙만 적용한다."""
    catalog = _resource_catalog(lambda _tool, _path: "company/plan.md")
    request = _resolve(catalog, _call("docs__read", path="company/plan.md"))
    output = {"resource_id": "company/plan.md", "label": "PUBLIC"}
    assert catalog.classify_read(request, output["resource_id"]) is Label.INTERNAL


def test_duplicate_destination_ids_are_configuration_error() -> None:
    destinations = (
        _boundary("same", tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL),
        _boundary("same"),
    )
    with pytest.raises(CatalogConfigError):
        Catalog(_Registry(_registration()), profile_id="default", destinations=destinations)


def test_invalid_registered_schema_is_configuration_error_without_metadata_content() -> None:
    marker = "private-metadata-marker"
    schema = {"type": "object", "properties": {"query": {"type": marker}}}
    catalog = Catalog(
        _Registry(_registration(tools=(_tool(input_schema=schema),))), profile_id="default"
    )
    with pytest.raises(CatalogConfigError) as raised:
        _resolve(catalog)
    assert marker not in str(raised.value)
    assert marker not in repr(raised.value)


def test_duplicate_tool_view_server_ids_are_configuration_error() -> None:
    catalog = Catalog(_Registry(_registration()), profile_id="default")
    with pytest.raises(CatalogConfigError):
        catalog.tool_views(("docs", "docs"))
