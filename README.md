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
