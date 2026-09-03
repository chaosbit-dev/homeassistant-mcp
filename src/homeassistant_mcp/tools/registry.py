"""Registry tools (v2) — entity/device/area registry reads, writes, and delete.

HA's registry lives only on the WebSocket API; REST cannot read `area_id` /
`disabled_by` / `unique_id`, and cannot mutate them at all. These tools ride the
same ephemeral-per-op client as person management (`ha_ws.py`).

Gating (see `docs/registry-write-tools.md` §3):
- Reads (`ha_list_config_entries`, `ha_get_registry`) are ungated.
- Writes require `HA_REGISTRY_WRITE_ENABLED`, which defaults **off** — a registry
  change can break every reference in the system. Every write is audit-logged.
- Changing an `entity_id` is reference-checked: HA does not rewrite references,
  so a rename silently breaks automations/scripts/scenes pointing at the old id.
  The check fails closed — if it cannot run, the rename is refused.
- `ha_delete_config_entry` is double-gated: it needs
  `HA_REGISTRY_DESTRUCTIVE_ENABLED` on top of the write gate, and stays dry-run
  until `confirm=True`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from mcp.server.fastmcp import Context  # noqa: TC002 — FastMCP resolves annotations at runtime
from mcp.types import ToolAnnotations

from homeassistant_mcp.ha_ws import HomeAssistantWSError
from homeassistant_mcp.server import mcp

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient
    from homeassistant_mcp.config import Settings
    from homeassistant_mcp.ha_ws import HomeAssistantWebSocketClient

_audit = logging.getLogger("homeassistant_mcp.audit")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

WRITE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

DESTRUCTIVE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

# Registry item types whose *configuration* names an entity_id, and which therefore
# break when that entity_id changes. Areas/devices link by id, not entity_id, so a
# rename does not disturb them — they are excluded on purpose.
_REFERENCING_TYPES = ("automation", "script", "scene", "group")


def _get_ws(ctx: Context[Any, Any]) -> HomeAssistantWebSocketClient:
    ws: HomeAssistantWebSocketClient = ctx.request_context.lifespan_context["ha_ws"]
    return ws


def _get_rest(ctx: Context[Any, Any]) -> HomeAssistantClient:
    """The REST client. Needed because config-entry DELETE has no WS command."""
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


def _get_settings(ctx: Context[Any, Any]) -> Settings:
    settings: Settings = ctx.request_context.lifespan_context["settings"]
    return settings


def _write_gate(ctx: Context[Any, Any], op: str) -> str | None:
    """Return an error string if registry writes are disabled, else None.

    Checked BEFORE any socket is opened, so a disabled server never presents an
    admin-capable connection at all.
    """
    if not _get_settings(ctx).registry_write_enabled:
        _audit.warning("registry.%s REFUSED — registry writes disabled (kill switch)", op)
        return (
            f"Refused: registry {op} is disabled. Registry write ops are gated by "
            "`HA_REGISTRY_WRITE_ENABLED`, which is currently off (it ships off by "
            "default because registry changes can break references system-wide)."
        )
    return None


def _destructive_gate(ctx: Context[Any, Any], op: str) -> str | None:
    """Return an error string unless BOTH gates are open, else None.

    Deliberately independent of :func:`_write_gate` — a destructive op must clear
    the write gate *and* this one, so turning registry writes on for a rename does
    not silently also enable integration deletion.
    """
    if not _get_settings(ctx).registry_destructive_enabled:
        _audit.warning("registry.%s REFUSED — destructive ops disabled (second gate)", op)
        return (
            f"Refused: registry {op} is a DESTRUCTIVE op and is disabled. It requires "
            "`HA_REGISTRY_DESTRUCTIVE_ENABLED` (currently off) in addition to "
            "`HA_REGISTRY_WRITE_ENABLED`."
        )
    return None


def _normalize_area_id(area_id: str) -> str | None:
    """Map the empty string to None so a caller can *clear* an area assignment.

    HA distinguishes an absent key ("leave alone") from an explicit null ("clear"),
    but a tool parameter cannot be "absent" — so empty string is the clear signal.
    """
    cleaned = area_id.strip()
    return cleaned or None


async def _check_entity_references(
    ws: HomeAssistantWebSocketClient, entity_id: str
) -> tuple[dict[str, list[str]], str | None]:
    """Find config that references ``entity_id``. Returns (references, error).

    A non-None error means the check could not be completed — callers must treat
    that as a refusal, not as "no references found".
    """
    try:
        related = await ws.search_related("entity", entity_id)
    except HomeAssistantWSError as exc:
        return {}, str(exc)
    found = {
        kind: ids for kind, ids in related.items() if kind in _REFERENCING_TYPES and ids
    }
    return found, None


def _format_references(references: dict[str, list[str]]) -> str:
    parts = []
    for kind in sorted(references):
        ids = ", ".join(f"`{i}`" for i in references[kind])
        parts.append(f"- **{kind}** ({len(references[kind])}): {ids}")
    return "\n".join(parts)


@mcp.tool(annotations=READ_ONLY)
async def ha_list_config_entries(ctx: Context[Any, Any]) -> str:
    """List Home Assistant config entries (integration instances) with their entry_ids.

    READ-only and ungated. This is the tool for spotting DUPLICATE integrations —
    the same integration set up twice shows as two entries with the same `domain`
    and `title` but different `entry_id`s.

    The `entry_id` shown here is what `ha_delete_config_entry` takes.
    """
    ws = _get_ws(ctx)
    try:
        entries = await ws.list_config_entries()
    except HomeAssistantWSError as exc:
        return f"Error listing config entries: {exc}"

    if not entries:
        return "No config entries found."

    lines = [
        f"## Config entries ({len(entries)})",
        "",
        "| Title | Domain | Entry ID | State |",
        "| --- | --- | --- | --- |",
    ]
    def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
        return str(entry.get("domain", "")), str(entry.get("title", ""))

    for entry in sorted(entries, key=_sort_key):
        title = entry.get("title") or "—"
        domain = entry.get("domain") or "—"
        entry_id = entry.get("entry_id") or "—"
        state = entry.get("state") or "—"
        lines.append(f"| {title} | `{domain}` | `{entry_id}` | {state} |")

    # Duplicate detection is the whole point of this tool — surface it, don't make
    # the caller eyeball a long table.
    seen: dict[tuple[str, str], int] = {}
    for entry in entries:
        key = (str(entry.get("domain", "")), str(entry.get("title", "")))
        seen[key] = seen.get(key, 0) + 1
    dupes = [f"`{d}` / {t} x{n}" for (d, t), n in sorted(seen.items()) if n > 1]
    if dupes:
        lines.extend(["", f"**Possible duplicates:** {'; '.join(dupes)}"])

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def ha_get_registry(
    ctx: Context[Any, Any],
    kind: str = "entity",
    entity_id: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> str:
    """Read the entity, device, or area registry — the fields REST cannot see.

    READ-only and ungated. Returns `area_id`, `disabled_by`, `unique_id`, and
    `config_entry_id`, none of which are available over REST.

    `kind` is `entity` (default), `device`, or `area`. Use `kind="area"` to get
    **`area_id` values**, which `ha_list_areas` does not show but
    `ha_assign_entity_area` / `ha_assign_device_area` require.

    Pass `entity_id` for full detail on one entity. Otherwise `search` filters by
    substring against the id and name, capped at `limit` (the registry is large).

    Example: kind="area" to find an area_id; kind="entity", search="denon" to find
    duplicate or badly-named entities.
    """
    ws = _get_ws(ctx)
    kind = kind.strip().lower()
    if kind not in ("entity", "device", "area"):
        return f"Error: `kind` must be one of entity, device, area (got {kind!r})."

    try:
        if entity_id:
            entry = await ws.get_entity_registry_entry(entity_id)
            if not entry:
                return f"No registry entry for `{entity_id}`."
            lines = [f"## `{entity_id}`", ""]
            for field in (
                "name",
                "original_name",
                "platform",
                "area_id",
                "device_id",
                "config_entry_id",
                "unique_id",
                "disabled_by",
                "hidden_by",
                "entity_category",
            ):
                lines.append(f"- **{field}:** {entry.get(field) if entry.get(field) else '—'}")
            return "\n".join(lines)

        if kind == "entity":
            rows = await ws.list_entity_registry()
        elif kind == "device":
            rows = await ws.list_device_registry()
        else:
            rows = await ws.list_area_registry()
    except HomeAssistantWSError as exc:
        return f"Error reading {kind} registry: {exc}"

    if search:
        needle = search.lower()
        rows = [
            r
            for r in rows
            if needle in str(r.get("entity_id", "")).lower()
            or needle in str(r.get("name") or "").lower()
            or needle in str(r.get("name_by_user") or "").lower()
        ]

    if not rows:
        return f"No {kind} registry entries{f' matching {search!r}' if search else ''}."

    total = len(rows)
    truncated = total > limit
    rows = rows[:limit]

    if kind == "area":
        lines = ["| Area | Area ID |", "| --- | --- |"]
        for r in sorted(rows, key=lambda a: str(a.get("name", ""))):
            lines.append(f"| {r.get('name', '—')} | `{r.get('area_id', '—')}` |")
    elif kind == "device":
        lines = ["| Device | Device ID | Area ID | Disabled |", "| --- | --- | --- | --- |"]
        for r in sorted(rows, key=lambda d: str(d.get("name_by_user") or d.get("name") or "")):
            name = r.get("name_by_user") or r.get("name") or "—"
            lines.append(
                f"| {name} | `{r.get('id', '—')}` | `{r.get('area_id') or '—'}` "
                f"| {r.get('disabled_by') or '—'} |"
            )
    else:
        lines = ["| Entity ID | Name | Area ID | Disabled |", "| --- | --- | --- | --- |"]
        for r in sorted(rows, key=lambda e: str(e.get("entity_id", ""))):
            name = r.get("name") or r.get("original_name") or "—"
            lines.append(
                f"| `{r.get('entity_id', '—')}` | {name} | `{r.get('area_id') or '—'}` "
                f"| {r.get('disabled_by') or '—'} |"
            )

    header = f"## {kind.title()} registry ({total})"
    footer = f"\n\n_Showing {limit} of {total}; narrow with `search`._" if truncated else ""
    return f"{header}\n\n" + "\n".join(lines) + footer


@mcp.tool(annotations=WRITE_ACTION)
async def ha_rename_entity(
    ctx: Context[Any, Any],
    entity_id: str,
    name: str | None = None,
    new_entity_id: str | None = None,
    force: bool = False,
) -> str:
    """Rename an entity's friendly name and/or change its entity_id.

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged). Requires
    an admin HA token.

    `name` changes only the DISPLAY name — always safe, and the right choice for
    voice-readiness work ("Denon AVR X3800H 2" → "Living Room Receiver").

    `new_entity_id` changes the ACTUAL id and is the dangerous one: Home Assistant
    does NOT rewrite references, so every automation, script, scene, and dashboard
    pointing at the old id silently breaks. This tool refuses the change when it
    finds references, and lists them so you can fix them first. `force=True`
    overrides — only after you have handled every reference reported.

    Example: entity_id="media_player.denon_avr_x3800h", name="Living Room Receiver"
    """
    gate = _write_gate(ctx, "rename_entity")
    if gate is not None:
        return gate

    if name is None and new_entity_id is None:
        return "Error: provide at least one of `name` or `new_entity_id`."

    ws = _get_ws(ctx)
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name

    if new_entity_id is not None and new_entity_id != entity_id:
        if "." not in new_entity_id:
            return (
                f"Error: `new_entity_id` must be a full entity id like "
                f"`{entity_id.split('.')[0]}.new_name` (got {new_entity_id!r})."
            )
        if new_entity_id.split(".")[0] != entity_id.split(".")[0]:
            return (
                "Error: an entity's domain cannot change — "
                f"`{entity_id}` → `{new_entity_id}` crosses domains."
            )

        references, check_error = await _check_entity_references(ws, entity_id)
        if check_error is not None and not force:
            _audit.warning(
                "registry.rename_entity REFUSED entity_id=%r — reference check failed: %s",
                entity_id,
                check_error,
            )
            return (
                f"Refused: could not verify what references `{entity_id}`, so changing "
                f"its entity_id is unsafe (reference check failed: {check_error}). "
                "Re-call with `force=True` to override."
            )
        if references and not force:
            _audit.warning(
                "registry.rename_entity REFUSED entity_id=%r — %d referencing item(s)",
                entity_id,
                sum(len(v) for v in references.values()),
            )
            return (
                f"Refused: `{entity_id}` is referenced by config that HA will NOT "
                f"update automatically. Renaming its entity_id would break:\n\n"
                f"{_format_references(references)}\n\n"
                "Fix those references first, or pass `name` alone to change just the "
                "display name (always safe). `force=True` overrides."
            )
        changes["new_entity_id"] = new_entity_id

    _audit.info(
        "registry.rename_entity entity_id=%r name=%r new_entity_id=%r force=%s",
        entity_id,
        name,
        new_entity_id,
        force,
    )
    try:
        entry = await ws.update_entity_registry(entity_id, **changes)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.rename_entity FAILED entity_id=%r error=%s", entity_id, exc)
        return f"Error renaming entity: {exc}"

    final_id = entry.get("entity_id", changes.get("new_entity_id", entity_id))
    return (
        f"Updated `{entity_id}`\n"
        f"- **Name:** {entry.get('name') or name or '—'}\n"
        f"- **Entity ID:** `{final_id}`"
    )


@mcp.tool(annotations=WRITE_ACTION)
async def ha_assign_entity_area(
    ctx: Context[Any, Any],
    entity_id: str,
    area_id: str,
) -> str:
    """Assign an entity to an area (or clear it with an empty string).

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    `area_id` is the registry ID, not the display name — get it from
    `ha_get_registry(kind="area")`. Create a missing area with `ha_create_area`.
    Pass `area_id=""` to unassign the entity from its area.

    This overrides the area the entity would otherwise inherit from its device.
    """
    gate = _write_gate(ctx, "assign_entity_area")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    target = _normalize_area_id(area_id)
    _audit.info("registry.assign_entity_area entity_id=%r area_id=%r", entity_id, target)
    try:
        entry = await ws.update_entity_registry(entity_id, area_id=target)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.assign_entity_area FAILED entity_id=%r error=%s", entity_id, exc)
        return f"Error assigning area: {exc}"

    if target is None:
        return f"Cleared the area assignment on `{entity_id}`."
    return f"Assigned `{entity_id}` to area `{entry.get('area_id', target)}`."


@mcp.tool(annotations=WRITE_ACTION)
async def ha_disable_entity(
    ctx: Context[Any, Any],
    entity_id: str,
    disabled: bool = True,
) -> str:
    """Disable (or re-enable) an entity in the registry.

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    A disabled entity stops updating and disappears from state queries, but is NOT
    deleted — pass `disabled=False` to bring it back. This is the safe way to
    retire a dead or duplicate entity without touching its integration.
    """
    gate = _write_gate(ctx, "disable_entity")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    # HA's convention: "user" marks a human-disabled entity; null re-enables it.
    disabled_by = "user" if disabled else None
    _audit.info("registry.disable_entity entity_id=%r disabled=%s", entity_id, disabled)
    try:
        await ws.update_entity_registry(entity_id, disabled_by=disabled_by)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.disable_entity FAILED entity_id=%r error=%s", entity_id, exc)
        return f"Error {'disabling' if disabled else 'enabling'} entity: {exc}"

    return f"{'Disabled' if disabled else 'Enabled'} `{entity_id}`."


@mcp.tool(annotations=WRITE_ACTION)
async def ha_create_area(ctx: Context[Any, Any], name: str) -> str:
    """Create a new area (room/zone) in Home Assistant.

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    Returns the new `area_id`, which is what `ha_assign_entity_area` and
    `ha_assign_device_area` take.

    Example: name="Kids Room"
    """
    gate = _write_gate(ctx, "create_area")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    _audit.info("registry.create_area name=%r", name)
    try:
        area = await ws.create_area(name)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.create_area FAILED name=%r error=%s", name, exc)
        return f"Error creating area: {exc}"

    return f"Created area **{area.get('name', name)}** (`{area.get('area_id', '—')}`)"


@mcp.tool(annotations=WRITE_ACTION)
async def ha_rename_area(ctx: Context[Any, Any], area_id: str, name: str) -> str:
    """Rename an existing area.

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    `area_id` comes from `ha_get_registry(kind="area")`. Renaming an area is safe:
    entities and devices link to the area by id, not by name.
    """
    gate = _write_gate(ctx, "rename_area")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    _audit.info("registry.rename_area area_id=%r name=%r", area_id, name)
    try:
        area = await ws.update_area(area_id, name)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.rename_area FAILED area_id=%r error=%s", area_id, exc)
        return f"Error renaming area: {exc}"

    return f"Renamed area `{area_id}` to **{area.get('name', name)}**."


@mcp.tool(annotations=WRITE_ACTION)
async def ha_assign_device_area(
    ctx: Context[Any, Any],
    device_id: str,
    area_id: str,
) -> str:
    """Assign a device to an area (or clear it with an empty string).

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    Prefer this over `ha_assign_entity_area` when a device owns several entities —
    they inherit the device's area unless individually overridden. Get `device_id`
    from `ha_get_registry(kind="device")` and `area_id` from `kind="area"`.
    """
    gate = _write_gate(ctx, "assign_device_area")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    target = _normalize_area_id(area_id)
    _audit.info("registry.assign_device_area device_id=%r area_id=%r", device_id, target)
    try:
        device = await ws.update_device_registry(device_id, area_id=target)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.assign_device_area FAILED device_id=%r error=%s", device_id, exc)
        return f"Error assigning device area: {exc}"

    if target is None:
        return f"Cleared the area assignment on device `{device_id}`."
    return f"Assigned device `{device_id}` to area `{device.get('area_id', target)}`."


@mcp.tool(annotations=WRITE_ACTION)
async def ha_rename_device(
    ctx: Context[Any, Any],
    device_id: str,
    name_by_user: str,
) -> str:
    """Set a device's user-facing name.

    GATED write op (requires `HA_REGISTRY_WRITE_ENABLED`, audit-logged).

    This sets `name_by_user`, an override layered on top of the name the
    integration reported — the original is preserved and the change does not touch
    any entity_id, so nothing breaks.

    Example: device_id="abc123", name_by_user="Living Room Receiver"
    """
    gate = _write_gate(ctx, "rename_device")
    if gate is not None:
        return gate

    ws = _get_ws(ctx)
    _audit.info("registry.rename_device device_id=%r name_by_user=%r", device_id, name_by_user)
    try:
        device = await ws.update_device_registry(device_id, name_by_user=name_by_user)
    except HomeAssistantWSError as exc:
        _audit.warning("registry.rename_device FAILED device_id=%r error=%s", device_id, exc)
        return f"Error renaming device: {exc}"

    return f"Renamed device `{device_id}` to **{device.get('name_by_user', name_by_user)}**."


@mcp.tool(annotations=DESTRUCTIVE_ACTION)
async def ha_delete_config_entry(
    ctx: Context[Any, Any],
    entry_id: str,
    confirm: bool = False,
) -> str:
    """Delete a config entry (integration instance) and everything it owns. DESTRUCTIVE.

    DOUBLE-GATED write op: requires `HA_REGISTRY_WRITE_ENABLED` **and**
    `HA_REGISTRY_DESTRUCTIVE_ENABLED`, and is audit-logged. Requires an admin token.

    **Dry-run by default.** Without `confirm=True` this changes nothing — it reports
    exactly which entities and devices would be removed. Read that report, then
    re-call with `confirm=True` to execute.

    This removes the integration instance along with every entity and device it
    owns. It is the tool for de-duplicating an integration that was set up twice;
    get `entry_id` from `ha_list_config_entries` and make sure you have the
    duplicate, not the original.

    Devices shared with another config entry survive the delete and are called out
    separately in the report.
    """
    gate = _write_gate(ctx, "delete_config_entry")
    if gate is not None:
        return gate
    destructive_gate = _destructive_gate(ctx, "delete_config_entry")
    if destructive_gate is not None:
        return destructive_gate

    ws = _get_ws(ctx)

    # Build the impact report BEFORE touching anything — it is both the dry-run
    # output and the "are you deleting the right entry" check for a confirmed run.
    try:
        entries = await ws.list_config_entries()
        entry = next((e for e in entries if e.get("entry_id") == entry_id), None)
        if entry is None:
            return (
                f"Error: no config entry with `entry_id` `{entry_id}`. "
                "Use `ha_list_config_entries` to find the right one."
            )
        entities = [
            e for e in await ws.list_entity_registry() if e.get("config_entry_id") == entry_id
        ]
        devices = [
            d
            for d in await ws.list_device_registry()
            if entry_id in (d.get("config_entries") or [])
        ]
    except HomeAssistantWSError as exc:
        _audit.warning(
            "registry.delete_config_entry FAILED preview entry_id=%r: %s", entry_id, exc
        )
        return f"Error inspecting config entry: {exc}"

    # A device attached to more than one entry is NOT removed — only its link to
    # this entry is. Reporting those separately keeps the blast radius honest.
    doomed = [d for d in devices if len(d.get("config_entries") or []) <= 1]
    shared = [d for d in devices if len(d.get("config_entries") or []) > 1]

    report = [
        f"**{entry.get('title', '—')}** (`{entry.get('domain', '—')}`, `{entry_id}`)",
        "",
        f"- **Entities removed:** {len(entities)}",
        f"- **Devices removed:** {len(doomed)}",
    ]
    if shared:
        report.append(f"- **Devices kept** (shared with another entry): {len(shared)}")
    if entities:
        listed = ", ".join(f"`{e.get('entity_id')}`" for e in entities[:25])
        more = f" … and {len(entities) - 25} more" if len(entities) > 25 else ""
        report.extend(["", f"Entities: {listed}{more}"])
    if doomed:
        names = ", ".join(
            str(d.get("name_by_user") or d.get("name") or d.get("id")) for d in doomed
        )
        report.extend(["", f"Devices: {names}"])

    if not confirm:
        _audit.info(
            "registry.delete_config_entry DRY-RUN entry_id=%r entities=%d devices=%d",
            entry_id,
            len(entities),
            len(doomed),
        )
        return (
            "**DRY RUN — nothing was deleted.**\n\n"
            + "\n".join(report)
            + "\n\nRe-call with `confirm=True` to execute this deletion."
        )

    _audit.info(
        "registry.delete_config_entry EXECUTE entry_id=%r title=%r entities=%d devices=%d",
        entry_id,
        entry.get("title"),
        len(entities),
        len(doomed),
    )
    try:
        # REST, not ws — HA has no WebSocket command for this. See
        # HomeAssistantClient.delete_config_entry for the evidence.
        result = await _get_rest(ctx).delete_config_entry(entry_id)
    except (HomeAssistantWSError, httpx.HTTPError) as exc:
        _audit.warning("registry.delete_config_entry FAILED entry_id=%r error=%s", entry_id, exc)
        return f"Error deleting config entry: {exc}"

    restart_note = (
        "\n\n**Home Assistant requires a restart** to finish removing this integration."
        if result.get("require_restart")
        else ""
    )
    return "Deleted config entry:\n\n" + "\n".join(report) + restart_note
