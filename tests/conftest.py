"""Shared fixtures for HA MCP tests."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(url="http://localhost:8123", token="test-token")


@pytest.fixture
def ha_client(settings: Settings) -> HomeAssistantClient:
    return HomeAssistantClient(settings)


SAMPLE_STATES: list[dict[str, Any]] = [
    {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {
            "friendly_name": "Living Room Light",
            "brightness": 200,
            "color_mode": "brightness",
        },
        "last_changed": "2026-03-16T10:00:00+00:00",
        "last_updated": "2026-03-16T10:00:00+00:00",
    },
    {
        "entity_id": "light.kitchen",
        "state": "off",
        "attributes": {"friendly_name": "Kitchen Light"},
        "last_changed": "2026-03-16T09:00:00+00:00",
        "last_updated": "2026-03-16T09:00:00+00:00",
    },
    {
        "entity_id": "climate.living_room",
        "state": "cool",
        "attributes": {
            "friendly_name": "Living Room Thermostat",
            "current_temperature": 73,
            "temperature": 70,
            "hvac_modes": ["heat", "cool", "heat_cool", "off"],
        },
        "last_changed": "2026-03-16T08:00:00+00:00",
        "last_updated": "2026-03-16T08:30:00+00:00",
    },
    {
        "entity_id": "sensor.outdoor_temperature",
        "state": "85",
        "attributes": {
            "friendly_name": "Outdoor Temperature",
            "unit_of_measurement": "°F",
            "device_class": "temperature",
        },
        "last_changed": "2026-03-16T10:05:00+00:00",
        "last_updated": "2026-03-16T10:05:00+00:00",
    },
    {
        "entity_id": "switch.porch_light",
        "state": "on",
        "attributes": {"friendly_name": "Porch Light"},
        "last_changed": "2026-03-16T07:00:00+00:00",
        "last_updated": "2026-03-16T07:00:00+00:00",
    },
]

SAMPLE_AREAS: list[dict[str, Any]] = [
    {
        "area_id": "living_room",
        "name": "Living Room",
        "entities": ["light.living_room", "climate.living_room"],
    },
    {
        "area_id": "kitchen",
        "name": "Kitchen",
        "entities": ["light.kitchen"],
    },
    {
        "area_id": "outdoor",
        "name": "Outdoor",
        "entities": ["sensor.outdoor_temperature", "switch.porch_light"],
    },
]
