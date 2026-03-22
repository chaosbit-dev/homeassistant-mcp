"""Scene tools — activate and list scenes."""

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

WRITE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


def _get_ha(ctx: Context[Any, Any]) -> HomeAssistantClient:
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


@mcp.tool(annotations=READ_ONLY)
async def ha_list_scenes(ctx: Context[Any, Any]) -> str:
    """List all available scenes in Home Assistant.

    Scenes are predefined configurations that set multiple entities to specific
    states at once (e.g., "Movie Night" dims lights, closes blinds, turns on TV).
    """
    ha = _get_ha(ctx)
    states = await ha.get_states()
    scenes = [e for e in states if e.get("entity_id", "").startswith("scene.")]

    if not scenes:
        return "No scenes configured in Home Assistant."

    lines = ["| Scene | Entity ID |", "| --- | --- |"]
    for scene in sorted(scenes, key=lambda s: s.get("attributes", {}).get("friendly_name", "")):
        attrs = scene.get("attributes", {})
        name = attrs.get("friendly_name", scene["entity_id"])
        eid = scene["entity_id"]
        lines.append(f"| {name} | `{eid}` |")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_activate_scene(
    ctx: Context[Any, Any],
    entity_id: str,
) -> str:
    """Activate a Home Assistant scene.

    Scenes set multiple entities to predefined states at once.
    Activating a scene is idempotent — calling it again has no additional effect.

    Example: entity_id="scene.movie_night"
    """
    ha = _get_ha(ctx)

    await ha.call_service("scene", "turn_on", {"entity_id": entity_id})

    return f"Activated scene `{entity_id}`"
