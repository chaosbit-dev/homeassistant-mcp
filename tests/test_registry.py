"""Tests for the registry tools — gating, the entity_id reference check, error surfacing.

Negative testing is the pass condition (docs/registry-write-tools.md §3): writes must
be REFUSED when the kill switch is off, an `entity_id` change must be REFUSED when
anything references it, the reference check must FAIL CLOSED when it cannot run, and
HA's own ``success: false`` must surface as an error rather than a silent success.
"""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant_mcp.config import Settings
from homeassistant_mcp.ha_ws import HomeAssistantCommandError, HomeAssistantWSError
from homeassistant_mcp.tools.registry import (
    ha_assign_device_area,
    ha_assign_entity_area,
    ha_create_area,
    ha_disable_entity,
    ha_get_registry,
    ha_list_config_entries,
    ha_rename_area,
    ha_rename_device,
    ha_rename_entity,
)


class FakeWS:
    """Records calls; optionally raises to simulate HA errors."""

    def __init__(
        self,
        *,
        raises: Exception | None = None,
        related: dict[str, list[str]] | None = None,
        related_raises: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._raises = raises
        self._related = related or {}
        self._related_raises = related_raises

    def _record(self, call: str, /, **kwargs: Any) -> None:
        # Positional-only: several commands take a `name` field of their own.
        self.calls.append((call, kwargs))
        if self._raises:
            raise self._raises

    async def list_config_entries(self) -> list[dict[str, Any]]:
        self._record("list_config_entries")
        return [
            {"entry_id": "e1", "domain": "denonavr", "title": "Denon AVR", "state": "loaded"},
            {"entry_id": "e2", "domain": "denonavr", "title": "Denon AVR", "state": "loaded"},
            {"entry_id": "e3", "domain": "hue", "title": "Hue Bridge", "state": "loaded"},
        ]

    async def list_entity_registry(self) -> list[dict[str, Any]]:
        self._record("list_entity_registry")
        return [
            {
                "entity_id": "media_player.denon_avr",
                "name": "Denon",
                "area_id": None,
                "disabled_by": None,
            },
            {
                "entity_id": "light.kitchen",
                "name": None,
                "original_name": "Kitchen",
                "area_id": "kitchen",
                "disabled_by": None,
            },
        ]

    async def get_entity_registry_entry(self, entity_id: str) -> dict[str, Any]:
        self._record("get_entity_registry_entry", entity_id=entity_id)
        return {
            "entity_id": entity_id,
            "name": "Denon",
            "platform": "denonavr",
            "area_id": None,
            "unique_id": "aabbcc",
            "config_entry_id": "e1",
            "disabled_by": None,
        }

    async def update_entity_registry(self, entity_id: str, **changes: Any) -> dict[str, Any]:
        self._record("update_entity_registry", entity_id=entity_id, **changes)
        return {
            "entity_id": changes.get("new_entity_id", entity_id),
            "name": changes.get("name", "Denon"),
            "area_id": changes.get("area_id"),
            "disabled_by": changes.get("disabled_by"),
        }

    async def list_device_registry(self) -> list[dict[str, Any]]:
        self._record("list_device_registry")
        return [{"id": "d1", "name": "AVR", "name_by_user": None, "area_id": None}]

    async def update_device_registry(self, device_id: str, **changes: Any) -> dict[str, Any]:
        self._record("update_device_registry", device_id=device_id, **changes)
        return {"id": device_id, **changes}

    async def list_area_registry(self) -> list[dict[str, Any]]:
        self._record("list_area_registry")
        return [{"area_id": "living_room", "name": "Living Room"}]

    async def create_area(self, name: str) -> dict[str, Any]:
        self._record("create_area", name=name)
        return {"area_id": "kids_room", "name": name}

    async def update_area(self, area_id: str, name: str) -> dict[str, Any]:
        self._record("update_area", area_id=area_id, name=name)
        return {"area_id": area_id, "name": name}

    async def search_related(self, item_type: str, item_id: str) -> dict[str, list[str]]:
        self.calls.append(("search_related", {"item_type": item_type, "item_id": item_id}))
        if self._related_raises:
            raise self._related_raises
        return self._related

    @property
    def write_calls(self) -> list[tuple[str, dict[str, Any]]]:
        """Calls that actually mutate — the ones a refusal must prevent."""
        return [c for c in self.calls if c[0].startswith(("update_", "create_"))]


class _FakeContext:
    def __init__(self, ws: FakeWS, settings: Settings) -> None:
        self.request_context = type(
            "_Req", (), {"lifespan_context": {"ha_ws": ws, "settings": settings}}
        )()


def _settings(*, writes_enabled: bool = True) -> Settings:
    return Settings(
        url="http://ha.test:8123",
        token="admin-token",
        registry_write_enabled=writes_enabled,
    )


class TestDefaults:
    def test_registry_writes_default_off(self) -> None:
        """Ships off by design — unlike person writes, which default on."""
        settings = Settings(url="http://ha.test:8123", token="t")
        assert settings.registry_write_enabled is False
        assert settings.person_writes_enabled is True


class TestReads:
    async def test_config_entries_flags_duplicates(self) -> None:
        ctx = _FakeContext(FakeWS(), _settings())
        result = await ha_list_config_entries(ctx)
        assert "`e1`" in result and "`e2`" in result
        assert "Possible duplicates" in result
        assert "denonavr" in result

    async def test_reads_ungated(self) -> None:
        """Reads work even with the write kill switch off."""
        ctx = _FakeContext(FakeWS(), _settings(writes_enabled=False))
        assert "Possible duplicates" in await ha_list_config_entries(ctx)
        assert "Living Room" in await ha_get_registry(ctx, kind="area")

    async def test_area_registry_exposes_area_id(self) -> None:
        """area_id is the whole reason this read exists — ha_list_areas hides it."""
        ctx = _FakeContext(FakeWS(), _settings())
        result = await ha_get_registry(ctx, kind="area")
        assert "`living_room`" in result

    async def test_entity_detail(self) -> None:
        ctx = _FakeContext(FakeWS(), _settings())
        result = await ha_get_registry(ctx, entity_id="media_player.denon_avr")
        assert "aabbcc" in result
        assert "denonavr" in result

    async def test_search_filters(self) -> None:
        ctx = _FakeContext(FakeWS(), _settings())
        result = await ha_get_registry(ctx, kind="entity", search="denon")
        assert "denon_avr" in result
        assert "light.kitchen" not in result

    async def test_invalid_kind(self) -> None:
        ctx = _FakeContext(FakeWS(), _settings())
        assert "must be one of" in await ha_get_registry(ctx, kind="nonsense")

    async def test_ws_error_surfaced(self) -> None:
        ws = FakeWS(raises=HomeAssistantCommandError("unauthorized", "not admin"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_list_config_entries(ctx)
        assert "Error listing config entries" in result
        assert "not admin" in result


class TestWriteGate:
    """Every write refuses before any socket op when the kill switch is off."""

    @pytest.mark.parametrize(
        ("tool", "args", "kwargs"),
        [
            (ha_rename_entity, ("light.a",), {"name": "A"}),
            (ha_assign_entity_area, ("light.a", "living_room"), {}),
            (ha_disable_entity, ("light.a",), {}),
            (ha_create_area, ("Kids Room",), {}),
            (ha_rename_area, ("kids_room", "Kids"), {}),
            (ha_assign_device_area, ("d1", "living_room"), {}),
            (ha_rename_device, ("d1", "AVR"), {}),
        ],
    )
    async def test_refused_when_disabled(
        self, tool: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings(writes_enabled=False))
        result = await tool(ctx, *args, **kwargs)
        assert "Refused" in result
        assert "HA_REGISTRY_WRITE_ENABLED" in result
        assert ws.calls == []  # gate short-circuits before any socket op


class TestRenameEntity:
    async def test_name_only_is_safe_and_skips_reference_check(self) -> None:
        """A display-name change cannot break references, so don't even look."""
        ws = FakeWS(related={"automation": ["automation.x"]})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "media_player.denon_avr", name="Receiver")
        assert "Updated" in result
        assert [c[0] for c in ws.calls] == ["update_entity_registry"]
        assert ws.calls[0][1] == {"entity_id": "media_player.denon_avr", "name": "Receiver"}

    async def test_requires_a_field(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a")
        assert "at least one" in result
        assert ws.calls == []

    async def test_entity_id_change_refused_when_referenced(self) -> None:
        ws = FakeWS(related={"automation": ["automation.porch"], "scene": ["scene.night"]})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b")
        assert "Refused" in result
        assert "automation.porch" in result
        assert "scene.night" in result
        assert ws.write_calls == []  # nothing mutated

    async def test_entity_id_change_allowed_when_unreferenced(self) -> None:
        ws = FakeWS(related={})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b")
        assert "Updated" in result
        assert "`light.b`" in result
        assert ws.calls[-1][1]["new_entity_id"] == "light.b"

    async def test_force_overrides_references(self) -> None:
        ws = FakeWS(related={"automation": ["automation.porch"]})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b", force=True)
        assert "Updated" in result
        assert ws.calls[-1][1]["new_entity_id"] == "light.b"

    async def test_reference_check_failure_fails_closed(self) -> None:
        """If we cannot verify references, refuse — never assume "none found"."""
        ws = FakeWS(related_raises=HomeAssistantWSError("search integration unavailable"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b")
        assert "Refused" in result
        assert "could not verify" in result
        assert ws.write_calls == []

    async def test_reference_check_failure_overridable(self) -> None:
        ws = FakeWS(related_raises=HomeAssistantWSError("search unavailable"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b", force=True)
        assert "Updated" in result

    async def test_area_and_device_relations_do_not_block(self) -> None:
        """Areas/devices link by id, not entity_id — they survive a rename."""
        ws = FakeWS(related={"area": ["living_room"], "device": ["d1"]})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="light.b")
        assert "Updated" in result

    async def test_rejects_cross_domain_rename(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="switch.b")
        assert "crosses domains" in result
        assert ws.calls == []

    async def test_rejects_bare_new_id(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", new_entity_id="just_a_name")
        assert "must be a full entity id" in result
        assert ws.calls == []

    async def test_same_entity_id_is_a_noop_not_a_reference_check(self) -> None:
        ws = FakeWS(related={"automation": ["automation.x"]})
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.a", name="A", new_entity_id="light.a")
        assert "Updated" in result
        assert "search_related" not in [c[0] for c in ws.calls]

    async def test_ha_error_surfaced(self) -> None:
        ws = FakeWS(raises=HomeAssistantCommandError("not_found", "unknown entity"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_entity(ctx, "light.ghost", name="X")
        assert "Error renaming entity" in result
        assert "unknown entity" in result


class TestAreaAssignment:
    async def test_assign_entity_area(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_assign_entity_area(ctx, "light.a", "living_room")
        assert "Assigned" in result
        assert ws.calls[0][1] == {"entity_id": "light.a", "area_id": "living_room"}

    async def test_empty_area_id_clears(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_assign_entity_area(ctx, "light.a", "")
        assert "Cleared" in result
        assert ws.calls[0][1]["area_id"] is None

    async def test_assign_device_area(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_assign_device_area(ctx, "d1", "living_room")
        assert "Assigned device" in result
        assert ws.calls[0][1] == {"device_id": "d1", "area_id": "living_room"}

    async def test_create_area_returns_id(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_create_area(ctx, "Kids Room")
        assert "kids_room" in result
        assert ws.calls[0][1] == {"name": "Kids Room"}

    async def test_rename_area(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_area(ctx, "kids_room", "Kids Bedroom")
        assert "Kids Bedroom" in result


class TestDisableEntity:
    async def test_disable_sets_disabled_by_user(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_disable_entity(ctx, "light.a")
        assert "Disabled" in result
        assert ws.calls[0][1]["disabled_by"] == "user"

    async def test_enable_clears_disabled_by(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_disable_entity(ctx, "light.a", disabled=False)
        assert "Enabled" in result
        assert ws.calls[0][1]["disabled_by"] is None


class TestRenameDevice:
    async def test_sets_name_by_user(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_rename_device(ctx, "d1", "Living Room Receiver")
        assert "Living Room Receiver" in result
        assert ws.calls[0][1] == {"device_id": "d1", "name_by_user": "Living Room Receiver"}
