# TODO

## Feature Expansion (reviewed batch, June 2026) — COMPLETE

> Source docs reviewed against the live 14-tool surface (index was stale). All five
> addressed; tool surface is now 18. Camera/logbook/media are deployed; the
> automations enhancement ships on the next push to master (CI → registry → Flux).

- [x] **`ha_get_camera_snapshot` (Task 02)** — Deployed. Image via HA `camera_proxy` + Pillow downscale; resolves `camera.*` or occupancy sensors. Wrecker-validated on live cameras.
- [x] **`ha_get_logbook` (Task 04A)** — Deployed. Cross-entity event stream, capped at `HA_LOGBOOK_MAX_EVENTS`, default-drops noisy `image` domain (`HA_LOGBOOK_EXCLUDE_DOMAINS`).
- [x] **`ha_media_player_control` (Task 03)** — Deployed. Typed media tool + `media/deeplinks.py` (Disney+ v1; Watchmode/Companion deferred to v2).
- [x] **Automations enhancement (Task 01)** — Built + committed. `ha_list_automations` (enabled state + last_triggered) and a `skip_condition` param on `ha_trigger_automation` (defaults to respecting conditions). **Pending deploy.**
- [x] **Area-aware queries (Task 04B)** — Already existed (`ha_list_areas`, `ha_get_area_summary`); nothing to build.

## Future Considerations

- [ ] **Entity registry caching** — TTL cache (~60s) on area/entity data to reduce HA API load
- [ ] **Fuzzy search** — Upgrade `ha_search_entities` from substring to Levenshtein if needed
- [ ] **WebSocket API** — Real-time subscriptions for state changes (Phase 6 territory)

## Done

- [x] **OAuth for HTTP transport** — Solved at the edge, not in-app. Public access for the Claude app uses Cloudflare Tunnel + Cloudflare Access Managed OAuth (GitHub IdP, email-allow policy) as the OAuth authz server; no app-level OAuth code needed for the connect path. Hostname `mcp.example.com`, endpoint `/mcp`. See `docs/cloudflare-access.md`. (Remaining in-server JWT hardening tracked under Infrastructure.)
- [x] **In-server Cf-Access-Jwt-Assertion validation** — Built: `access.py` ASGI middleware verifies the `Cf-Access-Jwt-Assertion` header (signature/`aud`/`iss`/`email`) against the team JWKS. Config-gated (`HA_REQUIRE_ACCESS_JWT`, default on for HTTP), stdio unaffected. **Live** (deployed via GitOps: push to master → CI → Flux). AUD `<your-access-aud>`.
- [x] **Lock down mcp-ha endpoints** — Removed the unauthenticated tailnet Traefik IngressRoute (`mcp.example.internal`); HA is reached via a different tailnet URL. Added a default-deny NetworkPolicy so only the cloudflared tunnel pods reach `:8100`. Pinned Access `allowed_idps` to GitHub/LinkedIn/Cloudflare.
- [x] ~~**TLS certs for mcp.example.internal**~~ — Superseded: that tailnet IngressRoute was removed (see lock-down above). Public path uses `mcp.example.com` via Cloudflare Tunnel.
- [x] **Scene support** — `ha_list_scenes` and `ha_activate_scene` tools.
