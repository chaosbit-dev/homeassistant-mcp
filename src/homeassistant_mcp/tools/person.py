"""Person management tools (v1) — the registry/config write surface.

These are the highest-blast-radius tools on the server: they cross from "control
what devices *do*" to "change what the system *is*". They ride the WebSocket client
(``ha_ws.py``) because HA exposes person storage-collection ops only over WebSocket.

Gating (the registry-write equivalents are documented in
`docs/registry-write-tools.md` §3; person writes follow the same shape):
- Reads (``ha_list_persons``) are ungated.
- Writes (create/update/delete) require ``HA_PERSON_WRITES_ENABLED`` (default on) —
  the fail-closed kill switch — and every write emits an audit log line.
- ``ha_delete_person`` additionally requires ``confirm=True`` (destructive).
- HA's own ``success: false`` is surfaced as a tool error, never a silent pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import Context  # noqa: TC002 — MCPServer resolves annotations at runtime
from mcp.types import ToolAnnotations

from homeassistant_mcp.ha_ws import HomeAssistantWSError
from homeassistant_mcp.server import mcp

if TYPE_CHECKING:
    from homeassistant_mcp.config import Settings
    from homeassistant_mcp.ha_ws import HomeAssistantWebSocketClient

_audit = logging.getLogger("homeassistant_mcp.audit")

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

WRITE_ACTION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

DESTRUCTIVE_ACTION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


def _get_ws(ctx: Context[Any, Any]) -> HomeAssistantWebSocketClient:
    ws: HomeAssistantWebSocketClient = ctx.request_context.lifespan_context["ha_ws"]
    return ws


def _get_settings(ctx: Context[Any, Any]) -> Settings:
    settings: Settings = ctx.request_context.lifespan_context["settings"]
    return settings


def _write_gate(ctx: Context[Any, Any], op: str) -> str | None:
    """Return an error string if writes are disabled, else None.

    The kill switch is checked BEFORE any socket is opened, so a disabled server
    never presents an admin-capable connection at all.
    """
    if not _get_settings(ctx).person_writes_enabled:
        _audit.warning("person.%s REFUSED — person writes disabled (kill switch)", op)
        return (
            f"Refused: person {op} is disabled. Person write ops are gated by "
            "`HA_PERSON_WRITES_ENABLED`, which is currently off."
        )
    return None


def _format_persons(persons: list[dict[str, Any]]) -> str:
    """Render person/list output as a readable table."""
    if not persons:
        return "No persons configured."

    lines = [
        f"## Persons ({len(persons)})",
        "",
        "| Name | Person ID | User | Device trackers | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in persons:
        name = p.get("name", "—")
        pid = p.get("id") or p.get("person_id") or "—"
        user = p.get("user_id") or "—"
        trackers = p.get("device_trackers") or []
        trackers_str = ", ".join(f"`{t}`" for t in trackers) if trackers else "—"
        source = p.get("source", "—")
        editable = "" if source != "config" else " (read-only)"
        lines.append(f"| {name} | `{pid}` | {user} | {trackers_str} | {source}{editable} |")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def ha_list_persons(ctx: Context[Any, Any]) -> str:
    """List Home Assistant persons and the device_trackers bound to each.

    READ-only and ungated — this is the WebSocket read path and the diagnostic
    starting point for presence setup. Use it to confirm whether a person already
    exists, what trackers they are bound to, and to get the `person_id` needed by
    `ha_update_person` / `ha_delete_person`.

    `source` is `storage` (UI/registry-managed, editable by these tools) or `config`
    (defined in YAML, read-only here).
    """
    ws = _get_ws(ctx)
    try:
        persons = await ws.list_persons()
    except HomeAssistantWSError as exc:
        return f"Error listing persons: {exc}"
    return _format_persons(persons)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_create_person(
    ctx: Context[Any, Any],
    name: str,
    device_trackers: list[str],
    user_id: str | None = None,
) -> str:
    """Create a Home Assistant person bound to existing device_trackers.

    GATED write op (requires `HA_PERSON_WRITES_ENABLED`, audit-logged). Requires an
    admin HA token.

    This binds *existing* `device_tracker.*` entities — it cannot create a tracker
    source. The phone/router tracker must already exist (Companion app or router
    integration); use `ha_list_persons` and entity listings to find its entity_id
    first.

    Example: name="Alice", device_trackers=["device_tracker.alice_phone",
    "device_tracker.alice_tracker"]
    """
    gate = _write_gate(ctx, "create")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    _audit.info(
        "person.create name=%r device_trackers=%r user_id=%r",
        name,
        device_trackers,
        user_id,
    )
    try:
        person = await ws.create_person(name, device_trackers, user_id)
    except HomeAssistantWSError as exc:
        _audit.warning("person.create FAILED name=%r error=%s", name, exc)
        return f"Error creating person: {exc}"

    pid = person.get("id") or person.get("person_id") or "—"
    trackers = ", ".join(f"`{t}`" for t in person.get("device_trackers") or [])
    return (
        f"Created person **{person.get('name', name)}** (`{pid}`)\n"
        f"- **Device trackers:** {trackers or '—'}"
    )


@mcp.tool(annotations=WRITE_ACTION)
async def ha_update_person(
    ctx: Context[Any, Any],
    person_id: str,
    name: str | None = None,
    device_trackers: list[str] | None = None,
) -> str:
    """Update an existing (storage-managed) person's name and/or device_trackers.

    GATED write op (requires `HA_PERSON_WRITES_ENABLED`, audit-logged). Requires an
    admin HA token.

    Omitting `device_trackers` PRESERVES the person's current trackers. Passing an
    explicit empty list `[]` clears them.

    To ADD a tracker, pass the full desired list — this REPLACES the person's
    tracker list, it does not merge.

    `person_id` is the `id` shown by `ha_list_persons` (only `storage`-source
    persons are editable; `config`/YAML persons are not).
    """
    gate = _write_gate(ctx, "update")
    if gate is not None:
        return gate

    if name is None and device_trackers is None:
        return "Error: provide at least one of `name` or `device_trackers` to update."

    ws = _get_ws(ctx)

    if device_trackers is None:
        # HA's person/update schema defaults an ABSENT device_trackers to [], so
        # simply omitting the field from the payload WIPES the bindings — a
        # name-only edit would silently destroy presence detection. Read the
        # current list and send it back unchanged.
        #
        # Fail closed: if we cannot establish the current trackers, refuse rather
        # than send a request that would clear them.
        try:
            persons = await ws.list_persons()
        except HomeAssistantWSError as exc:
            _audit.warning(
                "person.update REFUSED person_id=%r — tracker read-back failed: %s",
                person_id,
                exc,
            )
            return (
                f"Refused: could not read the current device_trackers for `{person_id}`, "
                f"and updating without them would CLEAR the person's trackers ({exc}). "
                "Pass `device_trackers` explicitly to proceed."
            )
        current = next(
            (p for p in persons if (p.get("id") or p.get("person_id")) == person_id), None
        )
        if current is None:
            _audit.warning(
                "person.update REFUSED person_id=%r — person not found on read-back", person_id
            )
            return (
                f"Refused: no person with id `{person_id}` (checked before updating, because "
                "an update without `device_trackers` would clear them). "
                "Use `ha_list_persons` to find the right id."
            )
        device_trackers = [str(t) for t in current.get("device_trackers") or []]

    _audit.info(
        "person.update person_id=%r name=%r device_trackers=%r",
        person_id,
        name,
        device_trackers,
    )
    try:
        person = await ws.update_person(person_id, name, device_trackers)
    except HomeAssistantWSError as exc:
        _audit.warning("person.update FAILED person_id=%r error=%s", person_id, exc)
        return f"Error updating person: {exc}"

    trackers = ", ".join(f"`{t}`" for t in person.get("device_trackers") or [])
    return (
        f"Updated person **{person.get('name', person_id)}** (`{person_id}`)\n"
        f"- **Device trackers:** {trackers or '—'}"
    )


@mcp.tool(annotations=DESTRUCTIVE_ACTION)
async def ha_delete_person(
    ctx: Context[Any, Any],
    person_id: str,
    confirm: bool = False,
) -> str:
    """Delete a (storage-managed) Home Assistant person. DESTRUCTIVE.

    GATED + CONFIRM-REQUIRED write op (requires `HA_PERSON_WRITES_ENABLED`,
    audit-logged). Requires an admin HA token.

    Pass `confirm=True` to actually delete — without it the call is refused so a
    person is never removed by accident. This does not delete the underlying
    device_trackers, only the person profile that binds them.
    """
    gate = _write_gate(ctx, "delete")
    if gate is not None:
        return gate

    if not confirm:
        return (
            f"Refused: deleting person `{person_id}` is destructive. "
            "Re-call with `confirm=True` to proceed."
        )

    ws = _get_ws(ctx)
    _audit.info("person.delete person_id=%r confirm=True", person_id)
    try:
        await ws.delete_person(person_id)
    except HomeAssistantWSError as exc:
        _audit.warning("person.delete FAILED person_id=%r error=%s", person_id, exc)
        return f"Error deleting person: {exc}"

    return f"Deleted person `{person_id}`."
