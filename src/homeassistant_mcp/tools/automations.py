"""Phase 2: Automation tools — trigger, enable/disable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.server.mcpserver import Context  # noqa: TC002 — MCPServer resolves annotations at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient

from homeassistant_mcp.server import mcp

WRITE_ACTION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _get_ha(ctx: Context[Any, Any]) -> HomeAssistantClient:
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


@mcp.tool(annotations=READ_ONLY)
async def ha_list_automations(ctx: Context[Any, Any]) -> str:
    """List automations with their enabled state and when each last fired.

    Where `ha_list_entities(domain="automation")` gives the bare list, this
    surfaces the automation-specific bits: whether each is enabled (on) or
    disabled (off), and its `last_triggered` time — answering "does this exist,
    and did it actually run." Use the entity_id with `ha_trigger_automation`
    or `ha_set_automation`.
    """
    ha = _get_ha(ctx)
    states = await ha.get_states()
    autos = [s for s in states if str(s.get("entity_id", "")).startswith("automation.")]
    if not autos:
        return "No automations found."

    autos.sort(
        key=lambda s: str(
            s.get("attributes", {}).get("friendly_name", s.get("entity_id", ""))
        ).lower()
    )

    enabled_label = {"on": "✅ on", "off": "⛔ off"}
    lines = [
        "## Automations",
        f"**{len(autos)} automations**\n",
        "| Name | Entity ID | Enabled | Last triggered |",
        "| --- | --- | --- | --- |",
    ]
    for s in autos:
        attrs = s.get("attributes", {})
        eid = str(s.get("entity_id", ""))
        name = str(attrs.get("friendly_name", eid))
        state = str(s.get("state", "unknown"))
        enabled = enabled_label.get(state, state)  # unavailable/unknown shown as-is
        last = attrs.get("last_triggered") or "never"
        lines.append(f"| {name} | `{eid}` | {enabled} | {last} |")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_trigger_automation(
    ctx: Context[Any, Any],
    entity_id: str,
    skip_condition: bool = False,
) -> str:
    """Trigger a Home Assistant automation to run now.

    By default the automation's own conditions are respected
    (skip_condition=False): it runs its action sequence only if its conditions
    currently pass, exactly as if its trigger had fired normally. Set
    skip_condition=True to force the actions to run regardless of conditions
    (Home Assistant's raw default for this service) — this bypasses the
    automation's own guards, so use it deliberately.

    Examples:
    - Respect conditions (safe default): entity_id="automation.morning_routine"
    - Force-run, ignore conditions: entity_id="automation.morning_routine", skip_condition=True
    """
    ha = _get_ha(ctx)

    await ha.call_service(
        "automation",
        "trigger",
        {"entity_id": entity_id, "skip_condition": skip_condition},
    )

    entity = await ha.get_state(entity_id)
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", entity_id)
    state = entity.get("state", "unknown")
    last_triggered = attrs.get("last_triggered", "never")

    mode = "ignoring conditions" if skip_condition else "respecting conditions"
    return (
        f"Triggered **{name}** ({mode})\n"
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
