"""A155: pure operator edits against registered metadata, never remote annotations."""

from collections.abc import Mapping

from guard_app.domain.enums import RegStatus
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.services.bridge_types import ToolSettingsEdit, ToolSettingsRow
from guard_app.services.catalog import load_profile_settings
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_admission import SessionRuntimeError


def tool_settings_rows(
    document: Mapping[str, object], registry: ServerRegistry, server_id: str
) -> tuple[int, tuple[ToolSettingsRow, ...]]:
    entry = registry.entry(server_id)
    loaded = load_profile_settings(document, registry)
    server = next((s for s in loaded.servers if s.id == server_id), None)
    if (
        entry is None
        or server is None
        or entry.registration.status not in (RegStatus.PENDING, RegStatus.ACTIVE)
    ):
        raise SessionRuntimeError("REG_TOOLS_STALE")
    configured = {t.name: t for t in server.tools}
    rows = []
    for tool in entry.registration.tools:
        setting = configured.get(tool.tool_name)
        labels = [
            r.label
            for r in loaded.profile.source_rules
            if r.server_id == server_id and r.tool_name == tool.tool_name and not r.prefix
        ]
        properties = tool.input_schema.to_dict().get("properties", {})
        strings = (
            tuple(
                name
                for name, schema in properties.items()
                if isinstance(schema, dict) and schema.get("type") == "string"
            )
            if isinstance(properties, dict)
            else ()
        )
        rows.append(
            ToolSettingsRow(
                tool.tool_name,
                tool.kind.value,
                setting.kind.value if setting else "other",
                setting.destination_arg if setting else None,
                max(labels).name if labels else None,
                strings,
                setting is not None and setting.resource_arg is not None,
            )
        )
    return entry.registration.row_revision, tuple(rows)


def tool_settings_document(
    current: Mapping[str, object],
    registry: ServerRegistry,
    server_id: str,
    expected_row_revision: int,
    edits: tuple[ToolSettingsEdit, ...],
) -> dict[str, object]:
    revision, rows = tool_settings_rows(current, registry, server_id)
    if type(expected_row_revision) is not int or expected_row_revision != revision:
        raise SessionRuntimeError("REG_TOOLS_STALE")
    by_name = {r.name: r for r in rows}
    seen: set[str] = set()
    if type(edits) is not tuple or not edits:
        raise SessionRuntimeError("REG_TOOLS_INVALID")
    for edit in edits:
        if (
            type(edit) is not ToolSettingsEdit
            or type(edit.name) is not str
            or edit.name not in by_name
            or edit.name in seen
            or by_name[edit.name].readonly
            or type(edit.kind) is not str
            or edit.kind not in ("read", "send", "other")
            or (edit.kind == "read" and edit.label not in ("PUBLIC", "INTERNAL", "CONFIDENTIAL"))
            or (edit.kind != "read" and edit.label is not None)
            or (edit.kind != "send" and edit.destination_arg is not None)
            or (
                edit.kind == "send"
                and (
                    type(edit.destination_arg) is not str
                    or edit.destination_arg not in by_name[edit.name].string_arguments
                )
            )
        ):
            raise SessionRuntimeError("REG_TOOLS_INVALID")
        seen.add(edit.name)
    document = FrozenJsonObject(current).to_dict()
    # The B loader above validated these document shapes.
    servers = document["servers"]
    if not isinstance(servers, list):
        raise SessionRuntimeError("REG_TOOLS_INVALID")
    server = next(s for s in servers if s["id"] == server_id)
    server["tools"] = [t for t in server["tools"] if t["name"] not in seen]
    for edit in edits:
        record: dict[str, object] = {"name": edit.name, "kind": edit.kind}
        if edit.kind == "send":
            record["destination_arg"] = edit.destination_arg
        server["tools"].append(record)
    profile = document["profile"]
    if not isinstance(profile, dict):
        raise SessionRuntimeError("REG_TOOLS_INVALID")
    profile["sources"] = [
        s
        for s in profile.get("sources", [])
        if not (s["server"] == server_id and s["tool"] in seen and s["prefix"] == [])
    ]
    profile["sources"].extend(
        {"server": server_id, "tool": e.name, "prefix": [], "label": e.label}
        for e in edits
        if e.kind == "read"
    )
    if server_id not in profile["servers"]:
        profile["servers"].append(server_id)
    changed = {e.name for e in edits if e.kind != by_name[e.name].kind}
    rules = document.get("access_rules", {})
    if not isinstance(rules, dict):
        raise SessionRuntimeError("REG_TOOLS_INVALID")
    rules["allow"] = [
        p for p in rules.get("allow", []) if not (p[0] == server_id and p[1] in changed)
    ]
    document["access_rules"] = rules
    return document
