"""Tests for the camera snapshot tool and its binary client path."""

from __future__ import annotations

import io

import httpx
import pytest
import respx
from mcp.server.mcpserver import Image
from PIL import Image as PILImage

from homeassistant_mcp.client import CameraUnavailableError, HomeAssistantClient
from homeassistant_mcp.config import Settings
from homeassistant_mcp.tools.cameras import (
    _recompress,
    _resolve_camera_entity,
    ha_get_camera_snapshot,
)


def _make_jpeg(width: int, height: int, color: tuple[int, int, int] = (40, 80, 120)) -> bytes:
    """Generate a small solid-color JPEG for use as a fake camera frame."""
    img = PILImage.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _FakeContext:
    """Minimal stand-in for MCPServer Context exposing the lifespan context."""

    def __init__(self, ha: HomeAssistantClient, settings: Settings) -> None:
        self.request_context = type(
            "_Req", (), {"lifespan_context": {"ha": ha, "settings": settings}}
        )()


@pytest.fixture
def settings() -> Settings:
    return Settings(url="http://ha.test:8123", token="test-token")


@pytest.fixture
async def client(settings: Settings) -> HomeAssistantClient:
    c = HomeAssistantClient(settings)
    await c.start()
    return c


# ── Entity resolution ────────────────────────────────────────────


class TestResolveCameraEntity:
    def test_camera_passthrough(self) -> None:
        assert _resolve_camera_entity("camera.front_door") == "camera.front_door"

    def test_occupancy_sensor_maps_to_camera(self) -> None:
        assert (
            _resolve_camera_entity("binary_sensor.front_door_car_occupancy") == "camera.front_door"
        )

    def test_multi_word_camera_name(self) -> None:
        assert (
            _resolve_camera_entity("binary_sensor.front_yard_west_person_occupancy")
            == "camera.front_yard_west"
        )

    def test_bad_entity_id_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot resolve"):
            _resolve_camera_entity("light.kitchen")

    def test_non_occupancy_binary_sensor_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot resolve"):
            _resolve_camera_entity("binary_sensor.front_door_motion")


# ── Recompression ────────────────────────────────────────────────


class TestRecompress:
    def test_large_image_is_downscaled(self) -> None:
        raw = _make_jpeg(4000, 3000)
        out = _recompress(raw, max_dimension=1280, quality=75)
        with PILImage.open(io.BytesIO(out)) as img:
            assert max(img.width, img.height) == 1280
            assert img.width == 1280
        assert len(out) < len(raw)

    def test_small_image_not_upscaled(self) -> None:
        raw = _make_jpeg(640, 480)
        out = _recompress(raw, max_dimension=1280, quality=75)
        with PILImage.open(io.BytesIO(out)) as img:
            assert img.width == 640
            assert img.height == 480


# ── Client binary fetch ──────────────────────────────────────────


class TestGetCameraImage:
    @respx.mock
    async def test_returns_bytes(self, client: HomeAssistantClient) -> None:
        raw = _make_jpeg(100, 100)
        respx.get("http://ha.test:8123/api/camera_proxy/camera.front_door").mock(
            return_value=httpx.Response(200, content=raw)
        )
        result = await client.get_camera_image("camera.front_door")
        assert result == raw

    @respx.mock
    async def test_non_200_raises_camera_unavailable(self, client: HomeAssistantClient) -> None:
        respx.get("http://ha.test:8123/api/camera_proxy/camera.broken").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(CameraUnavailableError, match=r"camera\.broken"):
            await client.get_camera_image("camera.broken")


# ── Tool end-to-end ──────────────────────────────────────────────


class TestCameraSnapshotTool:
    @respx.mock
    async def test_returns_image_content_block(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        raw = _make_jpeg(800, 600)
        respx.get("http://ha.test:8123/api/camera_proxy/camera.front_door").mock(
            return_value=httpx.Response(200, content=raw)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_camera_snapshot(ctx, entity_id="camera.front_door")

        # Must be a real Image content block, not a base64 string.
        assert isinstance(result, Image)
        assert not isinstance(result, str)
        content = result.to_image_content()
        assert content.type == "image"
        # mcp 2.x renamed the ImageContent field mimeType -> mime_type (the JSON
        # wire key is unchanged; only the Python attribute moved).
        assert content.mime_type == "image/jpeg"

    @respx.mock
    async def test_large_frame_is_downscaled(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        raw = _make_jpeg(3840, 2160)
        respx.get("http://ha.test:8123/api/camera_proxy/camera.backyard").mock(
            return_value=httpx.Response(200, content=raw)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_camera_snapshot(ctx, entity_id="camera.backyard")

        assert result.data is not None
        with PILImage.open(io.BytesIO(result.data)) as img:
            assert max(img.width, img.height) == settings.camera_max_dimension
        assert len(result.data) < len(raw)

    @respx.mock
    async def test_occupancy_sensor_resolves_to_camera(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        raw = _make_jpeg(640, 480)
        route = respx.get("http://ha.test:8123/api/camera_proxy/camera.front_door").mock(
            return_value=httpx.Response(200, content=raw)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_camera_snapshot(
            ctx, entity_id="binary_sensor.front_door_car_occupancy"
        )
        assert route.called
        assert isinstance(result, Image)

    @respx.mock
    async def test_unavailable_camera_raises_clean_error(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get("http://ha.test:8123/api/camera_proxy/camera.front_door").mock(
            return_value=httpx.Response(503)
        )
        ctx = _FakeContext(client, settings)
        with pytest.raises(CameraUnavailableError, match=r"camera\.front_door"):
            await ha_get_camera_snapshot(ctx, entity_id="camera.front_door")

    async def test_bad_entity_id_raises(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        ctx = _FakeContext(client, settings)
        with pytest.raises(ValueError, match="Cannot resolve"):
            await ha_get_camera_snapshot(ctx, entity_id="switch.porch_light")
