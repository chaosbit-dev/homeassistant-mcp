"""Configuration via environment variables."""

from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# DESIGN-GAP: These Cloudflare Access defaults are the live, non-secret values for
# the public `mcp.example.com` app (already committed in docs/cloudflare-access.md).
# The AUD tag is an identifier, not a credential, so baking it in as a default keeps
# the HTTP path fail-closed-secure by default while remaining overridable via env.
_DEFAULT_ACCESS_AUD = "<your-access-aud>"
_DEFAULT_ACCESS_TEAM_DOMAIN = "example.cloudflareaccess.com"
_DEFAULT_ACCESS_ALLOWED_EMAILS = ["user@example.com", "alt@example.com"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HA_")

    url: str = Field(
        description="Home Assistant base URL (e.g., http://ha.example.internal:8123)"
    )
    token: str = Field(description="Long-Lived Access Token from HA")
    transport: Literal["stdio", "http"] = Field(
        default="stdio",
        description="MCP transport mode",
    )
    http_port: int = Field(
        default=8100,
        description="Port for HTTP transport mode",
    )
    request_timeout: float = Field(
        default=10.0,
        description="HTTP request timeout in seconds",
    )

    # --- WebSocket / registry-config write path (person management) ---
    ws_url: str | None = Field(
        default=None,
        description=(
            "Home Assistant WebSocket API URL. If unset, it is derived from `url` "
            "by swapping http→ws / https→wss and appending /api/websocket. Override "
            "only when the WS endpoint differs from the REST host."
        ),
    )
    ws_token: str | None = Field(
        default=None,
        description=(
            "Long-lived token used for the WebSocket (registry/config) path. Registry "
            "mutation requires an ADMIN token. If unset, `token` is reused. Provision a "
            "separate, narrowly-scoped admin token here so a read/control-token "
            "compromise does not also grant registry write."
        ),
    )
    ws_timeout: float = Field(
        default=15.0,
        description="WebSocket connect/handshake/command timeout in seconds.",
    )
    person_writes_enabled: bool = Field(
        default=True,
        description=(
            "Master gate for person write ops (create/update/delete). When False the "
            "write tools refuse before opening any admin-capable socket; reads "
            "(ha_list_persons) are always allowed. This is the fail-closed kill switch."
        ),
    )
    registry_write_enabled: bool = Field(
        default=False,
        description=(
            "Master gate for entity/device/area registry writes (rename, assign area, "
            "create area, disable). Registry reads are always allowed. Defaults OFF — "
            "unlike person writes — because a registry change can break every reference "
            "in the system (see docs/registry-write-tools.md §3). Flip on deliberately."
        ),
    )

    # --- Camera snapshot processing ---
    camera_max_dimension: int = Field(
        default=1280,
        description=(
            "Max pixel size of a snapshot's longest side. Larger frames are "
            "downscaled (aspect preserved) before being returned to the model."
        ),
    )
    camera_jpeg_quality: int = Field(
        default=75,
        ge=1,
        le=95,
        description="JPEG quality (1-95) used when recompressing camera snapshots.",
    )

    # --- Logbook ---
    logbook_max_events: int = Field(
        default=150,
        ge=1,
        description=(
            "Max number of logbook events returned by ha_get_logbook. The HA "
            "logbook is high-volume (Frigate image entities and sensors churn "
            "constantly); results beyond this cap are truncated with a note."
        ),
    )
    logbook_exclude_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["image"],
        description=(
            "Entity domains excluded from ha_get_logbook on broad (no-entity_id) "
            "queries. Comma-separated in env (HA_LOGBOOK_EXCLUDE_DOMAINS). The "
            "`image` domain is Frigate snapshot entities (e.g. image.backyard_bird) "
            "that flood the logbook with non-events. Empty disables exclusion."
        ),
    )

    @field_validator("logbook_exclude_domains", mode="before")
    @classmethod
    def _split_exclude_domains(cls, value: object) -> object:
        """Accept comma-separated env strings or native lists; normalize lower."""
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            return [str(item).strip().lower() for item in value if str(item).strip()]
        return value

    # --- Cloudflare Access JWT validation (HTTP transport only) ---
    require_access_jwt: bool = Field(
        default=True,
        description=(
            "Enforce Cloudflare Access `Cf-Access-Jwt-Assertion` validation. "
            "Only takes effect when transport == 'http'; stdio/local usage is "
            "never affected."
        ),
    )
    access_aud: str = Field(
        default=_DEFAULT_ACCESS_AUD,
        description="Expected Cloudflare Access application AUD tag.",
    )
    access_team_domain: str = Field(
        default=_DEFAULT_ACCESS_TEAM_DOMAIN,
        description="Cloudflare Access team domain, e.g. <team>.cloudflareaccess.com.",
    )
    access_allowed_emails: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_ACCESS_ALLOWED_EMAILS),
        description=(
            "Allowlist of `email` claim values. Comma-separated in env "
            "(HA_ACCESS_ALLOWED_EMAILS). Empty list disables the email check."
        ),
    )

    @field_validator("access_allowed_emails", mode="before")
    @classmethod
    def _split_emails(cls, value: object) -> object:
        """Accept comma-separated env strings as well as native lists."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def websocket_url(self) -> str:
        """WebSocket API URL, derived from `url` when `ws_url` is unset.

        Swaps the REST scheme for its WS equivalent (http→ws, https→wss) and
        appends the `/api/websocket` path HA serves the WebSocket API on.
        """
        if self.ws_url:
            return self.ws_url
        base = self.url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/api/websocket"

    @property
    def websocket_token(self) -> str:
        """Token for the WebSocket path — the dedicated admin token or `token`."""
        return self.ws_token or self.token

    @property
    def access_issuer(self) -> str:
        """Expected JWT `iss` claim for the configured team."""
        return f"https://{self.access_team_domain}"

    @property
    def access_jwks_url(self) -> str:
        """Cloudflare Access signing-key (JWKS) endpoint for the configured team."""
        return f"https://{self.access_team_domain}/cdn-cgi/access/certs"
