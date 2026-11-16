# TODO

## Feature Expansion (reviewed batch, June 2026) — COMPLETE

> Source docs reviewed against the live 14-tool surface (index was stale). All five
> addressed; tool surface is now 18. Camera/logbook/media are deployed; the
> automations enhancement ships on the next push to main (CI → registry → Flux).

- [x] **`ha_get_camera_snapshot` (Task 02)** — Deployed. Image via HA `camera_proxy` + Pillow downscale; resolves `camera.*` or occupancy sensors. Wrecker-validated on live cameras.
- [x] **`ha_get_logbook` (Task 04A)** — Deployed. Cross-entity event stream, capped at `HA_LOGBOOK_MAX_EVENTS`, default-drops noisy `image` domain (`HA_LOGBOOK_EXCLUDE_DOMAINS`).
- [x] **`ha_media_player_control` (Task 03)** — Deployed. Typed media tool + `media/deeplinks.py` (Disney+ v1; Watchmode/Companion deferred to v2).
- [x] **Automations enhancement (Task 01)** — Built + committed. `ha_list_automations` (enabled state + last_triggered) and a `skip_condition` param on `ha_trigger_automation` (defaults to respecting conditions). **Pending deploy.**
- [x] **Area-aware queries (Task 04B)** — Already existed (`ha_list_areas`, `ha_get_area_summary`); nothing to build.

## Future Considerations

- [ ] **Entity registry caching** — TTL cache (~60s) on area/entity data to reduce HA API load
- [ ] **Fuzzy search** — Upgrade `ha_search_entities` from substring to Levenshtein if needed
- [ ] **WebSocket API (subscriptions)** — Real-time `state_changed` streaming (Phase 6
      territory). The WS *transport* now exists (`ha_ws.py`, added for person mgmt), but
      it's ephemeral-per-op; subscriptions would need a persistent connection. Future fork.
- [ ] **v2 registry — Phase 3** — run the HA-audit cleanup through the tools (integration
      dedup, voice-readiness renames, area assignments, kids' rooms) and retire the UI
      checklist. Needs `HA_REGISTRY_WRITE_ENABLED` (and, for the dedup,
      `HA_REGISTRY_DESTRUCTIVE_ENABLED`) flipped on after a read-only smoke test.

## Done

- [x] **Registry tools via WebSocket (v2, Phase 2)** — `ha_delete_config_entry`
      (`config_entries/delete`). Double-gated (`HA_REGISTRY_DESTRUCTIVE_ENABLED` on top of
      the write gate) and **dry-run until `confirm=True`**. The unconfirmed call reports
      exactly which entities and devices would be removed; devices shared with another
      config entry are reported as kept, not deleted. A failed preview blocks the delete,
      and HA's `require_restart` is surfaced. Tool surface is now 32.
- [x] **Registry tools via WebSocket (v2, Phase 1)** — `tools/registry.py` on the existing
      `ha_ws.py` client (no transport work — the doc's §1 was stale; PR #1 already shipped it).
      Reads: `ha_list_config_entries` (flags duplicate integrations), `ha_get_registry`
      (entity/device/area; the only source of `area_id`). Safe writes: `ha_rename_entity`,
      `ha_assign_entity_area`, `ha_disable_entity`, `ha_create_area`, `ha_rename_area`,
      `ha_assign_device_area`, `ha_rename_device` — gated by `HA_REGISTRY_WRITE_ENABLED`
      (default **off**) + audit-logged. `entity_id` changes are reference-checked via
      `search/related` and fail closed. Tool surface is now 31.
- [x] **Person management via WebSocket (v1)** — Added `ha_ws.py` WebSocket client (auth
      handshake + id-correlated command/result, ephemeral-per-op) and `tools/person.py`:
      `ha_list_persons` (read, ungated), `ha_create_person`/`ha_update_person`/`ha_delete_person`
      (gated by `HA_PERSON_WRITES_ENABLED`, audit-logged; delete confirm-required). WS is the
      only path for registry ops (REST can't do it); needs an admin token (`HA_WS_TOKEN`, falls
      back to `HA_TOKEN`). Tool surface is now 22.
- [x] **OAuth for HTTP transport** — Solved at the edge, not in-app. Public access for the Claude app uses Cloudflare Tunnel + Cloudflare Access Managed OAuth (GitHub IdP, email-allow policy) as the OAuth authz server; no app-level OAuth code needed for the connect path. Hostname `mcp.example.com`, endpoint `/mcp`. See `docs/cloudflare-access.md`. (Remaining in-server JWT hardening tracked under Infrastructure.)
- [x] **In-server Cf-Access-Jwt-Assertion validation** — Built: `access.py` ASGI middleware verifies the `Cf-Access-Jwt-Assertion` header (signature/`aud`/`iss`/`email`) against the team JWKS. Config-gated (`HA_REQUIRE_ACCESS_JWT`, default on for HTTP), stdio unaffected. **Live** (deployed via GitOps: push to main → CI → Flux). AUD `<your-access-aud>`.
- [x] **Lock down mcp-ha endpoints** — Removed the unauthenticated tailnet Traefik IngressRoute (`mcp.example.internal`); HA is reached via a different tailnet URL. Added a default-deny NetworkPolicy so only the cloudflared tunnel pods reach `:8100`. Pinned Access `allowed_idps` to GitHub/LinkedIn/Cloudflare.
- [x] ~~**TLS certs for mcp.example.internal**~~ — Superseded: that tailnet IngressRoute was removed (see lock-down above). Public path uses `mcp.example.com` via Cloudflare Tunnel.
- [x] **Scene support** — `ha_list_scenes` and `ha_activate_scene` tools.
