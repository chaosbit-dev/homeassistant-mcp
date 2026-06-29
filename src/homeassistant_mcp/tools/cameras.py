"""Camera snapshot tool — fetch and return a live camera frame as an image.

This is the only tool that returns image content (an MCP `Image` block) rather
than text. Raw HA camera frames can be several megabytes at full resolution, so
each frame is downscaled and recompressed before being handed to the model.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import anyio.to_thread
from mcp.server.fastmcp import (
    Context,
    Image,
)
from mcp.types import ToolAnnotations
from PIL import Image as PILImage

if TYPE_CHECKING:
    from homeassistant_mcp.client import HomeAssistantClient
    from homeassistant_mcp.config import Settings

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


def _get_settings(ctx: Context[Any, Any]) -> Settings:
    settings: Settings = ctx.request_context.lifespan_context["settings"]
    return settings


def _resolve_camera_entity(entity_id: str) -> str:
    """Resolve a user-supplied entity_id to a `camera.*` entity.

    Accepts a camera entity directly, or derives one from a Vigil occupancy
    sensor named `binary_sensor.<camera>_<object>_occupancy` by stripping the
    trailing `_<object>_occupancy` and prefixing `camera.`
    (e.g. `binary_sensor.front_door_car_occupancy` -> `camera.front_door`).
    """
    if entity_id.startswith("camera."):
        return entity_id

    if entity_id.startswith("binary_sensor.") and entity_id.endswith("_occupancy"):
        body = entity_id[len("binary_sensor.") : -len("_occupancy")]
        # body is `<camera>_<object>`; drop the trailing object segment.
        camera_name, sep, _object = body.rpartition("_")
        if sep and camera_name:
            return f"camera.{camera_name}"

    msg = (
        f"Cannot resolve `{entity_id}` to a camera. Pass a `camera.*` entity "
        "(e.g. `camera.front_door`) or a `binary_sensor.<camera>_<object>_occupancy` sensor."
    )
    raise ValueError(msg)


def _recompress(raw: bytes, max_dimension: int, quality: int) -> bytes:
    """Downscale (longest side <= max_dimension) and re-encode as JPEG.

    Runs synchronously; the caller offloads it to a worker thread so the decode
    and resize of a large frame never block the event loop. Upscaling is skipped.
    """
    with PILImage.open(io.BytesIO(raw)) as img:
        rgb = img.convert("RGB")
        longest = max(rgb.width, rgb.height)
        if longest > max_dimension:
            scale = max_dimension / longest
            new_size = (round(rgb.width * scale), round(rgb.height * scale))
            rgb = rgb.resize(new_size, PILImage.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()


@mcp.tool(annotations=READ_ONLY)
async def ha_get_camera_snapshot(
    ctx: Context[Any, Any],
    entity_id: str,
) -> Image:
    """Capture the current frame from a camera and return it as a viewable image.

    Returns an actual image the model can see (not a JSON blob or base64 text).
    The frame is downscaled and recompressed so a full-resolution feed does not
    flood context.

    `entity_id` accepts either the camera directly or a Vigil occupancy sensor,
    which is mapped to its camera automatically:
    - Camera: entity_id="camera.front_door"
    - Occupancy sensor: entity_id="binary_sensor.front_door_car_occupancy"
      (resolves to camera.front_door)

    Live cameras: camera.front_door, camera.front_yard_west, camera.front_yard_east,
    camera.backyard.

    Raises a tool error if the entity_id can't be resolved to a camera or the
    camera is unavailable.
    """
    ha = _get_ha(ctx)
    settings = _get_settings(ctx)

    camera_entity = _resolve_camera_entity(entity_id)
    raw = await ha.get_camera_image(camera_entity)

    jpeg = await anyio.to_thread.run_sync(
        _recompress,
        raw,
        settings.camera_max_dimension,
        settings.camera_jpeg_quality,
    )
    return Image(data=jpeg, format="jpeg")
