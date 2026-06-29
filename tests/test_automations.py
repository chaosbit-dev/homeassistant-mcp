"""Tests for the automation tools (list, trigger with skip_condition)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from homeassistant_mcp.tools.automations import ha_list_automations, ha_trigger_automation


class _FakeContext:
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


STATES: list[dict[str, Any]] = [
    {
        "entity_id": "automation.morning_routine",
        "state": "on",
        "attributes": {
            "friendly_name": "Morning Routine",
            "last_triggered": "2026-06-29T12:30:00+00:00",
        },
    },
    {
        "entity_id": "automation.away_mode",
        "state": "off",
        "attributes": {"friendly_name": "Away Mode"},  # never triggered
    },
    {
        "entity_id": "automation.deep_freeze_alert",
        "state": "unavailable",
        "attributes": {"id": "1773616909816", "restored": True},  # no friendly_name
    },
    {"entity_id": "light.kitchen", "state": "on", "attributes": {}},  # non-automation
]


class TestListAutomations:
    @respx.mock
    async def test_lists_only_automations_with_enabled_and_last_triggered(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get("http://ha.test:8123/api/states").mock(
            return_value=httpx.Response(200, json=STATES)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_list_automations(ctx)

        assert "**3 automations**" in result  # light.kitchen excluded
        assert "light.kitchen" not in result
        assert "Morning Routine" in result
        assert "`automation.morning_routine` | ✅ on | 2026-06-29T12:30:00+00:00 |" in result
        assert "| Away Mode | `automation.away_mode` | ⛔ off | never |" in result

    @respx.mock
    async def test_unavailable_automation_without_friendly_name(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        respx.get("http://ha.test:8123/api/states").mock(
            return_value=httpx.Response(200, json=STATES)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_list_automations(ctx)
        # Falls back to entity_id for the name; state shown as-is; never triggered.
        assert "automation.deep_freeze_alert | `automation.deep_freeze_alert`" in result
        assert "| unavailable | never |" in result

    @respx.mock
    async def test_sorted_by_name(self, client: HomeAssistantClient, settings: Settings) -> None:
        respx.get("http://ha.test:8123/api/states").mock(
            return_value=httpx.Response(200, json=STATES)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_list_automations(ctx)
        # "automation.deep_freeze_alert" (no friendly name) sorts by entity_id,
        # before "Away Mode" / "Morning Routine".
        rows = [
            ln
            for ln in result.splitlines()
            if ln.startswith("| ") and "Entity ID" not in ln and "---" not in ln
        ]
        order = [r.split("|")[1].strip() for r in rows]
        assert order == ["automation.deep_freeze_alert", "Away Mode", "Morning Routine"]

    @respx.mock
    async def test_no_automations(self, client: HomeAssistantClient, settings: Settings) -> None:
        respx.get("http://ha.test:8123/api/states").mock(
            return_value=httpx.Response(
                200, json=[{"entity_id": "light.kitchen", "state": "on", "attributes": {}}]
            )
        )
        ctx = _FakeContext(client, settings)
        assert await ha_list_automations(ctx) == "No automations found."


TRIGGERED_STATE: dict[str, Any] = {
    "entity_id": "automation.morning_routine",
    "state": "on",
    "attributes": {
        "friendly_name": "Morning Routine",
        "last_triggered": "2026-06-29T13:00:00+00:00",
    },
}


class TestTriggerAutomation:
    @respx.mock
    async def test_default_respects_conditions(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        svc = respx.post("http://ha.test:8123/api/services/automation/trigger").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get("http://ha.test:8123/api/states/automation.morning_routine").mock(
            return_value=httpx.Response(200, json=TRIGGERED_STATE)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_trigger_automation(ctx, entity_id="automation.morning_routine")

        body = json.loads(svc.calls.last.request.content)
        assert body == {"entity_id": "automation.morning_routine", "skip_condition": False}
        assert "respecting conditions" in result
        assert "Morning Routine" in result

    @respx.mock
    async def test_skip_condition_true_forces_run(
        self, client: HomeAssistantClient, settings: Settings
    ) -> None:
        svc = respx.post("http://ha.test:8123/api/services/automation/trigger").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get("http://ha.test:8123/api/states/automation.morning_routine").mock(
            return_value=httpx.Response(200, json=TRIGGERED_STATE)
        )
        ctx = _FakeContext(client, settings)
        result = await ha_trigger_automation(
            ctx, entity_id="automation.morning_routine", skip_condition=True
        )

        body = json.loads(svc.calls.last.request.content)
        assert body["skip_condition"] is True
        assert "ignoring conditions" in result
