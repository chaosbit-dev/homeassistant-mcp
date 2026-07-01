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

## Deployment

Deployed to Gonk K3s via GitOps — no manual steps. A push to `master` is built and
pushed to `registry.example.internal` by CI (`.github/workflows/build-push.yml`);
Flux image automation selects the new tag and rolls the cluster. The Kubernetes
manifests live in [the infra repo](the infra repo) under
`k8s/workloads/homeassistant-mcp/`.
