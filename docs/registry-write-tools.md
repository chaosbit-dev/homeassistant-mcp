# Design: registry-write tools (WebSocket transport)

**Status:** proposed · **Author:** design session 2026-08-01 · **Tracking:** the old tracker (Homelab Board, "HA MCP: registry-write tools")

## Problem

The MCP is **REST-only** (`HomeAssistantClient` over `httpx`, `/api/*`). Home
Assistant's **registry** operations — renaming an entity, assigning an entity or
device to an area, creating an area, disabling an entity, deleting a config entry
(integration instance) — are **not in the REST API**. They live only on the
**WebSocket API** (`/api/websocket`, `config/entity_registry/*`,
`config/area_registry/*`, `config/device_registry/*`, `config_entries/*`).

Consequence surfaced by the 2026-08-01 HA audit: the cleanup backlog (dedup
duplicate integrations, rename MAC/hex/model entities for voice, assign orphaned
cameras to areas, add kids' rooms) **cannot be done through the MCP** — only in
the HA UI. As HA management scales (voice-assistant prep especially), that gap
gets expensive.

## Goal

Add a WebSocket transport and a set of **registry-write tools**, so registry
management is programmable through the MCP — reusable well beyond the one-time
cleanup. Non-goal: replacing the REST client (states/services/history stay REST).

## Design

### 1. WebSocket client (`ws_client.py`)

A thin async client alongside `HomeAssistantClient`, reusing the **same
Long-Lived Access Token** (`Settings.token`) and base URL:

- Connect `wss?://{host}/api/websocket`; handle the `auth_required` → `auth`
  (`{"type":"auth","access_token": <LLAT>}`) → `auth_ok` handshake.
- **id-correlated request/response**: monotonic `id`, futures keyed by id, single
  reader task fans results back. One long-lived connection (reconnect on drop),
  **not** a socket per call — mirrors the dropbear/one-session discipline used
  elsewhere in the homelab.
- Timeout + typed errors (`WSCommandError`) so tools fail cleanly.
- `httpx` can't do WS; add `websockets` (or `aiohttp`) as a dep.

### 2. Tools (`tools/registry.py`)

FastMCP tools, annotated by risk (the codebase already uses
`@mcp.tool(annotations=READ_ONLY)`; writes use the destructive/idempotent hints):

**Read (fills gaps REST can't):**
- `ha_list_config_entries` — integrations + their entry_ids (needed to find dupes)
- `ha_get_registry` — entity/device/area registry detail (area_id, disabled_by, unique_id, config_entry_id)

**Safe writes (Phase 1):**
- `ha_rename_entity(entity_id, name=None, new_entity_id=None)` → `config/entity_registry/update`
- `ha_assign_entity_area(entity_id, area_id)` → `config/entity_registry/update`
- `ha_disable_entity(entity_id, disabled=True)` → `config/entity_registry/update` (`disabled_by`)
- `ha_create_area(name)` / `ha_rename_area` → `config/area_registry/*`
- `ha_assign_device_area(device_id, area_id)` / `ha_rename_device(device_id, name_by_user)` → `config/device_registry/update`

**Destructive (Phase 2, gated):**
- `ha_delete_config_entry(entry_id)` → `config_entries/delete`. Removes an
  integration instance **and all its entities/devices** — this is the "dedup the
  duplicate ASUS/Brother/Denon/TV integrations" tool and the one that can do real
  damage.

### 3. Guardrails (load-bearing)

Registry writes — especially config-entry delete — are high-blast-radius, and the
MCP is reachable by any Claude session that passes Access. So:

- **`registry_write_enabled: bool = False`** (new `Settings` field). All write
  tools no-op-refuse unless enabled. Read tools always available.
- **Destructive ops double-gated:** `ha_delete_config_entry` additionally requires
  `registry_destructive_enabled: bool = False` **and** is **dry-run by default**
  (returns exactly what would be deleted — entry + its entities/devices — and
  makes no change unless `confirm=True`).
- Keep the existing **Cloudflare Access JWT** gate (`require_access_jwt`,
  `access_allowed_emails`) — only the operator reaches these at all.
- **Audit log** every write (tool, target, before→after) at INFO.
- Consider surfacing writes only on the `http` transport used in-cluster, not the
  stdio/dev transport, if that separation is useful.

### 4. Deploy

Build + push the image via the infra repo CI → Flux (the MCP runs in-cluster behind
Cloudflare Access + a default-deny NetworkPolicy admitting only cloudflared —
unchanged). Ship with `registry_write_enabled=false`; flip on deliberately.

## Phasing

1. **WS transport + read + safe writes** (rename/area/disable). Unblocks the
   audit's renames + area assignments + kids' rooms. Lower risk.
2. **`ha_delete_config_entry`** (dry-run, double-gated). Then execute the
   integration dedup (2nd ASUS router, 2nd Brother, 2nd Denon, dead TV players).
3. Use the tools to run the full HA-audit cleanup; retire the UI checklist.

## Testing

- Mock WS server (asserts handshake + command frames) for unit tests per tool.
- A dry-run integration test against a throwaway area/entity before trusting the
  destructive path.

## Alternatives considered

- **UI, one-time:** ~15 min for tonight's cleanup, zero code — but no reusable
  capability, and every future rename/area change is manual again.
- **REST-only workarounds:** none exist — registry ops aren't in REST. WS is
  required.
