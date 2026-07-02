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

## Person management (WebSocket)

HA MCP gained a WebSocket client (`ha_ws.py`) for registry/config mutation, starting
with person management. Person tools (`tools/person.py`) are gated
(`HA_PERSON_WRITES_ENABLED` kill switch) + audit-logged; delete is confirm-required.
Requires an **admin** HA token (`HA_WS_TOKEN`, falls back to `HA_TOKEN`). v1 is
person-only by design — entity/area registry mutation is a deliberate future scope
(v2) behind the same gating.

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
