# homeassistant-mcp

MCP server wrapping the Home Assistant REST API. Exposes home automation capabilities as MCP tools for use with Claude Code, Open WebUI, and other MCP-compatible clients.

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env with your HA_URL and HA_TOKEN
```

## Usage

```bash
# stdio mode (Claude Code)
uv run homeassistant-mcp

# HTTP mode (remote clients)
HA_TRANSPORT=http uv run homeassistant-mcp
```

For public exposure to the Claude app (Cloudflare Tunnel + Access Managed OAuth),
see [docs/cloudflare-access.md](docs/cloudflare-access.md).

## Person management (WebSocket write path)

Most tools wrap HA's REST API, but REST cannot mutate the registry. Creating a
person and binding device_trackers is a storage-collection op HA exposes only over
its **WebSocket API** (`/api/websocket`), so the server also carries a small
WebSocket client (`ha_ws.py`) that opens a fresh connection per config operation.

Tools: `ha_list_persons` (read, ungated), `ha_create_person`, `ha_update_person`,
`ha_delete_person`. The write ops require an **admin** HA token, are gated by
`HA_PERSON_WRITES_ENABLED` (a fail-closed kill switch), and are audit-logged; delete
additionally requires `confirm=True`. Provide a dedicated admin token via
`HA_WS_TOKEN` to keep it distinct from the read/control `HA_TOKEN`. These tools bind
*existing* device_trackers — the tracker source (Companion app / router integration)
must already exist.

## Registry management (WebSocket write path)

The same WebSocket client backs the **registry** tools — reads that REST cannot do
(`area_id`, `disabled_by`, `unique_id`, `config_entry_id`) and the writes REST cannot
do at all. See [docs/registry-write-tools.md](docs/registry-write-tools.md).

Reads (ungated): `ha_list_config_entries` (finds duplicate integrations),
`ha_get_registry` (entity/device/area registry; the only source of `area_id`).

Writes (gated by `HA_REGISTRY_WRITE_ENABLED`, audit-logged, admin token required):
`ha_rename_entity`, `ha_assign_entity_area`, `ha_disable_entity`, `ha_create_area`,
`ha_rename_area`, `ha_assign_device_area`, `ha_rename_device`.

Destructive (double-gated): `ha_delete_config_entry` removes an integration instance
and every entity and device it owns. It needs `HA_REGISTRY_DESTRUCTIVE_ENABLED` on top
of the write gate, and is **dry-run until `confirm=True`** — the unconfirmed call
reports exactly which entities and devices would go, so you can check you have the
duplicate and not the original. Devices shared with another config entry survive and
are reported separately.

Unlike `HA_PERSON_WRITES_ENABLED`, this gate defaults **off** — a registry change can
break references system-wide. In particular, changing an entity's `entity_id` (as
opposed to its display `name`) breaks every automation, script, scene, and dashboard
referencing the old id, because HA does not rewrite them. `ha_rename_entity`
reference-checks via HA's `search/related` and refuses when it finds any, listing
them; it also refuses if the check itself cannot run. `force=True` overrides.

## Deployment

Deployed to Gonk K3s via GitOps — no manual steps. A push to `main` is built and
pushed to `registry.example.internal` by CI (`.github/workflows/build-push.yml`);
Flux image automation selects the new tag and rolls the cluster. The Kubernetes
manifests live in [the infra repo](the infra repo) under
`k8s/workloads/homeassistant-mcp/`.
