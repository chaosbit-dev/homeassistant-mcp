# Public exposure: Cloudflare Tunnel + Access Managed OAuth

How `homeassistant-mcp` is reached by the **Claude mobile / web app** over the
public internet. Proven working 2026-06-29.

**Canonical runbook (do the steps there):**
a separate Cloudflare MCP setup runbook
This doc records only the HA-specific values and the gotcha that bit us.

## Architecture

```
Claude app ──HTTPS──> Cloudflare edge ──┐
                      (Access = OAuth    │  cloudflared tunnel
                       authz server,     ▼
                       Managed OAuth)   Kubernetes cluster
                                        automation/ svc <service-cluster-ip>:8100
                                        (FastMCP streamable-http)
```

- **Edge OAuth, in-server JWT check.** Cloudflare Access is the OAuth authorization
  server (GitHub IdP, email-allow policy); the MCP server implements no OAuth. It does
  re-validate the `Cf-Access-Jwt-Assertion` header itself (see Hardening below).
- **Two-domain model:**
  - `*.home.example.com` → private (tailnet/LAN only), served by the cluster ingress.
  - `*.example.com` single-level → public, via the Cloudflare tunnel, gated by
    Cloudflare Access. This doc is about that public path.

## HA-specific values

| Thing | Value |
|---|---|
| Public hostname | `mcp.example.com` (**single-level — see trap**) |
| MCP endpoint | `https://mcp.example.com/mcp` |
| Tunnel | `<your-tunnel>` (cloudflared) |
| Origin | the cluster Service, `<namespace>/<service>:8100` |
| Access app name | `mcp-ha` |
| Access app id | `<your-access-app-id>` |
| Access AUD tag | `<your-access-aud>` |
| Access policy | email-allow: `user@example.com`, `alt@example.com` |

## The single-level hostname trap

Use `mcp.example.com`. The original two-level name
`mcp.remote.example.com` **failed TLS** (`ERR_SSL_VERSION_OR_CIPHER_MISMATCH`)
because the free Universal SSL wildcard `*.example.com` covers **one level
only** — a deeper name has no edge cert. Don't pay for Total TLS; just keep the
name single-level.

## Enable / verify DCR

Managed OAuth's Dynamic Client Registration is OFF by default with no dashboard
UI. The Claude app can't self-register until DCR is enabled on the Access app.
Use the comlink helper scripts (service-agnostic, keyed by `MCP_APP_DOMAIN`) —
don't copy them here. Run from the comlink repo:

```bash
cd path/to/comlink

# inspect current OAuth/DCR config (read-only)
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=<cloudflare-api-token> \
  uv run python experiments/auth_probe/cf_inspect.py

# dry-run, then apply (backs up app, GET→merge→PUT, preserves policy)
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=<cloudflare-api-token> \
  uv run python experiments/auth_probe/cf_enable_dcr.py            # add --apply to write
```

## Verify

```bash
# edge + Access healthy: unauthenticated /mcp must be 401 Bearer challenge
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 8 https://mcp.example.com/mcp
# expect 401 (WWW-Authenticate: Bearer). 302/403 -> login redirect is also fine.

# DCR live: 404 -> 200 once a client can register
cd path/to/comlink
MCP_APP_DOMAIN=mcp.example.com \
  CF_API_TOKEN=<cloudflare-api-token> \
  uv run python experiments/auth_probe/cf_dcr_probe.py
```

## Connect the Claude app

Add a custom connector → `https://mcp.example.com/mcp`, complete the
Cloudflare → GitHub login with an allow-listed email. Success: `POST /mcp 200`,
a session id, and `ListToolsRequest` in the origin logs.

## Hardening: in-server Access JWT check (done)

The server no longer trusts the tunnel alone. In HTTP mode it validates the
`Cf-Access-Jwt-Assertion` header on every request: signature against the team's
keys (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`), `aud` equal to
`HA_ACCESS_AUD`, `iss` equal to the team domain, and optionally the email against
`HA_ACCESS_ALLOWED_EMAILS`. It fails closed: startup errors unless `HA_ACCESS_AUD` and
`HA_ACCESS_TEAM_DOMAIN` are set, or `HA_REQUIRE_ACCESS_JWT=false`. See the README's HTTP
section.
