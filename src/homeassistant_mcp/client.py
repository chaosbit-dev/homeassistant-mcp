"""Async client for the Home Assistant REST API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from homeassistant_mcp.config import Settings


class CameraUnavailableError(RuntimeError):
    """Raised when a camera frame cannot be fetched (unavailable/404/etc.)."""


class HomeAssistantClient:
    """Async client for Home Assistant REST API."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.token}",
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(settings.request_timeout)
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api",
            headers=self._headers,
            timeout=self._timeout,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            msg = "Client not started. Call start() first."
            raise RuntimeError(msg)
        return self._client

    # ── State endpoints ──────────────────────────────────────────

    async def get_states(self) -> list[dict[str, Any]]:
        """GET /api/states — all entity states."""
        resp = await self.client.get("/states")
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json()
        return result

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        """GET /api/states/<entity_id> — single entity state."""
        resp = await self.client.get(f"/states/{entity_id}")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    # ── Template endpoint ────────────────────────────────────────

    async def render_template(self, template: str) -> str:
        """POST /api/template — render a Jinja2 template server-side."""
        resp = await self.client.post("/template", json={"template": template})
        resp.raise_for_status()
        return resp.text

    # ── Area helpers (via template API) ──────────────────────────

    async def get_areas(self) -> list[dict[str, Any]]:
        """Get all areas with their entity IDs via template rendering."""
        template = (
            "{%- set ns = namespace(result=[]) -%}"
            "{%- for aid in areas() -%}"
            "{%- set ns.result = ns.result + ["
            '{"area_id": aid, "name": area_name(aid), '
            '"entities": area_entities(aid)}'
            "] -%}"
            "{%- endfor -%}"
            "{{ ns.result | to_json }}"
        )
        raw = await self.render_template(template)
        result: list[dict[str, Any]] = json.loads(raw)
        return result

    async def get_entity_area_map(self) -> dict[str, str]:
        """Build a map of entity_id -> area_name for all area-assigned entities."""
        areas = await self.get_areas()
        entity_area: dict[str, str] = {}
        for area in areas:
            name: str = area["name"]
            for eid in area["entities"]:
                entity_area[str(eid)] = name
        return entity_area

    # ── Service endpoints ────────────────────────────────────────

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """POST /api/services/<domain>/<service>."""
        resp = await self.client.post(
            f"/services/{domain}/{service}",
            json=data or {},
        )
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json()
        return result

    # ── Camera endpoint ──────────────────────────────────────────

    async def get_camera_image(self, entity_id: str) -> bytes:
        """GET /api/camera_proxy/<entity_id> — current camera frame as raw bytes.

        Returns the JPEG bytes of the current frame. Raises CameraUnavailableError
        on any non-200 response (e.g. the camera is `unavailable` or unknown), so the
        caller can surface a clean tool error instead of returning a corrupt image.
        """
        resp = await self.client.get(f"/camera_proxy/{entity_id}")
        if resp.status_code != httpx.codes.OK:
            msg = (
                f"Could not fetch a camera frame for `{entity_id}` "
                f"(HTTP {resp.status_code}). The camera may be unavailable or unknown."
            )
            raise CameraUnavailableError(msg)
        return resp.content

    # ── History endpoint ─────────────────────────────────────────

    async def get_history(
        self,
        entity_id: str,
        start: str,
        end: str | None = None,
    ) -> list[list[dict[str, Any]]]:
        """GET /api/history/period/<timestamp>."""
        params: dict[str, str] = {"filter_entity_id": entity_id}
        if end:
            params["end_time"] = end
        resp = await self.client.get(f"/history/period/{start}", params=params)
        resp.raise_for_status()
        result: list[list[dict[str, Any]]] = resp.json()
        return result

    # ── Logbook endpoint ─────────────────────────────────────────

    async def get_logbook(
        self,
        start: str,
        end: str | None = None,
        entity: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/logbook/<timestamp>.

        Returns a FLAT array of logbook events (unlike history, which nests per
        entity). Each event carries `when`, `name`, `entity_id`, and either
        `state` or `message` depending on event type.
        """
        params: dict[str, str] = {}
        if end:
            params["end_time"] = end
        if entity:
            params["entity"] = entity
        resp = await self.client.get(f"/logbook/{start}", params=params)
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json()
        return result
