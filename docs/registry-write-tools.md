# Design: registry-write tools (WebSocket transport)

**Status:** implemented (phases 1–2; phase 3 is operational work, not code) · **Author:** design session 2026-08-01

> **Revised 2026-08-01.** The first draft was written against a REST-only server and
> proposed building a WebSocket client from scratch. That is now stale: the WS
> transport shipped in **PR #1** (person management, `ha_ws.py`). §1 is rewritten to
> reuse it, and the ephemeral-vs-persistent connection question it raised is settled
> below. Tool surface, guardrails, and phasing are otherwise unchanged.

## Problem

Home Assistant's **registry** operations — renaming an entity, assigning an entity or
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

Add **registry-write tools** on the existing WebSocket transport, so registry
management is programmable through the MCP — reusable well beyond the one-time
cleanup. Non-goal: replacing the REST client (states/services/history stay REST).
Non-goal: real-time state subscriptions (see §1.2).

## Design

### 1. WebSocket client — reuse `ha_ws.py` (already shipped)

No new client. `HomeAssistantWebSocketClient` (`ha_ws.py`, PR #1) already provides
the auth handshake, id-correlated command/result, and typed errors
(`HomeAssistantWSError` / `HomeAssistantAuthError` / `HomeAssistantCommandError`).
Its `command(command_type, **fields)` dispatch was written generically so registry
commands land as **new callers, not a refactor**. Registry work adds convenience
methods alongside the existing person ones.

Config already exists and is unchanged by this design: `websocket_url` (derived
from `url`, http→ws / https→wss + `/api/websocket`), `websocket_token`
(`HA_WS_TOKEN`, falling back to `HA_TOKEN`), `ws_timeout`.

**Token requirement:** registry mutation requires an **admin** HA token. The
draft's "reuse the same LLAT" is only correct if that LLAT belongs to an admin
user. `HA_WS_TOKEN` exists precisely so a narrowly-scoped admin token can be
provisioned separately, and a read/control-token compromise does not also grant
registry write.

#### 1.1 Connection model: ephemeral per operation (decided)

The draft specified one long-lived connection with monotonic ids, futures keyed by
id, a reader task, and reconnect-on-drop. **We are keeping `ha_ws.py`'s
ephemeral-per-op model instead:** each command opens a fresh connection
(connect → auth → send one command at `id: 1` → await `result` → close).

Rationale:

- **Registry writes are rare and one-shot.** A rename or area assignment is a
  handful of calls in a cleanup session, not a hot path. Connection setup is not
  the bottleneck; there is nothing to amortize.
- **Blast-radius containment.** A persistent connection holds an **admin-capable**
  socket open indefinitely in a pod reachable through Cloudflare Access. Ephemeral
  means that socket exists only for the milliseconds of an actual mutation. This
  was a deliberate security decision in PR #1, and registry writes — higher blast
  radius than person writes — strengthen rather than weaken it.
- **No lifecycle to maintain.** No keepalive, reconnect, backoff, or fan-out
  reader task, and no partially-torn-down socket to reason about mid-mutation.
- **It's already built and tested** (`tests/test_ha_ws.py`), against the exact
  footguns HA's WS API has.

#### 1.2 If subscriptions ever arrive

A persistent connection is genuinely required for `subscribe_events`
(`state_changed`) / `subscribe_trigger` — but that is a **different use case with
the opposite lifetime requirement**, and it is not this design.

It is also worth being clear about what subscriptions would and would not buy an
**MCP server**, since the two get conflated. MCP is pull-based: nothing listens to
this server between tool calls. Current state ("where is Alice?") is already a
REST read via `ha_get_entity_state` — `person.*` carries the zone as its state plus
`source` / `latitude` / `longitude` attributes. Historical state ("when did they get
home?") is already `ha_get_history` / `ha_get_logbook`, backed by HA's recorder. A
subscription only pays off if the server maintains its own cache or **initiates**
action — and event-driven action is what HA automations are for; the MCP should not
grow a second automation engine that only runs while a socket happens to be up.

The plausible future payoffs are narrow: invalidating an entity-registry cache on
`entity_registry_updated` (vs. the simpler ~60s TTL in `TODO.md`), or a long-running
watch mode. If either lands, add a `subscribe()` async-context-manager as a
**second mode** on the same client, alongside `command()` — additive, not a
rewrite of the request/response path. Choosing ephemeral now does not foreclose it.

### 2. Tools (`tools/registry.py`)

FastMCP tools, annotated by risk, following the `tools/person.py` pattern
(`READ_ONLY` / `WRITE_ACTION` / `DESTRUCTIVE_ACTION` annotations, gate checked
before any socket is opened, HA's `success: false` surfaced as a tool error):

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
  Note the deliberate asymmetry with `person_writes_enabled`, which defaults
  **on**: person writes touch one storage collection, registry writes can break
  every reference in the system. This one ships off and gets flipped on
  deliberately.
- **Destructive ops double-gated:** `ha_delete_config_entry` additionally requires
  `registry_destructive_enabled: bool = False` **and** is **dry-run by default**
  (returns exactly what would be deleted — entry + its entities/devices — and
  makes no change unless `confirm=True`).
- **`entity_id` rename is reference-checked.** Changing an `entity_id` (as opposed
  to the friendly `name`) silently breaks every automation, script, scene, and
  dashboard referencing the old id — HA does not rewrite references. `name`-only
  renames are safe and are the common case for voice-readiness work.
  `new_entity_id` must therefore: scan automations/scripts/scenes for references
  to the old id, refuse (or require an explicit override) when any are found, and
  report them so the caller can fix them. This is the single biggest footgun in
  the whole surface.
- Keep the existing **Cloudflare Access JWT** gate (`require_access_jwt`,
  `access_allowed_emails`) — only the operator reaches these at all.
- **Audit log** every write (tool, target, before→after) at INFO, via the existing
  `homeassistant_mcp.audit` logger.
- Consider surfacing writes only on the `http` transport used in-cluster, not the
  stdio/dev transport, if that separation is useful.

### 4. Deploy

Build + push the image via the infrastructure repo's CI → Flux (the MCP runs in-cluster behind
Cloudflare Access + a default-deny NetworkPolicy admitting only cloudflared —
unchanged). Ship with `registry_write_enabled=false`; flip on deliberately.
Update the claude.ai MCP connector once the tool list changes.

## Phasing

1. ✅ **Read + safe writes** (rename/area/disable) on the existing client — shipped
   in **PR #3**. Unblocks the audit's renames + area assignments + kids' rooms.
   *(The draft's "WS transport" step was already done in PR #1.)*
2. ✅ **`ha_delete_config_entry`** (dry-run, double-gated) — shipped in **PR #4**.
3. ⬜ Use the tools to run the full HA-audit cleanup (integration dedup: 2nd ASUS
   router, 2nd Brother, 2nd Denon, dead TV players; voice renames; area
   assignments) and retire the UI checklist. Operational, not code — it needs
   `HA_REGISTRY_WRITE_ENABLED` (and `HA_REGISTRY_DESTRUCTIVE_ENABLED` for the
   dedup) flipped on in the deployment config, ideally after a read-only smoke test since the
   WS command shapes are covered by scripted fakes rather than a live HA.

## Testing

- Reuse the injected-connector pattern from `tests/test_ha_ws.py`: a scripted fake
  connection asserts the handshake and command frames per tool, with no live HA.
- Negative tests per `tests/test_person.py`: writes refused when the gate is off
  (no socket opened), destructive refused without `confirm`, HA `success: false`
  surfaced rather than silently passed.
- Reference-check tests for `new_entity_id`: refuses when an automation references
  the old id, allows a `name`-only rename unconditionally.
- A dry-run integration test against a throwaway area/entity before trusting the
  destructive path.

## Alternatives considered

- **UI, one-time:** ~15 min for tonight's cleanup, zero code — but no reusable
  capability, and every future rename/area change is manual again.
- **REST-only workarounds:** none exist — registry ops aren't in REST. WS is
  required.
- **Persistent WS connection:** see §1.1. Rejected for this scope; revisit only
  if subscriptions land, and then as an additive second mode (§1.2).
