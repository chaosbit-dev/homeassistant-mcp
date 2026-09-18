"""Tests for the ha_media_player_control action tool."""

from __future__ import annotations

import httpx
import pytest
import respx

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from homeassistant_mcp.media.deeplinks import disney_plus_deeplink
from homeassistant_mcp.tools.media import ha_media_player_control

ENTITY = "media_player.living_room"
DENON = "media_player.denon_avr_x3800h"


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


def _mock_state(entity_id: str, state: str = "playing") -> None:
    respx.get(f"http://ha.test:8123/api/states/{entity_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entity_id": entity_id,
                "state": state,
                "attributes": {"friendly_name": entity_id},
            },
        )
    )


def _mock_service(service: str, entity_id: str) -> respx.Route:
    return respx.post(f"http://ha.test:8123/api/services/media_player/{service}").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "entity_id": entity_id,
                    "state": "playing",
                    "attributes": {"friendly_name": entity_id},
                }
            ],
        )
    )


# ── action -> service mapping ─────────────────────────────────────


class TestActionServiceMapping:
    @respx.mock
    @pytest.mark.parametrize(
        ("action", "service"),
        [
            ("play", "media_play"),
            ("pause", "media_pause"),
            ("stop", "media_stop"),
            ("on", "turn_on"),
            ("off", "turn_off"),
        ],
    )
    async def test_simple_actions(
        self,
        client: HomeAssistantClient,
        settings: Settings,
        action: str,
        service: str,
    ) -> None:
        _mock_state(ENTITY)
        route = _mock_service(service, ENTITY)
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id=ENTITY, action=action)

        assert route.called
        sent = route.calls.last.request
        assert b'"entity_id"' in sent.content
        assert f"media_player.{service}" in result


# ── volume mapping + clamping ─────────────────────────────────────


class TestVolume:
    @respx.mock
    async def test_volume_pct_maps_to_level(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(DENON)
        route = _mock_service("volume_set", DENON)
        ctx = _FakeContext(client, settings)

        await ha_media_player_control(ctx, entity_id=DENON, action="volume", volume_pct=30)

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["volume_level"] == pytest.approx(0.3)
        assert body["entity_id"] == DENON

    @respx.mock
    async def test_volume_clamps_above_100(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(DENON)
        route = _mock_service("volume_set", DENON)
        ctx = _FakeContext(client, settings)

        await ha_media_player_control(ctx, entity_id=DENON, action="volume", volume_pct=250)

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["volume_level"] == pytest.approx(1.0)

    @respx.mock
    async def test_volume_clamps_below_zero(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(DENON)
        route = _mock_service("volume_set", DENON)
        ctx = _FakeContext(client, settings)

        await ha_media_player_control(ctx, entity_id=DENON, action="volume", volume_pct=-20)

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["volume_level"] == pytest.approx(0.0)

    @respx.mock
    async def test_volume_missing_pct_errors(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(DENON)
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id=DENON, action="volume")
        assert result.startswith("Error:")
        assert "volume_pct" in result


# ── source selection ──────────────────────────────────────────────


class TestSource:
    @respx.mock
    async def test_select_source(self, client: HomeAssistantClient, settings: Settings) -> None:
        _mock_state(DENON)
        route = _mock_service("select_source", DENON)
        ctx = _FakeContext(client, settings)

        await ha_media_player_control(
            ctx, entity_id=DENON, action="source", source="PlayStation 5"
        )

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["source"] == "PlayStation 5"

    @respx.mock
    async def test_source_missing_errors(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(DENON)
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id=DENON, action="source")
        assert result.startswith("Error:")
        assert "source" in result


# ── play_media / deep links ───────────────────────────────────────


class TestPlayMedia:
    @respx.mock
    async def test_disney_plus_deeplink_payload(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(ENTITY)
        route = _mock_service("play_media", ENTITY)
        ctx = _FakeContext(client, settings)

        content_id, content_type = disney_plus_deeplink("abc123")
        await ha_media_player_control(
            ctx,
            entity_id=ENTITY,
            action="play_media",
            media_content_id=content_id,
            media_content_type=content_type,
        )

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["media_content_id"] == "https://www.disneyplus.com/browse/entity-abc123"
        assert body["media_content_type"] == "url"
        assert body["entity_id"] == ENTITY

    @respx.mock
    async def test_play_media_missing_content_id_errors(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(ENTITY)
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id=ENTITY, action="play_media")
        assert result.startswith("Error:")
        assert "media_content_id" in result


# ── unavailable / failure handling ────────────────────────────────


class TestFailureHandling:
    @respx.mock
    async def test_unavailable_player_clean_error(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state("media_player.master_tv", state="unavailable")
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(
            ctx, entity_id="media_player.master_tv", action="pause"
        )
        assert result.startswith("Error:")
        assert "unavailable" in result

    @respx.mock
    async def test_unknown_entity_clean_error(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get("http://ha.test:8123/api/states/media_player.ghost").mock(
            return_value=httpx.Response(404)
        )
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id="media_player.ghost", action="play")
        assert result.startswith("Error:")
        assert "not found" in result

    @respx.mock
    async def test_service_call_failure_clean_error(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        _mock_state(ENTITY)
        respx.post("http://ha.test:8123/api/services/media_player/media_play").mock(
            return_value=httpx.Response(500)
        )
        ctx = _FakeContext(client, settings)

        result = await ha_media_player_control(ctx, entity_id=ENTITY, action="play")
        assert result.startswith("Error:")

    async def test_bad_action_clean_error(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        ctx = _FakeContext(client, settings)
        result = await ha_media_player_control(ctx, entity_id=ENTITY, action="explode")
        assert result.startswith("Error:")
        assert "Unknown action" in result
