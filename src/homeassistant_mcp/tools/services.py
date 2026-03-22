"""Phase 2: Service action tools — call_service, toggle, climate, lights."""

from __future__ import annotations

import json
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


def _format_service_result(
    domain: str, service: str, entity_id: str | None, result: list[dict[str, Any]]
) -> str:
    """Format service call result as readable output."""
    lines = [f"Called `{domain}.{service}`"]
    if entity_id:
        lines[0] += f" on `{entity_id}`"

    if result:
        for entity in result:
            eid = entity.get("entity_id", "")
            state = entity.get("state", "unknown")
            attrs = entity.get("attributes", {})
            name = attrs.get("friendly_name", eid)
            lines.append(f"- **{name}** (`{eid}`): {state}")
    else:
        lines.append("Service executed successfully (no state returned).")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_call_service(
    ctx: Context[Any, Any],
    domain: str,
    service: str,
    entity_id: str | None = None,
    data: dict[str, Any] | str | None = None,
) -> str:
    """Call any Home Assistant service. This is the generic escape hatch for any action
    not covered by convenience tools.

    The data parameter accepts a JSON string of key-value pairs for additional service data.

    Examples:
    - Turn on a light: domain="light", service="turn_on", entity_id="light.kitchen"
    - Set volume: domain="media_player", service="volume_set",
      entity_id="media_player.living_room", data='{"volume_level": 0.5}'
    - Lock a door: domain="lock", service="lock", entity_id="lock.front_door"
    """
    ha = _get_ha(ctx)

    service_data: dict[str, Any] = {}
    if isinstance(data, str):
        service_data = json.loads(data)
    elif data:
        service_data = data
    if entity_id:
        service_data["entity_id"] = entity_id

    result = await ha.call_service(domain, service, service_data)
    return _format_service_result(domain, service, entity_id, result)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_toggle(
    ctx: Context[Any, Any],
    entity_id: str,
    action: Literal["on", "off", "toggle"] = "toggle",
) -> str:
    """Toggle a light, switch, fan, or other toggleable entity on, off, or toggle.

    Works with any entity that supports homeassistant.turn_on/turn_off/toggle.

    Examples:
    - Toggle porch light: entity_id="switch.porch_light", action="toggle"
    - Turn on kitchen light: entity_id="light.kitchen", action="on"
    - Turn off fan: entity_id="fan.bedroom", action="off"
    """
    ha = _get_ha(ctx)
    domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"

    service_map = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}
    service = service_map[action]

    result = await ha.call_service(domain, service, {"entity_id": entity_id})
    return _format_service_result(domain, service, entity_id, result)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_set_climate(
    ctx: Context[Any, Any],
    entity_id: str,
    temperature: float | None = None,
    hvac_mode: Literal["heat", "cool", "heat_cool", "auto", "off"] | None = None,
) -> str:
    """Set thermostat temperature and/or HVAC mode.

    Temperature is in the unit configured in Home Assistant (typically °F).
    At least one of temperature or hvac_mode must be provided.

    Examples:
    - Set to 72°F: entity_id="climate.living_room", temperature=72
    - Cool mode at 70: entity_id="climate.living_room", temperature=70, hvac_mode="cool"
    - Turn off HVAC: entity_id="climate.living_room", hvac_mode="off"
    """
    ha = _get_ha(ctx)

    if temperature is None and hvac_mode is None:
        return "Error: At least one of temperature or hvac_mode must be provided."

    if temperature is not None and not (55 <= temperature <= 90):
        return f"Error: Temperature {temperature}°F is outside safe range (55-90°F)."

    service_data: dict[str, Any] = {"entity_id": entity_id}

    if hvac_mode:
        await ha.call_service("climate", "set_hvac_mode", {
            "entity_id": entity_id,
            "hvac_mode": hvac_mode,
        })

    if temperature is not None:
        service_data["temperature"] = temperature
        await ha.call_service("climate", "set_temperature", service_data)

    # Fetch updated state
    entity = await ha.get_state(entity_id)
    attrs = entity.get("attributes", {})
    lines = [
        f"## {attrs.get('friendly_name', entity_id)}",
        f"- **HVAC mode:** {entity.get('state', 'unknown')}",
    ]
    current = attrs.get("current_temperature")
    target = attrs.get("temperature")
    if current is not None:
        lines.append(f"- **Current temp:** {current}°F")
    if target is not None:
        lines.append(f"- **Target temp:** {target}°F")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE_ACTION)
async def ha_set_light(
    ctx: Context[Any, Any],
    entity_id: str,
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
    turn_on: bool = True,
) -> str:
    """Control a light's brightness and color temperature.

    Brightness is 0-100 percent. Color temperature is in Kelvin (2000=warm, 6500=cool).
    By default turns the light on; set turn_on=False to adjust without turning on.

    Examples:
    - Dim to 50%: entity_id="light.living_room", brightness_pct=50
    - Warm and dim: entity_id="light.bedroom", brightness_pct=30, color_temp_kelvin=2700
    - Turn off: entity_id="light.kitchen", turn_on=False
    """
    ha = _get_ha(ctx)

    if not turn_on:
        result = await ha.call_service("light", "turn_off", {"entity_id": entity_id})
        return _format_service_result("light", "turn_off", entity_id, result)

    service_data: dict[str, Any] = {"entity_id": entity_id}

    if brightness_pct is not None:
        brightness_pct = max(0, min(100, brightness_pct))
        service_data["brightness_pct"] = brightness_pct

    if color_temp_kelvin is not None:
        service_data["color_temp_kelvin"] = color_temp_kelvin

    result = await ha.call_service("light", "turn_on", service_data)

    # Fetch updated state
    entity = await ha.get_state(entity_id)
    attrs = entity.get("attributes", {})
    state = entity.get("state", "unknown")
    lines = [f"## {attrs.get('friendly_name', entity_id)}", f"- **State:** {state}"]

    brightness = attrs.get("brightness")
    if brightness is not None:
        pct = round(int(brightness) / 255 * 100)
        lines.append(f"- **Brightness:** {pct}%")

    ct = attrs.get("color_temp_kelvin")
    if ct is not None:
        lines.append(f"- **Color temp:** {ct}K")

    return "\n".join(lines)
