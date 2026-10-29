"""Pure selection of public overview projections; no Qt or backend access."""

from .models import AttentionItem, LoadState, ServerSummary, Surface


def unresolved_attention(
    surface: Surface[tuple[AttentionItem, ...]],
) -> tuple[AttentionItem, ...]:
    if surface.state not in (LoadState.READY, LoadState.STALE):
        return ()
    seen: set[str] = set()
    items: list[AttentionItem] = []
    for item in surface.value or ():
        if not item.resolved and item.item_id not in seen:
            seen.add(item.item_id)
            items.append(item)
    return tuple(items)


def priority_server_ids(surface: Surface[tuple[AttentionItem, ...]]) -> set[str]:
    # Priority intentionally uses all unresolved entries, even duplicate IDs;
    # stale attention remains visible but does not reprioritize servers.
    return (
        {item.server_id for item in surface.value or () if not item.resolved and item.server_id}
        if surface.state == LoadState.READY
        else set()
    )


def connected_servers(
    surface: Surface[tuple[ServerSummary, ...]], priority: set[str]
) -> tuple[ServerSummary, ...]:
    ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
    connected = (
        tuple(server for server in surface.value or () if server.session_status == "connected")
        if ready
        else ()
    )
    # Keep the backend's ordering within each group. A related unresolved
    # item makes that server more useful than an unrelated ID-first preview.
    return tuple(server for server in connected if server.server_id in priority) + tuple(
        server for server in connected if server.server_id not in priority
    )
