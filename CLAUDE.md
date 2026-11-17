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
  (default **off**); delete is confirm-required. `ha_update_person` reads the
  person's current `device_trackers` back and re-sends them when the caller omits
  the field: HA's `person/update` defaults an **absent** `device_trackers` to `[]`,
  so omitting it wipes presence bindings. Explicit `[]` still clears.
- **Registry** (`tools/registry.py`, v2) — entity/device/area registry reads + writes,
  gated by `HA_REGISTRY_WRITE_ENABLED` (default **off**, because a registry change can
  break references system-wide). Design: `docs/registry-write-tools.md`.
- **Destructive** (`ha_delete_config_entry`) — needs `HA_REGISTRY_DESTRUCTIVE_ENABLED`
  *in addition to* the write gate, and stays **dry-run until `confirm=True`**. The
  two gates are independent on purpose: enabling writes for a rename must not also
  enable integration deletion. The impact preview is computed before anything is
  touched, so a failed preview blocks the delete.

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

CI runs exactly these three on every PR and on main (`.github/workflows/ci.yml`),
each step independently so one run reports every failure. It also runs
`uv sync --locked`, which fails if a dependency change skipped `uv lock`.

## Deploy

GitOps: push to `main` → CI builds/pushes image → Flux rolls the cluster. Manifests
in a separate private infrastructure repo. Update the claude.ai MCP connector when the tool list
changes (new tools won't appear until the connector re-reads the tool list).
