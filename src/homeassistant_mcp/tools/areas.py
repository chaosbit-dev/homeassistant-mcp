"""Phase 1: Area read tools — list areas, area summary."""

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


@mcp.tool(annotations=READ_ONLY)
async def ha_list_areas(ctx: Context[Any, Any]) -> str:
    """List all areas (rooms/zones) configured in Home Assistant with entity counts.

    Returns a table of area names and how many entities are in each.
    """
    ha = _get_ha(ctx)
    areas = await ha.get_areas()

    if not areas:
        return "No areas configured in Home Assistant."

    lines = ["| Area | Entities |", "| --- | --- |"]
    for area in sorted(areas, key=lambda a: str(a["name"])):
        name = area["name"]
        count = len(area["entities"])
        lines.append(f"| {name} | {count} |")

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def ha_get_area_summary(
    ctx: Context[Any, Any],
    area: str,
) -> str:
    """Get a summary of all entities in a specific area, grouped by domain.

    Returns lights and their states, climate settings, sensor readings, etc.
    for the given area.

    Example: area="Living Room"
    """
    ha = _get_ha(ctx)
    areas = await ha.get_areas()

    # Find the matching area (case-insensitive)
    area_lower = area.lower()
    target = next((a for a in areas if str(a["name"]).lower() == area_lower), None)

    if target is None:
        available = ", ".join(str(a["name"]) for a in sorted(areas, key=lambda a: str(a["name"])))
        return f"Area '{area}' not found. Available areas: {available}"

    entity_ids: list[str] = [str(eid) for eid in target["entities"]]
    if not entity_ids:
        return f"No entities in area '{target['name']}'."

    # Fetch state for each entity in the area
    states = await ha.get_states()
    state_lookup = {str(s["entity_id"]): s for s in states}

    # Group by domain
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for eid in entity_ids:
        domain = eid.split(".")[0] if "." in eid else "unknown"
        entity_state = state_lookup.get(eid)
        if entity_state:
            by_domain.setdefault(domain, []).append(entity_state)

    lines = [f"## {target['name']}"]

    for domain in sorted(by_domain):
        domain_entities = by_domain[domain]
        lines.append(f"\n### {domain.replace('_', ' ').title()} ({len(domain_entities)})")
        for e in domain_entities:
            eid = e.get("entity_id", "")
            attrs: dict[str, Any] = e.get("attributes", {})
            name = str(attrs.get("friendly_name", eid))
            state = e.get("state", "unknown")

            detail_parts: list[str] = []
            # Add useful attribute details depending on domain
            if domain == "climate":
                temp = attrs.get("current_temperature")
                target_temp = attrs.get("temperature")
                if temp is not None:
                    detail_parts.append(f"current: {temp}°F")
                if target_temp is not None:
                    detail_parts.append(f"target: {target_temp}°F")
            elif domain == "light" and state == "on":
                brightness = attrs.get("brightness")
                if brightness is not None:
                    pct = round(int(brightness) / 255 * 100)
                    detail_parts.append(f"{pct}% brightness")
            elif domain == "sensor":
                unit = attrs.get("unit_of_measurement", "")
                if unit:
                    detail_parts.append(f"{state} {unit}")

            detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
            lines.append(f"- **{name}**: {state}{detail}")

    return "\n".join(lines)
