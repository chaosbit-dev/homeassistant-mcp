"""Tests for the logbook tool and its REST client path."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from homeassistant_mcp.tools.logbook import ha_get_logbook


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


SAMPLE_EVENTS: list[dict[str, Any]] = [
    {
        "when": "2026-06-29T08:00:00+00:00",
        "name": "Front Door Motion",
        "entity_id": "binary_sensor.front_door_motion",
        "state": "on",
    },
    {
        "when": "2026-06-29T08:01:00+00:00",
        "name": "Living Room Light",
        "entity_id": "light.living_room",
        "state": "off",
    },
]


class TestGetLogbookClient:
    @respx.mock
    async def test_returns_flat_list(self, client: HomeAssistantClient) -> None:
        route = respx.get("http://ha.test:8123/api/logbook/2026-06-29T07:00:00+00:00").mock(
            return_value=httpx.Response(200, json=SAMPLE_EVENTS)
        )
        result = await client.get_logbook(start="2026-06-29T07:00:00+00:00")
        assert route.called
        assert result == SAMPLE_EVENTS

    @respx.mock
    async def test_passes_end_and_entity_params(self, client: HomeAssistantClient) -> None:
        route = respx.get("http://ha.test:8123/api/logbook/2026-06-29T07:00:00+00:00").mock(
            return_value=httpx.Response(200, json=[])
        )
        await client.get_logbook(
            start="2026-06-29T07:00:00+00:00",
            end="2026-06-29T08:00:00+00:00",
            entity="light.living_room",
        )
        request = route.calls.last.request
        assert request.url.params["end_time"] == "2026-06-29T08:00:00+00:00"
        assert request.url.params["entity"] == "light.living_room"

    @respx.mock
    async def test_omits_optional_params_when_absent(self, client: HomeAssistantClient) -> None:
        route = respx.get("http://ha.test:8123/api/logbook/2026-06-29T07:00:00+00:00").mock(
            return_value=httpx.Response(200, json=[])
        )
        await client.get_logbook(start="2026-06-29T07:00:00+00:00")
        request = route.calls.last.request
        assert "end_time" not in request.url.params
        assert "entity" not in request.url.params


class TestLogbookTool:
    @respx.mock
    async def test_events_render_into_table(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=SAMPLE_EVENTS)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1)

        assert "## Logbook — Last 1h" in result
        assert "**2 events**" in result
        assert "| Time | Name | What |" in result
        assert "| 2026-06-29 08:00:00 | Front Door Motion | on |" in result
        assert "| 2026-06-29 08:01:00 | Living Room Light | off |" in result

    @respx.mock
    async def test_empty_window_clean_message(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=[])
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=3)
        assert result == "No logbook events in the last 3 hours."

    @respx.mock
    async def test_empty_window_with_entity_filter(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=[])
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=2, entity_id="light.kitchen")
        assert result == "No logbook events for `light.kitchen` in the last 2 hours."

    @respx.mock
    async def test_entity_id_passes_query_param(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        route = respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=SAMPLE_EVENTS)
        )
        ctx = _FakeContext(client, settings)
        await ha_get_logbook(ctx, hours=1, entity_id="binary_sensor.front_door_motion")
        request = route.calls.last.request
        assert request.url.params["entity"] == "binary_sensor.front_door_motion"

    @respx.mock
    async def test_large_result_truncates_with_note(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        events = [
            {
                "when": f"2026-06-29T08:{i % 60:02d}:00+00:00",
                "name": f"Sensor {i}",
                "entity_id": f"sensor.s_{i}",
                "state": "on",
            }
            for i in range(300)
        ]
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=events)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=12)

        cap = settings.logbook_max_events
        # Data rows render with the friendly name in the middle column.
        rendered = [ln for ln in result.splitlines() if " | Sensor " in ln]
        assert len(rendered) == cap
        assert f"**{cap} of 300 events**" in result
        assert f"Showing {cap} of 300 events" in result
        assert "narrow the window" in result
        # Most-recent events kept: last event (Sensor 299) present, earliest (Sensor 0) dropped.
        assert "| Sensor 299 |" in result
        assert "| Sensor 0 |" not in result

    @respx.mock
    async def test_message_used_when_state_absent(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        events = [
            {
                "when": "2026-06-29T08:00:00+00:00",
                "name": "Automation Triggered",
                "entity_id": "automation.morning",
                "message": "has been triggered",
            },
        ]
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=events)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1)
        assert "| Automation Triggered | has been triggered |" in result

    @respx.mock
    async def test_missing_state_and_message_degrade_gracefully(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        events = [
            {
                "when": "2026-06-29T08:00:00+00:00",
                "name": "Mystery Event",
                "entity_id": "sensor.mystery",
            },
        ]
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=events)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1)
        assert "| Mystery Event | — |" in result

    @respx.mock
    async def test_missing_name_falls_back_to_entity_id(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        events = [
            {
                "when": "2026-06-29T08:00:00+00:00",
                "entity_id": "sensor.nameless",
                "state": "42",
            },
        ]
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=events)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1)
        assert "| sensor.nameless | 42 |" in result


MIXED_EVENTS: list[dict[str, Any]] = [
    {
        "when": "2026-06-29T08:00:00+00:00",
        "name": "Backyard Bird",
        "entity_id": "image.backyard_bird",
        "state": "2026-06-29T08:00:00",
    },
    {
        "when": "2026-06-29T08:01:00+00:00",
        "name": "Front Door Motion",
        "entity_id": "binary_sensor.front_door_motion",
        "state": "on",
    },
    {
        "when": "2026-06-29T08:02:00+00:00",
        "name": "Washer",
        "entity_id": "sensor.washer",
        "state": "running",
    },
]


class TestLogbookFiltering:
    @respx.mock
    async def test_default_excludes_image_domain(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=MIXED_EVENTS)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1)

        assert "Backyard Bird" not in result  # image.* dropped by default
        assert "| Front Door Motion | on |" in result
        assert "| Washer | running |" in result
        assert "**2 events**" in result
        assert "Hid 1 events from excluded domains (image)" in result

    @respx.mock
    async def test_exclude_domains_empty_includes_everything(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=MIXED_EVENTS)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_get_logbook(ctx, hours=1, exclude_domains=[])

        assert "| Backyard Bird |" in result
        assert "**3 events**" in result
        assert "Hid" not in result

    @respx.mock
    async def test_per_call_exclude_overrides_default(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=MIXED_EVENTS)
        )
        ctx = _FakeContext(client, settings)
        # Override replaces the default — so image is NOT excluded here, sensor is.
        result = await ha_get_logbook(ctx, hours=1, exclude_domains=["sensor"])

        assert "| Backyard Bird |" in result
        assert "| Front Door Motion | on |" in result
        assert "Washer" not in result
        assert "Hid 1 events from excluded domains (sensor)" in result

    @respx.mock
    async def test_exclusion_skipped_when_entity_id_given(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        image_only = [MIXED_EVENTS[0]]
        respx.get(url__regex=r"http://ha\.test:8123/api/logbook/.*").mock(
            return_value=httpx.Response(200, json=image_only)
        )
        ctx = _FakeContext(client, settings)
        # Explicitly asking for an image entity must NOT be filtered out.
        result = await ha_get_logbook(ctx, hours=1, entity_id="image.backyard_bird")

        assert "| Backyard Bird |" in result
        assert "Hid" not in result

    async def test_exclude_domains_env_parses_comma_separated(self) -> None:
        s = Settings(
            url="http://ha.test:8123",
            token="t",
            logbook_exclude_domains="image, Sensor ,update",  # type: ignore[arg-type]
        )
        assert s.logbook_exclude_domains == ["image", "sensor", "update"]
