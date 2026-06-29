"""Media-player control tool — typed `action` switch over media_player services.

One typed tool is far more legible to the model than remembering eight service
names (`media_play`, `volume_set`, `select_source`, `play_media`, ...) and their
data shapes. `volume_pct` (0-100) is mapped to HA's `volume_level` (0.0-1.0)
server-side, mirroring how `ha_set_light` maps `brightness_pct`.

Deep-link knowledge (Disney+ format, the Netflix metadata gap) lives in
`homeassistant_mcp.media.deeplinks`, not inline here, so app knowledge stays
maintainable as data.

Netflix caveat (confirmed live on `media_player.living_room`): an Apple TV
exposes `app_id`/`app_name` but not `media_title` while Netflix is playing, so
this tool can launch and control Netflix but cannot report what is playing — a
Companion-protocol limitation, not a bug.

Security: this is an action (write) tool. The HTTP surface is gated by Cloudflare
Access OAuth plus the in-server `Cf-Access-Jwt-Assertion` middleware, which fronts
every request — reads and actions alike — so all action tools already sit behind
auth. Media control is low-consequence (worst case: wrong thing plays / volume),
so no per-tool allowlist is needed for v1; it just stays on the action side of the
surface (no `readOnlyHint`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
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

MediaAction = Literal["play", "pause", "stop", "volume", "source", "play_media", "on", "off"]

#: action -> media_player service name.
_SERVICE_MAP: dict[str, str] = {
    "play": "media_play",
    "pause": "media_pause",
    "stop": "media_stop",
    "volume": "volume_set",
    "source": "select_source",
    "play_media": "play_media",
    "on": "turn_on",
    "off": "turn_off",
}


def _get_ha(ctx: Context[Any, Any]) -> HomeAssistantClient:
    ha: HomeAssistantClient = ctx.request_context.lifespan_context["ha"]
    return ha


def _format_service_result(service: str, entity_id: str, result: list[dict[str, Any]]) -> str:
    """Format a media_player service-call result as readable output."""
    lines = [f"Called `media_player.{service}` on `{entity_id}`"]
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
async def ha_media_player_control(
    ctx: Context[Any, Any],
    entity_id: str,
    action: str,
    volume_pct: int | None = None,
    source: str | None = None,
    media_content_id: str | None = None,
    media_content_type: str | None = None,
) -> str:
    """Control a media player: play/pause/stop, volume, source, app/deep-link launch, on/off.

    `action` is one of:
    - `play` / `pause` / `stop` — transport control (media_play/pause/stop)
    - `volume` — set volume; requires `volume_pct` (0-100, mapped to 0.0-1.0)
    - `source` — select an input; requires `source` (e.g. "PlayStation 5", "Mac")
    - `play_media` — launch a deep link / app; requires `media_content_id`
      (and usually `media_content_type`, e.g. "url" for a Disney+ deep link)
    - `on` / `off` — power the player on/off

    Examples:
    - Pause: entity_id="media_player.living_room", action="pause"
    - Volume to 30%: entity_id="media_player.denon_avr_x3800h", action="volume", volume_pct=30
    - Select input: entity_id="media_player.denon_avr_x3800h", action="source", source="Mac"
    - Disney+ deep link on Apple TV: action="play_media",
      media_content_id="https://www.disneyplus.com/browse/entity-abc123",
      media_content_type="url"

    Netflix note: an Apple TV does not publish `media_title` while Netflix is
    playing, so this tool can launch/control Netflix but cannot report the title.

    Returns a clean error string (not an exception) if the action is unknown, a
    required parameter is missing, or the player is unavailable / the call fails.
    """
    ha = _get_ha(ctx)

    service = _SERVICE_MAP.get(action)
    if service is None:
        valid = ", ".join(_SERVICE_MAP)
        return f"Error: Unknown action `{action}`. Valid actions: {valid}."

    service_data: dict[str, Any] = {"entity_id": entity_id}

    if action == "volume":
        if volume_pct is None:
            return "Error: action `volume` requires `volume_pct` (0-100)."
        clamped = max(0, min(100, volume_pct))
        service_data["volume_level"] = clamped / 100
    elif action == "source":
        if source is None:
            return "Error: action `source` requires `source` (an input name)."
        service_data["source"] = source
    elif action == "play_media":
        if media_content_id is None:
            return "Error: action `play_media` requires `media_content_id`."
        service_data["media_content_id"] = media_content_id
        if media_content_type is not None:
            service_data["media_content_type"] = media_content_type

    # Reject up front when the player is unavailable, so the failure is a clean,
    # explanatory message rather than an opaque service error. Several players in
    # this environment (e.g. master_tv, media_room) are routinely `unavailable`.
    try:
        state = await ha.get_state(entity_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == httpx.codes.NOT_FOUND:
            return f"Error: media player `{entity_id}` was not found."
        return f"Error: could not reach `{entity_id}` (HTTP {exc.response.status_code})."
    except httpx.HTTPError:
        return f"Error: could not reach `{entity_id}` (request failed)."

    if state.get("state") == "unavailable":
        return (
            f"Error: media player `{entity_id}` is currently unavailable "
            "(powered off or disconnected). Try powering it on first."
        )

    try:
        result = await ha.call_service("media_player", service, service_data)
    except httpx.HTTPStatusError as exc:
        return (
            f"Error: `media_player.{service}` on `{entity_id}` failed "
            f"(HTTP {exc.response.status_code})."
        )
    except httpx.HTTPError:
        return f"Error: `media_player.{service}` on `{entity_id}` failed (request error)."

    return _format_service_result(service, entity_id, result)
