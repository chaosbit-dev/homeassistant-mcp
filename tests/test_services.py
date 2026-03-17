"""Tests for Phase 2 service/action tools — client-level tests."""

from __future__ import annotations

import httpx
import respx

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from tests.conftest import SAMPLE_STATES


@respx.mock
async def test_call_service_with_data() -> None:
    settings = Settings(url="http://ha.test:8123", token="test-token")
    client = HomeAssistantClient(settings)
    await client.start()

    updated_state = {**SAMPLE_STATES[0], "state": "off"}
    respx.post("http://ha.test:8123/api/services/light/turn_off").mock(
        return_value=httpx.Response(200, json=[updated_state])
    )

    result = await client.call_service(
        "light", "turn_off", {"entity_id": "light.living_room"}
    )
    assert len(result) == 1
    assert result[0]["state"] == "off"


@respx.mock
async def test_call_service_empty_data() -> None:
    settings = Settings(url="http://ha.test:8123", token="test-token")
    client = HomeAssistantClient(settings)
    await client.start()

    respx.post("http://ha.test:8123/api/services/homeassistant/toggle").mock(
        return_value=httpx.Response(200, json=[SAMPLE_STATES[4]])
    )

    result = await client.call_service(
        "homeassistant", "toggle", {"entity_id": "switch.porch_light"}
    )
    assert len(result) == 1


@respx.mock
async def test_history_endpoint() -> None:
    settings = Settings(url="http://ha.test:8123", token="test-token")
    client = HomeAssistantClient(settings)
    await client.start()

    history_data = [
        [
            {
                "entity_id": "sensor.outdoor_temperature",
                "state": "80",
                "attributes": {
                    "friendly_name": "Outdoor Temperature",
                    "unit_of_measurement": "\u00b0F",
                },
                "last_changed": "2026-03-16T08:00:00+00:00",
            },
            {
                "entity_id": "sensor.outdoor_temperature",
                "state": "85",
                "attributes": {
                    "friendly_name": "Outdoor Temperature",
                    "unit_of_measurement": "\u00b0F",
                },
                "last_changed": "2026-03-16T10:00:00+00:00",
            },
        ]
    ]

    respx.get(url__startswith="http://ha.test:8123/api/history/period/").mock(
        return_value=httpx.Response(200, json=history_data)
    )

    result = await client.get_history(
        entity_id="sensor.outdoor_temperature",
        start="2026-03-16T00:00:00+00:00",
        end="2026-03-16T12:00:00+00:00",
    )
    assert len(result) == 1
    assert len(result[0]) == 2
    assert result[0][0]["state"] == "80"
    assert result[0][1]["state"] == "85"


@respx.mock
async def test_climate_service_call() -> None:
    settings = Settings(url="http://ha.test:8123", token="test-token")
    client = HomeAssistantClient(settings)
    await client.start()

    respx.post("http://ha.test:8123/api/services/climate/set_temperature").mock(
        return_value=httpx.Response(200, json=[SAMPLE_STATES[2]])
    )

    result = await client.call_service(
        "climate",
        "set_temperature",
        {"entity_id": "climate.living_room", "temperature": 72},
    )
    assert len(result) == 1
    assert result[0]["entity_id"] == "climate.living_room"
