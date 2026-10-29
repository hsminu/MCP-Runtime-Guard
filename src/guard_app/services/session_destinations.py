"""Operator destination edits; B validates the resulting complete configuration."""

from collections.abc import Mapping
from unicodedata import category

from guard_app.domain.json_types import FrozenJsonObject
from guard_app.services.bridge_types import DestinationEdit
from guard_app.services.session_admission import SessionRuntimeError

# Match the bridge's existing identifier ceiling; never truncate or normalize.
DESTINATION_ID_LIMIT = 256


def destination_document(current: Mapping[str, object], edit: DestinationEdit) -> dict[str, object]:
    if (
        type(edit) is not DestinationEdit
        or edit.action not in ("add", "update", "delete")
        or type(edit.destination_id) is not str
        or not 1 <= len(edit.destination_id) <= DESTINATION_ID_LIMIT
        or edit.destination_id != edit.destination_id.strip()
        or any(category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in edit.destination_id)
        or type(edit.selected) is not bool
        or (
            edit.action != "delete"
            and (
                type(edit.tier) is not str
                or edit.tier not in ("internal", "approved_external", "general_external")
                or type(edit.max_label) is not str
                or edit.max_label not in ("PUBLIC", "INTERNAL", "CONFIDENTIAL")
                or (edit.tier != "internal" and edit.max_label == "CONFIDENTIAL")
            )
        )
    ):
        raise SessionRuntimeError("POLICY_DESTINATION_INVALID")
    document = FrozenJsonObject(current).to_dict()
    destinations = document.setdefault("destinations", [])
    profile = document.get("profile")
    if not isinstance(destinations, list) or not isinstance(profile, dict):
        raise SessionRuntimeError("POLICY_DESTINATION_INVALID")
    selected = profile.setdefault("destinations", [])
    if not isinstance(selected, list):
        raise SessionRuntimeError("POLICY_DESTINATION_INVALID")
    exists = any(isinstance(d, dict) and d.get("id") == edit.destination_id for d in destinations)
    if exists != (edit.action != "add"):
        raise SessionRuntimeError("POLICY_DESTINATION_INVALID")
    record: dict[str, object] = {
        "id": edit.destination_id,
        "tier": edit.tier,
        "max_label": edit.max_label,
    }
    if edit.action == "add":
        destinations.append(record)
    elif edit.action == "update":
        destinations[:] = [
            record if isinstance(d, dict) and d.get("id") == edit.destination_id else d
            for d in destinations
        ]
    else:
        destinations[:] = [
            d for d in destinations if not isinstance(d, dict) or d.get("id") != edit.destination_id
        ]
    if edit.action == "delete" or not edit.selected:
        selected[:] = [d for d in selected if d != edit.destination_id]
    elif edit.destination_id not in selected:
        selected.append(edit.destination_id)
    return document
