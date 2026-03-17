"""Phase 1: Entity read tools — list, get state, search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context  # noqa: TC002 — FastMCP resolves annotations at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient

from homeassistant_mcp.server import mcp

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _get_ha(ctx: Context[Any, Any]) -> HomeAssistantClient:
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


def _friendly_name(entity: dict[str, Any]) -> str:
    attrs: dict[str, Any] = entity.get("attributes", {})
    return str(attrs.get("friendly_name", entity.get("entity_id", "unknown")))


def _format_entity_table(entities: list[dict[str, Any]], area_map: dict[str, str]) -> str:
    """Format entities as a markdown table."""
    if not entities:
        return "No entities found."

    lines = ["| Entity ID | Name | State | Area |", "| --- | --- | --- | --- |"]
    for e in entities:
        eid = e.get("entity_id", "")
        name = _friendly_name(e)
        state = e.get("state", "unknown")
        area = area_map.get(eid, "—")
        lines.append(f"| `{eid}` | {name} | {state} | {area} |")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def ha_list_entities(
    ctx: Context[Any, Any],
    domain: str | None = None,
    area: str | None = None,
    state: str | None = None,
) -> str:
    """List Home Assistant entities with optional filtering by domain, area, and/or state.

    Examples:
    - All lights: domain="light"
    - Lights that are on: domain="light", state="on"
    - Everything in the kitchen: area="Kitchen"
    """
    ha = _get_ha(ctx)
    entities = await ha.get_states()
    area_map = await ha.get_entity_area_map()

    if domain:
        entities = [e for e in entities if e.get("entity_id", "").startswith(f"{domain}.")]

    if area:
        area_lower = area.lower()
        area_entity_ids = {eid for eid, aname in area_map.items() if aname.lower() == area_lower}
        entities = [e for e in entities if e.get("entity_id") in area_entity_ids]

    if state:
        entities = [e for e in entities if e.get("state") == state]

    return _format_entity_table(entities, area_map)


@mcp.tool(annotations=READ_ONLY)
async def ha_get_entity_state(
    ctx: Context[Any, Any],
    entity_id: str,
) -> str:
    """Get detailed state and attributes for a single Home Assistant entity.

    Returns the current state, all attributes (temperature, brightness, etc.),
    and last changed/updated timestamps.

    Example: entity_id="climate.living_room"
    """
    ha = _get_ha(ctx)
    entity = await ha.get_state(entity_id)

    eid: str = entity.get("entity_id", entity_id)
    name = _friendly_name(entity)
    state: str = entity.get("state", "unknown")
    last_changed: str = entity.get("last_changed", "—")
    last_updated: str = entity.get("last_updated", "—")
    attrs: dict[str, Any] = entity.get("attributes", {})

    lines = [
        f"## {name}",
        f"- **Entity ID:** `{eid}`",
        f"- **State:** {state}",
        f"- **Last changed:** {last_changed}",
        f"- **Last updated:** {last_updated}",
        "",
        "### Attributes",
    ]
    for key, value in sorted(attrs.items()):
        if key == "friendly_name":
            continue
        lines.append(f"- **{key}:** {value}")

    if len(attrs) <= 1:
        lines.append("No additional attributes.")

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def ha_search_entities(
    ctx: Context[Any, Any],
    query: str,
    domain: str | None = None,
) -> str:
    """Search Home Assistant entities by name or entity ID substring.

    Useful for finding entities when you don't know the exact entity_id.
    Case-insensitive substring matching.

    Examples:
    - query="kitchen" finds all entities with "kitchen" in their name or ID
    - query="temperature", domain="sensor" finds temperature sensors
    """
    ha = _get_ha(ctx)
    entities = await ha.get_states()
    area_map = await ha.get_entity_area_map()
    query_lower = query.lower()

    if domain:
        entities = [e for e in entities if e.get("entity_id", "").startswith(f"{domain}.")]

    matches = []
    for e in entities:
        eid = e.get("entity_id", "").lower()
        name = _friendly_name(e).lower()
        if query_lower in eid or query_lower in name:
            matches.append(e)

    if not matches:
        return f"No entities found matching '{query}'."

    return _format_entity_table(matches, area_map)
