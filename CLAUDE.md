# CLAUDE.md

FastMCP server wrapping Home Assistant. Python 3.13, `uv`, strict mypy, ruff (line
length 99). Tools live in `src/homeassistant_mcp/tools/`; each imports the shared
`mcp` instance from `server.py` and registers via `@mcp.tool()`. Register a new tool
module by adding its import to `server.py`.

## Transports

- **REST** (`client.py`, httpx) — state reads + service calls. The default path.
- **WebSocket** (`ha_ws.py`, websockets) — registry/config mutation. WS is the ONLY
  path for registry ops; REST **cannot** mutate the registry. Ephemeral-per-op:
  each command opens a fresh connection (connect → auth → send → result → close).

## WebSocket write surface

Both WS write modules require an **admin** HA token (`HA_WS_TOKEN`, falls back to
`HA_TOKEN`), check their kill switch *before* opening any socket, and audit-log every
write to the `homeassistant_mcp.audit` logger.

- **Person** (`tools/person.py`, v1) — gated by `HA_PERSON_WRITES_ENABLED`
  (default **on**); delete is confirm-required.
- **Registry** (`tools/registry.py`, v2) — entity/device/area registry reads + safe
  writes, gated by `HA_REGISTRY_WRITE_ENABLED` (default **off**, because a registry
  change can break references system-wide). Design: `docs/registry-write-tools.md`.
  Phase 2 (`ha_delete_config_entry`, dry-run + double-gated) is not built yet.

**The `entity_id` footgun:** changing an entity's `entity_id` breaks every automation,
script, scene, and dashboard referencing the old id — HA does not rewrite references.
`ha_rename_entity` checks via `search/related` and refuses when it finds any; the check
**fails closed** (if it can't run, the rename is refused). Changing the display `name`
is always safe and is the common case.

**WS footguns:** the auth message MUST be the first frame after connect; the message
`id` must strictly increase per connection; a `result` with `success: false` is a
FAILURE even though the socket was happy — surface HA's `error.message`, never treat
"got a response" as "it worked".

## Dev loop

```bash
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy src           # types (strict)
```

## Deploy

GitOps: push to `master` → CI builds/pushes image → Flux rolls the cluster. Manifests
in the `the infra repo` repo. Update the claude.ai MCP connector when the tool list
changes (new tools won't appear until the connector re-reads the tool list).
