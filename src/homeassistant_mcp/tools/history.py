"""Phase 2: History tool — entity state history lookup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import Context  # noqa: TC002 — MCPServer resolves annotations at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient

from homeassistant_mcp.server import mcp

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
async def ha_get_history(
    ctx: Context[Any, Any],
    entity_id: str,
    hours: int = 24,
) -> str:
    """Get state change history for an entity over a time period.

    Returns a chronological list of state changes. Useful for understanding
    patterns, troubleshooting, or checking when something last changed.

    Examples:
    - Last 24 hours of thermostat: entity_id="climate.living_room"
    - Last 4 hours of front door motion: entity_id="binary_sensor.front_door_motion", hours=4
    - Freezer temp over 48 hours: entity_id="sensor.deep_freezer_temperature", hours=48
    """
    ha = _get_ha(ctx)

    hours = max(1, min(168, hours))  # Clamp to 1h-7d

    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=hours)

    history = await ha.get_history(
        entity_id=entity_id,
        start=start.isoformat(),
        end=end.isoformat(),
    )

    if not history or not history[0]:
        return f"No history found for `{entity_id}` in the last {hours} hours."

    states = history[0]

    # Get entity name from first entry
    first = states[0]
    attrs: dict[str, Any] = first.get("attributes", {})
    name = attrs.get("friendly_name", entity_id)
    unit = attrs.get("unit_of_measurement", "")

    lines = [
        f"## {name} — Last {hours}h",
        f"**{len(states)} state changes**\n",
        "| Time | State |",
        "| --- | --- |",
    ]

    for entry in states:
        state = entry.get("state", "unknown")
        last_changed = entry.get("last_changed", "")

        # Parse and format timestamp
        if last_changed:
            try:
                dt = datetime.fromisoformat(last_changed)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                time_str = last_changed
        else:
            time_str = "—"

        display_state = f"{state} {unit}" if unit else state
        lines.append(f"| {time_str} | {display_state} |")

    return "\n".join(lines)
