"""Tests for the person management tools — gating, confirm, and error surfacing.

Negative testing is the pass condition here: a disabled
write must be REFUSED, an unconfirmed delete must be REFUSED, and HA's own
``success: false`` must surface as an error, not a silent success.
"""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant_mcp.config import Settings
from homeassistant_mcp.ha_ws import HomeAssistantCommandError
from homeassistant_mcp.tools.person import (
    ha_create_person,
    ha_delete_person,
    ha_list_persons,
    ha_update_person,
)


class FakeWS:
    """Records calls; optionally raises to simulate HA errors."""

    def __init__(
        self,
        *,
        raises: Exception | None = None,
        list_raises: Exception | None = None,
        persons: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._raises = raises
        self._list_raises = list_raises
        self._persons = persons

    @property
    def write_calls(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """Calls that reach HA as mutations — what a refusal must prevent."""
        mutating = ("create_person", "update_person", "delete_person")
        return [c for c in self.calls if c[0] in mutating]

    async def list_persons(self) -> list[dict[str, Any]]:
        self.calls.append(("list_persons", (), {}))
        if self._list_raises:
            raise self._list_raises
        if self._persons is not None:
            return self._persons
        if self._raises:
            raise self._raises
        return [
            {
                "id": "alice1",
                "name": "Alice",
                "device_trackers": ["device_tracker.alice_phone"],
                "user_id": None,
                "source": "storage",
            }
        ]

    async def create_person(
        self, name: str, device_trackers: list[str], user_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("create_person", (name, device_trackers, user_id), {}))
        if self._raises:
            raise self._raises
        return {"id": "new1", "name": name, "device_trackers": device_trackers}

    async def update_person(
        self,
        person_id: str,
        name: str | None = None,
        device_trackers: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("update_person", (person_id, name, device_trackers), {}))
        if self._raises:
            raise self._raises
        return {
            "id": person_id,
            "name": name or "Alice",
            "device_trackers": device_trackers or [],
        }

    async def delete_person(self, person_id: str) -> None:
        self.calls.append(("delete_person", (person_id,), {}))
        if self._raises:
            raise self._raises


class _FakeContext:
    def __init__(self, ws: FakeWS, settings: Settings) -> None:
        self.request_context = type(
            "_Req", (), {"lifespan_context": {"ha_ws": ws, "settings": settings}}
        )()


def _settings(*, writes_enabled: bool = True) -> Settings:
    return Settings(
        url="http://ha.test:8123",
        token="admin-token",
        person_writes_enabled=writes_enabled,
    )


class TestListPersons:
    async def test_lists_persons_with_trackers(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_list_persons(ctx)
        assert "Alice" in result
        assert "`device_tracker.alice_phone`" in result
        assert "storage" in result

    async def test_empty(self) -> None:
        ws = FakeWS()
        ws.list_persons = _empty_list  # type: ignore[method-assign]
        ctx = _FakeContext(ws, _settings())
        assert await ha_list_persons(ctx) == "No persons configured."

    async def test_ws_error_surfaced(self) -> None:
        ws = FakeWS(raises=HomeAssistantCommandError("unauthorized", "not admin"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_list_persons(ctx)
        assert "Error listing persons" in result
        assert "not admin" in result


async def _empty_list() -> list[dict[str, Any]]:
    return []


class TestCreatePerson:
    async def test_create_happy_path(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_create_person(
            ctx, "Alice", ["device_tracker.alice_phone", "device_tracker.alice_face"]
        )
        assert "Created person" in result
        assert ws.calls[0][0] == "create_person"
        assert ws.calls[0][1][1] == [
            "device_tracker.alice_phone",
            "device_tracker.alice_face",
        ]

    async def test_refused_when_writes_disabled(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings(writes_enabled=False))
        result = await ha_create_person(ctx, "Alice", ["device_tracker.k"])
        assert "Refused" in result
        assert ws.calls == []  # gate short-circuits before any socket op

    async def test_ha_failure_surfaced_not_silent(self) -> None:
        ws = FakeWS(raises=HomeAssistantCommandError("invalid_format", "bad tracker"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_create_person(ctx, "Alice", ["nope"])
        assert "Error creating person" in result
        assert "bad tracker" in result


class TestUpdatePerson:
    async def test_update_appends_tracker(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_update_person(
            ctx, "alice1", device_trackers=["device_tracker.alice_phone", "device_tracker.new"]
        )
        assert "Updated person" in result
        assert ws.calls[0][1] == (
            "alice1",
            None,
            ["device_tracker.alice_phone", "device_tracker.new"],
        )

    async def test_requires_a_field(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_update_person(ctx, "alice1")
        assert "at least one" in result
        assert ws.calls == []

    async def test_refused_when_writes_disabled(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings(writes_enabled=False))
        result = await ha_update_person(ctx, "alice1", name="New")
        assert "Refused" in result
        assert ws.calls == []


class TestDeletePerson:
    async def test_refused_without_confirm(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_delete_person(ctx, "alice1")
        assert "Refused" in result
        assert "confirm=True" in result
        assert ws.calls == []  # nothing deleted

    async def test_deletes_with_confirm(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings())
        result = await ha_delete_person(ctx, "alice1", confirm=True)
        assert "Deleted person" in result
        assert ws.calls[0] == ("delete_person", ("alice1",), {})

    async def test_refused_when_writes_disabled_even_with_confirm(self) -> None:
        ws = FakeWS()
        ctx = _FakeContext(ws, _settings(writes_enabled=False))
        result = await ha_delete_person(ctx, "alice1", confirm=True)
        assert "Refused" in result
        assert "disabled" in result
        assert ws.calls == []

    async def test_ha_error_surfaced(self) -> None:
        ws = FakeWS(raises=HomeAssistantCommandError("not_found", "no such person"))
        ctx = _FakeContext(ws, _settings())
        result = await ha_delete_person(ctx, "ghost", confirm=True)
        assert "Error deleting person" in result
        assert "no such person" in result


@pytest.mark.parametrize(
    "tool",
    [ha_create_person, ha_update_person],
)
async def test_write_gate_logs_refusal(tool: Any) -> None:
    """The kill switch refuses without opening a socket, for every write tool."""
    ws = FakeWS()
    ctx = _FakeContext(ws, _settings(writes_enabled=False))
    if tool is ha_create_person:
        result = await tool(ctx, "Alice", ["device_tracker.k"])
    else:
        result = await tool(ctx, "alice1", name="X")
    assert "Refused" in result
    assert ws.calls == []


class TestGateDefaultsClosed:
    """An unset HA_PERSON_WRITES_ENABLED must mean OFF, not ON.

    Regression for the 2026-08-01 audit: the gate was wired correctly, but it
    defaulted to True, so a deployment that never set the variable had person
    writes live while the operator believed they were disabled. An *open* gate and
    a *missing* gate look identical from outside — hence these assertions.
    """

    def test_default_is_off(self) -> None:
        assert Settings(url="http://ha.test:8123", token="t").person_writes_enabled is False

    @pytest.mark.parametrize(
        ("tool", "args", "kwargs"),
        [
            (ha_create_person, ("Alice", ["device_tracker.k"]), {}),
            (ha_update_person, ("alice1",), {"name": "X"}),
            (ha_delete_person, ("alice1",), {"confirm": True}),
        ],
    )
    async def test_no_ha_dispatch_when_gate_defaulted(
        self, tool: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        """With the env var UNSET, no request may reach HA.

        Asserting on the transport, not just the return string: a gated tool that
        returns an error sourced from Home Assistant means the request escaped the
        gate. Zero write calls is the pass condition.
        """
        ws = FakeWS()
        settings = Settings(url="http://ha.test:8123", token="t")  # nothing set
        result = await tool(_FakeContext(ws, settings), *args, **kwargs)
        assert "Refused" in result
        assert "HA_PERSON_WRITES_ENABLED" in result
        assert ws.write_calls == []


class TestUpdatePreservesTrackers:
    """Omitting device_trackers must not wipe them.

    HA's ``person/update`` defaults an ABSENT ``device_trackers`` to ``[]``, so
    leaving the field out of the payload silently destroys presence bindings. The
    tool reads the current list back and re-sends it.
    """

    def _ctx(self, ws: FakeWS) -> _FakeContext:
        return _FakeContext(ws, _settings())

    async def test_name_only_edit_preserves_trackers(self) -> None:
        ws = FakeWS()
        result = await ha_update_person(self._ctx(ws), "alice1", name="Alice Example")
        assert "Updated person" in result
        update = next(c for c in ws.calls if c[0] == "update_person")
        # The current tracker list is re-sent, not dropped.
        assert update[1][2] == ["device_tracker.alice_phone"]

    async def test_explicit_empty_list_still_clears(self) -> None:
        """Clearing must remain possible — [] is distinct from omitted."""
        ws = FakeWS()
        await ha_update_person(self._ctx(ws), "alice1", device_trackers=[])
        update = next(c for c in ws.calls if c[0] == "update_person")
        assert update[1][2] == []
        assert not [c for c in ws.calls if c[0] == "list_persons"]  # no read-back needed

    async def test_explicit_list_replaces(self) -> None:
        ws = FakeWS()
        await ha_update_person(self._ctx(ws), "alice1", device_trackers=["device_tracker.new"])
        update = next(c for c in ws.calls if c[0] == "update_person")
        assert update[1][2] == ["device_tracker.new"]

    async def test_readback_failure_refuses_rather_than_clearing(self) -> None:
        ws = FakeWS(list_raises=HomeAssistantCommandError("unauthorized", "not admin"))
        result = await ha_update_person(self._ctx(ws), "alice1", name="X")
        assert "Refused" in result
        assert "would CLEAR" in result
        assert ws.write_calls == []

    async def test_unknown_person_refused_before_update(self) -> None:
        ws = FakeWS()
        result = await ha_update_person(self._ctx(ws), "ghost", name="X")
        assert "Refused" in result
        assert ws.write_calls == []

    async def test_person_with_no_trackers_round_trips_empty(self) -> None:
        ws = FakeWS(persons=[{"id": "solo", "name": "Solo", "device_trackers": []}])
        await ha_update_person(self._ctx(ws), "solo", name="Solo Renamed")
        update = next(c for c in ws.calls if c[0] == "update_person")
        assert update[1][2] == []
