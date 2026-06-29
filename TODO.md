# TODO

## Feature Expansion (reviewed batch, June 2026)

> Source docs reviewed against the live 14-tool surface; inventory in the batch index was stale. Status below reflects what's actually net-new.

- [~] **`ha_get_camera_snapshot` (Task 02)** — Built (uncommitted), in verification. Image-returning tool via HA `camera_proxy` + Pillow downscale; resolves `camera.*` or occupancy sensors. The one genuine new capability (model gains sight; ties to Vigil). Pending: Wrecker e2e, deploy.
- [ ] **`ha_get_logbook` (Task 04A)** — Cross-entity event stream ("catch me up on the property"). Low-risk additive read, mirrors `ha_get_history`. Net-new.
- [ ] **`ha_media_player_control` (Task 03)** — Typed media tool + deep-link knowledge module (Disney+/Apple TV/Watchmode). Biggest surface; **supersedes the Apple TV play helper below**. Action tool — confirm gating.
- [ ] **Automations enhancement (Task 01)** — `ha_trigger_automation` already exists; remaining work is `ha_list_automations` (sugar over `list_entities`) + a `skip_condition` param on the existing trigger. Low value/effort.

## Convenience Tools

- [ ] **Apple TV play helper** — Single tool that handles: wake → select user → open deep link → wait ~1s → select. Eliminates the multi-step dance for playing content. Could generalize to other media players. *(Folded into Task 03 above.)*

## Future Considerations

- [ ] **Entity registry caching** — TTL cache (~60s) on area/entity data to reduce HA API load
- [ ] **Fuzzy search** — Upgrade `ha_search_entities` from substring to Levenshtein if needed
- [ ] **WebSocket API** — Real-time subscriptions for state changes (Phase 6 territory)

## Done

- [x] **OAuth for HTTP transport** — Solved at the edge, not in-app. Public access for the Claude app uses Cloudflare Tunnel + Cloudflare Access Managed OAuth (GitHub IdP, email-allow policy) as the OAuth authz server; no app-level OAuth code needed for the connect path. Hostname `mcp.example.com`, endpoint `/mcp`. See `docs/cloudflare-access.md`. (Remaining in-server JWT hardening tracked under Infrastructure.)
- [x] **In-server Cf-Access-Jwt-Assertion validation** — Built: `access.py` ASGI middleware verifies the `Cf-Access-Jwt-Assertion` header (signature/`aud`/`iss`/`email`) against the team JWKS. Config-gated (`HA_REQUIRE_ACCESS_JWT`, default on for HTTP), stdio unaffected. **Activates on the next image deploy** (`./scripts/deploy.sh`). AUD `<your-access-aud>`.
- [x] **Lock down mcp-ha endpoints** — Removed the unauthenticated tailnet Traefik IngressRoute (`mcp.example.internal`); HA is reached via a different tailnet URL. Added a default-deny NetworkPolicy so only the cloudflared tunnel pods reach `:8100`. Pinned Access `allowed_idps` to GitHub/LinkedIn/Cloudflare.
- [x] ~~**TLS certs for mcp.example.internal**~~ — Superseded: that tailnet IngressRoute was removed (see lock-down above). Public path uses `mcp.example.com` via Cloudflare Tunnel.
- [x] **Area-aware queries (Task 04B)** — Already shipped: `ha_list_areas` + `ha_get_area_summary` (`tools/areas.py`) plus `ha_list_entities(area=…)`. The batch doc's "investigate first" answer is that it exists; nothing to build.
- [x] **Scene support** — `ha_list_scenes` and `ha_activate_scene` tools.
