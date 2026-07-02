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

## Deployment

Deployed to Gonk K3s via GitOps — no manual steps. A push to `master` is built and
pushed to `registry.example.internal` by CI (`.github/workflows/build-push.yml`);
Flux image automation selects the new tag and rolls the cluster. The Kubernetes
manifests live in [the infra repo](the infra repo) under
`k8s/workloads/homeassistant-mcp/`.
