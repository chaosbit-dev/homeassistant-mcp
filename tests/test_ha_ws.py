"""Tests for the Home Assistant WebSocket client (auth handshake + command/result).

Uses a scripted in-memory fake connection injected via the connector hook, so no
live HA or real socket is required. This exercises the recurring footguns: auth
must be first, ids must correlate, and ``success: false`` is a failure even though
the socket was happy.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from homeassistant_mcp.config import Settings
from homeassistant_mcp.ha_ws import (
    HomeAssistantAuthError,
    HomeAssistantCommandError,
    HomeAssistantWebSocketClient,
    HomeAssistantWSError,
)


class FakeConnection:
    """Scripted WS connection: replays `outgoing` frames, records `sent` frames.

    `outgoing` is a list of dicts (JSON-encoded on recv). The optional `on_send`
    callback lets a test append dynamic replies (e.g. echo the command id).
    """

    def __init__(
        self,
        outgoing: list[dict[str, Any]],
        on_send: Any = None,
    ) -> None:
        self._outgoing = list(outgoing)
        self.sent: list[dict[str, Any]] = []
        self._on_send = on_send

    async def send(self, data: str) -> None:
        payload = json.loads(data)
        self.sent.append(payload)
        if self._on_send is not None:
            self._on_send(payload, self._outgoing)

    async def recv(self) -> str:
        if not self._outgoing:
            raise AssertionError("recv() called with no scripted frames left")
        return json.dumps(self._outgoing.pop(0))


def _connector(conn: FakeConnection) -> Any:
    @asynccontextmanager
    async def connect(url: str) -> Any:
        _ = url
        yield conn

    return connect


def _settings() -> Settings:
    return Settings(url="http://ha.test:8123", token="admin-token")


class TestWebsocketUrlDerivation:
    def test_http_to_ws(self) -> None:
        assert _settings().websocket_url == "ws://ha.test:8123/api/websocket"

    def test_https_to_wss(self) -> None:
        s = Settings(url="https://ha.test/", token="t")
        assert s.websocket_url == "wss://ha.test/api/websocket"

    def test_explicit_override(self) -> None:
        s = Settings(url="http://ha.test:8123", token="t", ws_url="ws://other/api/websocket")
        assert s.websocket_url == "ws://other/api/websocket"

    def test_ws_token_defaults_to_token(self) -> None:
        assert _settings().websocket_token == "admin-token"

    def test_ws_token_override(self) -> None:
        s = Settings(url="http://ha.test:8123", token="read", ws_token="admin")
        assert s.websocket_token == "admin"


class TestAuthHandshake:
    async def test_happy_path_sends_auth_first_then_command(self) -> None:
        def echo_id(payload: dict[str, Any], outgoing: list[dict[str, Any]]) -> None:
            if "id" in payload:  # the command frame — queue its result
                outgoing.append(
                    {"id": payload["id"], "type": "result", "success": True, "result": {"ok": 1}}
                )

        conn = FakeConnection(
            outgoing=[{"type": "auth_required"}, {"type": "auth_ok"}],
            on_send=echo_id,
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))

        result = await client.command("person/list")

        assert result == {"ok": 1}
        # First client message MUST be the auth message.
        assert conn.sent[0] == {"type": "auth", "access_token": "admin-token"}
        # Command carries an incrementing integer id starting at 1.
        assert conn.sent[1]["id"] == 1
        assert conn.sent[1]["type"] == "person/list"

    async def test_auth_invalid_raises(self) -> None:
        conn = FakeConnection(
            outgoing=[
                {"type": "auth_required"},
                {"type": "auth_invalid", "message": "Invalid access token"},
            ]
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        with pytest.raises(HomeAssistantAuthError, match="Invalid access token"):
            await client.command("person/list")

    async def test_unexpected_greeting_raises(self) -> None:
        conn = FakeConnection(outgoing=[{"type": "pong"}])
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        with pytest.raises(HomeAssistantAuthError, match="unexpected greeting"):
            await client.command("person/list")


class TestCommandResult:
    async def test_success_false_is_surfaced_as_error(self) -> None:
        conn = FakeConnection(
            outgoing=[
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "id": 1,
                    "type": "result",
                    "success": False,
                    "error": {"code": "not_found", "message": "person not found"},
                },
            ]
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        with pytest.raises(HomeAssistantCommandError) as exc:
            await client.command("person/delete", person_id="nope")
        assert exc.value.code == "not_found"
        assert "person not found" in exc.value.message

    async def test_skips_non_matching_frames(self) -> None:
        conn = FakeConnection(
            outgoing=[
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 99, "type": "event", "event": {"noise": True}},  # not ours
                {"id": 1, "type": "result", "success": True, "result": [1, 2]},
            ]
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        assert await client.command("person/list") == [1, 2]

    async def test_non_json_frame_raises(self) -> None:
        class BadConn:
            async def send(self, data: str) -> None:
                _ = data

            async def recv(self) -> str:
                return "not json{"

        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(BadConn()))  # type: ignore[arg-type]
        with pytest.raises(HomeAssistantWSError, match="non-JSON"):
            await client.command("person/list")


class TestPersonHelpers:
    def _authed(self, result: Any) -> FakeConnection:
        def echo_id(payload: dict[str, Any], outgoing: list[dict[str, Any]]) -> None:
            if "id" in payload:
                outgoing.append(
                    {"id": payload["id"], "type": "result", "success": True, "result": result}
                )

        return FakeConnection(
            outgoing=[{"type": "auth_required"}, {"type": "auth_ok"}], on_send=echo_id
        )

    async def test_list_persons_merges_storage_and_config(self) -> None:
        conn = self._authed(
            {
                "storage": [
                    {"id": "abc", "name": "Alice", "device_trackers": ["device_tracker.k"]}
                ],
                "config": [{"id": "yaml1", "name": "Legacy"}],
            }
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        persons = await client.list_persons()
        assert len(persons) == 2
        assert persons[0]["source"] == "storage"
        assert persons[1]["source"] == "config"

    async def test_create_person_sends_expected_fields(self) -> None:
        conn = self._authed(
            {"id": "new1", "name": "Alice", "device_trackers": ["device_tracker.k"]}
        )
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        await client.create_person("Alice", ["device_tracker.k"], user_id="u1")
        cmd = conn.sent[1]
        assert cmd["type"] == "person/create"
        assert cmd["name"] == "Alice"
        assert cmd["device_trackers"] == ["device_tracker.k"]
        assert cmd["user_id"] == "u1"

    async def test_update_person_omits_unset_fields(self) -> None:
        conn = self._authed({"id": "abc", "name": "Alice", "device_trackers": ["a", "b"]})
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        await client.update_person("abc", device_trackers=["a", "b"])
        cmd = conn.sent[1]
        assert cmd["type"] == "person/update"
        assert cmd["person_id"] == "abc"
        assert cmd["device_trackers"] == ["a", "b"]
        assert "name" not in cmd

    async def test_delete_person_sends_person_id(self) -> None:
        conn = self._authed(None)
        client = HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))
        await client.delete_person("abc")
        cmd = conn.sent[1]
        assert cmd["type"] == "person/delete"
        assert cmd["person_id"] == "abc"


class TestRegistryHelpers:
    """Registry commands (v2) — command names, field shaping, and result unwrapping."""

    def _authed(self, result: Any) -> FakeConnection:
        def echo_id(payload: dict[str, Any], outgoing: list[dict[str, Any]]) -> None:
            if "id" in payload:
                outgoing.append(
                    {"id": payload["id"], "type": "result", "success": True, "result": result}
                )

        return FakeConnection(
            outgoing=[{"type": "auth_required"}, {"type": "auth_ok"}], on_send=echo_id
        )

    def _client(self, conn: FakeConnection) -> HomeAssistantWebSocketClient:
        return HomeAssistantWebSocketClient(_settings(), connector=_connector(conn))

    async def test_list_config_entries_command_name(self) -> None:
        conn = self._authed([{"entry_id": "e1", "domain": "hue"}])
        entries = await self._client(conn).list_config_entries()
        assert conn.sent[1]["type"] == "config_entries/get"
        assert entries[0]["entry_id"] == "e1"

    async def test_entity_update_unwraps_entity_entry(self) -> None:
        """HA wraps the update result but not the get result — normalize both."""
        conn = self._authed({"entity_entry": {"entity_id": "light.b", "name": "B"}})
        entry = await self._client(conn).update_entity_registry("light.a", new_entity_id="light.b")
        assert entry["entity_id"] == "light.b"
        cmd = conn.sent[1]
        assert cmd["type"] == "config/entity_registry/update"
        assert cmd["entity_id"] == "light.a"
        assert cmd["new_entity_id"] == "light.b"

    async def test_entity_update_sends_only_passed_fields(self) -> None:
        """An absent key means "leave alone" to HA — never send unset params."""
        conn = self._authed({"entity_entry": {"entity_id": "light.a"}})
        await self._client(conn).update_entity_registry("light.a", name="A")
        cmd = conn.sent[1]
        assert cmd["name"] == "A"
        assert "area_id" not in cmd
        assert "disabled_by" not in cmd

    async def test_entity_update_forwards_explicit_none(self) -> None:
        """Explicit null is how a field gets CLEARED — it must survive the wire."""
        conn = self._authed({"entity_entry": {"entity_id": "light.a"}})
        await self._client(conn).update_entity_registry("light.a", area_id=None)
        assert conn.sent[1]["area_id"] is None

    async def test_get_entity_returns_bare_entry(self) -> None:
        conn = self._authed({"entity_id": "light.a", "unique_id": "u1"})
        entry = await self._client(conn).get_entity_registry_entry("light.a")
        assert entry["unique_id"] == "u1"
        assert conn.sent[1]["type"] == "config/entity_registry/get"

    async def test_area_registry_list_and_create(self) -> None:
        conn = self._authed([{"area_id": "living_room", "name": "Living Room"}])
        areas = await self._client(conn).list_area_registry()
        assert conn.sent[1]["type"] == "config/area_registry/list"
        assert areas[0]["area_id"] == "living_room"

        conn2 = self._authed({"area_id": "kids", "name": "Kids"})
        area = await self._client(conn2).create_area("Kids")
        assert conn2.sent[1]["type"] == "config/area_registry/create"
        assert area["area_id"] == "kids"

    async def test_device_update_command_and_fields(self) -> None:
        conn = self._authed({"id": "d1", "name_by_user": "AVR"})
        await self._client(conn).update_device_registry("d1", name_by_user="AVR")
        cmd = conn.sent[1]
        assert cmd["type"] == "config/device_registry/update"
        assert cmd["device_id"] == "d1"
        assert cmd["name_by_user"] == "AVR"

    async def test_search_related_keeps_only_list_values(self) -> None:
        conn = self._authed({"automation": ["automation.x"], "config_entry": "e1"})
        related = await self._client(conn).search_related("entity", "light.a")
        cmd = conn.sent[1]
        assert cmd["type"] == "search/related"
        assert cmd["item_type"] == "entity"
        assert cmd["item_id"] == "light.a"
        assert related == {"automation": ["automation.x"]}

    async def test_list_helpers_tolerate_empty_result(self) -> None:
        conn = self._authed(None)
        assert await self._client(conn).list_entity_registry() == []

    async def test_delete_config_entry_surfaces_restart_flag(self) -> None:
        conn = self._authed({"require_restart": True})
        result = await self._client(conn).delete_config_entry("e1")
        cmd = conn.sent[1]
        assert cmd["type"] == "config_entries/delete"
        assert cmd["entry_id"] == "e1"
        assert result["require_restart"] is True
