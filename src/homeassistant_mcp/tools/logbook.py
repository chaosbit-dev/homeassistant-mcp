"""Phase 2: Logbook tool — cross-entity event stream over a time window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import Context  # noqa: TC002 — MCPServer resolves annotations at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient
    from homeassistant_mcp.config import Settings

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


def _get_settings(ctx: Context[Any, Any]) -> Settings:
    settings: Settings = ctx.request_context.lifespan_context["settings"]
    return settings


def _format_when(when: str) -> str:
    """Format a logbook `when` ISO timestamp to match history's table format."""
    if not when:
        return "—"
    try:
        dt = datetime.fromisoformat(when)
    except (ValueError, TypeError):
        return when
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool(annotations=READ_ONLY)
async def ha_get_logbook(
    ctx: Context[Any, Any],
    hours: int = 1,
    entity_id: str | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Get the cross-entity logbook — what happened across the house in a window.

    Where `ha_get_history` answers "how did *this one entity* change over time,"
    the logbook answers "what happened *across the whole house* in the last N
    hours." It's the natural pair to your Frigate occupancy sensors for a
    "catch me up on the property" query.

    The logbook is high-volume — a busy unfiltered window can hold thousands of
    events (Frigate image entities and sensors churn constantly). By default,
    broad (no-`entity_id`) queries exclude noisy domains (see
    HA_LOGBOOK_EXCLUDE_DOMAINS; defaults to `image`, the Frigate snapshot
    entities like image.backyard_bird that otherwise dominate the stream).
    Results are then capped (see HA_LOGBOOK_MAX_EVENTS), keeping the most recent.

    Examples:
    - Catch me up on the last hour across the house: hours=1
    - What happened overnight: hours=8
    - Just the front door, last 6 hours: entity_id="binary_sensor.front_door_motion", hours=6
    - Include everything (even noisy image events): exclude_domains=[]
    - Also drop sensor churn: exclude_domains=["image", "sensor"]

    Prefer `ha_get_history` when you care about one entity's state values over
    time; prefer this when you want the chronological event stream across many
    entities.
    """
    ha = _get_ha(ctx)
    settings = _get_settings(ctx)

    hours = max(1, min(168, hours))  # Clamp to 1h-7d

    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=hours)

    events = await ha.get_logbook(
        start=start.isoformat(),
        end=end.isoformat(),
        entity=entity_id,
    )

    scope = f" for `{entity_id}`" if entity_id else ""

    # Drop noisy domains on broad queries (the `image` domain is Frigate snapshot
    # entities that flood the stream). Skipped when an entity_id is given — the
    # caller asked for that entity specifically. Per-call exclude_domains overrides
    # the configured default; pass [] to include everything.
    hidden = 0
    excluded: set[str] = set()
    if entity_id is None:
        source = (
            exclude_domains if exclude_domains is not None else settings.logbook_exclude_domains
        )
        excluded = {d.strip().lower() for d in source if d.strip()}
        if excluded:
            kept = [
                e
                for e in events
                if str(e.get("entity_id", "")).split(".", 1)[0].lower() not in excluded
            ]
            hidden = len(events) - len(kept)
            events = kept

    if not events:
        base = f"No logbook events{scope} in the last {hours} hours."
        if hidden:
            base += f" ({hidden} hidden from excluded domains: {', '.join(sorted(excluded))}.)"
        return base

    total = len(events)
    cap = settings.logbook_max_events
    truncated = total > cap
    # Keep the most recent events when over the cap, but still display oldest-first
    # for chronological consistency with ha_get_history.
    shown = events[-cap:] if truncated else events

    heading = f"## Logbook{scope} — Last {hours}h"
    summary = f"**{len(shown)} of {total} events**" if truncated else f"**{total} events**"

    lines = [
        heading,
        f"{summary}\n",
        "| Time | Name | What |",
        "| --- | --- | --- |",
    ]

    for event in shown:
        time_str = _format_when(str(event.get("when", "")))
        name = str(event.get("name", event.get("entity_id", "—")) or "—")
        # Non-state events carry `message`; state changes carry `state`. Prefer
        # whichever is present, degrading gracefully when neither exists.
        what = event.get("message", event.get("state"))
        what_str = str(what) if what is not None else "—"
        lines.append(f"| {time_str} | {name} | {what_str} |")

    if truncated:
        lines.append(
            f"\n_Showing {len(shown)} of {total} events — "
            f"narrow the window (lower `hours`) or pass an `entity_id`._"
        )

    if hidden:
        lines.append(
            f"\n_Hid {hidden} events from excluded domains "
            f"({', '.join(sorted(excluded))}). Pass `exclude_domains=[]` to include them._"
        )

    return "\n".join(lines)
