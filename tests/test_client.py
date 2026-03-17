"""Tests for HomeAssistantClient."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from tests.conftest import SAMPLE_AREAS, SAMPLE_STATES


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(url="http://ha.test:8123", token="test-token-123")


@pytest.fixture
async def client(mock_settings: Settings) -> HomeAssistantClient:
    c = HomeAssistantClient(mock_settings)
    await c.start()
    return c


class TestGetStates:
    @respx.mock
    async def test_get_states(self, client: HomeAssistantClient) -> None:
        respx.get("http://ha.test:8123/api/states").mock(
            return_value=httpx.Response(200, json=SAMPLE_STATES)
        )
        result = await client.get_states()
        assert len(result) == 5
        assert result[0]["entity_id"] == "light.living_room"

    @respx.mock
    async def test_get_single_state(self, client: HomeAssistantClient) -> None:
        respx.get("http://ha.test:8123/api/states/light.kitchen").mock(
            return_value=httpx.Response(200, json=SAMPLE_STATES[1])
        )
        result = await client.get_state("light.kitchen")
        assert result["state"] == "off"
        assert result["attributes"]["friendly_name"] == "Kitchen Light"


class TestRenderTemplate:
    @respx.mock
    async def test_render_template(self, client: HomeAssistantClient) -> None:
        respx.post("http://ha.test:8123/api/template").mock(
            return_value=httpx.Response(200, text='["living_room","kitchen"]')
        )
        result = await client.render_template("{{ areas() | to_json }}")
        assert json.loads(result) == ["living_room", "kitchen"]


class TestGetAreas:
    @respx.mock
    async def test_get_areas(self, client: HomeAssistantClient) -> None:
        respx.post("http://ha.test:8123/api/template").mock(
            return_value=httpx.Response(200, text=json.dumps(SAMPLE_AREAS))
        )
        result = await client.get_areas()
        assert len(result) == 3
        assert result[0]["name"] == "Living Room"
        assert "light.living_room" in result[0]["entities"]

    @respx.mock
    async def test_get_entity_area_map(self, client: HomeAssistantClient) -> None:
        respx.post("http://ha.test:8123/api/template").mock(
            return_value=httpx.Response(200, text=json.dumps(SAMPLE_AREAS))
        )
        result = await client.get_entity_area_map()
        assert result["light.living_room"] == "Living Room"
        assert result["light.kitchen"] == "Kitchen"
        assert result["switch.porch_light"] == "Outdoor"


class TestCallService:
    @respx.mock
    async def test_call_service(self, client: HomeAssistantClient) -> None:
        respx.post("http://ha.test:8123/api/services/light/turn_off").mock(
            return_value=httpx.Response(200, json=[SAMPLE_STATES[0]])
        )
        result = await client.call_service("light", "turn_off", {"entity_id": "light.living_room"})
        assert len(result) == 1


class TestClientLifecycle:
    async def test_client_property_raises_before_start(self, mock_settings: Settings) -> None:
        c = HomeAssistantClient(mock_settings)
        with pytest.raises(RuntimeError, match="Client not started"):
            _ = c.client
