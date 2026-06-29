# TODO

## Convenience Tools

- [ ] **Apple TV play helper** — Single tool that handles: wake → select user → open deep link → wait ~1s → select. Eliminates the multi-step dance for playing content. Could generalize to other media players.

## Infrastructure

- [ ] **In-server Cf-Access-Jwt-Assertion validation** — The public path trusts the `gonk` tunnel today. Add a second gate: verify the `Cf-Access-Jwt-Assertion` header's `aud` == the mcp-ha AUD tag (`<your-access-aud>`), keys from the team `cloudflareaccess.com/cdn-cgi/access/certs` JWKS. See `docs/cloudflare-access.md`.

## Future Considerations

- [ ] **Entity registry caching** — TTL cache (~60s) on area/entity data to reduce HA API load
- [ ] **Fuzzy search** — Upgrade `ha_search_entities` from substring to Levenshtein if needed
- [ ] **WebSocket API** — Real-time subscriptions for state changes (Phase 6 territory)

## Done

- [x] **OAuth for HTTP transport** — Solved at the edge, not in-app. Public access for the Claude app uses Cloudflare Tunnel + Cloudflare Access Managed OAuth (GitHub IdP, email-allow policy) as the OAuth authz server; no app-level OAuth code needed for the connect path. Hostname `mcp.example.com`, endpoint `/mcp`. See `docs/cloudflare-access.md`. (Remaining in-server JWT hardening tracked under Infrastructure.)
- [x] **TLS certs for mcp.example.internal** — Traefik IngressRoute with `certResolver: cloudflare`. Let's Encrypt cert auto-renewing.
- [x] **Scene support** — `ha_list_scenes` and `ha_activate_scene` tools.
