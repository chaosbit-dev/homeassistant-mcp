"""Async WebSocket client for the Home Assistant WebSocket API.

The REST API (``client.py``) can read state and call services but **cannot mutate
the registry / storage collections**. Creating a person, renaming an entity, and
assigning an area are all operations HA exposes only over its WebSocket API
(``/api/websocket``). This module is the WS transport that unlocks that path.

Connection model — **ephemeral per operation**. Config mutations are rare, so we
open a fresh connection per command (connect → auth → send → await result → close)
rather than maintaining a persistent, always-open, admin-capable socket. That keeps
the lifecycle trivial and the attack surface small. If real-time subscriptions
(``state_changed`` streaming) are ever added, a persistent connection becomes worth
it — that is a deliberate future fork, not needed here.

The client is written generically (``command()`` dispatch + auth handshake + error
handling), which is why the v2 registry commands (entity/device/area, config
entries, ``search/related``) landed as new callers rather than a refactor. Both the
person and registry command sets are wrapped in convenience methods below.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import websockets

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from homeassistant_mcp.config import Settings

    # A connector is an async-context-manager factory: connect(url) yields a
    # connection with async ``send(str)`` / ``recv() -> str | bytes``. Kept as a
    # loose Callable so both ``websockets.connect`` and test fakes satisfy it.
    Connector = Callable[[str], Any]


class HomeAssistantWSError(RuntimeError):
    """Base error for the WebSocket path."""


class HomeAssistantAuthError(HomeAssistantWSError):
    """Raised when the WebSocket auth handshake fails (bad/non-admin token)."""


class HomeAssistantCommandError(HomeAssistantWSError):
    """Raised when HA returns ``success: false`` for a command.

    Carries HA's own error ``code`` and ``message`` so the tool layer can surface
    the real reason rather than treating "got a response" as "it worked".
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@asynccontextmanager
async def _default_connect(url: str) -> AsyncIterator[Any]:
    """Default connector — a thin wrapper over ``websockets.connect``.

    Isolated behind a small async-context-manager factory so tests can inject a
    scripted fake connection without a live HA or a real socket.
    """
    async with websockets.connect(url) as conn:
        yield conn


class HomeAssistantWebSocketClient:
    """Ephemeral-per-op WebSocket client for HA registry/config mutation.

    Each :meth:`command` opens a fresh connection, performs the auth handshake, sends
    exactly one id-correlated command, awaits its ``result``, and closes. There is no
    persistent connection, keepalive, or reconnect logic to maintain.
    """

    def __init__(
        self,
        settings: Settings,
        connector: Connector | None = None,
    ) -> None:
        self._url = settings.websocket_url
        self._token = settings.websocket_token
        self._timeout = settings.ws_timeout
        # Injectable for tests; defaults to a real websockets connection.
        self._connect: Any = connector or _default_connect

    async def command(self, command_type: str, **fields: Any) -> Any:
        """Run a single WS command and return its ``result`` payload.

        Opens a connection, authenticates, sends ``{"id": 1, "type": command_type,
        **fields}``, and returns the ``result`` field of the matching response.

        Raises:
            HomeAssistantAuthError: the auth handshake was rejected.
            HomeAssistantCommandError: HA replied ``success: false``.
            HomeAssistantWSError: the socket closed or misbehaved mid-exchange.
        """
        try:
            async with self._connect(self._url) as conn:
                await self._authenticate(conn)
                return await self._send_command(conn, command_type, fields)
        except HomeAssistantWSError:
            raise
        except (OSError, websockets.WebSocketException) as exc:
            raise HomeAssistantWSError(f"WebSocket transport error: {exc}") from exc

    async def _authenticate(self, conn: Any) -> None:
        """Perform the HA auth handshake. Must run before any command.

        HA sends ``auth_required`` on connect; the very first client message MUST be
        the auth message or HA disconnects. HA then replies ``auth_ok`` or
        ``auth_invalid`` (the latter typically a bad token — and registry mutation
        additionally requires the token's user to be an admin).
        """
        greeting = await self._recv_json(conn)
        greeting_type = greeting.get("type")
        if greeting_type == "auth_ok":
            # Some deployments may not require auth; nothing more to do.
            return
        if greeting_type != "auth_required":
            raise HomeAssistantAuthError(
                f"unexpected greeting from HA WebSocket: {greeting_type!r}"
            )

        await self._send_json(conn, {"type": "auth", "access_token": self._token})
        result = await self._recv_json(conn)
        result_type = result.get("type")
        if result_type == "auth_ok":
            return
        if result_type == "auth_invalid":
            message = result.get("message", "authentication failed")
            raise HomeAssistantAuthError(str(message))
        raise HomeAssistantAuthError(f"unexpected auth response: {result_type!r}")

    async def _send_command(self, conn: Any, command_type: str, fields: dict[str, Any]) -> Any:
        """Send one id-correlated command and await its result.

        Uses ``id`` 1 (the ephemeral model sends a single command per connection, so
        strict monotonicity is trivial). Skips any non-matching message (e.g. a
        stray event) and only accepts the ``result`` whose ``id`` echoes ours.
        """
        message_id = 1
        await self._send_json(conn, {"id": message_id, "type": command_type, **fields})

        while True:
            response = await self._recv_json(conn)
            if response.get("id") != message_id:
                # Not our reply (unexpected event/echo) — keep reading.
                continue
            if response.get("type") != "result":
                continue
            if not response.get("success", False):
                error = response.get("error") or {}
                raise HomeAssistantCommandError(
                    str(error.get("code", "unknown")),
                    str(error.get("message", "command failed")),
                )
            return response.get("result")

    @staticmethod
    async def _send_json(conn: Any, payload: dict[str, Any]) -> None:
        send: Callable[[str], Awaitable[None]] = conn.send
        await send(json.dumps(payload))

    @staticmethod
    async def _recv_json(conn: Any) -> dict[str, Any]:
        raw = await conn.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HomeAssistantWSError(f"non-JSON WebSocket frame: {raw!r}") from exc
        if not isinstance(data, dict):
            raise HomeAssistantWSError(f"unexpected WebSocket frame shape: {data!r}")
        return data

    # ── Person storage-collection commands (v1) ──────────────────────

    async def list_persons(self) -> list[dict[str, Any]]:
        """``person/list`` → persons with their bound device_trackers.

        HA returns ``{"storage": [...], "config": [...]}``: ``storage`` persons are
        UI/registry-managed (editable), ``config`` persons come from YAML (read-only).
        Both are returned, tagged with a ``source`` so callers can tell them apart.
        """
        result = await self.command("person/list")
        persons: list[dict[str, Any]] = []
        if isinstance(result, dict):
            for source in ("storage", "config"):
                for person in result.get(source) or []:
                    persons.append({**person, "source": source})
        elif isinstance(result, list):
            persons.extend(result)
        return persons

    async def create_person(
        self,
        name: str,
        device_trackers: list[str],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """``person/create`` — create a person bound to existing device_trackers."""
        fields: dict[str, Any] = {"name": name, "device_trackers": device_trackers}
        if user_id is not None:
            fields["user_id"] = user_id
        result: dict[str, Any] = await self.command("person/create", **fields)
        return result

    async def update_person(
        self,
        person_id: str,
        name: str | None = None,
        device_trackers: list[str] | None = None,
    ) -> dict[str, Any]:
        """``person/update`` — modify an existing storage-managed person."""
        fields: dict[str, Any] = {"person_id": person_id}
        if name is not None:
            fields["name"] = name
        if device_trackers is not None:
            fields["device_trackers"] = device_trackers
        result: dict[str, Any] = await self.command("person/update", **fields)
        return result

    async def delete_person(self, person_id: str) -> None:
        """``person/delete`` — remove a storage-managed person (destructive)."""
        await self.command("person/delete", person_id=person_id)

    # ── Registry commands (v2) ───────────────────────────────────────
    #
    # Entity/device/area registry + config entries. These are the ops REST cannot
    # do at all (see docs/registry-write-tools.md). Each is a thin wrapper over
    # ``command()`` — no new transport, per that doc's §1.

    @staticmethod
    def _unwrap(result: Any, key: str) -> dict[str, Any]:
        """Return ``result[key]`` when HA wraps the entry, else ``result`` itself.

        HA is inconsistent here: ``config/entity_registry/update`` replies
        ``{"entity_entry": {...}}`` while ``.../get`` replies with the entry
        directly. Normalizing once keeps the tool layer from caring.
        """
        if isinstance(result, dict):
            inner = result.get(key)
            if isinstance(inner, dict):
                return inner
            return result
        return {}

    @staticmethod
    def _as_list(result: Any) -> list[dict[str, Any]]:
        """Coerce a list-returning command's result, tolerating an empty reply."""
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    async def list_config_entries(self) -> list[dict[str, Any]]:
        """``config_entries/get`` → integration instances with their ``entry_id``."""
        return self._as_list(await self.command("config_entries/get"))

    async def list_entity_registry(self) -> list[dict[str, Any]]:
        """``config/entity_registry/list`` → every registry entry (id, area, disabled_by)."""
        return self._as_list(await self.command("config/entity_registry/list"))

    async def get_entity_registry_entry(self, entity_id: str) -> dict[str, Any]:
        """``config/entity_registry/get`` → full detail for one entity."""
        result = await self.command("config/entity_registry/get", entity_id=entity_id)
        return self._unwrap(result, "entity_entry")

    async def update_entity_registry(self, entity_id: str, **changes: Any) -> dict[str, Any]:
        """``config/entity_registry/update`` — apply only the fields passed.

        Callers must pass **only** the keys they intend to change: HA treats an
        absent key as "leave alone" and an explicit ``null`` as "clear", so
        forwarding unset params would silently wipe fields.
        """
        result = await self.command(
            "config/entity_registry/update", entity_id=entity_id, **changes
        )
        return self._unwrap(result, "entity_entry")

    async def list_device_registry(self) -> list[dict[str, Any]]:
        """``config/device_registry/list`` → devices with area and config-entry links."""
        return self._as_list(await self.command("config/device_registry/list"))

    async def update_device_registry(self, device_id: str, **changes: Any) -> dict[str, Any]:
        """``config/device_registry/update`` — same only-what-changed rule as entities."""
        result = await self.command(
            "config/device_registry/update", device_id=device_id, **changes
        )
        return self._unwrap(result, "device_entry")

    async def list_area_registry(self) -> list[dict[str, Any]]:
        """``config/area_registry/list`` → areas **with their ``area_id``**.

        The REST-derived ``ha_list_areas`` shows names only; assigning an entity to
        an area needs the id, which only this command exposes.
        """
        return self._as_list(await self.command("config/area_registry/list"))

    async def create_area(self, name: str) -> dict[str, Any]:
        """``config/area_registry/create`` — add a new area."""
        return self._unwrap(await self.command("config/area_registry/create", name=name), "area")

    async def update_area(self, area_id: str, name: str) -> dict[str, Any]:
        """``config/area_registry/update`` — rename an existing area."""
        result = await self.command("config/area_registry/update", area_id=area_id, name=name)
        return self._unwrap(result, "area")

    # NOTE: there is deliberately no delete_config_entry here. Home Assistant
    # exposes no WebSocket command for it — both config_entries/delete and
    # config_entries/remove answer `unknown_command` (verified live 2026-09-03).
    # Deletion lives on the REST client: HomeAssistantClient.delete_config_entry.

    async def search_related(self, item_type: str, item_id: str) -> dict[str, list[str]]:
        """``search/related`` → what else in HA references this item.

        Backs the ``entity_id``-rename reference check: HA does not rewrite
        references when an ``entity_id`` changes, so anything listed here under
        ``automation``/``script``/``scene`` would silently break.
        """
        result = await self.command("search/related", item_type=item_type, item_id=item_id)
        if not isinstance(result, dict):
            return {}
        related: dict[str, list[str]] = {}
        for key, value in result.items():
            if isinstance(value, list):
                related[str(key)] = [str(item) for item in value]
        return related
