# Public exposure: Cloudflare Tunnel + Access Managed OAuth

How `homeassistant-mcp` is reached by the **Claude mobile / web app** over the
public internet. Proven working 2026-06-29.

**Canonical runbook (do the steps there):**
`<comlink docs>`
This doc records only the HA-specific values and the gotcha that bit us.

## Architecture

```
Claude app ──HTTPS──> Cloudflare edge ──┐
                      (Access = OAuth    │  cloudflared tunnel "gonk"
                       authz server,     ▼
                       Managed OAuth)   Gonk K3s
                                        automation/ svc <service-cluster-ip>:8100
                                        (FastMCP streamable-http)
```

- **Edge auth only.** Cloudflare Access is the OAuth authorization server
  (GitHub IdP, email-allow policy). The MCP server implements **no** OAuth — it
  trusts the tunnel. See hardening below for the planned second gate.
- **Two-domain model:**
  - `*.example.internal` → Tailscale-private, served by Traefik IngressRoute
    (the existing `mcp.example.internal` LE cert path).
  - `*.chaosbit.dev` single-level → public, via the `gonk` tunnel, gated by
    Cloudflare Access. This doc is about that public path.

## HA-specific values

| Thing | Value |
|---|---|
| Public hostname | `mcp.example.com` (**single-level — see trap**) |
| MCP endpoint | `https://mcp.example.com/mcp` |
| Tunnel | `gonk` (cloudflared) |
| Origin | Gonk K3s, `automation` ns, svc ClusterIP `<service-cluster-ip>:8100` |
| Access app name | `mcp-ha` |
| Access app id | `<your-access-app-id>` |
| Access AUD tag | `<your-access-aud>` |
| Access policy | email-allow: `user@example.com`, `alt@example.com` |

## The single-level hostname trap

Use `mcp.example.com`. The original two-level name
`mcp.remote.example.com` **failed TLS** (`ERR_SSL_VERSION_OR_CIPHER_MISMATCH`)
because the free Universal SSL wildcard `*.chaosbit.dev` covers **one level
only** — a deeper name has no edge cert. Don't pay for Total TLS; just keep the
name single-level.

## Enable / verify DCR

Managed OAuth's Dynamic Client Registration is OFF by default with no dashboard
UI. The Claude app can't self-register until DCR is enabled on the Access app.
Use the comlink helper scripts (service-agnostic, keyed by `MCP_APP_DOMAIN`) —
don't copy them here. Run from the comlink repo:

```bash
cd <comlink docs>

# inspect current OAuth/DCR config (read-only)
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=$(security find-generic-password -s cloudflare-api -w) \
  uv run python experiments/auth_probe/cf_inspect.py

# dry-run, then apply (backs up app, GET→merge→PUT, preserves policy)
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=$(security find-generic-password -s cloudflare-api -w) \
  uv run python experiments/auth_probe/cf_enable_dcr.py            # add --apply to write
```

## Verify

```bash
# edge + Access healthy: unauthenticated /mcp must be 401 Bearer challenge
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 8 https://mcp.example.com/mcp
# expect 401 (WWW-Authenticate: Bearer). 302/403 -> login redirect is also fine.

# DCR live: 404 -> 200 once a client can register
cd <comlink docs>
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=$(security find-generic-password -s cloudflare-api -w) \
  uv run python experiments/auth_probe/cf_dcr_probe.py
```

## Connect the Claude app

Add a custom connector → `https://mcp.example.com/mcp`, complete the
Cloudflare → GitHub login with an allow-listed email. Success: `POST /mcp 200`,
a session id, and `ListToolsRequest` in the origin logs.

## Hardening follow-up (NOT done)

The server currently trusts the tunnel. Add in-server validation of the
`Cf-Access-Jwt-Assertion` header as a second gate: verify the JWT `aud` equals
the mcp-ha AUD tag
`<your-access-aud>`
(signing keys at `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`).
Tracked in `TODO.md`.
