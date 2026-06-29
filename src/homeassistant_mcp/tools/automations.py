"""Phase 2: Automation tools — trigger, enable/disable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.server.fastmcp import Context  # noqa: TC002 — FastMCP resolves annotations at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient

from homeassistant_mcp.server import mcp

WRITE_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def _get_ha(ctx: Context[Any, Any]) -> HomeAssistantClient:
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


@mcp.tool(annotations=WRITE_ACTION)
async def ha_trigger_automation(
    ctx: Context[Any, Any],
    entity_id: str,
) -> str:
    """Trigger a Home Assistant automation to run immediately.

    The automation runs once regardless of its conditions.

    Example: entity_id="automation.turn_on_porch_lights_at_sunset"
    """
    ha = _get_ha(ctx)

    await ha.call_service("automation", "trigger", {"entity_id": entity_id})

    entity = await ha.get_state(entity_id)
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", entity_id)
    state = entity.get("state", "unknown")
    last_triggered = attrs.get("last_triggered", "never")

    return (
        f"Triggered **{name}**\n"
        f"- **Entity:** `{entity_id}`\n"
        f"- **State:** {state}\n"
        f"- **Last triggered:** {last_triggered}"
    )


@mcp.tool(annotations=WRITE_ACTION)
async def ha_set_automation(
    ctx: Context[Any, Any],
    entity_id: str,
    action: Literal["enable", "disable"],
) -> str:
    """Enable or disable a Home Assistant automation.

    Disabled automations will not fire on their triggers until re-enabled.

    Examples:
    - Disable: entity_id="automation.motion_lights", action="disable"
    - Enable: entity_id="automation.motion_lights", action="enable"
    """
    ha = _get_ha(ctx)

    service = "turn_on" if action == "enable" else "turn_off"
    await ha.call_service("automation", service, {"entity_id": entity_id})

    entity = await ha.get_state(entity_id)
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", entity_id)
    state = entity.get("state", "unknown")

    return f"**{name}** is now **{state}**\n- **Entity:** `{entity_id}`"
