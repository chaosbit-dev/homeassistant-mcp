# TODO

## Convenience Tools

- [ ] **Apple TV play helper** — Single tool that handles: wake → select user → open deep link → wait ~1s → select. Eliminates the multi-step dance for playing content. Could generalize to other media players.

## Infrastructure

- [ ] **OAuth for HTTP transport** — Claude Code's HTTP MCP client requires OAuth authentication even on private networks. Add OAuth support so Claude Code and Claude Desktop can connect via streamable-http instead of stdio. FastMCP supports `auth_server_provider` and `token_verifier` in its constructor.

## Future Considerations

- [ ] **Entity registry caching** — TTL cache (~60s) on area/entity data to reduce HA API load
- [ ] **Fuzzy search** — Upgrade `ha_search_entities` from substring to Levenshtein if needed
- [ ] **WebSocket API** — Real-time subscriptions for state changes (Phase 6 territory)

## Done

- [x] **TLS certs for mcp.example.internal** — Traefik IngressRoute with `certResolver: cloudflare`. Let's Encrypt cert auto-renewing.
- [x] **Scene support** — `ha_list_scenes` and `ha_activate_scene` tools.
